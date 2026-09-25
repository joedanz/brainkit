"""Standing corrections: what a person told their agent it got wrong.

A correction is one file under ``People/<pid>/Corrections/``. Its ``rule:``
is an imperative sentence that `contextgen` renders into the generated
protocol, so the agent loads it on every turn rather than having to think to
search for it — an agent that knew to look up the rule would not have needed
it.

Pure over a directory on purpose, in the spirit of `facts.py`: `contextgen`
renders from it and `doctor` reports on it, and neither has to import the
other. The budget lives here too, so "what the agent sees" and "what doctor
warns about" can never be computed two different ways.

A rule renders only once its person (or an admin) has confirmed that exact
text: the record lives in master at People/<pid>/.corrections.json, which is
never compiled into a vault, so the agent that writes a correction cannot
also confirm it. See docs/superpowers/specs/2026-09-25-corrections-confirm-design.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from brain import hermes_filter
from brain.compiler import CONFIRMED_NAME
from brain.errors import HANDLED, BrainError
from brain.frontmatter import split_frontmatter
from brain.holds import CYCLE_EMAIL, CYCLE_NAME
from brain.writeback import commit_paths

CORRECTIONS_LIMIT = 4000
CORRECTIONS_DIR = "Corrections"
RULE_MAX = 280
RECORD_REL = f"People/{{person_id}}/{CONFIRMED_NAME}"
GRANDFATHERED = "grandfathered"
PENDING_NOTE_REL = "People/{person_id}/Pending-corrections.md"

_HEADING = "## Standing corrections\n\n"
_WEB_ADDRESS = re.compile(r"https?://|www\.", re.IGNORECASE)


class CorrectionError(BrainError, ValueError):
    """A correction or its confirmation record cannot be used as asked."""


@dataclass(frozen=True)
class Rejected:
    slug: str
    rule: str
    reason: str  # plain language, shown to the person


@dataclass(frozen=True)
class Correction:
    slug: str
    rule: str
    from_date: str | None  # YYYY-MM-DD, or None when missing/unparseable


@dataclass(frozen=True)
class CorrectionSet:
    rendered: tuple[Correction, ...]   # fit the budget, in render order
    omitted: tuple[Correction, ...]    # well-formed, but the budget ran out
    oversized: tuple[Correction, ...]  # longer than the whole budget — never fit
    unusable: tuple[str, ...]          # slugs with no `rule:` — never rendered
    undated: tuple[str, ...]           # slugs whose `from:` did not parse
    unreadable: tuple[str, ...]        # slugs the OS would not hand over
    misfiled: tuple[str, ...]          # paths under the dir the loader ignores
    flagged: tuple[Correction, ...] = ()  # Hermes would drop the protocol -- withheld
    pending: tuple[Correction, ...] = ()   # well-shaped, not (or no longer) confirmed
    rejected: tuple[Rejected, ...] = ()    # fails the shape limits -- never renders
    record_error: str | None = None        # the record could not be used; all pending

    @property
    def active(self) -> tuple[Correction, ...]:
        """Every rule still in play: it renders now, or could once confirmed
        or once the budget allows. Used by confirm, the CLI list, and the
        dashboard view -- none of them care about `unusable`/`undated`/
        `unreadable`/`misfiled`, which are filing defects, not live rules."""
        return self.rendered + self.omitted + self.oversized + self.pending


def _read_text(path: Path) -> str | None:
    """A correction's text, or None if the OS refuses to hand the file over.

    `errors="replace"`, exactly as `doctor._read_text` reads every other note:
    one Windows-1252 smart quote pasted out of a document must not raise
    UnicodeDecodeError up through `generate_context_files` and abort the whole
    compile for that person — or, from `doctor`, abort the whole run. The rule
    still renders, with a replacement character where the byte was, which is a
    visible blemish rather than a silent drop. A file that cannot be read at
    all becomes `CorrectionSet.unreadable`: reported, never fatal.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()).isoformat()
    except ValueError:
        return None


def _bullet(c: Correction) -> str:
    return f"- {c.rule}\n"


def rule_hash(rule: str) -> str:
    """What a confirmation pins: the rule text exactly as it renders."""
    return hashlib.sha256(rule.encode("utf-8")).hexdigest()


def shape_problem(rule: str) -> str | None:
    """Why this rule can never be a standing correction, or None.

    A correction is one short imperative sentence. Anything else -- a
    paragraph, a link to follow, a command to run -- is what a planted
    instruction looks like, so it is refused even if someone confirms it."""
    if "\n" in rule or "\r" in rule:
        return "it is more than one line"
    if len(rule) > RULE_MAX:
        return f"it is longer than {RULE_MAX} characters"
    if _WEB_ADDRESS.search(rule):
        return "it contains a web address"
    if "`" in rule:
        return "it contains a backtick"
    return None


def load_record(root: Path, pid: str) -> dict[str, dict]:
    """The person's confirmation record, or {} when there is none yet."""
    rel = RECORD_REL.format(person_id=pid)
    path = root / rel
    if not path.is_file():
        return {}
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CorrectionError(f"{rel}: unreadable corrections record ({e})") from e
    if not isinstance(rec, dict) or not all(
        isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("sha256"), str)
        for k, v in rec.items()
    ):
        raise CorrectionError(f"{rel}: corrections record is the wrong shape")
    return rec


def confirmed_hashes(record: Mapping[str, dict]) -> dict[str, str]:
    return {slug: entry["sha256"] for slug, entry in record.items()}


def flag_patterns(c: Correction) -> tuple[str, ...]:
    """The Hermes filter ids this rule's rendered bullet matches, or ().

    A match anywhere in the protocol makes Hermes Agent drop the whole file,
    so a matching rule is withheld rather than rendered."""
    return hermes_filter.blocks(_bullet(c))


def load_corrections(
    vault: Path, pid: str, *, limit: int = CORRECTIONS_LIMIT,
    confirmed: Mapping[str, str] | None = None,
) -> CorrectionSet:
    """Read, order and budget one person's corrections.

    Order is newest `from:` first, slug ascending within a date. A record
    whose date does not parse still renders — losing a rule to a typo would be
    exactly the silent drop this design exists to prevent — but sorts after
    every well-formed one, so a typo can never push a real rule out of the
    budget.

    Two ways a rule fails to render, kept apart because the fix differs. The
    budget running out cascades: every rule after the first that does not fit
    is omitted too. A rule longer than the entire budget never cascades — it
    is omitted alone, and the rules after it still render. A third,
    `flagged`: a rule the Hermes filter matches is withheld alone, like an
    oversized one, because rendering it would make Hermes drop the whole
    protocol.

    Only `*.md` directly in the directory is a correction. Anything else under
    it — a subfolder, another extension — is `misfiled`: recorded here rather
    than passed over, because a rule nobody is told about is the silent drop
    this design exists to prevent. `misfiled` is derived by subtracting the
    files this function actually read, so the two can never drift apart.

    Before the budget, each rule passes three gates in order: the shape
    limits (`rejected`, never rendered even if confirmed), the Hermes filter
    (`flagged`), and confirmation (`pending` unless the record holds this
    exact text's hash). `confirmed` maps slug to hash; None reads the record
    under `vault`, and a record that cannot be read leaves every rule pending
    (fail closed) with `record_error` set for doctor.
    """
    d = vault / "People" / pid / CORRECTIONS_DIR
    if not d.is_dir():
        return CorrectionSet((), (), (), (), (), (), ())

    record_error: str | None = None
    if confirmed is None:
        try:
            confirmed = confirmed_hashes(load_record(vault, pid))
        except CorrectionError as e:
            confirmed, record_error = {}, str(e)

    parsed: list[Correction] = []
    unusable: list[str] = []
    undated: list[str] = []
    unreadable: list[str] = []

    loaded: set[Path] = set()

    for f in sorted(d.glob("*.md")):
        if not f.is_file():
            continue
        loaded.add(f)
        slug = f.stem
        text = _read_text(f)
        if text is None:
            unreadable.append(slug)
            continue
        fm, _body = split_frontmatter(text)
        rule = (fm.get("rule") or "").strip()
        if not rule:
            unusable.append(slug)
            continue
        from_date = _parse_date(fm.get("from"))
        if from_date is None:
            undated.append(slug)
        parsed.append(Correction(slug, rule, from_date))

    # Everything under the directory that was not read above. Dotfiles are
    # excluded: `.DS_Store` is not a correction someone believes is in force,
    # and a digest saying so would train people to ignore this finding.
    misfiled = sorted(
        p.relative_to(d).as_posix()
        for p in d.rglob("*")
        if p.is_file() and p not in loaded
        and not any(part.startswith(".") for part in p.relative_to(d).parts)
    )

    # Two stable sorts rather than one composite key: dates are strings, so
    # "newest first" cannot be expressed as a single ascending tuple without
    # inverting them. Sorting by slug first and then by date (stable) leaves
    # slug order intact inside each date.
    dated = sorted([c for c in parsed if c.from_date], key=lambda c: c.slug)
    dated.sort(key=lambda c: c.from_date or "", reverse=True)
    ordered = dated + sorted([c for c in parsed if not c.from_date], key=lambda c: c.slug)

    rendered: list[Correction] = []
    omitted: list[Correction] = []
    oversized: list[Correction] = []
    flagged: list[Correction] = []
    pending: list[Correction] = []
    rejected: list[Rejected] = []
    used = len(_HEADING)
    full = False
    for c in ordered:
        problem = shape_problem(c.rule)
        if problem is not None:
            rejected.append(Rejected(c.slug, c.rule, problem))
            continue
        bullet = _bullet(c)
        if hermes_filter.blocks(bullet):
            # Like oversized, this never cascades: it is a defect in one
            # rule, and the rules after it still render. Checked first,
            # because a rule that would drop the whole protocol must never
            # render, whatever its length.
            flagged.append(c)
            continue
        if confirmed.get(c.slug) != rule_hash(c.rule):
            # Not confirmed, or confirmed as different text. Never cascades
            # and never spends budget: only a rule that can render does.
            pending.append(c)
            continue
        cost = len(bullet)
        if len(_HEADING) + cost > limit:
            # This one cannot fit even an empty budget, so nothing anyone
            # prunes around it will ever render it. That is a defect in one
            # rule, not a full block — and because order is newest-first, a
            # single over-long rule sorts to the front, so letting it cascade
            # would delete the person's whole standing-corrections block.
            oversized.append(c)
            continue
        if full or used + cost > limit:
            # Ordinary running out of room, which does cascade: once one rule
            # is omitted every later rule is too, so the rendered set depends
            # on the stated order rather than on which rules happen to fit.
            full = True
            omitted.append(c)
            continue
        rendered.append(c)
        used += cost

    return CorrectionSet(
        rendered=tuple(rendered),
        omitted=tuple(omitted),
        oversized=tuple(oversized),
        unusable=tuple(unusable),
        undated=tuple(undated),
        unreadable=tuple(unreadable),
        misfiled=tuple(misfiled),
        flagged=tuple(flagged),
        pending=tuple(pending),
        rejected=tuple(rejected),
        record_error=record_error,
    )


def render_corrections(cs: CorrectionSet) -> str:
    """The markdown block, or "" when there is nothing to say.

    Empty means empty: no heading, no "none yet" copy. A heading with nothing
    under it invites an agent to wonder what it is missing.
    """
    if not cs.rendered:
        return ""
    return _HEADING + "".join(_bullet(c) for c in cs.rendered)


def render_pending_note(cs: CorrectionSet) -> str | None:
    """The person's read-only notice, or None when nothing waits on them.

    Pending rules are listed with their text, because the person has to read
    exactly what they are confirming. Rejected ones show only why: their text
    is the part that failed the limits."""
    if not cs.pending and not cs.rejected:
        return None
    lines = [
        "---", "generated: true", "---",
        "# Corrections waiting for you", "",
        "Your agent wrote these corrections. A correction only reaches your",
        "agent after you confirm it, so none of these is in effect yet.", "",
        "Open your dashboard and use the Corrections tab to confirm or dismiss",
        "each one. This file is rebuilt every time your vault is refreshed, so",
        "edits here are discarded.", "",
    ]
    if cs.pending:
        lines += ["## Waiting for you", "",
                  *(f"- {c.slug}: {c.rule}" for c in cs.pending), ""]
    if cs.rejected:
        lines += ["## Cannot be used", "",
                  "These can't be confirmed. Dismiss them, or ask your agent to",
                  "rewrite each as one short sentence.", "",
                  *(f"- {r.slug}: {r.reason}" for r in cs.rejected), ""]
    return "\n".join(lines)


def _write_record(master: Path, pid: str, record: Mapping[str, dict]) -> str:
    rel = RECORD_REL.format(person_id=pid)
    path = master / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(record), indent=2, sort_keys=True) + "\n")
    return rel


def grandfather(master: Path, person_ids: Iterable[str], *, now: str) -> list[str]:
    """Record every current well-shaped rule as confirmed, once per person.

    Runs at the start of a cycle, before write-back, so only rules already in
    master on upgrade day are kept in force. A person with no corrections
    still gets `{}`: without it, the first rule their agent wrote later would
    be grandfathered on the next cycle. A record that exists -- even one that
    cannot be read -- means this already happened; doctor reports a broken
    one, and it is never overwritten here."""
    written: list[str] = []
    for pid in person_ids:
        if (master / RECORD_REL.format(person_id=pid)).exists():
            continue
        cs = load_corrections(master, pid, confirmed={})
        record = {c.slug: {"sha256": rule_hash(c.rule), "by": GRANDFATHERED, "at": now}
                  for c in (*cs.pending, *cs.flagged)}
        written.append(_write_record(master, pid, record))
    if not written:
        return written
    try:
        commit_paths(master, written, name=CYCLE_NAME, email=CYCLE_EMAIL,
                     message=f"corrections: keep existing corrections for "
                             f"{len(written)} person(s) in force")
    except HANDLED:
        # Leave nothing half-done: with the records gone, every rule stays
        # pending (fail closed) and the next cycle tries again.
        for rel in written:
            (master / rel).unlink(missing_ok=True)
        raise
    return written
