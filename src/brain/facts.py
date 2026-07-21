"""Bi-temporal fact lines and typed entity pages — the pure parsing layer.

A fact is any markdown bullet whose text carries a ``[from:: date]`` Dataview
inline field; ``[until::]`` closes it, ``[source::]`` cites it. The functions
here are pure over text on purpose: the indexer runs them over working-tree
files and belief-time queries run the same functions over file blobs from any
git commit — one grammar, two clocks (valid time in the text, transaction
time in git).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

from brain.compiler import extract_wikilinks

# [from:: v] / [until:: v] / [source:: v] — v may contain [[wikilinks]],
# so the value grammar is "wikilinks or any non-bracket run".
_FIELD = re.compile(r"\[(from|until|source)::\s*((?:\[\[[^\]]*\]\]|[^\[\]])*?)\s*\]")
_BULLET = re.compile(r"^(\s*)- ")


@dataclass
class Fact:
    line: int  # 1-based line number of the bullet start
    statement: str
    from_date: str  # normalized YYYY-MM-DD
    until_date: str | None
    sources: list[str]
    targets: list[str]  # raw wikilink targets found in the statement


def _valid_date(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _normalize(raw: str, *, month_end: bool) -> str | None:
    parts = raw.strip().split("-")
    if not all(p.isascii() and p.isdigit() for p in parts):
        return None
    if len(parts) == 2:
        y, m = int(parts[0]), int(parts[1])
        if not 1 <= m <= 12:
            return None
        d = calendar.monthrange(y, m)[1] if month_end else 1
        return f"{y:04d}-{m:02d}-{d:02d}"
    if len(parts) == 3:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{y:04d}-{m:02d}-{d:02d}" if 1 <= m <= 12 and _valid_date(y, m, d) else None
    return None


def normalize_from(raw: str) -> str | None:
    return _normalize(raw, month_end=False)


def normalize_until(raw: str) -> str | None:
    return _normalize(raw, month_end=True)


def _list_items(text: str) -> list[tuple[int, str]]:
    """Collect (1-based start line, joined text) for every markdown bullet,
    folding indented continuation lines into their item."""
    items: list[tuple[int, str]] = []
    cur_line, cur_parts = 0, []
    for i, line in enumerate(text.splitlines(), 1):
        if _BULLET.match(line):
            if cur_parts:
                items.append((cur_line, " ".join(cur_parts)))
            cur_line, cur_parts = i, [line.strip()[2:].strip()]
        elif cur_parts and line[:1] in (" ", "\t") and line.strip():
            cur_parts.append(line.strip())
        else:
            if cur_parts:
                items.append((cur_line, " ".join(cur_parts)))
            cur_line, cur_parts = 0, []
    if cur_parts:
        items.append((cur_line, " ".join(cur_parts)))
    return items


def _fields(item_text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"from": [], "until": [], "source": []}
    for key, value in _FIELD.findall(item_text):
        out[key].append(value.strip())
    return out


def parse_facts(text: str) -> list[Fact]:
    facts: list[Fact] = []
    for line, item in _list_items(text):
        fields = _fields(item)
        if not fields["from"]:
            continue
        from_date = normalize_from(fields["from"][0])
        if from_date is None:
            continue
        until_date = None
        if fields["until"]:
            until_date = normalize_until(fields["until"][0])
            if until_date is None or until_date < from_date:
                continue
        statement = _FIELD.sub("", item)
        statement = re.sub(r"\s+", " ", statement).strip()
        facts.append(Fact(
            line=line, statement=statement, from_date=from_date,
            until_date=until_date, sources=fields["source"],
            targets=extract_wikilinks(statement),
        ))
    return facts


def lint_facts(text: str) -> list[tuple[int, str]]:
    """Problems that keep a bullet from being a fact. Warn-only material."""
    problems: list[tuple[int, str]] = []
    for line, item in _list_items(text):
        fields = _fields(item)
        if fields["until"] and not fields["from"]:
            problems.append((line, "until without from"))
            continue
        if not fields["from"]:
            continue
        from_date = normalize_from(fields["from"][0])
        if from_date is None:
            problems.append((line, f"unparseable from date: {fields['from'][0]!r}"))
            continue
        if fields["until"]:
            until_date = normalize_until(fields["until"][0])
            if until_date is None:
                problems.append((line, f"unparseable until date: {fields['until'][0]!r}"))
            elif until_date < from_date:
                problems.append((line, f"until {until_date} is before from {from_date}"))
    return problems


def parse_entity(meta: dict[str, str]) -> tuple[str, list[str]] | None:
    """(type, aliases) from flat frontmatter, or None when not an entity page.
    aliases accepts the `[a, b]` inline-list form frontmatter.py leaves verbatim."""
    if "entity" not in meta:
        return None
    etype = meta["entity"].strip()
    raw = meta.get("aliases", "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    aliases = [a.strip() for a in raw.split(",") if a.strip()]
    return etype, aliases
