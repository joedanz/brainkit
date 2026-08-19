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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from brain.frontmatter import split_frontmatter

CORRECTIONS_LIMIT = 4000
CORRECTIONS_DIR = "Corrections"

_HEADING = "## Standing corrections\n\n"


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


def load_corrections(
    vault: Path, pid: str, *, limit: int = CORRECTIONS_LIMIT
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
    is omitted alone, and the rules after it still render.
    """
    d = vault / "People" / pid / CORRECTIONS_DIR
    if not d.is_dir():
        return CorrectionSet((), (), (), (), (), ())

    parsed: list[Correction] = []
    unusable: list[str] = []
    undated: list[str] = []
    unreadable: list[str] = []

    for f in sorted(d.glob("*.md")):
        if not f.is_file():
            continue
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
    used = len(_HEADING)
    full = False
    for c in ordered:
        cost = len(_bullet(c))
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
    )


def render_corrections(cs: CorrectionSet) -> str:
    """The markdown block, or "" when there is nothing to say.

    Empty means empty: no heading, no "none yet" copy. A heading with nothing
    under it invites an agent to wonder what it is missing.
    """
    if not cs.rendered:
        return ""
    return _HEADING + "".join(_bullet(c) for c in cs.rendered)
