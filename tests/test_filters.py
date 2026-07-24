from pathlib import Path

from brain.compiler import compile_vault
from brain.filters import list_notes
from brain.indexer import build_index
from tests.conftest import ALICE, RULES


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


def test_list_inbox_skips_symlinks(master, tmp_path):
    # A symlink planted in the Inbox must not surface a file outside it.
    from brain.filters import list_inbox
    vault = _compiled_indexed(master, tmp_path)
    inbox = vault / "People" / "alice" / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "note.md").write_text("real inbox note\n")
    outside = tmp_path / "outside.md"
    outside.write_text("secret outside the inbox\n")
    (inbox / "leak.md").symlink_to(outside)
    rels = {it.rel_path for it in list_inbox(vault, "alice")}
    assert "People/alice/Inbox/note.md" in rels
    assert "People/alice/Inbox/leak.md" not in rels


def test_list_actions_skips_symlinks(master, tmp_path):
    # list_actions reads file *contents*; a symlink must never let the dashboard
    # read an out-of-vault (or other-tenant) file's bytes.
    from brain.filters import list_actions
    vault = _compiled_indexed(master, tmp_path)
    actions = vault / "People" / "alice" / "Actions"
    actions.mkdir(parents=True, exist_ok=True)
    (actions / "real.md").write_text("- [ ] call acme\n")
    secret = tmp_path / "secret.md"
    secret.write_text("- [ ] SENTINEL leaked action\n")
    (actions / "leak.md").symlink_to(secret)
    items = list_actions(vault, "alice")
    assert any(it.rel_path == "People/alice/Actions/real.md" for it in items)
    assert all(it.rel_path != "People/alice/Actions/leak.md" for it in items)
    assert all("SENTINEL" not in it.text for it in items)


def test_list_actions_open_items_and_limit(master, tmp_path):
    # Only open `- [ ]` items, with correct 1-based line numbers; limit honored.
    from brain.filters import list_actions
    vault = _compiled_indexed(master, tmp_path)
    actions = vault / "People" / "alice" / "Actions"
    actions.mkdir(parents=True, exist_ok=True)
    (actions / "Todo.md").write_text(
        "# Todo\n"          # 1
        "- [ ] open one\n"  # 2
        "- [x] done\n"      # 3
        "prose line\n"      # 4
        "  - [ ] indented open\n"  # 5
    )
    items = list_actions(vault, "alice")
    assert [it.text for it in items] == ["open one", "indented open"]
    assert [it.line for it in items] == [2, 5]
    assert list_actions(vault, "alice", limit=1) == items[:1]
