"""Weekly digest: what changed this week in the notes a person can read.

Built from master's git history, mechanically; no model is called. One net
change set is computed for the previous ISO week, then filtered per person so
nothing from a space they cannot read can reach them. See
docs/superpowers/specs/2026-10-05-weekly-digest-design.md.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath

from brain.compiler import WIKILINK_RE, is_generated_person_note
from brain.errors import BrainError
from brain.facts import parse_facts
from brain.resolver import can_read, space_of_path
from brain.schemas import Org, Person, SpaceRule

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


class DigestError(BrainError, ValueError):
    """The digest could not be built (git failed or the master is not a repo)."""


@dataclass(frozen=True)
class Window:
    start: datetime  # inclusive: a Monday 00:00 UTC
    end: datetime  # exclusive: the next Monday 00:00 UTC
    covered: str  # the ISO week the digest covers, e.g. "2026-W41"
    due: str  # the ISO week it is delivered in, e.g. "2026-W42"


def window_for(today: str) -> Window:
    """The previous full ISO week relative to `today` (YYYY-MM-DD)."""
    day = date.fromisoformat(today)
    monday = day - timedelta(days=day.weekday())
    end = datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
    start = end - timedelta(days=7)
    cy, cw, _ = start.isocalendar()
    dy, dw, _ = day.isocalendar()
    return Window(start, end, f"{cy}-W{cw:02d}", f"{dy}-W{dw:02d}")


@dataclass(frozen=True)
class FactChange:
    kind: str  # "started" | "ended" | "removed"
    statement: str
    from_date: str
    until_date: str | None


@dataclass(frozen=True)
class NoteChange:
    status: str  # "added" | "modified" | "deleted" | "renamed"
    path: str  # the path at the end of the week (at the start, when deleted)
    old_path: str | None
    changed_lines: int
    headings: tuple[str, ...]  # sections holding changed lines, at most three
    authors: frozenset[tuple[str, str]]  # (email, name) of every commit touching it
    facts: tuple[FactChange, ...]
    facts_as_new: tuple[FactChange, ...]  # every fact at the end, as "started"


def _git(master: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(master), *args],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        raise DigestError(f"git {args[0]} failed: {r.stderr.strip() or 'no output'}")
    return r.stdout


def _rev_before(master: Path, when: datetime) -> str | None:
    out = _git(master, "rev-list", "-1",
               f"--before={when.strftime('%Y-%m-%d %H:%M:%S')} +0000", "HEAD").strip()
    return out or None


def _blob(master: Path, rev: str | None, path: str) -> str:
    if rev is None:
        return ""
    r = subprocess.run(["git", "-C", str(master), "show", f"{rev}:{path}"],
                       capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""


def _name_status(raw: str):
    toks = raw.split("\0")
    i = 0
    while i < len(toks) and toks[i]:
        kind = toks[i][0]
        if kind in "RC":
            yield kind, toks[i + 1], toks[i + 2]
            i += 3
        else:
            yield kind, None, toks[i + 1]
            i += 2


def _headings_for(lines: list[str], touched: list[int]) -> tuple[str, ...]:
    out: list[str] = []
    for idx in sorted(set(touched)):
        i = min(idx, len(lines) - 1)
        while i >= 0:
            m = _HEADING.match(lines[i])
            if m:
                if m.group(1) not in out:
                    out.append(m.group(1))
                break
            i -= 1
        if len(out) >= 3:
            break
    return tuple(out)


def _edit_stats(before: str, after: str) -> tuple[int, tuple[str, ...]]:
    a, b = before.splitlines(), after.splitlines()
    changed = 0
    touched: list[int] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        changed += max(i2 - i1, j2 - j1)
        touched.extend(range(j1, max(j2, j1 + 1)))  # a pure deletion marks where it happened
    return changed, _headings_for(b, touched)


def _fact_changes(before: str | None, after: str | None) -> tuple[FactChange, ...]:
    old = {(f.statement, f.from_date): f for f in parse_facts(before or "")}
    new = {(f.statement, f.from_date): f for f in parse_facts(after or "")}
    out: list[FactChange] = []
    for key, f in new.items():
        prev = old.get(key)
        if prev is None:
            out.append(FactChange("started", f.statement, f.from_date, f.until_date))
        elif f.until_date and not prev.until_date:
            out.append(FactChange("ended", f.statement, f.from_date, f.until_date))
    if after is not None:  # the note still exists: a vanished fact line was removed
        out += [FactChange("removed", f.statement, f.from_date, f.until_date)
                for key, f in old.items() if key not in new]
    return tuple(out)


def _authors(master: Path, start: str | None, end: str) -> dict[str, set[tuple[str, str]]]:
    rng = f"{start}..{end}" if start else end
    out = _git(master, "log", rng, "--name-only", "--format=@@%ae%x1f%an")
    by_path: dict[str, set[tuple[str, str]]] = {}
    who: tuple[str, str] | None = None
    for line in out.splitlines():
        if line.startswith("@@"):
            email, name = line[2:].split("\x1f", 1)
            who = (email, name)
        elif line.strip() and who is not None:
            by_path.setdefault(line, set()).add(who)
    return by_path


def collect_changes(master: Path, window: Window) -> list[NoteChange]:
    """The net change to .md notes across the window, as `git diff -M` sees it:
    a note created and deleted inside the week produces nothing."""
    start = _rev_before(master, window.start)
    end = _rev_before(master, window.end)
    if end is None or end == start:
        return []
    if start is None:  # the history begins inside the window: everything is new
        tree = _git(master, "ls-tree", "-r", "--name-only", "-z", end).split("\0")
        raw = "".join(f"A\0{p}\0" for p in tree if p.endswith(".md"))
    else:
        raw = _git(master, "diff", "--name-status", "-M", "-z", start, end, "--", "*.md")
    authors = _authors(master, start, end)
    changes: list[NoteChange] = []
    for kind, old, new in _name_status(raw):
        if kind == "A":
            status, before, after = "added", "", _blob(master, end, new)
        elif kind == "D":
            status, before, after = "deleted", _blob(master, start, new), None
        elif kind == "R":
            status = "renamed"
            before, after = _blob(master, start, old), _blob(master, end, new)
        else:  # M, T and anything else git reports: treat as an edit
            status = "modified"
            before, after = _blob(master, start, new), _blob(master, end, new)
        lines, headings = (0, ()) if status in ("added", "deleted") \
            else _edit_stats(before, after or "")
        who = frozenset(authors.get(new, set()) | authors.get(old or new, set()))
        started = _fact_changes("", after) if after is not None else ()
        changes.append(NoteChange(
            status, new, old, lines, headings, who,
            started if status == "added" else _fact_changes(before, after),
            started if status in ("added", "renamed") else ()))
    return sorted(changes, key=lambda c: c.path)

MAX_FACTS = 15
MAX_NOTES = 25
MIN_CHANGED_LINES = 3
_SKIP_SEGMENTS = frozenset({"Inbox", "Sessions"})
_SECTIONS = (("added", "New notes"), ("modified", "Edited notes"),
             ("moved", "Removed or renamed"))


@dataclass(frozen=True)
class PersonDigest:
    facts: tuple[str, ...]
    notes: tuple[tuple[str, str, str], ...]  # (section, space, line)


def _plain(text: str) -> str:
    """Flatten wikilinks to their label or target, so the digest adds no edges."""
    return WIKILINK_RE.sub(lambda m: (m.group(4) or m.group(1)).strip(), text)


def _visible(path: str, person: Person, rules: tuple[SpaceRule, ...], shared: str) -> bool:
    space = space_of_path(path, shared)
    if space is None or space == f"People/{person.id}" or not can_read(space, person, rules):
        return False
    return not (_SKIP_SEGMENTS & set(PurePosixPath(path).parts)
                or is_generated_person_note(path))


def _fact_line(fc: FactChange, path: str) -> str:
    text = _plain(fc.statement)
    if fc.kind == "removed":
        return f"removed: {text} — `{path}`"
    dates = f"from {fc.from_date}"
    if fc.kind == "ended":
        dates += f", ended {fc.until_date}"
    return f"{text} ({dates}) — `{path}`"


def build_person_digest(person: Person, changes: list[NoteChange], org: Org,
                        rules: tuple[SpaceRule, ...], shared: str) -> PersonDigest | None:
    """What changed for `person`: only notes they can read, never their own
    edits, and no path from a space they cannot read, even through a rename."""
    me = f"{person.id}@brain.local"
    names = {f"{p.id}@brain.local": p.name for p in org.people.values()}
    fact_rows: list[tuple[int, str, str, str]] = []
    notes: list[tuple[int, str, str, str, str]] = []  # rank, space, path, section, line

    for c in changes:
        if c.authors and all(email == me for email, _name in c.authors):
            continue
        by = sorted({names[e] for e, _n in c.authors if e in names and e != me})
        suffix = f" (by {', '.join(by)})" if by else ""
        new_ok = c.status != "deleted" and _visible(c.path, person, rules, shared)
        old_ok = c.old_path is not None and _visible(c.old_path, person, rules, shared)
        shown, facts, kind = c.path, c.facts, None
        if c.status == "deleted":
            kind = "removed" if _visible(c.path, person, rules, shared) else None
            facts = ()
        elif c.status == "renamed":
            if new_ok and old_ok:
                kind = "renamed"
            elif new_ok:
                kind, facts = "added", c.facts_as_new  # the old path is never revealed
            elif old_ok:
                kind, shown, facts = "removed", c.old_path, ()
        elif c.status == "added":
            kind = "added" if new_ok else None
        else:
            kind = "modified" if new_ok else None
        if kind is None:
            continue
        space = space_of_path(shown, shared) or ""
        for fc in facts:
            order = {"started": 0, "ended": 1, "removed": 2}[fc.kind]
            fact_rows.append((order, shown, fc.statement, _fact_line(fc, shown)))
        if kind == "added":
            notes.append((0, space, shown, "added", f"`{shown}`{suffix}"))
        elif kind == "modified":
            if c.changed_lines >= MIN_CHANGED_LINES or c.facts:
                secs = f" — {'; '.join(_plain(h) for h in c.headings)}" if c.headings else ""
                notes.append((1, space, shown, "modified", f"`{shown}`{secs}{suffix}"))
        elif kind == "renamed":
            edit = ""
            if c.changed_lines >= MIN_CHANGED_LINES and c.headings:
                edit = f" — edited: {'; '.join(_plain(h) for h in c.headings)}"
            notes.append((2, space, shown, "moved",
                          f"`{shown}` (renamed from `{c.old_path}`){edit}{suffix}"))
        else:
            notes.append((2, space, shown, "moved", f"`{shown}` (removed){suffix}"))

    if not fact_rows and not notes:
        return None
    fact_lines = tuple(row[3] for row in sorted(fact_rows))
    ordered = sorted(notes)
    return PersonDigest(fact_lines, tuple((n[3], n[1], n[4]) for n in ordered))


def render_weekly(pd: PersonDigest, window: Window) -> tuple[str, str]:
    """The note's text and a fingerprint of its body (so an unchanged week is
    not rewritten). Plain text only: no wikilinks and no fact markup."""
    last = (window.end - timedelta(days=1)).date().isoformat()
    first = window.start.date().isoformat()
    body = ["# Weekly digest", "",
            f"What changed in the spaces you can read, {first} to {last}. Tell your",
            "human what matters in it, once, in plain words. Do not edit or delete",
            "this note: it is replaced each Monday and disappears in a week with",
            "nothing to report."]
    if pd.facts:
        body += ["", "## Facts that changed", ""]
        body += [f"- {line}" for line in pd.facts[:MAX_FACTS]]
        if len(pd.facts) > MAX_FACTS:
            body.append(f"- +{len(pd.facts) - MAX_FACTS} more fact changes")
    shown = pd.notes[:MAX_NOTES]
    overflow: dict[str, int] = defaultdict(int)
    for _section, space, _line in pd.notes[MAX_NOTES:]:
        overflow[space] += 1
    for key, title in _SECTIONS:
        rows = [(space, line) for sec, space, line in shown if sec == key]
        if not rows:
            continue
        body += ["", f"## {title}"]
        current = None
        for space, line in rows:
            if space != current:
                body += ["", f"### {space}", ""]
                current = space
            body.append(f"- {line}")
    if overflow:
        body += ["", "## Not shown", ""]
        body += [f"- +{n} more in {space}" for space, n in sorted(overflow.items())]
    text = "\n".join(body) + "\n"
    fingerprint = hashlib.sha256(f"{window.covered}\n{text}".encode()).hexdigest()
    head = ["---", "title: Weekly digest", "source: digest", f"week: {window.covered}",
            f"from: {first}", f"through: {last}", f"fingerprint: {fingerprint}", "---", ""]
    return "\n".join(head) + text, fingerprint
