"""Generate Map.md: what is in this vault right now.

The companion to contextgen's AGENTS.md. That file is instructions — stable
protocol, fixed size, raises when it exceeds its budget. This one is state —
regenerated every compile, data-driven, and it must NEVER raise: failing here
would fail the compile for the person with the largest vault, precisely the
person the map helps most.

Deliberately NOT an index. `brain_search` resolves aliases and has no size
cap; enumerating entity names here overruns the budget at ~127-322 entities
and answers the lookup worse. This file ships counts and shape so a cold
agent knows which searches are worth making.

Everything is computed from the compiler's building tree, which contains only
the spaces this person may read — so the map structurally cannot name a note
the person cannot see. No subprocess, no master handle, no filtering.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from brain.compiler import extract_wikilinks
from brain.frontmatter import split_frontmatter


@dataclass(frozen=True)
class NoteFacts:
    """One note's contribution to the map, read from its SHIPPED text."""

    entity: str  # "" when the note is not an entity page
    targets: tuple[str, ...]  # raw wikilink targets, in document order


def scan_note(text: str) -> NoteFacts:
    from brain.facts import parse_entity

    meta, _body = split_frontmatter(text)
    parsed = parse_entity(meta)
    # Scan the whole file, not just the body: typed relations (up/down/same/
    # prev/next) are wikilinks in frontmatter, and indexer._resolve_links
    # counts them. Scanning the body only would undercount hub degree.
    return NoteFacts(
        entity=parsed[0] if parsed else "",
        targets=tuple(extract_wikilinks(text)),
    )


def link_degree(notes: dict[str, NoteFacts]) -> dict[str, int]:
    """In+out resolved wikilink degree per note.

    Resolution mirrors indexer._resolve_links: lowercased stem, and on a
    duplicate stem the lexicographically first path wins (sorted iteration +
    setdefault). Unresolvable targets and self-links contribute nothing —
    the latter matches stats._build_graph's `src != target` filter.
    """
    from brain.compiler import _stem

    by_stem: dict[str, str] = {}
    for rel in sorted(notes):
        by_stem.setdefault(_stem(rel), rel)

    degree: dict[str, int] = {rel: 0 for rel in notes}
    for src, facts in notes.items():
        for raw in facts.targets:
            target = by_stem.get(_stem(raw))
            if target is None or target == src:
                continue
            degree[src] += 1
            degree[target] += 1
    return degree


def rank_hubs(degree: dict[str, int], cap: int) -> list[tuple[str, int]]:
    """Most-connected notes first. Zero-degree notes are not hubs."""
    ranked = sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(rel, n) for rel, n in ranked if n > 0][:cap]


UNTYPED = "(untyped)"


@dataclass(frozen=True)
class EntityGroup:
    etype: str  # the `entity:` frontmatter value, or UNTYPED
    count: int  # entity spaces of this type
    exemplars: tuple[str, ...]  # entity names, most-linked first


def group_entities(
    notes: dict[str, NoteFacts],
    spaces_rw: list[tuple[str, bool]],
    degree: dict[str, int],
    config,
    *,
    exemplars: int = 3,
) -> list[EntityGroup]:
    """Entity spaces grouped by their `entity:` type, biggest group first.

    One bucket per type, carrying a count and a few most-linked names as a
    foothold. Individual lookups belong to `brain_search`, which resolves
    aliases; this is orientation only, so the output size depends on the
    number of TYPES, never the number of entities.
    """
    from brain.resolver import space_of_path

    prefix = f"{config.entities}/"

    # Bucket notes by space once — the alternative (rescanning every note per
    # entity space) is O(spaces x notes), and a mature vault has hundreds of
    # each.
    by_space: dict[str, list[str]] = {}
    for rel in notes:
        space = space_of_path(rel)
        if space is not None:
            by_space.setdefault(space, []).append(rel)

    buckets: dict[str, list[tuple[int, str]]] = {}
    for space, _writable in spaces_rw:
        if not space.startswith(prefix):
            continue
        name = space[len(prefix):]
        etype = ""
        best = 0
        for rel in by_space.get(space, ()):
            if not etype and notes[rel].entity:
                etype = notes[rel].entity
            best = max(best, degree.get(rel, 0))
        buckets.setdefault(etype or UNTYPED, []).append((best, name))

    groups = []
    for etype, items in buckets.items():
        items.sort(key=lambda pair: (-pair[0], pair[1]))
        groups.append(EntityGroup(
            etype=etype,
            count=len(items),
            exemplars=tuple(name for _degree, name in items[:exemplars]),
        ))
    groups.sort(key=lambda g: (-g.count, g.etype))
    return groups


@dataclass(frozen=True)
class Pending:
    inbox: int
    needs_routing: int | None  # None => the note does not exist


def collect_pending(building: Path, person) -> Pending:
    """Inbox depth and Needs-Routing size, from this vault's own tree."""
    base = building / "People" / person.id

    inbox_dir = base / "Inbox"
    inbox = 0
    if inbox_dir.is_dir():
        # Mirrors stats._count_files: symlinks are never counted (the
        # compiler never materializes them, so one here is not our content).
        inbox = sum(
            1 for f in inbox_dir.rglob("*")
            if f.is_file() and not f.is_symlink()
        )

    needs: int | None = None
    routing = base / "Needs-Routing.md"
    if routing.is_file() and not routing.is_symlink():
        _meta, body = split_frontmatter(
            routing.read_text(encoding="utf-8", errors="replace"))
        needs = sum(1 for line in body.splitlines() if line.strip())

    return Pending(inbox=inbox, needs_routing=needs)


MAP_NAME = "Map.md"

# Budget matches contextgen.SPACE_LIMIT. Unlike contextgen this is NEVER
# enforced by raising — a data-driven file must not fail a compile. The caps
# and truncation below bound the document by construction; MAP_LIMIT is a
# test assertion over them, not a runtime behavior.
MAP_LIMIT = 8_000
SPACE_CAP = 20
TYPE_CAP = 12
EXEMPLARS = 3
HUB_CAP = 10
FIELD_LEN = 48

_INTRO = (
    "Generated state, refreshed on every compile — edits here are discarded.\n"
    "Protocol and routing rules are in `AGENTS.md`.\n\n"
    "This is an orientation summary, not an index: it says what kinds of\n"
    "things are here and how much. To find a specific note or entity, search\n"
    "(`brain_search`) — it resolves aliases and has no size limit.\n\n"
)


def _trunc(value: str, limit: int = FIELD_LEN) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def render_map(
    person,
    spaces_rw: list[tuple[str, bool]],
    notes_total: int,
    space_notes: dict[str, int],
    groups: list[EntityGroup],
    hubs: list[tuple[str, int]],
    pending: Pending,
    config,
) -> str:
    prefix = f"{config.entities}/"
    plain = [(s, w) for s, w in spaces_rw if not s.startswith(prefix)]
    entities_total = sum(g.count for g in groups)

    out: list[str] = [
        "---\ngenerated: true\n---\n",
        f"# Map — vault of {_trunc(person.name)} ({_trunc(person.id)})\n\n",
        _INTRO,
        f"**{notes_total} notes · {len(plain)} spaces · "
        f"{entities_total} entities**\n\n",
    ]

    if plain:
        out.append("## Spaces\n\n| space | notes | access |\n| --- | --- | --- |\n")
        for space, writable in plain[:SPACE_CAP]:
            access = "writable" if writable else "read-only"
            out.append(
                f"| `{_trunc(space)}` | {space_notes.get(space, 0)} | {access} |\n")
        if len(plain) > SPACE_CAP:
            out.append(f"\n…and {len(plain) - SPACE_CAP} more spaces\n")
        out.append("\n")

    if groups:
        out.append("## Entities\n\n")
        for group in groups[:TYPE_CAP]:
            line = f"- **{_trunc(group.etype)}** ({group.count}) — `{prefix}`"
            if group.exemplars:
                names = ", ".join(f"[[{_trunc(n)}]]" for n in group.exemplars)
                line += f", most linked: {names}"
            out.append(line + "\n")
        if len(groups) > TYPE_CAP:
            # Degrade at the TYPE level: "3 more types" tells the agent a whole
            # category exists that it cannot see, which it can act on. A count
            # of hidden names would not.
            out.append(f"…and {len(groups) - TYPE_CAP} more types\n")
        out.append("\n")

    if hubs:
        out.append("## Hubs\n\nMost-connected notes in this vault:\n\n")
        for i, (rel, count) in enumerate(hubs[:HUB_CAP], 1):
            stem = rel.rsplit("/", 1)[-1].removesuffix(".md")
            out.append(f"{i}. [[{_trunc(stem)}]] — {count} link(s) "
                       f"(`{_trunc(rel)}`)\n")
        out.append("\n")

    out.append("## Pending\n\n")
    out.append(f"- Inbox: {pending.inbox} item(s) — "
               f"`People/{person.id}/Inbox/`\n")
    if pending.needs_routing is not None:
        out.append(f"- `People/{person.id}/Needs-Routing.md`: "
                   f"{pending.needs_routing} line(s)\n")
    out.append(f"- Promotion and share status: "
               f"`People/{person.id}/Shares.md`\n")
    return "".join(out)


def scan_vault(building: Path, rels: list[str]) -> dict[str, NoteFacts]:
    """Read every shipped .md. The map's own pass — deliberately NOT fused
    into the compiler's link-stubbing loop.

    Fusing would save one re-read of the read-only files: measured at 2.3 ms
    on a 1,200-note vault, 1.1% of the copy the compiler already does. Not
    worth a diff to the security-relevant loop, which stays untouched.

    Runs after stubbing, so it reads what actually shipped. Lenient decode
    and no writes: these bytes are never written back, so an undecodable
    file degrades one map entry instead of failing a compile.

    `rels` MUST be the compiler's `compiled` list, never a glob of the built
    tree. Generated files are already on disk by the time this runs, and
    AGENTS.md contains the literal text `[[wikilinks]]` while Shares.md links
    to promotion targets — globbing would feed both into the hub graph and
    invent edges no author wrote.
    """
    notes: dict[str, NoteFacts] = {}
    for rel in rels:
        if not rel.endswith(".md"):
            continue
        try:
            text = (building / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        notes[rel] = scan_note(text)
    return notes


def generate_map(
    building: Path,
    person,
    spaces_rw: list[tuple[str, bool]],
    compiled: list[str],
    config,
) -> str:
    """The one call the compiler makes. Reads only the building tree."""
    from brain.resolver import space_of_path

    notes = scan_vault(building, compiled)
    space_notes: dict[str, int] = {}
    for rel in notes:
        space = space_of_path(rel)
        if space is not None:
            space_notes[space] = space_notes.get(space, 0) + 1

    degree = link_degree(notes)
    return render_map(
        person,
        spaces_rw,
        len(notes),
        space_notes,
        group_entities(notes, spaces_rw, degree, config, exemplars=EXEMPLARS),
        rank_hubs(degree, HUB_CAP),
        collect_pending(building, person),
        config,
    )
