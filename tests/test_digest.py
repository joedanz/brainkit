import hashlib
import os
import random
import subprocess
from datetime import UTC, datetime

import pytest

from brain.compiler import compile_vault, is_weekly_digest
from brain.digest import (
    MARKER_REL,
    build_person_digest,
    collect_changes,
    render_weekly,
    run_digest,
    window_for,
)
from brain.doctor import run_doctor
from brain.indexer import build_index
from brain.schemas import load_org, load_spaces
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


def _people(m):
    org = load_org(m / "_meta/org.yaml")
    return org, load_spaces(m / "_meta/spaces.yaml")


def _text_for(m, pid, today="2026-10-12"):
    org, rules = _people(m)
    w = window_for(today)
    pd = build_person_digest(org.people[pid], collect_changes(m, w), org, rules, "Company")
    return None if pd is None else render_weekly(pd, w)[0]


def test_alice_sees_what_others_changed_in_spaces_she_reads(tmp_path):
    m = _scenario(tmp_path)
    text = _text_for(m, "alice")
    assert "## Facts that changed" in text
    assert "Omar is main contact (from 2026-10-01) — `Company/F.md`" in text
    assert "Sarah is main contact (from 2026-01-01, ended 2026-10-31)" in text
    assert "removed: Dana was lead" in text
    assert "`Company/A.md` — Title (by Bob)" in text  # edited, with the section
    assert "`Company/New.md` (by Bob)" in text
    assert "`Teams/alpha/T.md`" in text
    # her own rename and deletion are left out; spaces she cannot read too
    assert "Newname" not in text and "Gone" not in text
    assert "Teams/beta" not in text and "B.md" not in text
    # a rename out of a space she can read into one she cannot is a removal
    assert "`Company/Moved.md` (removed)" in text
    # a rename she can see both ends of names both ends
    assert "`Company/Revealed.md` (renamed from `Teams/alpha/Secret.md`)" in text


def test_bob_never_sees_teams_alpha_not_even_through_a_rename(tmp_path):
    m = _scenario(tmp_path)
    text = _text_for(m, "bob")
    assert "`Company/Revealed.md`" in text  # shown as a new note
    assert "Secret" not in text and "Teams/alpha" not in text
    assert "`Teams/beta/B.md`" in text
    assert "`Teams/beta/Moved.md` (renamed from `Company/Moved.md`)" in text
    assert "`Company/Newname.md` (renamed from `Company/Old.md`) (by Alice)" in text
    assert "`Company/Gone.md` (removed) (by Alice)" in text
    assert "`Company/A.md`" not in text  # bob's own edit


def test_a_person_with_nothing_readable_that_changed_gets_no_digest(tmp_path):
    m = _scenario(tmp_path)
    org, rules = _people(m)
    only_own = [c for c in collect_changes(m, window_for("2026-10-12"))
                if c.path.startswith("Teams/beta/B")]
    carol = org.people["carol"]  # team alpha: cannot read Teams/beta
    assert build_person_digest(carol, only_own, org, rules, "Company") is None


def test_the_digest_never_reproduces_fact_markup_or_wikilinks(tmp_path):
    m = _scenario(tmp_path)
    _commit(m, {"Company/L.md": "# L\n\n- Works with [[Q3 Pipeline|the pipeline]] and "
                                "[[Big Deal]] [from:: 2026-10] [source:: [[F]]]\n"},
            "2026-10-08T12:00:00+0000")
    text = _text_for(m, "alice")
    assert "the pipeline" in text and "Big Deal" in text
    for bad in ("[[", "]]", "[from::", "[until::", "[source::"):
        assert bad not in text


def test_trivial_edits_are_left_out_but_a_fact_change_always_counts(tmp_path):
    m = _scenario(tmp_path)
    _commit(m, {"Company/Tweak.md": _body().replace("line 3", "line three")},
            "2026-10-10T12:00:00+0000", BOB_ID)  # one changed line: below the bar
    assert "Tweak.md" not in _text_for(m, "alice")
    _commit(m, {"Company/Tweak.md": "# Title\n\n- Fact [from:: 2026-10]\n"
                                    + _body().split("\n", 2)[2]},
            "2026-10-10T13:00:00+0000", BOB_ID)  # a fact change always counts
    assert "`Company/Tweak.md`" in _text_for(m, "alice")


def test_caps_add_a_more_line(tmp_path):
    m = tmp_path / "m"
    m.mkdir()
    _git(m, "init", "-q")
    base = {"_meta/org.yaml": ORG_YAML, "_meta/spaces.yaml": SPACES_YAML,
            "Company/Z.md": "z\n"}
    _commit(m, base, "2026-09-30T10:00:00+0000")
    notes = {f"Company/N{i:02d}.md": f"# N{i}\n" for i in range(30)}
    facts = "# Many\n\n" + "".join(f"- F{i} [from:: 2026-10]\n" for i in range(20))
    _commit(m, {**notes, "Company/Many.md": facts}, "2026-10-07T10:00:00+0000", BOB_ID)
    text = _text_for(m, "alice")
    assert "+5 more fact changes" in text
    assert "+6 more in Company" in text  # 31 added notes, 25 shown


def test_rendering_is_stable_and_has_frontmatter(tmp_path):
    m = _scenario(tmp_path)
    org, rules = _people(m)
    w = window_for("2026-10-12")
    pd = build_person_digest(org.people["alice"], collect_changes(m, w), org, rules, "Company")
    content, fp = render_weekly(pd, w)
    assert content == render_weekly(pd, w)[0]
    head = content.split("---\n")[1]
    for line in ("title: Weekly digest", "source: digest", "week: 2026-W41",
                 "from: 2026-10-05", "through: 2026-10-11", f"fingerprint: {fp}"):
        assert line in head


def test_no_digest_ever_contains_something_its_reader_cannot_see(tmp_path):
    """Random histories across spaces with different readers. Every note has a
    unique name and a unique word (each ends in "z" so none contains another); none of those from an unreadable space may
    appear in a person's digest, whatever was renamed where."""
    rng = random.Random(20261005)
    spaces = {"Company": {"alice", "bob", "carol"}, "Teams/alpha": {"alice", "carol"},
              "Teams/beta": {"bob"}, "People/alice": {"alice"}}
    who = [ALICE_ID, BOB_ID, ("Carol", "carol@brain.local"), SERVER]
    for trial in range(15):
        m = tmp_path / f"m{trial}"
        m.mkdir()
        _git(m, "init", "-q")
        _commit(m, {"_meta/org.yaml": ORG_YAML, "_meta/spaces.yaml": SPACES_YAML,
                    "Company/Seed.md": "seed\n"}, "2026-09-30T10:00:00+0000")
        alive: dict[str, set[str]] = {}  # path -> readers
        token_of: dict[str, str] = {}  # path -> the unique word in its content
        secrets: dict[str, set[str]] = {}  # unique name or word -> who may see it
        pre: dict[str, str] = {}  # notes that already exist when the week starts
        for k, space in enumerate(spaces):
            for j in range(2):
                path = f"{space}/Pre{trial}x{k}{j}z.md"
                token = f"ptok{trial}x{k}{j}z"
                pre[path] = (f"# Pre\n{token}\n- {token}fact [from:: 2026-09]\n"
                             + _body(5, tag=token))
                alive[path] = spaces[space]
                token_of[path] = token
                secrets[token] = spaces[space]
                secrets[f"Pre{trial}x{k}{j}z"] = spaces[space]
        _commit(m, pre, "2026-09-30T11:00:00+0000")
        n = 0
        for step in range(rng.randint(6, 12)):
            when = f"2026-10-{6 + step % 5:02d}T{10 + step:02d}:00:00+0000"
            op = rng.choice(["add", "add", "edit", "delete", "move"])
            who_ = rng.choice(who)
            if op == "add" or not alive:
                n += 1
                space = rng.choice(list(spaces))
                path = f"{space}/Note{trial}x{n}z.md"
                token = f"tok{trial}x{n}z"
                fact = f"- {token}fact [from:: 2026-10]\n"
                _commit(m, {path: f"# Sec{trial}x{n}\n{token}\n{fact}" + _body(5)}, when, who_)
                alive[path] = spaces[space]
                token_of[path] = token
                secrets[token] = spaces[space]
                secrets[f"Note{trial}x{n}z"] = spaces[space]  # the original name
            elif op == "edit":
                path = rng.choice(list(alive))
                _commit(m, {path: (m / path).read_text() + _body(4)}, when, who_)
            elif op == "delete":
                path = rng.choice(list(alive))
                _commit(m, {path: None}, when, who_)
                secrets[token_of.pop(path)] = set()  # a deleted note's content is shown to nobody
                del alive[path]
            else:
                old = rng.choice(list(alive))
                space = rng.choice(list(spaces))
                new = f"{space}/Moved{trial}x{step}z.md"
                _rename(m, old, new, when, who_)
                secrets[f"Moved{trial}x{step}z"] = spaces[space]
                secrets[token_of[old]] = spaces[space]  # content now lives in the new space
                token_of[new] = token_of.pop(old)
                alive[new] = spaces[space]
                del alive[old]
        org, rules = _people(m)
        w = window_for("2026-10-12")
        changes = collect_changes(m, w)
        for pid, person in org.people.items():
            pd = build_person_digest(person, changes, org, rules, "Company")
            text = "" if pd is None else render_weekly(pd, w)[0]
            for secret, readers in secrets.items():
                if pid not in readers:
                    assert secret not in text, (trial, pid, secret)


def test_the_first_cycle_of_a_new_week_writes_digests_once(tmp_path):
    m = _scenario(tmp_path)
    report = run_digest(m, today="2026-10-12")
    assert (report.ran, report.written, report.removed, report.warnings) == (True, 3, 0, [])  # alice, bob, carol
    assert (m / MARKER_REL).read_text().strip() == "2026-W42"
    alice = (m / "People/alice/Inbox/weekly-digest.md").read_text()
    assert "week: 2026-W41" in alice and "Omar is main contact" in alice
    log = _git(m, "log", "-1", "--format=%an <%ae>|%s", "--name-only")
    assert log.startswith("Brain Digest <digest@brain.local>|digest: ")
    assert "People/alice/Inbox/weekly-digest.md" in log
    # later cycles that week do nothing, and do not even look at git
    again = run_digest(m, today="2026-10-14")
    assert (again.ran, again.written) == (False, 0)


def test_a_missed_monday_catches_up_and_a_lost_marker_rebuilds_without_rewriting(tmp_path):
    m = _scenario(tmp_path)
    run_digest(m, today="2026-10-14")  # first run happens on a Wednesday
    assert (m / "People/alice/Inbox/weekly-digest.md").exists()
    (m / MARKER_REL).unlink()
    report = run_digest(m, today="2026-10-14")
    assert (report.ran, report.written, report.removed) == (True, 0, 0)  # same fingerprints


def test_an_empty_week_removes_last_weeks_digest(tmp_path):
    m = _scenario(tmp_path)
    run_digest(m, today="2026-10-12")
    assert (m / "People/alice/Inbox/weekly-digest.md").exists()
    report = run_digest(m, today="2026-11-02")  # nothing changed in the week before
    assert report.removed == 3 and report.written == 0
    assert not (m / "People/alice/Inbox/weekly-digest.md").exists()


def test_a_master_that_is_not_a_repo_warns_and_does_not_set_the_marker(tmp_path):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta/org.yaml").write_text(ORG_YAML)
    (tmp_path / "_meta/spaces.yaml").write_text(SPACES_YAML)
    report = run_digest(tmp_path, today="2026-10-12")
    assert report.ran and any("digest skipped" in w for w in report.warnings)
    assert not (tmp_path / MARKER_REL).exists()


CAROL_ID = ("Carol", "carol@brain.local")


def _pairing_master(tmp_path, deleter):
    """Git pairs a deleted readable note with a similar new note in a space the
    reader cannot see, and calls it a rename."""
    m = tmp_path / "m"
    m.mkdir()
    _git(m, "init", "-q")
    body = _body(8, tag="pair")
    _commit(m, {"_meta/org.yaml": ORG_YAML, "_meta/spaces.yaml": SPACES_YAML,
                "Company/X.md": body}, "2026-09-30T10:00:00+0000")
    _commit(m, {"Company/X.md": None}, "2026-10-06T10:00:00+0000", deleter)
    _commit(m, {"Teams/beta/Plan.md": body}, "2026-10-07T10:00:00+0000", BOB_ID)
    return m


def test_a_rename_git_invents_never_credits_the_hidden_side_author(tmp_path):
    m = _pairing_master(tmp_path, CAROL_ID)
    text = _text_for(m, "alice")  # cannot read Teams/beta, where Bob worked
    assert "`Company/X.md` (removed) (by Carol)" in text
    assert "Bob" not in text and "Plan" not in text


def test_a_removal_you_made_yourself_is_not_credited_to_someone_else(tmp_path):
    m = _pairing_master(tmp_path, ALICE_ID)
    assert _text_for(m, "alice") is None  # her own deletion; Bob's work is hidden


def _mini(tmp_path):
    """A master with only the org and spaces files, ready for commits that go
    in chronological order (the window logic relies on history running forward)."""
    m = tmp_path / "mini"
    m.mkdir()
    _git(m, "init", "-q")
    _commit(m, {"_meta/org.yaml": ORG_YAML, "_meta/spaces.yaml": SPACES_YAML,
                "Company/Seed.md": "seed\n"}, "2026-09-29T10:00:00+0000")
    return m


def test_a_system_commit_on_your_own_edit_does_not_make_it_someone_elses(tmp_path):
    m = _mini(tmp_path)
    _commit(m, {"Company/Mine.md": _body(8, tag="mine")}, "2026-09-30T12:00:00+0000")
    _commit(m, {"Company/Mine.md": _body(12, tag="mine")}, "2026-10-06T09:00:00+0000", ALICE_ID)
    _commit(m, {"Company/Mine.md": _body(12, tag="mine").replace("# Title", "# Titled")},
            "2026-10-07T09:00:00+0000")  # a server rewrite, as relink would make
    assert "Mine.md" not in (_text_for(m, "alice") or "")
    assert "`Company/Mine.md`" in _text_for(m, "bob")
    assert "(by Alice)" in _text_for(m, "bob")


def test_a_note_only_the_system_touched_is_still_shown(tmp_path):
    m = _mini(tmp_path)
    _commit(m, {"Company/Sys.md": _body(8, tag="sys")}, "2026-09-30T12:00:00+0000")
    _commit(m, {"Company/Sys.md": _body(12, tag="sys")}, "2026-10-06T09:00:00+0000")
    text = _text_for(m, "alice")
    assert "`Company/Sys.md`" in text and "(by" not in text.split("Sys.md", 1)[1].split("\n")[0]


def test_a_heading_with_fact_markup_never_reaches_the_digest(tmp_path):
    m = _mini(tmp_path)
    head = "# H\n\n## Lead [from:: 2026-01] [source:: [[F]]]\n\n"
    _commit(m, {"Company/H.md": head + "row\n" * 4}, "2026-09-30T12:00:00+0000")
    _commit(m, {"Company/H.md": head + "row\n" * 8}, "2026-10-06T09:00:00+0000", BOB_ID)
    text = _text_for(m, "alice")
    assert "`Company/H.md` — Lead (by Bob)" in text
    for bad in ("[from::", "[source::", "[["):
        assert bad not in text


def test_paths_with_quotes_keep_their_authors_and_odd_names_do_not_crash(tmp_path):
    m = _mini(tmp_path)
    quoted = 'Company/Say "hi".md'
    _commit(m, {quoted: _body(8, tag="q"), "@@top.md": "x\n"}, "2026-09-30T12:00:00+0000")
    _commit(m, {quoted: _body(12, tag="q")}, "2026-10-06T09:00:00+0000", ALICE_ID)
    by_path = {c.path: c for c in collect_changes(m, window_for("2026-10-12"))}
    assert by_path[quoted].authors == frozenset({ALICE_ID[::-1]})
    assert "Say" not in (_text_for(m, "alice") or "")  # her own edit, still hers
    assert "Say" in _text_for(m, "bob")


def test_a_huge_note_does_not_stall_the_edit_stats(tmp_path):
    import time

    m = tmp_path / "m"
    m.mkdir()
    _git(m, "init", "-q")
    old = [f"row {i % 40}" for i in range(12000)]
    new = [f"changed {i}" if i % 97 == 0 else row for i, row in enumerate(old)]
    _commit(m, {"Company/Big.md": "# Big\n\n" + "\n".join(old) + "\n"},
            "2026-09-30T10:00:00+0000")
    _commit(m, {"Company/Big.md": "# Big\n\n" + "\n".join(new) + "\n"},
            "2026-10-06T10:00:00+0000", BOB_ID)
    started = time.monotonic()
    (change,) = collect_changes(m, window_for("2026-10-12"))
    assert time.monotonic() - started < 2
    assert change.changed_lines == 124  # git's own count of the replaced lines
