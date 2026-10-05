import json
import subprocess
from pathlib import Path

import pytest

from brain.relink import RelinkError, relink_master


def _git(m, *args):
    return subprocess.run(["git", "-C", str(m), "-c", "user.name=t",
                           "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


def _master(tmp_path: Path) -> Path:
    m = tmp_path / "master"
    files = {
        "Company/Old.md": "# Old\n",
        "Company/Hub.md": "See [[Old]] and [[Company/Old]].\n",
        "Company/Other.md": "# Other\n",
        "Teams/ops/Runbook.md": "Ops: [[Old]]\n",
        "People/alice/Memory.md": "mine\n",
        "People/alice/Shares.md": "generated: [[Old]]\n",
    }
    for rel, text in files.items():
        p = m / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (m / "_meta").mkdir()
    (m / "_meta/org.yaml").write_text("people: {}\n")
    _git(m, "init", "-q")
    _git(m, "add", "-A")
    _git(m, "commit", "-qm", "seed")
    return m


def _snapshot(m):
    return {str(p.relative_to(m)): p.read_bytes()
            for p in sorted(m.rglob("*")) if p.is_file() and ".git" not in p.parts}


def test_move_writes_one_scoped_commit_and_rewrites_links(tmp_path):
    m = _master(tmp_path)
    (m / "Company/Other.md").write_text("# Other, edited and not committed\n")
    before_log = _git(m, "rev-list", "--count", "HEAD").strip()

    rep = relink_master(m, "Company/Old.md", "Company/New.md", write=True)

    assert (rep.mode, rep.committed, rep.written) == ("move", True, True)
    assert not (m / "Company/Old.md").exists() and (m / "Company/New.md").exists()
    assert (m / "Company/Hub.md").read_text() == "See [[New]] and [[Company/New]].\n"
    assert (m / "Teams/ops/Runbook.md").read_text() == "Ops: [[New]]\n"
    assert (m / "People/alice/Shares.md").read_text() == "generated: [[Old]]\n"
    assert int(_git(m, "rev-list", "--count", "HEAD")) == int(before_log) + 1
    changed = set(_git(m, "show", "--no-renames", "--name-only", "--format=", "HEAD").split())
    assert changed == {"Company/Old.md", "Company/New.md", "Company/Hub.md",
                       "Teams/ops/Runbook.md"}
    assert "Other.md" in _git(m, "status", "--porcelain")  # still dirty, not swept in


def test_dry_run_changes_nothing(tmp_path):
    m = _master(tmp_path)
    before, head = _snapshot(m), _git(m, "rev-parse", "HEAD")
    rep = relink_master(m, "Company/Old.md", "Company/New.md")
    assert (rep.written, rep.committed, rep.notes_touched) == (False, False, 2)
    assert rep.links_rewritten == 3
    assert _snapshot(m) == before and _git(m, "rev-parse", "HEAD") == head


def test_heal_after_an_agent_renamed_the_note(tmp_path):
    m = _master(tmp_path)
    _git(m, "mv", "Company/Old.md", "Company/New.md")
    _git(m, "commit", "-qm", "agent rename")
    rep = relink_master(m, "Company/Old.md", "Company/New.md", write=True)
    assert rep.mode == "heal" and rep.committed
    assert (m / "Company/Hub.md").read_text() == "See [[New]] and [[Company/New]].\n"


def test_running_it_again_does_nothing(tmp_path):
    m = _master(tmp_path)
    relink_master(m, "Company/Old.md", "Company/New.md", write=True)
    head = _git(m, "rev-parse", "HEAD")
    rep = relink_master(m, "Company/Old.md", "Company/New.md", write=True)
    assert (rep.mode, rep.notes_touched, rep.committed) == ("heal", 0, False)
    assert _git(m, "rev-parse", "HEAD") == head


def test_a_stop_between_the_move_and_the_rewrite_is_finished_by_a_rerun(tmp_path):
    m = _master(tmp_path)
    (m / "Company/Old.md").rename(m / "Company/New.md")  # moved, nothing else done
    rep = relink_master(m, "Company/Old.md", "Company/New.md", write=True)
    assert rep.mode == "heal" and rep.links_rewritten == 3
    assert _git(m, "status", "--porcelain").strip() == ""  # the move was committed too


def test_crlf_and_non_utf8_notes_are_preserved(tmp_path):
    m = _master(tmp_path)
    (m / "Company/Crlf.md").write_bytes(b"line one\r\nSee [[Old]]\r\n")
    (m / "Company/Latin.md").write_bytes(b"caf\xe9 [[Old]]\n")
    relink_master(m, "Company/Old.md", "Company/New.md", write=True)
    assert (m / "Company/Crlf.md").read_bytes() == b"line one\r\nSee [[New]]\r\n"
    assert (m / "Company/Latin.md").read_bytes() == b"caf\xe9 [[Old]]\n"


def test_a_symlinked_note_is_never_written_through(tmp_path):
    m = _master(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("[[Old]]\n")
    (m / "Company/Link.md").symlink_to(outside)
    rep = relink_master(m, "Company/Old.md", "Company/New.md", write=True)
    assert outside.read_text() == "[[Old]]\n"
    assert rep.skipped_symlinks == 1


@pytest.mark.parametrize("old, new, needle", [
    ("Company/Old.md", "Teams/ops/Old.md", "different spaces"),
    ("Company/Old.md", "_meta/New.md", "_meta"),
    ("Company/Old.txt", "Company/New.txt", ".md"),
    ("Company/Old.md", "Company/../x.md", ".."),
    ("Company/Old.md", "Company.md", "space"),
    ("People/alice/Shares.md", "People/alice/Moved.md", "generated"),
    ("Company/Old.md", "Company/Other.md", "both"),
])
def test_invalid_requests_are_refused_and_change_nothing(tmp_path, old, new, needle):
    m = _master(tmp_path)
    before = _snapshot(m)
    with pytest.raises(RelinkError, match=needle):
        relink_master(m, old, new, write=True)
    assert _snapshot(m) == before


def test_a_pending_promotion_naming_the_note_blocks_it(tmp_path):
    m = _master(tmp_path)
    d = m / "_meta/promotions/pending"
    d.mkdir(parents=True)
    (d / "p1.md").write_text(
        "---\npromotion-id: p1\nfrom: alice\ntarget-path: Company/Old.md\n"
        "source: People/alice/Memory.md\ncreated: 2026-10-05\n---\nbody\n")
    with pytest.raises(RelinkError, match="promotion"):
        relink_master(m, "Company/Old.md", "Company/New.md", write=True)


def test_a_held_edit_naming_the_note_blocks_it(tmp_path):
    m = _master(tmp_path)
    (m / "People/alice/.held.json").write_text(json.dumps(
        {"sha": "x", "at": "t", "paths": [{"kind": "edit", "path": "Company/Old.md",
                                           "reason": "r"}]}))
    with pytest.raises(RelinkError, match="held"):
        relink_master(m, "Company/Old.md", "Company/New.md", write=True)
