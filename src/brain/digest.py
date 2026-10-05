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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath

import yaml

from brain.compiler import WEEKLY_DIGEST_NAME, WIKILINK_RE, is_generated_person_note
from brain.errors import BrainError
from brain.facts import _FIELD, parse_facts
from brain.inboxnote import sync_inbox_note
from brain.promotions import PromotionError, _commit
from brain.resolver import can_read, space_of_path
from brain.schemas import (
    Org,
    Person,
    SchemaError,
    SpaceRule,
    load_config,
    load_org,
    load_spaces,
)

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")

MAX_FACTS = 15
MAX_NOTES = 25
MIN_CHANGED_LINES = 3
MAX_DIFF_LINES = 2000  # difflib is quadratic; beyond this git's own count is used
MARKER_REL = "_meta/cache/digest-week"
_SKIP_SEGMENTS = frozenset({"Inbox", "Sessions"})
_SECTIONS = (("added", "New notes"), ("modified", "Edited notes"),
             ("moved", "Removed or renamed"))
_SECTION_ORDER = {key: i for i, (key, _title) in enumerate(_SECTIONS)}
_FACT_ORDER = {"started": 0, "ended": 1, "removed": 2}
_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, '"': 34, "\\": 92}


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
    facts: tuple[FactChange, ...]
    facts_as_new: tuple[FactChange, ...]  # every fact at the end, as "started"
    # Who touched each side, as (email, name). A reader who can see only one side
    # of a rename must be credited from that side alone: git pairs unrelated
    # deleted and added notes as a "rename", and the other side's author is not
    # theirs to see. For a note without a rename, `authors_new` is everyone.
    authors_old: frozenset[tuple[str, str]] = frozenset()
    authors_new: frozenset[tuple[str, str]] = frozenset()

    @property
    def authors(self) -> frozenset[tuple[str, str]]:
        return self.authors_old | self.authors_new


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




def _numstat(master: Path, start: str, end: str, *paths: str) -> int:
    """Changed lines for a note, as git counts them (linear in the file)."""
    out = _git(master, "diff", "--numstat", "-M", "-z", start, end, "--", *paths)
    first = out.split("\0", 1)[0].split("\t")
    try:
        return max(int(first[0]), int(first[1]))
    except (ValueError, IndexError):  # binary or nothing to count
        return 0


def _edit_stats(before: str, after: str, numstat) -> tuple[int, tuple[str, ...]]:
    a, b = before.splitlines(), after.splitlines()
    if len(a) > MAX_DIFF_LINES or len(b) > MAX_DIFF_LINES:
        return numstat(), ()  # too big to find the sections cheaply: count only
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




def _unquote(path: str) -> str:
    """Undo git's C-style quoting of a name with a quote, backslash or control
    character (non-ASCII is left alone by core.quotepath=false)."""
    if not (len(path) >= 2 and path[0] == '"' and path[-1] == '"'):
        return path
    raw, out, i = path[1:-1], bytearray(), 0
    while i < len(raw):
        if raw[i] != "\\":
            out += raw[i].encode("utf-8")
            i += 1
        elif raw[i + 1] in _C_ESCAPES:
            out.append(_C_ESCAPES[raw[i + 1]])
            i += 2
        else:  # \ooo: three octal digits
            out.append(int(raw[i + 1:i + 4], 8))
            i += 4
    return out.decode("utf-8", errors="replace")


def _authors(master: Path, start: str | None, end: str) -> dict[str, set[tuple[str, str]]]:
    rng = f"{start}..{end}" if start else end
    # \x01 starts a commit line: git C-quotes a raw control character in a
    # name, so no real path line can begin with it (a name starting "@@" can).
    out = _git(master, "log", rng, "--name-only", "--format=%x01%ae%x1f%an")
    by_path: dict[str, set[tuple[str, str]]] = {}
    who: tuple[str, str] | None = None
    for line in out.splitlines():
        if line.startswith("\x01"):
            email, name = line[1:].split("\x1f", 1)
            who = (email, name)
        elif line.strip() and who is not None:
            by_path.setdefault(_unquote(line), set()).add(who)
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
        if status in ("added", "deleted"):
            lines, headings = 0, ()
        else:
            lines, headings = _edit_stats(
                before, after or "",
                lambda o=old, n=new: _numstat(master, start, end, *((o, n) if o else (n,))))
        started = _fact_changes("", after) if after is not None else ()
        changes.append(NoteChange(
            status=status, path=new, old_path=old, changed_lines=lines, headings=headings,
            facts=started if status == "added" else _fact_changes(before, after),
            facts_as_new=started if status in ("added", "renamed") else (),
            authors_old=frozenset(authors.get(old, ())) if old else frozenset(),
            authors_new=frozenset(authors.get(new, ()))))
    return sorted(changes, key=lambda c: c.path)



@dataclass(frozen=True)
class _Row:
    section: str  # a key of _SECTIONS
    space: str
    path: str
    line: str


@dataclass(frozen=True)
class PersonDigest:
    facts: tuple[str, ...]
    notes: tuple[_Row, ...]


@dataclass(frozen=True)
class _View:
    """What one reader may see of one change."""
    kind: str  # "added" | "modified" | "renamed" | "removed"
    shown: str  # the path to name: never one the reader cannot see
    facts: tuple[FactChange, ...]
    who: frozenset[tuple[str, str]]  # only the authors of the side they can see


def _plain(text: str) -> str:
    """Flatten wikilinks to their label or target and drop fact fields, so the
    digest adds no edges and nothing in it parses as a fact."""
    flat = WIKILINK_RE.sub(lambda m: (m.group(4) or m.group(1)).strip(), _FIELD.sub("", text))
    return re.sub(r"\s+", " ", flat).strip()


@lru_cache(maxsize=65536)
def _path_info(path: str, shared: str) -> tuple[str | None, bool]:
    """(space, eligible): the checks that do not depend on who is reading."""
    space = space_of_path(path, shared)
    eligible = space is not None and not (
        _SKIP_SEGMENTS & set(PurePosixPath(path).parts) or is_generated_person_note(path))
    return space, eligible


def _visible(path: str, person: Person, rules: tuple[SpaceRule, ...], shared: str) -> bool:
    space, eligible = _path_info(path, shared)
    return (eligible and space != f"People/{person.id}"
            and can_read(space, person, rules))


def _view_for(c: NoteChange, person: Person, rules: tuple[SpaceRule, ...],
              shared: str) -> _View | None:
    """What `person` may see of change `c`, or None. A rename they can see only
    one end of is shown as a plain addition or removal, never naming the other
    path or crediting the other side's authors."""
    if c.status == "deleted":
        return _View("removed", c.path, (), c.authors) \
            if _visible(c.path, person, rules, shared) else None
    new_ok = _visible(c.path, person, rules, shared)
    if c.status == "renamed":
        old_ok = _visible(c.old_path, person, rules, shared)
        if new_ok and old_ok:
            return _View("renamed", c.path, c.facts, c.authors)
        if new_ok:
            return _View("added", c.path, c.facts_as_new, c.authors_new)
        if old_ok:
            return _View("removed", c.old_path, (), c.authors_old)
        return None
    return _View(c.status, c.path, c.facts, c.authors) if new_ok else None


def _fact_line(fc: FactChange, path: str) -> str:
    text = _plain(fc.statement)
    if fc.kind == "removed":
        return f"removed: {text} — `{path}`"
    dates = f"from {fc.from_date}"
    if fc.kind == "ended":
        dates += f", ended {fc.until_date}"
    return f"{text} ({dates}) — `{path}`"


def _sections_text(headings: tuple[str, ...]) -> str:
    return "; ".join(_plain(h) for h in headings)


def build_person_digest(person: Person, changes: list[NoteChange], org: Org,
                        rules: tuple[SpaceRule, ...], shared: str) -> PersonDigest | None:
    """What changed for `person`: only notes they can read, never their own
    edits, and no path from a space they cannot read, even through a rename."""
    me = f"{person.id}@brain.local"
    names = {f"{p.id}@brain.local": p.name for p in org.people.values()}
    fact_rows: list[tuple[int, str, str, str]] = []
    notes: list[_Row] = []

    for c in changes:
        view = _view_for(c, person, rules, shared)
        if view is None:
            continue
        # Your own work is not news to you. System commits (a link rewrite, a
        # server action) do not make it anyone else's, but a note that only the
        # system touched is shown.
        people = {email for email, _name in view.who if email in names}
        if people == {me}:
            continue
        by = sorted({names[e] for e in people if e != me})
        suffix = f" (by {', '.join(by)})" if by else ""
        for fc in view.facts:
            fact_rows.append((_FACT_ORDER[fc.kind], view.shown, fc.statement,
                              _fact_line(fc, view.shown)))
        section, detail = None, ""
        if view.kind == "added":
            section = "added"
        elif view.kind == "modified":
            if c.changed_lines >= MIN_CHANGED_LINES or c.facts:
                section = "modified"
                detail = f" — {_sections_text(c.headings)}" if c.headings else ""
        elif view.kind == "renamed":
            section = "moved"
            edit = (f" — edited: {_sections_text(c.headings)}"
                    if c.changed_lines >= MIN_CHANGED_LINES and c.headings else "")
            detail = f" (renamed from `{c.old_path}`){edit}"
        else:
            section, detail = "moved", " (removed)"
        if section:
            notes.append(_Row(section, space_of_path(view.shown, shared) or "", view.shown,
                              f"`{view.shown}`{detail}{suffix}"))

    if not fact_rows and not notes:
        return None
    notes.sort(key=lambda r: (_SECTION_ORDER[r.section], r.space, r.path, r.line))
    return PersonDigest(tuple(row[3] for row in sorted(fact_rows)), tuple(notes))


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
    for row in pd.notes[MAX_NOTES:]:
        overflow[row.space] += 1
    for key, title in _SECTIONS:
        rows = [r for r in shown if r.section == key]
        if not rows:
            continue
        body += ["", f"## {title}"]
        current = None
        for r in rows:
            if r.space != current:
                body += ["", f"### {r.space}", ""]
                current = r.space
            body.append(f"- {r.line}")
    if overflow:
        body += ["", "## Not shown", ""]
        body += [f"- +{n} more in {space}" for space, n in sorted(overflow.items())]
    text = "\n".join(body) + "\n"
    fingerprint = hashlib.sha256(f"{window.covered}\n{text}".encode()).hexdigest()
    head = ["---", "title: Weekly digest", "source: digest", f"week: {window.covered}",
            f"from: {first}", f"through: {last}", f"fingerprint: {fingerprint}", "---", ""]
    return "\n".join(head) + text, fingerprint

@dataclass
class DigestReport:
    ran: bool = False
    written: int = 0
    removed: int = 0
    warnings: list[str] = field(default_factory=list)


def _marker(master: Path) -> str | None:
    try:
        return (master / MARKER_REL).read_text().strip() or None
    except OSError:
        return None


def run_digest(master: Path, *, today: str) -> DigestReport:
    """Write each person's weekly digest if this ISO week's is due.

    Due means the marker in the gitignored cache names an earlier week. The
    marker is set only after a clean finish, so a failure retries next cycle,
    and losing it costs one rebuild whose unchanged notes are not rewritten.
    """
    window = window_for(today)
    if _marker(master) == window.due:
        return DigestReport()
    report = DigestReport(ran=True)
    try:
        org = load_org(master / "_meta/org.yaml")
        rules = load_spaces(master / "_meta/spaces.yaml")
        shared = load_config(master).shared
    except (SchemaError, OSError, yaml.YAMLError) as e:
        report.warnings.append(f"meta unreadable — no digests written: {e}")
        return report
    try:
        changes = collect_changes(master, window)
    except DigestError as e:
        report.warnings.append(f"digest skipped: {e}")
        return report

    changed: list[str] = []
    for person in org.people.values():
        built = build_person_digest(person, changes, org, rules, shared)
        content, fp = render_weekly(built, window) if built else (None, None)
        outcome = sync_inbox_note(master, person, rules, shared, WEEKLY_DIGEST_NAME,
                                  content=content, fingerprint=fp,
                                  warnings=report.warnings)
        if outcome in ("written", "removed"):
            report.written += outcome == "written"
            report.removed += outcome == "removed"
            changed.append(f"People/{person.id}/Inbox/{WEEKLY_DIGEST_NAME}")
    if changed:
        try:
            _commit(master, changed,
                    f"digest: {report.written} written, {report.removed} removed",
                    "Brain Digest", "digest@brain.local")
        except PromotionError as e:
            report.warnings.append(f"git commit failed: {e}")
            return report
    try:
        (master / MARKER_REL).parent.mkdir(parents=True, exist_ok=True)
        (master / MARKER_REL).write_text(window.due + "\n")
    except OSError as e:
        report.warnings.append(f"digest marker not saved: {e}")
    return report
