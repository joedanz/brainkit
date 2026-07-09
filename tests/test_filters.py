from pathlib import Path

import pytest

from brain.compiler import compile_vault
from brain.filters import list_notes
from brain.indexer import build_index
from tests.conftest import ALICE, RULES


@pytest.fixture(autouse=True)
def _no_ambient_provider(monkeypatch, tmp_path):
    for var in ("BRAIN_EMBED_BASE_URL", "BRAIN_EMBED_API_KEY", "BRAIN_EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "no-config.yaml"))


def _compiled_indexed(master: Path, tmp_path: Path) -> Path:
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    return vault


def test_lists_all_notes(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    rows = list_notes(vault)
    paths = {r.rel_path for r in rows}
    assert "Company/Home.md" in paths
    assert "Teams/sales/Q3 Pipeline.md" in paths
    # Home links two notes; each shows inbound >= 1
    by_path = {r.rel_path: r for r in rows}
    assert by_path["Company/Decisions/Big Deal Decision.md"].inbound >= 1


def test_space_filter(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    rows = list_notes(vault, space="Company")
    assert rows
    assert all(r.space == "Company" for r in rows)


def test_path_contains_filter(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    rows = list_notes(vault, path_contains="Pipeline")
    assert [r.rel_path for r in rows] == ["Teams/sales/Q3 Pipeline.md"]


def test_unresolved_only(master, tmp_path):
    (master / "Company/Dangling.md").write_text("See [[Nowhere In Particular]].\n")
    vault = _compiled_indexed(master, tmp_path)
    rows = list_notes(vault, unresolved_only=True)
    paths = {r.rel_path for r in rows}
    assert "Company/Dangling.md" in paths
    assert all(r.unresolved_out > 0 for r in rows)


def test_pending_only(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    assert list_notes(vault, pending_only=True) == []  # freshly indexed
    (master / "People/alice/Memory.md").write_text("Fresh memory.\n")
    compile_vault(master, ALICE, RULES, vault)  # recompiled, not reindexed
    pending = {r.rel_path for r in list_notes(vault, pending_only=True)}
    assert "People/alice/Memory.md" in pending


def test_limit(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    assert len(list_notes(vault, limit=1)) == 1


def test_no_index_returns_empty(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)  # compiled but never indexed
    assert list_notes(vault) == []


def test_note_links_inbound_and_outbound(master, tmp_path):
    from brain.filters import note_links
    vault = _compiled_indexed(master, tmp_path)
    links = note_links(vault, "Teams/sales/Q3 Pipeline.md")
    inbound = {r.rel_path for r in links.inbound}
    assert "Company/Home.md" in inbound  # Home links to the pipeline
    assert "Teams/sales/Q3 Pipeline.md" not in inbound  # never self


def test_note_links_empty_without_index(master, tmp_path):
    from brain.filters import note_links
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)  # compiled but not indexed
    links = note_links(vault, "Company/Home.md")
    assert links.inbound == [] and links.outbound == [] and links.unresolved_out == []


def test_list_inbox_newest_first(master, tmp_path):
    from brain.filters import list_inbox
    vault = _compiled_indexed(master, tmp_path)
    inbox_dir = vault / "People" / "alice" / "Inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / "note.md").write_text("hi")
    items = list_inbox(vault, "alice")
    assert any(it.rel_path == "People/alice/Inbox/note.md" for it in items)
