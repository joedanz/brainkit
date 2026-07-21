import sqlite3
from pathlib import Path

import pytest

from brain.compiler import compile_vault
from brain.embeddings import FakeEmbeddingProvider
from brain.indexer import build_index
from brain.stats import collect_vault_stats, format_vault_status
from tests.conftest import ACME, ALICE, RULES, requires_vectors


def _compiled_indexed(master: Path, tmp_path: Path, *, provider="fake") -> Path:
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    p = FakeEmbeddingProvider() if provider == "fake" else None
    build_index(vault, provider=p, cache=None)
    return vault


def _mtimes(root: Path) -> dict[str, float]:
    # SQLite recreates ephemeral -wal/-shm sidecars merely to READ a WAL-mode
    # database; they carry no content. What must never change is the data.
    return {
        str(p): p.stat().st_mtime_ns
        for p in root.rglob("*")
        if p.is_file() and not p.name.endswith(("-wal", "-shm"))
    }


@requires_vectors
def test_vault_stats_counts_match_fixture(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    s = collect_vault_stats(vault, include_graph=True)

    assert s.kind == "vault"
    assert s.person == "alice"
    by_space = {st.space: st for st in s.spaces}
    # alice reads Company, Teams/sales, People/alice, Clients/acme
    assert by_space["Company"].notes == 2
    assert by_space["Teams/sales"].notes == 1
    assert by_space["People/alice"].notes == 1
    assert by_space["Clients/acme"].notes == 1
    assert s.notes_total == sum(st.notes for st in s.spaces)
    # generated context files never count as notes
    assert all("AGENTS" not in st.space for st in s.spaces)
    assert s.chunks_total > 0
    assert s.pending_reindex == []
    assert s.index is not None and s.index.built_at

    # Home.md links both fixtures; they show up as inbound-linked notes.
    linked = {ln.rel_path for ln in s.top_linked}
    assert "Company/Decisions/Big Deal Decision.md" in linked
    assert "Teams/sales/Q3 Pipeline.md" in linked

    # hybrid build: every chunk has a vector
    assert s.embedded_chunks == s.chunks_total
    assert s.embedding_coverage == 1.0


def test_stats_collection_is_read_only(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    before = _mtimes(vault)
    collect_vault_stats(vault, include_graph=True)
    assert _mtimes(vault) == before


def test_pending_reindex_after_recompile(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    (master / "People/alice/Memory.md").write_text("Fresh memory.\n")
    compile_vault(master, ALICE, RULES, vault)  # recompiled, not reindexed
    s = collect_vault_stats(vault)
    assert "People/alice/Memory.md" in s.pending_reindex


def test_degraded_mode_without_index(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    s = collect_vault_stats(vault)
    assert s.index is None
    assert s.chunks_total == 0
    # space counts still work from the manifest alone
    assert {st.space: st.notes for st in s.spaces}["Company"] == 2
    assert s.pending_reindex  # everything awaits indexing
    assert any("brain index" in w for w in s.warnings)
    # index.db must not have appeared as a side effect
    assert not (vault / ".brain" / "index.db").exists()


def test_keyword_only_build_reports_zero_coverage(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path, provider=None)
    s = collect_vault_stats(vault)
    assert s.embedded_chunks == 0
    assert s.embedding_coverage == 0.0


def test_graph_shape_is_consistent(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    s = collect_vault_stats(vault, include_graph=True)
    g = s.graph
    assert g is not None and not g.truncated
    n = len(g.nodes)
    assert all(0 <= e.source < n and 0 <= e.target < n for e in g.edges)
    by_rel = {node.rel_path: node for node in g.nodes}
    home = by_rel["Company/Home.md"]
    assert home.degree == 2  # links out to both fixture notes
    assert home.space == "Company"
    # edges reference Home -> both targets
    targets = {g.nodes[e.target].rel_path for e in g.edges
               if g.nodes[e.source].rel_path == "Company/Home.md"}
    assert targets == {"Company/Decisions/Big Deal Decision.md",
                       "Teams/sales/Q3 Pipeline.md"}


def test_graph_cap_truncates_by_degree(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    s = collect_vault_stats(vault, include_graph=True, graph_cap=2)
    g = s.graph
    assert g.truncated
    assert len(g.nodes) == 2
    # the highest-degree node (Home.md, degree 2) survives the cut
    assert any(n.rel_path == "Company/Home.md" for n in g.nodes)


def test_format_vault_status_mentions_key_sections(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    text = format_vault_status(collect_vault_stats(vault))
    assert "notes:" in text
    assert "Company" in text
    assert "top linked:" in text


# ---- master (admin lens) -----------------------------------------------------

def _seeded_company(master, tmp_path):
    from brain.cli import main
    from brain.promotions import draft_promotion
    from tests.test_cli import seed_meta

    seed_meta(master)
    out_root = tmp_path / "compiled"
    assert main(["compile", "--master", str(master), "--out", str(out_root)]) == 0
    build_index(out_root / "alice", provider=None, cache=None)
    draft_promotion(master, "bob", "Company/Playbook/SOP.md",
                    "People/bob/Sessions/x.md", "Body.\n", "p-1", "2026-07-08")
    return out_root


def test_master_stats_full_picture(master, tmp_path):
    from brain.doctor import run_doctor
    from brain.stats import collect_master_stats

    out_root = _seeded_company(master, tmp_path)
    (out_root / "bob/Company/Home.md").write_text("defaced\n")

    s = collect_master_stats(master, out_root)
    assert s.kind == "master"
    assert s.people_count == 2
    by_person = {p.person_id: p for p in s.people}
    assert by_person["alice"].compiled and by_person["bob"].compiled
    assert by_person["alice"].drift == 0
    assert by_person["bob"].drift == 1
    assert by_person["alice"].index_built_at  # indexed above
    assert by_person["bob"].index_built_at is None
    assert by_person["alice"].disk_bytes > 0
    assert by_person["alice"].notes > 0

    assert [p.id for p in s.promotions_pending] == ["p-1"]

    perms = {p.space: p for p in s.permissions}
    assert "bob" not in perms["Company"].writers  # only role:admin writes
    assert "alice" in perms["Company"].writers
    assert perms["People/bob"].readers == ["bob"]

    # findings come from doctor verbatim, not a reimplementation
    assert s.findings == run_doctor(master, out_root)


def test_master_stats_without_out_root(master, tmp_path):
    from brain.stats import collect_master_stats
    from tests.test_cli import seed_meta

    seed_meta(master)
    s = collect_master_stats(master, None)
    assert s.out_root is None
    assert all(not p.compiled for p in s.people)
    assert s.spaces  # enumerated from master itself


def test_format_master_status_sections(master, tmp_path):
    from brain.stats import collect_master_stats, format_master_status

    out_root = _seeded_company(master, tmp_path)
    text = format_master_status(collect_master_stats(master, out_root))
    assert "people: 2" in text
    assert "vaults:" in text
    assert "permissions:" in text
    assert "p-1" in text


# ---- brain status CLI ---------------------------------------------------------

def test_cli_status_vault_text_and_json(master, tmp_path, capsys):
    import json

    from brain.cli import main

    vault = _compiled_indexed(master, tmp_path)
    assert main(["status", "--vault", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "Company" in out and "notes:" in out

    assert main(["status", "--vault", str(vault), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["kind"] == "vault"
    assert payload["person"] == "alice"


def test_cli_status_vault_without_index_still_succeeds(master, tmp_path, capsys):
    from brain.cli import main

    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    assert main(["status", "--vault", str(vault)]) == 0
    assert "index: none" in capsys.readouterr().out


def test_cli_status_master_lens(master, tmp_path, capsys):
    import json

    from brain.cli import main

    out_root = _seeded_company(master, tmp_path)
    capsys.readouterr()  # drain compile output from setup
    assert main(["status", "--master", str(master), "--out", str(out_root),
                 "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "master"
    assert payload["people_count"] == 2

    # without --out, per-vault sections are simply absent
    assert main(["status", "--master", str(master)]) == 0


def test_cli_status_error_paths(master, tmp_path, capsys):
    import pytest as _pytest

    from brain.cli import main

    # uncompiled dir → handled error
    assert main(["status", "--vault", str(tmp_path / "nope")]) == 1
    assert "cannot read vault" in capsys.readouterr().err
    # --vault with --out is a usage error
    vault = _compiled_indexed(master, tmp_path)
    assert main(["status", "--vault", str(vault), "--out", "x"]) == 2
    # --vault and --master are mutually exclusive (argparse exits 2)
    with _pytest.raises(SystemExit) as exc:
        main(["status", "--vault", "a", "--master", "b"])
    assert exc.value.code == 2


def test_graph_nodes_carry_entity_type(master, tmp_path):
    from brain.compiler import compile_vault
    from brain.embeddings import FakeEmbeddingProvider
    from brain.indexer import build_index
    from brain.stats import collect_vault_stats
    from tests.conftest import ALICE, RULES

    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme.md").write_text(
        "---\nentity: client\n---\n# Acme\nSee [[Home]].\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)

    stats = collect_vault_stats(vault, include_graph=True)
    by_path = {n.rel_path: n for n in stats.graph.nodes}
    assert by_path["Company/Intel/Acme.md"].entity == "client"
    assert by_path["Company/Home.md"].entity == ""


# ---- facts & entities (schema v3 surfacing) ---------------------------------

def _facts_vault(master, tmp_path):
    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme.md").write_text(ACME)
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    return vault


def test_facts_and_entity_counts(master, tmp_path):
    s = collect_vault_stats(_facts_vault(master, tmp_path))
    assert s.facts_total == 2
    assert s.entities_total == 1
    assert s.entity_types == ["client"]
    assert s.facts is None  # not requested


def test_include_facts_bakes_full_intervals(master, tmp_path):
    s = collect_vault_stats(_facts_vault(master, tmp_path), include_facts=True)
    assert s.facts is not None and len(s.facts) == 2  # ended fact included
    assert {f.statement for f in s.facts} == {
        "Sarah Kim is our main contact", "Dana Ortiz was our main contact"}
    ended = next(f for f in s.facts if f.until_date)
    assert ended.until_date == "2026-01-31"  # full interval travels


def test_graph_nodes_carry_entity_aliases(master, tmp_path):
    s = collect_vault_stats(_facts_vault(master, tmp_path), include_graph=True)
    acme = next(n for n in s.graph.nodes
                if n.rel_path == "Company/Intel/Acme.md")
    assert acme.entity == "client"
    assert acme.aliases == ["Acme Corp", "ACME"]
    plain = next(n for n in s.graph.nodes if n.rel_path == "Company/Home.md")
    assert plain.aliases == []


def test_pre_v3_index_degrades_counts_not_crashes(master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    conn = sqlite3.connect(vault / ".brain" / "index.db")
    for t in ("fact_entities", "facts", "entities"):
        conn.execute(f"DROP TABLE {t}")
    conn.commit()
    conn.close()

    s = collect_vault_stats(vault, include_graph=True, include_facts=True)
    assert s.facts_total == 0 and s.entities_total == 0
    assert s.entity_types == []
    assert s.facts == []
    assert any("index" in w for w in s.warnings)  # bake explains itself


def test_status_text_includes_counts(master, tmp_path):
    text = format_vault_status(collect_vault_stats(_facts_vault(master, tmp_path)))
    assert "facts: 2; entities: 1" in text


def test_newer_schema_index_degrades_facts_not_crashes(master, tmp_path):
    # A downgrade scenario: the on-disk index's schema version is newer than
    # this binary's SCHEMA_VERSION. query_facts -> IndexStore.open_readonly
    # raises StoreError (not sqlite3.Error) in that case — the bake must
    # catch it too, never crash collect_vault_stats.
    from brain.store import SCHEMA_VERSION

    vault = _facts_vault(master, tmp_path)
    conn = sqlite3.connect(vault / ".brain" / "index.db")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    s = collect_vault_stats(vault, include_facts=True)
    assert s.facts == []
    assert any("facts unavailable" in w for w in s.warnings)


def test_vault_stats_with_facts_serializes_cleanly(master, tmp_path):
    from dataclasses import asdict

    s = collect_vault_stats(_facts_vault(master, tmp_path), include_facts=True)
    payload = asdict(s)
    assert payload["facts"]
    statements = {f["statement"] for f in payload["facts"]}
    assert statements == {
        "Sarah Kim is our main contact", "Dana Ortiz was our main contact"}
