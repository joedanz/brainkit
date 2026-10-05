"""One doctor run reads the brain once.

The golden list below was captured from doctor before the per-run corpus
existed (base e11b4c5): the refactor that walks the tree once and reads each
note once must leave every finding, its wording and its order untouched.
"""

import os

import pytest

import brain.doctor
from brain.doctor import run_doctor

from .test_cli import SPACES_YAML, seed_meta
from .test_doctor import _FAKE_AWS, _FAKE_GH, BODY_A, _templated

requires_nonroot = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses file permissions, so an unreadable file can't be staged")


def _write(master, rel, text):
    p = master / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text)


def _varied_brain(master, tmp_path):
    """Something for nearly every content check to say, plus the awkward
    files a walk or a decode can get wrong."""
    seed_meta(master)
    (master / "_meta/spaces.yaml").write_text(
        SPACES_YAML
        + '  - {path: "Clients/Vandenberg", read: ["person:alice"], write: ["person:alice"]}\n')
    _write(master, "Clients/Vandenberg/Vandenberg.md", "# Vandenberg\nprivate.\n")
    # plain-ref and cross-refs
    _write(master, "Company/Memory.md",
           "We learned a lot from the Vandenberg expedition. [[Home]]\n")
    _write(master, "Company/Leaky.md", "See [[Vandenberg]] and [[Q3 Pipeline]].\n")
    # duplicates: exact (shared and private), templated near-dups, stem collision
    _write(master, "Company/Kickoff Notes.md", BODY_A)
    _write(master, "Company/Kickoff Recap.md", BODY_A)
    _write(master, "People/alice/Notes/Article.md", BODY_A)
    _write(master, "People/bob/Notes/Saved.md", BODY_A)
    _templated(master, "Company/Reports", 4)
    _write(master, "Company/Acme.md", "# Acme\n\ncompany-side view [[Home]]\n")
    _write(master, "Clients/acme/Acme.md",
           "---\nentity: client\n---\n# Acme\n\n"
           "- Acme's plan is Enterprise [from:: 2025-03]\n"
           "- Acme's plan is Growth [from:: 2026-01] [source:: [[Home]]]\n")
    # facts: malformed, empty entity, duplicate open fact across pages
    _write(master, "Company/Bad.md",
           "---\nentity: \n---\n# Bad\n\n"
           "- broken [from:: 2026-99]\n"
           "- inverted [from:: 2026-05] [until:: 2026-01]\n"
           "- orphan close [until:: 2026-01]\n")
    _write(master, "Teams/ops/Vendors.md",
           "- [[Acme]] renewed the contract [from:: 2026-02] [source:: [[Runbook]]]\n")
    _write(master, "Teams/ops/Vendors Copy.md",
           "- [[Acme]] renewed the contract [from:: 2026-02]\n")
    # unlinked, and the Inbox exemption
    _write(master, "People/bob/Notes/Solo.md", "Completely alone.\n")
    _write(master, "People/bob/Inbox/Capture.md", "Unprocessed capture.\n")
    # intel and citations (dates far enough back to be stale on any today)
    _write(master, "Company/Intel/Lisbon.md", "Lisbon, as of 2020-01.\n")
    _write(master, "Company/Intel/Porto.md", "Porto has no citation.\n")
    _write(master, "Company/Intel/Lisbon — updates 2026-01.md", "addendum\n")
    _write(master, "Company/Intel/Home.md", "[[Lisbon]] [[Porto]]\n")
    _write(master, "People/alice/Notes/Paper.md",
           "---\ndistilled: https://example.com/paper\n---\n\nclaims [[Article]]\n")
    _write(master, "People/alice/Notes/Old Paper.md",
           "---\ndistilled: a book\n---\n\n[ref](https://example.com/x), as of 2019-05 "
           "[[Paper]]\n")
    # secrets: a note, a non-note copied file, and a Sessions note (exempt from
    # duplicates, so the secrets scan reads it itself) with lone CRs
    _write(master, "Teams/ops/Deploy.md", f"# Deploy\n\nUse {_FAKE_GH} for CI.\n")
    _write(master, "Teams/ops/deploy.env", f"KEY={_FAKE_AWS}\n")
    _write(master, "People/bob/Sessions/Log.md",
           f"one\rtwo\r\n{_FAKE_AWS}\n".encode())
    _write(master, "Company/Crlf.md", f"a\r\nb\rc\r\n{_FAKE_GH}\r\n[[Home]]\r\n".encode())
    # undecodable bytes, an orphan, odd names the walk must treat as before
    _write(master, "Company/Latin.md", b"caf\xe9 au lait [[Home]]\n")
    _write(master, "Clients/Globex.md", "# Globex\nLoose, in no space.\n")
    _write(master, "Company/Shout.MD", "not a note by extension\n")
    _write(master, "Company/.hidden/Secret Plan.md", "hidden dir note\n")
    (master / "Company/Folder.md").mkdir()
    _write(master, "Company/Folder.md/Inside.md", "inside a dir named like a note [[Home]]\n")
    _write(master, "_meta/Notes.md", "reserved\n")
    outside = tmp_path / "outside"
    _write(outside, "Linked.md", "outside the brain\n")
    (master / "Company/Linked").symlink_to(outside, target_is_directory=True)
    _write(master, "People/bob/Notes/Locked.md", "secret\n")
    (master / "People/bob/Notes/Locked.md").chmod(0o000)


GOLDEN: list[tuple[str, str, str, tuple[str, ...]]] = [
    ('error', 'unreadable-files', 'Company/Folder.md: cannot be read (Is a directory) — doctor cannot check it and compile will fail on it; fix the file before the next cycle', ('Company/Folder.md',)),
    ('error', 'unreadable-files', 'People/bob/Notes/Locked.md: cannot be read (Permission denied) — doctor cannot check it and compile will fail on it; fix the file before the next cycle', ('People/bob/Notes/Locked.md',)),
    ('warn', 'orphan-files', 'Clients/Globex.md sits directly under Clients/ — not in any space, so it compiles into no vault; move it into a subfolder', ('Clients/Globex.md',)),
    ('warn', 'unlinked-notes', 'Clients/Globex.md: no links, relations, or facts connect this note — graph search can never reach it', ('Clients/Globex.md',)),
    ('warn', 'unlinked-notes', 'Clients/acme/Overview.md: no links, relations, or facts connect this note — graph search can never reach it', ('Clients/acme/Overview.md',)),
    ('warn', 'unlinked-notes', 'Company/.hidden/Secret Plan.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/.hidden/Secret Plan.md',)),
    ('warn', 'unlinked-notes', 'Company/Bad.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/Bad.md',)),
    ('warn', 'unlinked-notes', 'Company/Intel/Lisbon — updates 2026-01.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/Intel/Lisbon — updates 2026-01.md',)),
    ('warn', 'unlinked-notes', 'Company/Kickoff Notes.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/Kickoff Notes.md',)),
    ('warn', 'unlinked-notes', 'Company/Kickoff Recap.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/Kickoff Recap.md',)),
    ('warn', 'unlinked-notes', 'Company/Reports/Report 00.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/Reports/Report 00.md',)),
    ('warn', 'unlinked-notes', 'Company/Reports/Report 01.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/Reports/Report 01.md',)),
    ('warn', 'unlinked-notes', 'Company/Reports/Report 02.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/Reports/Report 02.md',)),
    ('warn', 'unlinked-notes', 'Company/Reports/Report 03.md: no links, relations, or facts connect this note — graph search can never reach it', ('Company/Reports/Report 03.md',)),
    ('warn', 'unlinked-notes', 'People/alice/Memory.md: no links, relations, or facts connect this note — graph search can never reach it', ('People/alice/Memory.md',)),
    ('warn', 'unlinked-notes', 'People/bob/Memory.md: no links, relations, or facts connect this note — graph search can never reach it', ('People/bob/Memory.md',)),
    ('warn', 'unlinked-notes', 'People/bob/Notes/Saved.md: no links, relations, or facts connect this note — graph search can never reach it', ('People/bob/Notes/Saved.md',)),
    ('warn', 'unlinked-notes', 'People/bob/Notes/Solo.md: no links, relations, or facts connect this note — graph search can never reach it', ('People/bob/Notes/Solo.md',)),
    ('warn', 'unlinked-notes', 'People/bob/Sessions/Bob Private Note.md: no links, relations, or facts connect this note — graph search can never reach it', ('People/bob/Sessions/Bob Private Note.md',)),
    ('warn', 'unlinked-notes', 'People/bob/Sessions/Log.md: no links, relations, or facts connect this note — graph search can never reach it', ('People/bob/Sessions/Log.md',)),
    ('warn', 'unlinked-notes', 'Teams/ops/Deploy.md: no links, relations, or facts connect this note — graph search can never reach it', ('Teams/ops/Deploy.md',)),
    ('warn', 'dup-exact', 'Company/Kickoff Notes.md and Company/Kickoff Recap.md have identical content — fold one into the other via a mode: patch promotion', ('Company/Kickoff Notes.md', 'Company/Kickoff Recap.md')),
    ('warn', 'dup-exact', 'Company/Kickoff Recap.md and People/alice/Notes/Article.md have identical content — fold one into the other via a mode: patch promotion', ('Company/Kickoff Recap.md', 'People/alice/Notes/Article.md')),
    ('info', 'dup-exact', 'People/alice/Notes/Article.md and People/bob/Notes/Saved.md hold identical content in unshared spaces — promotion candidate', ('People/alice/Notes/Article.md', 'People/bob/Notes/Saved.md')),
    ('warn', 'stem-collision', "Clients/acme/Acme.md and Company/Acme.md share the title stem 'acme' — a bare [[Acme]] resolves to whichever sorts first; rename one or link by full path", ('Clients/acme/Acme.md', 'Company/Acme.md')),
    ('warn', 'dup-near', '4 notes are near-duplicates of each other in Company/Reports: Report 00.md, Report 01.md, Report 02.md, and 1 more — merge them, or if they share a template on purpose, make them distinct', ('Company/Reports/Report 00.md', 'Company/Reports/Report 01.md', 'Company/Reports/Report 02.md', 'Company/Reports/Report 03.md')),
    ('error', 'secrets', 'Company/Crlf.md: GitHub token on line 4, readable by 2 people — treat it as leaked. Rotate it with whoever issued it, then remove it from the note: deleting the line alone does not remove it from git history or from vaults already synced', ('Company/Crlf.md',)),
    ('error', 'secrets', 'People/bob/Sessions/Log.md: AWS access key id on line 2, readable by 1 person. Rotate it with whoever issued it, then remove it from the note: deleting the line alone does not remove it from git history or from vaults already synced', ('People/bob/Sessions/Log.md',)),
    ('error', 'secrets', 'Teams/ops/Deploy.md: GitHub token on line 3, readable by 1 person. Rotate it with whoever issued it, then remove it from the note: deleting the line alone does not remove it from git history or from vaults already synced', ('Teams/ops/Deploy.md',)),
    ('error', 'secrets', 'Teams/ops/deploy.env: AWS access key id on line 1, readable by 1 person. Rotate it with whoever issued it, then remove it from the note: deleting the line alone does not remove it from git history or from vaults already synced', ('Teams/ops/deploy.env',)),
    ('warn', 'cross-refs', "Company/Home.md links to 'Teams/sales', but 1 reader(s) of 'Company' cannot see it: bob — the name leaks even though the file does not", ()),
    ('warn', 'cross-refs', "Company/Leaky.md links to 'Clients/Vandenberg', but 1 reader(s) of 'Company' cannot see it: bob — the name leaks even though the file does not", ()),
    ('warn', 'cross-refs', "Company/Leaky.md links to 'Teams/sales', but 1 reader(s) of 'Company' cannot see it: bob — the name leaks even though the file does not", ()),
    ('warn', 'plain-ref', "Company/Memory.md mentions 'Vandenberg' (Clients/Vandenberg) in prose, but 1 reader(s) of 'Company' cannot see that space: bob", ()),
    ('warn', 'facts', 'Company/Bad.md: empty entity type', ()),
    ('warn', 'facts', "Company/Bad.md:6: unparseable from date: '2026-99'", ()),
    ('warn', 'facts', 'Company/Bad.md:7: until 2026-01-31 is before from 2026-05-01', ()),
    ('warn', 'facts', 'Company/Bad.md:8: until without from', ()),
    ('info', 'charter-unset', "no charter set — the relevance test falls back to the vault's spaces; set one with `brain init --charter`", ('_meta/config.yaml',)),
    ('warn', 'fact-uncited', 'Clients/acme/Acme.md:6: fact has no [source::]', ()),
    ('warn', 'fact-uncited', 'Teams/ops/Vendors Copy.md:1: fact has no [source::]', ()),
    ('warn', 'fact-conflict', 'Clients/acme/Acme.md:6 ↔ Clients/acme/Acme.md:7: conflicting open facts about [[Clients/acme/Acme.md]]: "Acme\'s plan is Enterprise" (from 2025-03-01) vs "Acme\'s plan is Growth" (from 2026-01-01) — close the superseded fact with [until::]', ('Clients/acme/Acme.md', 'Clients/acme/Acme.md')),
    ('warn', 'fact-dup', 'Teams/ops/Vendors Copy.md:1 ↔ Teams/ops/Vendors.md:1: duplicate open fact "[[Acme]] renewed the contract" — delete one via write-back, or close the older with [until::]', ('Teams/ops/Vendors Copy.md', 'Teams/ops/Vendors.md')),
    ('error', 'symlinks', 'Company/Linked is a symlink — compiler and writeback skip links, so this content is dead weight or an escape attempt', ()),
    ('warn', 'intel', 'Company/Intel/Lisbon — updates 2026-01.md: unfolded addendum — fold it into its page and delete it, or have the agent resubmit as a mode: patch promotion', ('Company/Intel/Lisbon — updates 2026-01.md',)),
    ('warn', 'intel', 'Company/Intel/Lisbon.md: stale — newest citation 2020-01 is over 12 months old', ('Company/Intel/Lisbon.md',)),
    ('warn', 'intel', 'Company/Intel/Porto.md: no dated citations — every Intel claim needs `[source](URL), as of YYYY-MM` or `captured YYYY-MM`', ('Company/Intel/Porto.md',)),
    ('warn', 'citations', 'People/alice/Notes/Old Paper.md: distilled from a book, stale — newest citation 2019-05 is over 12 months old', ('People/alice/Notes/Old Paper.md',)),
    ('warn', 'citations', 'People/alice/Notes/Paper.md: distilled from https://example.com/paper but has no dated citations — the full source never enters the vault, so every claim needs `[source](URL), as of YYYY-MM` or `captured YYYY-MM` to stay recoverable', ('People/alice/Notes/Paper.md',)),
]


@requires_nonroot
def test_findings_match_the_pre_refactor_golden(master, tmp_path):
    _varied_brain(master, tmp_path)
    try:
        got = [(f.severity, f.check, f.message, f.paths) for f in run_doctor(master)]
    finally:
        (master / "People/bob/Notes/Locked.md").chmod(0o644)
    assert got == GOLDEN


@requires_nonroot
def test_one_run_walks_once_and_reads_each_note_once(master, tmp_path, monkeypatch):
    _varied_brain(master, tmp_path)
    walks: list[str] = []
    reads: list[str] = []
    parsed: list[str] = []
    real_walk = brain.doctor._walk_content
    real_read = brain.doctor._read_text
    real_parse = brain.doctor.parse_facts

    def walk(m, shared):
        walks.append(shared)
        return real_walk(m, shared)

    def read(path):
        reads.append(path.relative_to(master).as_posix())
        return real_read(path)

    def parse(text):
        parsed.append(text)
        return real_parse(text)

    monkeypatch.setattr(brain.doctor, "_walk_content", walk)
    monkeypatch.setattr(brain.doctor, "_read_text", read)
    monkeypatch.setattr(brain.doctor, "parse_facts", parse)
    try:
        run_doctor(master)
    finally:
        (master / "People/bob/Notes/Locked.md").chmod(0o644)
    assert walks == ["Company"]
    assert reads and len(reads) == len(set(reads))
    assert parsed and len(parsed) <= len(reads)


def _rglob_walk(master, shared):
    """The content walk as it was written before the string walk."""
    from brain.doctor import RESERVED, _is_own_digest, space_of_path

    rels = []
    for f in sorted(master.rglob("*.md")):
        parts = f.relative_to(master).parts
        if parts[0] in RESERVED or parts[0].startswith("."):
            continue
        rel = f.relative_to(master).as_posix()
        if _is_own_digest(rel, parts):
            continue
        if space_of_path(rel, shared) is not None:
            rels.append(rel)
    return rels


@requires_nonroot
def test_string_walk_matches_the_rglob_walk(master, tmp_path):
    _varied_brain(master, tmp_path)
    # Orderings where string and component comparison disagree.
    _write(master, "Company/a-c.md", "x\n")
    _write(master, "Company/a/b.md", "x\n")
    _write(master, "People/bob/Inbox/doctor-digest.md", "digest\n")
    (master / "Company/Locked Dir").mkdir()
    _write(master, "Company/Locked Dir/Hidden.md", "x\n")
    (master / "Company/Locked Dir").chmod(0o000)
    try:
        assert brain.doctor._walk_content(master, "Company") == _rglob_walk(master, "Company")
    finally:
        (master / "Company/Locked Dir").chmod(0o755)
        (master / "People/bob/Notes/Locked.md").chmod(0o644)
