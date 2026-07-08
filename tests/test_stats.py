from pathlib import Path

import pytest

from brain.compiler import compile_vault
from brain.embeddings import FakeEmbeddingProvider
from brain.indexer import build_index
from brain.stats import collect_vault_stats, format_vault_status
from tests.conftest import ALICE, RULES


@pytest.fixture(autouse=True)
def _no_ambient_provider(monkeypatch, tmp_path):
    # Ensure tests never pick up a real provider from the developer's env.
    for var in ("BRAIN_EMBED_BASE_URL", "BRAIN_EMBED_API_KEY", "BRAIN_EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "no-config.yaml"))


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
