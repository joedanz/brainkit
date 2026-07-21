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
    assert report.mode == "hybrid"
    assert any("Q3 Pipeline.md" in h.rel_path for h in report.hits)


@requires_vectors
def test_hybrid_search_marks_vector_source(indexed_alice):
    report = search_index(indexed_alice, "pipeline option", provider=FakeEmbeddingProvider())
    # with a provider present, at least one hit is corroborated by the vector leg
    assert any("vector" in h.sources for h in report.hits)


def test_keyword_only_when_no_provider(indexed_alice):
    report = search_index(indexed_alice, "pipeline", provider=None)
    assert report.mode == "keyword-only"
    assert all(h.sources == ["keyword"] for h in report.hits)
    assert report.hits


def test_keyword_only_flag_forces_no_vectors(indexed_alice):
    report = search_index(indexed_alice, "pipeline", provider=FakeEmbeddingProvider(),
                          keyword_only=True)
    assert report.mode == "keyword-only"


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
    assert "graph" not in orphan.sources
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
    assert report.mode == "keyword-only"  # no "+graph" suffix
    assert any("center note not in index" in w for w in report.warnings)
    assert report.hits  # search itself still works


def test_cli_search_center_flag(indexed_alice, capsys):
    assert main(["search", "pipeline", "--vault", str(indexed_alice),
                 "--json", "--center", "Company/Home.md"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"].endswith("+graph")
