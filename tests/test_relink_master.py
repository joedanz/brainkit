import json
import subprocess
from pathlib import Path

import pytest

from brain.cli import main
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
    (m / "_meta/org.yaml").write_text(
        "people:\n  alice: {name: Alice, teams: [alpha]}\n"
        "  bob: {name: Bob, teams: [beta]}\n")
    (m / "_meta/spaces.yaml").write_text(
        "spaces:\n"
        '  - {path: Company,    read: [everyone],        write: ["role:admin"]}\n'
        '  - {path: "Teams/*",  read: ["team:{name}"],   write: ["team:{name}"]}\n'
        '  - {path: "People/*", read: ["person:{name}"], write: ["person:{name}"]}\n')
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


def test_the_command_dry_runs_by_default_and_names_the_mode(tmp_path, capsys):
    m = _master(tmp_path)
    before = _snapshot(m)
    assert main(["relink", "--master", str(m), "Company/Old.md", "Company/New.md"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("move:") and "3 links in 2 notes" in out
    assert "--write" in out and _snapshot(m) == before


def test_the_command_writes_and_warns_about_stale_slices(tmp_path, capsys):
    m = _master(tmp_path)
    assert main(["relink", "--master", str(m), "Company/Old.md", "Company/New.md",
                 "--write", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "Company/Hub.md" in out and "cycle" in out
    assert (m / "Company/New.md").exists()


def test_the_command_reports_a_refusal_without_a_traceback(tmp_path, capsys):
    m = _master(tmp_path)
    assert main(["relink", "--master", str(m), "Company/Old.md", "Teams/ops/Old.md"]) == 1
    err = capsys.readouterr().err
    assert "different spaces" in err and "Traceback" not in err


def test_the_default_output_has_counts_but_no_note_paths(tmp_path, capsys):
    m = _master(tmp_path)
    main(["relink", "--master", str(m), "Company/Old.md", "Company/New.md"])
    assert "Hub.md" not in capsys.readouterr().out.split("\n", 1)[1]


def _commit_all(m, msg="more"):
    _git(m, "add", "-A")
    _git(m, "commit", "-qm", msg)


def test_a_link_some_reader_cannot_follow_is_left_alone_and_counted(tmp_path):
    m = _master(tmp_path)
    for rel, text in {"Teams/alpha/X.md": "# alpha\n", "Teams/beta/X.md": "# beta\n",
                      "Teams/beta/Note.md": "see [[X]]\n",
                      "Teams/alpha/Hub.md": "see [[X]]\n"}.items():
        (m / rel).parent.mkdir(parents=True, exist_ok=True)
        (m / rel).write_text(text)
    _commit_all(m)
    rep = relink_master(m, "Teams/alpha/X.md", "Teams/alpha/Q.md", write=True)
    assert (m / "Teams/beta/Note.md").read_text() == "see [[X]]\n"
    assert (m / "Teams/alpha/Hub.md").read_text() == "see [[Q]]\n"
    assert rep.skipped_links == 1


def test_rerunning_after_moving_a_note_that_never_won_its_name_is_fine(tmp_path):
    m = _master(tmp_path)
    for rel, text in {"Company/a/X.md": "# a\n", "Company/b/X.md": "# b\n",
                      "Company/H.md": "[[X]] [[Company/b/X]]\n"}.items():
        (m / rel).parent.mkdir(parents=True, exist_ok=True)
        (m / rel).write_text(text)
    _commit_all(m)
    relink_master(m, "Company/b/X.md", "Company/c/Y.md", write=True)
    assert (m / "Company/H.md").read_text() == "[[X]] [[Company/c/Y]]\n"
    rep = relink_master(m, "Company/b/X.md", "Company/c/Y.md", write=True)
    assert (rep.mode, rep.notes_touched, rep.committed) == ("heal", 0, False)


def test_rerunning_after_moving_the_winner_explains_instead_of_guessing(tmp_path):
    m = _master(tmp_path)
    for rel, text in {"Company/a/X.md": "# a\n", "Company/b/X.md": "# b\n",
                      "Company/H.md": "[[X]]\n"}.items():
        (m / rel).parent.mkdir(parents=True, exist_ok=True)
        (m / rel).write_text(text)
    _commit_all(m)
    relink_master(m, "Company/a/X.md", "Company/c/Y.md", write=True)
    with pytest.raises(RelinkError, match="nothing left to do"):
        relink_master(m, "Company/a/X.md", "Company/c/Y.md", write=True)


def test_a_failure_while_applying_leaves_everything_as_it_was(tmp_path, monkeypatch):
    import os

    m = _master(tmp_path)
    before = _snapshot(m)
    real = os.replace

    def boom(src, dst):
        if str(src).endswith("Company/Old.md"):
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RelinkError, match="nothing was changed"):
        relink_master(m, "Company/Old.md", "Company/New.md", write=True)
    monkeypatch.undo()
    assert _snapshot(m) == before
    assert _git(m, "status", "--porcelain").strip() == ""


def test_a_symlinked_folder_in_the_way_is_refused_before_anything_moves(tmp_path):
    m = _master(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (m / "Company/link").symlink_to(outside)
    before = _snapshot(m)
    with pytest.raises(RelinkError, match="symlink"):
        relink_master(m, "Company/Old.md", "Company/link/New.md", write=True)
    assert _snapshot(m) == before and list(outside.iterdir()) == []


def test_moving_a_symlinked_note_is_refused(tmp_path):
    m = _master(tmp_path)
    (m / "Company/Sym.md").symlink_to(m / "Company/Other.md")
    with pytest.raises(RelinkError, match="symlink"):
        relink_master(m, "Company/Sym.md", "Company/Sym2.md", write=True)
    assert (m / "Company/Sym.md").is_symlink()


def test_a_failed_commit_says_what_is_on_disk_and_how_to_finish(tmp_path, monkeypatch):
    from brain.promotions import PromotionError

    m = _master(tmp_path)

    def refuse(*a, **k):
        raise PromotionError("git commit failed: boom")

    monkeypatch.setattr("brain.relink._commit", refuse)
    with pytest.raises(RelinkError) as exc:
        relink_master(m, "Company/Old.md", "Company/New.md", write=True)
    msg = str(exc.value)
    assert "git commit failed: boom" in msg and "on disk" in msg and "commit" in msg
    assert (m / "Company/New.md").exists()


def test_the_command_reports_links_left_alone_for_visibility(tmp_path, capsys):
    m = _master(tmp_path)
    for rel, text in {"Teams/alpha/X.md": "# alpha\n", "Teams/beta/X.md": "# beta\n",
                      "Teams/beta/Note.md": "see [[X]]\n"}.items():
        (m / rel).parent.mkdir(parents=True, exist_ok=True)
        (m / rel).write_text(text)
    _commit_all(m)
    main(["relink", "--master", str(m), "Teams/alpha/X.md", "Teams/alpha/Q.md"])
    out = capsys.readouterr().out
    assert "left 1 links alone" in out and "cannot see" in out
