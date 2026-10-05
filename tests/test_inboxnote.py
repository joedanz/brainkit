from brain.inboxnote import sync_inbox_note
from brain.schemas import Person

from .conftest import RULES

BOB = Person(id="bob", name="Bob", teams=("ops",))
REL = "People/bob/Inbox/note.md"


def _note(fp, body="body"):
    return f"---\nfingerprint: {fp}\n---\n{body}\n"


def _call(master, content, fp, warnings, person=BOB):
    return sync_inbox_note(master, person, RULES, "Company", "note.md",
                           content=content, fingerprint=fp, warnings=warnings)


def test_writes_then_leaves_an_unchanged_note_alone_then_rewrites(tmp_path):
    w: list[str] = []
    assert _call(tmp_path, _note("a"), "a", w) == "written"
    assert (tmp_path / REL).read_text() == _note("a")
    assert _call(tmp_path, _note("a", "other body"), "a", w) == "unchanged"
    assert (tmp_path / REL).read_text() == _note("a")  # same fingerprint: not rewritten
    assert _call(tmp_path, _note("b"), "b", w) == "written"
    assert w == []


def test_removes_when_there_is_no_content(tmp_path):
    w: list[str] = []
    _call(tmp_path, _note("a"), "a", w)
    assert _call(tmp_path, None, None, w) == "removed"
    assert not (tmp_path / REL).exists()
    assert _call(tmp_path, None, None, w) == "unchanged"  # nothing to remove


def test_a_symlinked_ancestor_is_refused(tmp_path):
    (tmp_path / "People/bob").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "People/bob/Inbox").symlink_to(outside)
    w: list[str] = []
    assert _call(tmp_path, _note("a"), "a", w) == "skipped"
    assert w == [f"{REL}: ancestor is a symlink — refusing to write"]
    assert list(outside.iterdir()) == []


def test_a_symlinked_note_is_neither_written_nor_removed(tmp_path):
    (tmp_path / "People/bob/Inbox").mkdir(parents=True)
    target = tmp_path / "elsewhere.md"
    target.write_text("keep\n")
    (tmp_path / REL).symlink_to(target)
    w: list[str] = []
    assert _call(tmp_path, _note("a"), "a", w) == "skipped"
    assert _call(tmp_path, None, None, w) == "skipped"
    assert target.read_text() == "keep\n"
    assert any("refusing to write" in x for x in w)
    assert any("refusing to remove" in x for x in w)


def test_a_person_without_a_write_grant_on_their_space_is_skipped(tmp_path):
    from brain.schemas import SpaceRule

    no_write = (SpaceRule("People/*", read=("person:{name}",), write=()),)
    w: list[str] = []
    out = sync_inbox_note(tmp_path, BOB, no_write, "Company", "note.md",
                          content=_note("a"), fingerprint="a", warnings=w)
    assert out == "skipped"
    assert w == ["bob has no write grant on their own space — skipped"]
    assert not (tmp_path / REL).exists()
