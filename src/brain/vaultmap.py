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
from brain.schemas import Person, VaultConfig

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
UNTYPED = "(untyped)"


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

    Resolution is the STEM HALF of indexer._resolve_links: lowercased stem,
    and on a duplicate stem the lexicographically first path wins (sorted
    iteration + setdefault). Unresolvable targets and self-links contribute
    nothing — the latter matches stats._build_graph's `src != target` filter.

    It is deliberately NOT the whole of `_resolve_links`, and the difference
    is visible: that function also falls back to an alias index, so a link
    written to an entity's alias counts there and not here. Aliases live in
    the search index, which does not exist yet when the compiler runs — so
    hub degree here can read lower than `brain status` reports for the same
    vault. Orientation ranking, not an authoritative graph.
    """
    from brain.compiler import _stem

    # Memoize: targets repeat heavily (a hub is linked from many notes) and
    # _stem builds a PurePosixPath every call. Measured at 5k notes / 50k
    # targets this halves the step, ~32 ms per person. Deliberately a LOCAL
    # cache — _stem is shared with stub_links in the security-relevant
    # stubbing loop and must not grow state.
    stems: dict[str, str] = {}

    def stem(value: str) -> str:
        hit = stems.get(value)
        if hit is None:
            hit = stems[value] = _stem(value)
        return hit

    by_stem: dict[str, str] = {}
    for rel in sorted(notes):
        by_stem.setdefault(stem(rel), rel)

    degree: dict[str, int] = {rel: 0 for rel in notes}
    for src, facts in notes.items():
        for raw in facts.targets:
            target = by_stem.get(stem(raw))
            if target is None or target == src:
                continue
            degree[src] += 1
            degree[target] += 1
    return degree


def rank_hubs(degree: dict[str, int], cap: int) -> list[tuple[str, int]]:
    """Most-connected notes first. Zero-degree notes are not hubs."""
    ranked = sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(rel, n) for rel, n in ranked if n > 0][:cap]


def notes_by_space(notes: dict[str, NoteFacts]) -> dict[str, list[str]]:
    """Bucket note paths by their space. Computed once per vault and shared —
    `space_of_path` parses every path, and at 5k notes x 50 people a second
    pass is a quarter-million redundant parses per compile."""
    from brain.resolver import space_of_path

    buckets: dict[str, list[str]] = {}
    for rel in notes:
        space = space_of_path(rel)
        if space is not None:
            buckets.setdefault(space, []).append(rel)
    return buckets


@dataclass(frozen=True)
class EntityGroup:
    etype: str  # the `entity:` frontmatter value, or UNTYPED
    count: int  # entity spaces of this type
    exemplars: tuple[str, ...]  # entity names, most-linked first


def group_entities(
    notes: dict[str, NoteFacts],
    spaces_rw: list[tuple[str, bool]],
    degree: dict[str, int],
    config: VaultConfig,
    by_space: dict[str, list[str]],
) -> list[EntityGroup]:
    """Entity spaces grouped by their `entity:` type, biggest group first.

    One bucket per type, carrying a count and a few most-linked names as a
    foothold. Individual lookups belong to `brain_search`, which resolves
    aliases; this is orientation only, so the output size depends on the
    number of TYPES, never the number of entities.

    `by_space` is `notes_by_space(notes)`, required so it is computed once per
    vault. Looking spaces up in it (rather than rescanning notes per space) is
    what keeps this O(notes) instead of O(spaces x notes) — a mature vault has
    hundreds of both.
    """
    prefix = f"{config.entities}/"
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
            exemplars=tuple(name for _degree, name in items[:EXEMPLARS]),
        ))
    groups.sort(key=lambda g: (-g.count, g.etype))
    return groups


@dataclass(frozen=True)
class Pending:
    inbox: int
    needs_routing: int | None  # None => the note does not exist


def collect_pending(
    building: Path, person: Person, spaces_rw: list[tuple[str, bool]]
) -> Pending | None:
    """Inbox depth and Needs-Routing size, from this vault's own tree.

    None when the vault has no `People/<pid>` space — the default
    single-admin setup, for one. Every line of the Pending section points
    into that space, so a vault without it would advertise paths it does not
    contain. Absence is decided here, once, rather than collected and then
    discarded by the renderer: `generate_map` skips an rglob it would throw
    away, and "Pending exists ⟺ owner space present" stays one invariant in
    one place.
    """
    if not any(space == f"People/{person.id}" for space, _w in spaces_rw):
        return None

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


_INTRO = (
    "Generated state, refreshed on every compile — edits here are discarded.\n"
    "Protocol and routing rules are in `AGENTS.md`.\n\n"
    "This is an orientation summary, not an index: it says what kinds of\n"
    "things are here and how much. To find a specific note or entity, search\n"
    "(`brain_search`) — it resolves aliases and has no size limit.\n\n"
)


def _trunc(value: str) -> str:
    return value if len(value) <= FIELD_LEN else value[: FIELD_LEN - 1] + "…"


def _count(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def render_map(
    person: Person,
    spaces_rw: list[tuple[str, bool]],
    notes_total: int,
    space_notes: dict[str, int],
    groups: list[EntityGroup],
    hubs: list[tuple[str, int]],
    pending: Pending | None,
    config: VaultConfig,
) -> str:
    prefix = f"{config.entities}/"
    plain = [(s, w) for s, w in spaces_rw if not s.startswith(prefix)]
    entities_total = sum(g.count for g in groups)

    out: list[str] = [
        "---\ngenerated: true\n---\n",
        f"# Map — vault of {_trunc(person.name)} ({_trunc(person.id)})\n\n",
        _INTRO,
        f"**{_count(notes_total, 'note')} · {_count(len(plain), 'space')} · "
        f"{_count(entities_total, 'entity', 'entities')}**\n\n",
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

    if pending is not None:
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
    person: Person,
    spaces_rw: list[tuple[str, bool]],
    compiled: list[str],
    config: VaultConfig,
) -> str:
    """The one call the compiler makes. Reads only the building tree."""
    notes = scan_vault(building, compiled)
    by_space = notes_by_space(notes)
    degree = link_degree(notes)
    return render_map(
        person,
        spaces_rw,
        len(notes),
        {space: len(rels) for space, rels in by_space.items()},
        group_entities(notes, spaces_rw, degree, config, by_space),
        rank_hubs(degree, HUB_CAP),
        collect_pending(building, person, spaces_rw),
        config,
    )
