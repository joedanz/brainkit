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
import sqlite3
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


# Single-valued-attribute markers: a word-level common prefix ending in one of
# these (or in a token ending with ":") names one slot — "Acme's plan is" —
# so two open facts diverging after it assign that slot two values at once.
# Additive verbs (hired, met, shipped) are deliberately absent: those facts
# can all be true together, and a warn tier that cries wolf gets ignored.
_ATTR_MARKERS = {"is", "are", "="}


def _diverges(stmt_a: str, stmt_b: str) -> bool:
    a, b = stmt_a.casefold().split(), stmt_b.casefold().split()
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    if i < 2 or i == len(a) or i == len(b):
        # marker needs a preceding token; an empty tail is prefix-of, not conflict
        return False
    last = a[i - 1]
    return last in _ATTR_MARKERS or last.endswith(":")


def find_fact_conflicts(
    entries: list[tuple[str, Fact, frozenset[str]]],
) -> list[tuple[str, tuple, tuple]]:
    """Duplicate and contradicting *open* facts, per issue #79. Each entry is
    (rel_path, fact, entity_keys) with keys already resolved by the caller.
    Pure over its input like the rest of this module: no files, no vault.

    dup      — casefold-identical statements, equal key sets (double-landed
               ingest; `brain facts` returns the same line twice).
    conflict — statements sharing a key whose word-level common prefix ends in
               a single-valued-attribute marker and then diverges ("…plan is
               Enterprise" / "…plan is Growth" — a forgotten [until::]).

    Closed facts never participate; identical statements have an empty
    divergence, so no pair is ever both dup and conflict.
    """
    live = sorted((e for e in entries if e[1].until_date is None),
                  key=lambda e: (e[0], e[1].line))
    out: list[tuple[str, tuple, tuple]] = []
    for x in range(len(live)):
        for y in range(x + 1, len(live)):
            a, b = live[x], live[y]
            if not (a[2] & b[2]):
                continue
            if (a[1].statement.casefold() == b[1].statement.casefold()
                    and a[2] == b[2]):
                out.append(("dup", a, b))
            elif _diverges(a[1].statement, b[1].statement):
                out.append(("conflict", a, b))
    return out


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


import json as _json
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class FactHit:
    rel_path: str
    line: int
    statement: str
    from_date: str
    until_date: str | None
    sources: list[str]
    entities: list[str]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def query_facts(
    vault: Path,
    *,
    entity: str | None = None,
    etype: str | None = None,
    as_of: str | None = None,
    include_ended: bool = False,
) -> tuple[list[FactHit], list[str]]:
    """Facts true on `as_of` (default: today), optionally filtered to one
    entity (rel path, title stem, or alias) or one entity type."""
    from brain.store import IndexStore

    vault = Path(vault)
    db = vault / ".brain" / "index.db"
    if not db.is_file():
        return [], [f"no index at {db} — run: brain index --vault {vault}"]
    warnings: list[str] = []

    on = normalize_from(as_of) if as_of else _today()
    if on is None:
        return [], [f"unparseable date: {as_of!r}"]

    store = IndexStore.open_readonly(db, want_vectors=False)
    try:
        try:
            entity_paths: set[str] | None = None
            if entity is not None:
                known = {rel: (rel, etype_, aliases)
                         for rel, etype_, aliases in store.entities()}
                wanted = entity.strip().lower()
                matches = {rel for rel in known
                           if rel == entity
                           or Path(rel).stem.lower() == wanted
                           or wanted in (a.lower() for a in known[rel][2])}
                if not matches:
                    return [], [f"no entity matches {entity!r}"]
                entity_paths = matches
            etype_paths: set[str] | None = None
            if etype is not None:
                etype_paths = {rel for rel, t, _a in store.entities() if t == etype}

            by_fact: dict[int, list[str]] = {}
            for fid, target in store.conn.execute(
                    "SELECT fact_id, target_rel_path FROM fact_entities"):
                by_fact.setdefault(fid, []).append(target)

            hits: list[FactHit] = []
            for fid, rel, line, stmt, fdate, udate, sources in store.fact_rows():
                ents = sorted(by_fact.get(fid, []))
                if entity_paths is not None and not (entity_paths & set(ents)):
                    continue
                if etype_paths is not None and not (etype_paths & set(ents)):
                    continue
                if not include_ended:
                    if fdate > on or (udate is not None and udate < on):
                        continue
                hits.append(FactHit(rel, line, stmt, fdate, udate,
                                    _json.loads(sources), ents))
            return hits, warnings
        except sqlite3.OperationalError:
            # index predates schema v3 — facts/entities/fact_entities tables
            # don't exist yet (open_readonly deliberately skips DDL).
            return [], [f"index predates facts — run: brain index --vault {vault} --full"]
    finally:
        store.close()


def query_facts_at(
    vault: Path,
    on_date: str,
    *,
    entity: str | None = None,
    etype: str | None = None,
    include_ended: bool = False,
) -> tuple[list[FactHit], list[str]]:
    """What did we believe on `on_date`? Parses fact lines straight from the
    vault's git tree at the last commit on or before that date — the same pure
    parser the indexer uses, pointed at an older clock. No index involved.

    Entities in the hits are raw wikilink targets (plus the host page when it
    carries entity frontmatter): without an index there is no stem/alias
    resolution, and resolving against today's files would anachronistically
    apply the present to the past."""
    import subprocess

    from brain.frontmatter import split_frontmatter

    vault = Path(vault)
    on = normalize_from(on_date)
    if on is None:
        return [], [f"unparseable date: {on_date!r}"]

    def git(*argv: str) -> str:
        return subprocess.run(
            ["git", "-C", str(vault), *argv],
            capture_output=True, text=True, check=True,
        ).stdout

    try:
        commit = git("rev-list", "-1", f"--before={on}T23:59:59+0000", "HEAD").strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return [], ["no git history (vault is not a git repository)"]
    if not commit:
        return [], [f"no commit on or before {on}"]

    entities_then: dict[str, tuple[str, list[str]]] = {}
    page_facts: list[tuple[str, Fact]] = []
    for rel in git("ls-tree", "-r", "--name-only", commit).splitlines():
        if not rel.endswith(".md"):
            continue
        text = git("show", f"{commit}:{rel}")
        meta, _body = split_frontmatter(text)
        ent = parse_entity(meta)
        if ent is not None:
            entities_then[rel] = ent
        for f in parse_facts(text):
            page_facts.append((rel, f))

    # entity/type selection against the historical entity set
    entity_paths: set[str] | None = None
    if entity is not None:
        wanted = entity.strip().lower()
        entity_paths = {rel for rel, (t, aliases) in entities_then.items()
                        if rel == entity
                        or Path(rel).stem.lower() == wanted
                        or wanted in (a.lower() for a in aliases)}
        if not entity_paths:
            return [], [f"no entity matches {entity!r}"]
    etype_paths: set[str] | None = None
    if etype is not None:
        etype_paths = {rel for rel, (t, _a) in entities_then.items() if t == etype}

    hits: list[FactHit] = []
    for rel, f in page_facts:
        ents = sorted(set(f.targets) | ({rel} if rel in entities_then else set()))
        keyset = set(ents)
        if entity_paths is not None and not (entity_paths & keyset):
            continue
        if etype_paths is not None and not (etype_paths & keyset):
            continue
        if not include_ended:
            if f.from_date > on or (f.until_date is not None and f.until_date < on):
                continue
        hits.append(FactHit(rel, f.line, f.statement, f.from_date,
                            f.until_date, f.sources, ents))
    return hits, []
