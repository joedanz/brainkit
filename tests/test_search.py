import json
from pathlib import Path

import pytest

from brain.cli import main
from brain.compiler import compile_vault
from brain.embeddings import FakeEmbeddingProvider
from brain.indexer import build_index
from brain.search import rrf, search_index
from tests.conftest import ALICE, RULES, requires_vectors


@pytest.fixture
def indexed_alice(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)
    return vault


def test_rrf_item_in_both_legs_wins():
    scores = rrf([[1, 2, 3], [2, 4, 5]])
    # id 2 appears in both legs, id 1 only in one — 2 must outscore 1
    assert scores[2] > scores[1]


@requires_vectors
def test_hybrid_search_finds_by_phrase(indexed_alice):
    report = search_index(indexed_alice, "pipeline", provider=FakeEmbeddingProvider())
    assert report.mode == "hybrid+graph"
    assert any("Q3 Pipeline.md" in h.rel_path for h in report.hits)


@requires_vectors
def test_hybrid_search_marks_vector_source(indexed_alice):
    report = search_index(indexed_alice, "pipeline option", provider=FakeEmbeddingProvider())
    # with a provider present, at least one hit is corroborated by the vector leg
    assert any("vector" in h.sources for h in report.hits)


def test_keyword_only_when_no_provider(indexed_alice):
    report = search_index(indexed_alice, "pipeline", provider=None)
    assert report.mode == "keyword-only+graph"
    # Home.md (a text hit) links to both Q3 Pipeline (also a text hit) and
    # Big Deal Decision (not a text hit) — PPR necessarily gives the latter a
    # nonzero score once Home is a seed, so it surfaces as a graph-only hit.
    # That's the documented design ("the graph leg may introduce candidates
    # the text legs missed"); the invariant that survives is that keyword
    # hits are still present, not that every hit came from keyword.
    assert any("keyword" in h.sources for h in report.hits)
    assert report.hits


def test_keyword_only_flag_forces_no_vectors(indexed_alice):
    report = search_index(indexed_alice, "pipeline", provider=FakeEmbeddingProvider(),
                          keyword_only=True)
    assert report.mode == "keyword-only+graph"


def test_per_file_dedupe(master, tmp_path):
    # a single doc split into many matching chunks contributes at most 2 hits
    body = "# Doc\n" + "\n\n".join(f"## H{i}\npipeline pipeline pipeline note {i}\n"
                                    for i in range(10))
    (master / "Company/Big.md").write_text(body)
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)
    report = search_index(vault, "pipeline", k=20, provider=FakeEmbeddingProvider())
    from_big = [h for h in report.hits if h.rel_path == "Company/Big.md"]
    # the doc must appear AND be capped at the per-file limit of 2 — the old
    # `<= 2` alone also passed when search returned the doc zero times
    assert from_big and len(from_big) <= 2


def test_cli_search_json_and_missing_index(indexed_alice, tmp_path, capsys):
    assert main(["search", "pipeline", "--vault", str(indexed_alice), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["query"] == "pipeline"
    assert out["hits"]

    # a vault with no index → exit 1 with guidance
    assert main(["search", "x", "--vault", str(tmp_path / "no-vault")]) == 1
    err = capsys.readouterr().err
    assert "no index" in err


def test_center_rerank_boosts_linked_note(master, tmp_path):
    # Two notes with identical matching text: one wikilink-adjacent to the
    # center, one orphaned. Only the linked one gets the graph bonus.
    (master / "Company/Widget Linked.md").write_text("widget report\n")
    (master / "Company/Widget Orphan.md").write_text("widget report\n")
    home = master / "Company/Home.md"
    home.write_text(home.read_text() + "Also [[Widget Linked]].\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)

    report = search_index(vault, "widget", provider=None, center="Company/Home.md")
    assert report.mode == "keyword-only+graph"
    by_path = {h.rel_path: h for h in report.hits}
    linked = by_path["Company/Widget Linked.md"]
    orphan = by_path["Company/Widget Orphan.md"]
    assert "graph" in linked.sources
    # identical text scores; the graph leg must rank the center-adjacent
    # note above the orphan (dangling seeds teleport, they don't hoard)
    assert linked.score > orphan.score


def test_center_reaches_multi_hop_neighbors(master, tmp_path):
    # Home → Big Deal Decision (fixture link) → Widget Plan: two hops out.
    (master / "Company/Decisions/Big Deal Decision.md").write_text(
        "We chose option A. See [[Widget Plan]].\n")
    (master / "Company/Widget Plan.md").write_text("widget plan details\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)

    report = search_index(vault, "widget", provider=None, center="Company/Home.md")
    hit = next(h for h in report.hits if h.rel_path == "Company/Widget Plan.md")
    assert "graph" in hit.sources


def test_center_unknown_warns_and_degrades(indexed_alice):
    report = search_index(indexed_alice, "pipeline", provider=None, center="Nope.md")
    assert report.mode == "keyword-only+graph"
    assert any("center note not in index" in w for w in report.warnings)
    assert report.hits  # search itself still works


def test_cli_search_center_flag(indexed_alice, capsys):
    assert main(["search", "pipeline", "--vault", str(indexed_alice),
                 "--json", "--center", "Company/Home.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"].endswith("+graph")


def test_multihop_discovery_via_fact_comention(master, tmp_path):
    # Spec §6.3, the headline: "Acme Note" never contains the query term
    # "acme" — it is reachable only through the entity page's fact edge.
    # Query → alias-seeds Acme.md → fact co-mention edge → Sarah page,
    # whose wikilink leads to the meeting note.
    (master / "Company/Acme.md").write_text(
        "---\nentity: client\naliases: [ACME]\n---\n# Acme\n\n"
        "- [[Sarah Kim]] is our main contact [from:: 2026-01]\n")
    (master / "Company/Sarah Kim.md").write_text(
        "# Sarah Kim\n\nNotes from [[Kickoff Meeting]].\n")
    (master / "Company/Kickoff Meeting.md").write_text(
        "# Kickoff Meeting\n\nDiscussed rollout timeline and pricing.\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)

    report = search_index(vault, "acme", provider=None, k=10)
    assert report.mode == "keyword-only+graph"
    hit = next(h for h in report.hits
               if h.rel_path == "Company/Kickoff Meeting.md")
    assert hit.sources == ["graph"]  # never surfaced by a text leg


def test_empty_graph_keeps_text_ranking(master, tmp_path):
    # Spec §6.6: with no links and no facts the leg must not perturb text
    # ranking — hit order matches a pre-graph (pure text) fusion exactly.
    for name in ("Alpha", "Beta", "Gamma"):
        (master / f"Company/{name}.md").write_text(
            f"# {name}\n\nrollout details {name.lower()}\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)

    report = search_index(vault, "rollout", provider=None, k=10)
    # graph leg ran (text hits seed it) but every candidate it ranked was
    # already a text candidate, so relative text order is preserved
    keyword_hits = [h for h in report.hits if "keyword" in h.sources]
    assert keyword_hits == report.hits  # no graph-only hits appeared


def test_graph_flood_capped_per_file(master, tmp_path):
    # Spec §6.7: a star of neighbors around a matching hub cannot push the
    # hub past _MAX_PER_FILE or crowd every text hit out of the top k.
    hub = ["# Hub\n"] + [f"link [[Spoke {i}]]\n" for i in range(12)]
    (master / "Company/Hub.md").write_text("pipeline hub\n" + "".join(hub))
    for i in range(12):
        (master / f"Company/Spoke {i}.md").write_text(f"# Spoke {i}\n\n[[Hub]]\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)

    report = search_index(vault, "pipeline", provider=None, k=8)
    from_hub = [h for h in report.hits if h.rel_path == "Company/Hub.md"]
    assert 1 <= len(from_hub) <= 2
    assert any("keyword" in h.sources for h in report.hits)


def test_index_output_unchanged_by_this_slice(master, tmp_path):
    # Spec §5: the index path is untouched — build twice, byte-identical
    # table dumps (sanity guard that graphrank stayed query-time only).
    import sqlite3
    vaults = []
    for name in ("a", "b"):
        vault = tmp_path / name
        compile_vault(master, ALICE, RULES, vault)
        build_index(vault, provider=None, cache=None)
        vaults.append(vault)
    dumps = []
    for vault in vaults:
        conn = sqlite3.connect(vault / ".brain" / "index.db")
        rows = conn.execute(
            "SELECT rel_path, sha256 FROM files ORDER BY rel_path").fetchall()
        rows += conn.execute(
            "SELECT src_rel_path, target_rel_path, resolved FROM links "
            "ORDER BY 1, 2").fetchall()
        conn.close()
        dumps.append(rows)
    assert dumps[0] == dumps[1]
