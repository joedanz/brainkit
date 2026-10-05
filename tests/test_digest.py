import hashlib
import os
import subprocess
from datetime import UTC, datetime

import pytest

from brain.compiler import compile_vault, is_weekly_digest
from brain.digest import collect_changes, window_for
from brain.doctor import run_doctor
from brain.indexer import build_index
from brain.search import search_index
from tests.conftest import ALICE, RULES
from tests.test_cli import seed_meta


@pytest.mark.parametrize("rel, expected", [
    ("People/alice/Inbox/weekly-digest.md", True),
    ("People/bob/Inbox/weekly-digest.md", True),
    ("People/alice/Inbox/doctor-digest.md", False),
    ("People/alice/Notes/weekly-digest.md", False),
    ("Company/Inbox/weekly-digest.md", False),
    ("People/alice/Inbox/sub/weekly-digest.md", False),
])
def test_only_a_persons_inbox_digest_is_a_weekly_digest(rel, expected):
    assert is_weekly_digest(rel) is expected


DIGEST = ("---\ntitle: Weekly digest\nsource: digest\n---\n"
          "- `Company/Home.md`: zebrafruit statement (from 2026-01)\n"
          "- broken line with only an end date [until:: 2026-01]\n"
          "unlinked [[Nowhere Note]] mention\n")


def test_the_doctor_does_not_read_the_weekly_digest(master):
    seed_meta(master)
    p = master / "People/bob/Inbox/weekly-digest.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(DIGEST)
    findings = run_doctor(master)
    assert not [f for f in findings
                if any("weekly-digest.md" in x for x in f.paths)
                or "weekly-digest.md" in f.message]


def test_the_weekly_digest_is_not_searchable(master, tmp_path):
    p = master / "People/alice/Inbox/weekly-digest.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(DIGEST)
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    assert (vault / "People/alice/Inbox/weekly-digest.md").exists()
    build_index(vault, provider=None, cache=None)
    assert search_index(vault, "zebrafruit", keyword_only=True).hits == []
    # control: an ordinary note is still found
    assert search_index(vault, "pipeline", keyword_only=True).hits


ORG_YAML = ("people:\n  alice: {name: Alice, teams: [alpha]}\n"
            "  bob: {name: Bob, teams: [beta]}\n  carol: {name: Carol, teams: [alpha]}\n")
SPACES_YAML = ("spaces:\n"
               '  - {path: Company,    read: [everyone],      write: ["role:admin"]}\n'
               '  - {path: "Teams/*",  read: ["team:{name}"], write: ["team:{name}"]}\n'
               '  - {path: "People/*", read: ["person:{name}"], write: ["person:{name}"]}\n')
SERVER = ("Brain Server", "server@brain.local")
ALICE_ID = ("Alice", "alice@brain.local")
BOB_ID = ("Bob", "bob@brain.local")


def _git(m, *args, env=None):
    return subprocess.run(["git", "-C", str(m), *args], capture_output=True, text=True,
                          check=True, env={**os.environ, **(env or {})}).stdout


def _commit(m, files, when, who=SERVER, msg="c"):
    """files maps a path to its new text, or None to delete it. `when` is an
    ISO timestamp used as both author and committer date."""
    for rel, text in files.items():
        p = m / rel
        if text is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    _git(m, "add", "-A")
    _git(m, "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}",
         "commit", "-qm", msg, env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})


def _rename(m, old, new, when, who=SERVER):
    (m / new).parent.mkdir(parents=True, exist_ok=True)
    _git(m, "mv", old, new)
    _git(m, "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "commit", "-qm", "mv",
         env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})


def _body(n=8, tag=""):
    """A note body. A tag makes every line distinct (so git's rename detection
    cannot confuse two notes); without one the lines are plain `line N`."""
    if tag:
        rows = [hashlib.sha256(f"{tag}{i}".encode()).hexdigest() for i in range(n)]
    else:
        rows = [f"line {i}" for i in range(n)]
    return "# Title\n\n" + "".join(f"{r}\n" for r in rows)


def _scenario(tmp_path):
    """A master whose history has one note of every kind of change. The week
    under test is 2026-10-05 .. 2026-10-11 (digest date 2026-10-12)."""
    m = tmp_path / "master"
    m.mkdir()
    _git(m, "init", "-q")
    seed = {
        "_meta/org.yaml": ORG_YAML, "_meta/spaces.yaml": SPACES_YAML,
        "Company/A.md": _body(), "Company/Old.md": _body(tag="old"),
        "Company/Gone.md": _body(tag="gone"), "Company/F.md":
            "# F\n\n- Sarah is main contact [from:: 2026-01]\n"
            "- Dana was lead [from:: 2024-06]\n",
        "Teams/alpha/T.md": _body(), "Teams/beta/B.md": _body(),
        "Teams/alpha/Secret.md": _body(tag="secret"),
        "Company/Moved.md": _body(tag="moved"),
        "Company/Tweak.md": _body(),
        "People/alice/Memory.md": "mine\n",
    }
    _commit(m, seed, "2026-09-30T10:00:00+0000", msg="seed")
    _commit(m, {"Company/Early.md": _body()}, "2026-10-04T10:00:00+0000")
    _commit(m, {"Company/A.md": _body(12)}, "2026-10-06T10:00:00+0000", BOB_ID)
    _rename(m, "Company/Old.md", "Company/Newname.md", "2026-10-07T10:00:00+0000", ALICE_ID)
    _commit(m, {"Company/Gone.md": None}, "2026-10-07T11:00:00+0000", ALICE_ID)
    _commit(m, {
        "Company/New.md": "# New\n\n- Omar joined [from:: 2026-10]\n",
        "Company/F.md": "# F\n\n- Sarah is main contact [from:: 2026-01] [until:: 2026-10]\n"
                        "- Omar is main contact [from:: 2026-10]\n",
        "Company/Temp.md": "temp\n"}, "2026-10-08T10:00:00+0000", BOB_ID)
    _commit(m, {"Company/Temp.md": None}, "2026-10-09T10:00:00+0000", BOB_ID)
    _commit(m, {"Teams/alpha/T.md": _body(12), "Teams/beta/B.md": _body(12)},
            "2026-10-09T11:00:00+0000")
    _rename(m, "Teams/alpha/Secret.md", "Company/Revealed.md", "2026-10-10T10:00:00+0000")
    _rename(m, "Company/Moved.md", "Teams/beta/Moved.md", "2026-10-10T11:00:00+0000")
    # after the window: must never appear
    _commit(m, {"Company/A.md": _body(20)}, "2026-10-13T10:00:00+0000", BOB_ID)
    return m


def test_window_is_the_previous_iso_week():
    w = window_for("2026-10-12")  # a Monday
    assert w.start == datetime(2026, 10, 5, tzinfo=UTC)
    assert w.end == datetime(2026, 10, 12, tzinfo=UTC)
    assert (w.covered, w.due) == ("2026-W41", "2026-W42")
    assert window_for("2026-10-14") == w  # any day that week


def test_window_across_the_iso_year_boundary():
    w = window_for("2027-01-04")  # Monday of 2027-W01
    assert (w.covered, w.due) == ("2026-W53", "2027-W01")
    assert w.start == datetime(2026, 12, 28, tzinfo=UTC)


def test_the_net_change_set_for_the_week(tmp_path):
    m = _scenario(tmp_path)
    by_path = {c.path: c for c in collect_changes(m, window_for("2026-10-12"))}
    assert {p: c.status for p, c in by_path.items()} == {
        "Company/A.md": "modified", "Company/Newname.md": "renamed",
        "Company/Gone.md": "deleted", "Company/New.md": "added",
        "Company/F.md": "modified", "Teams/alpha/T.md": "modified",
        "Teams/beta/B.md": "modified", "Company/Revealed.md": "renamed",
        "Teams/beta/Moved.md": "renamed"}
    assert "Company/Temp.md" not in by_path  # created and deleted in the window
    assert "Company/Early.md" not in by_path  # before the window
    a = by_path["Company/A.md"]
    assert a.changed_lines == 4 and a.headings == ("Title",)
    assert a.authors == frozenset({BOB_ID[::-1]})
    renamed = by_path["Company/Newname.md"]
    assert renamed.old_path == "Company/Old.md" and renamed.authors == frozenset({ALICE_ID[::-1]})


def test_fact_changes_are_found_by_comparing_the_two_ends(tmp_path):
    m = _scenario(tmp_path)
    changes = {c.path: c for c in collect_changes(m, window_for("2026-10-12"))}
    kinds = sorted((f.kind, f.statement) for f in changes["Company/F.md"].facts)
    assert kinds == [("ended", "Sarah is main contact"),
                     ("removed", "Dana was lead"),
                     ("started", "Omar is main contact")]
    added = changes["Company/New.md"]
    assert [(f.kind, f.statement) for f in added.facts] == [("started", "Omar joined")]
    assert added.facts_as_new == added.facts


def test_a_history_that_starts_inside_the_window_reports_everything_as_added(tmp_path):
    m = tmp_path / "m"
    m.mkdir()
    _git(m, "init", "-q")
    _commit(m, {"Company/X.md": "x\n"}, "2026-10-06T10:00:00+0000")
    (c,) = collect_changes(m, window_for("2026-10-12"))
    assert (c.status, c.path) == ("added", "Company/X.md")


def test_an_empty_window_has_no_changes(tmp_path):
    m = _scenario(tmp_path)
    assert collect_changes(m, window_for("2026-11-02")) == []


def test_a_directory_that_is_not_a_repo_is_a_handled_error(tmp_path):
    from brain.digest import DigestError

    with pytest.raises(DigestError):
        collect_changes(tmp_path, window_for("2026-10-12"))
