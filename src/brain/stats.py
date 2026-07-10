"""Read-only stats over a compiled vault (and, later in this module, master).

Everything here observes and never mutates. In particular, index reads go
through a read-only sqlite connection (``file:...?mode=ro``): `IndexStore.open`
would create the database, switch it to WAL, and bump the schema version —
none of which a status command may do. A missing or unreadable index degrades
to file-level stats with a hint, never a crash, mirroring how search degrades
to keyword-only.
"""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from brain.doctor import Finding, run_doctor
from brain.promotions import list_pending
from brain.resolver import (
    _match_rule,
    can_read,
    can_write_path,
    enumerate_spaces,
    space_of_path,
)
from brain.schemas import load_org, load_spaces
from brain.writeback import ManifestError, _load_manifest, diff_vault


@dataclass
class SpaceStat:
    space: str
    notes: int
    chunks: int


@dataclass
class IndexInfo:
    built_at: str
    model: str
    dim: str
    schema_version: int


@dataclass
class LinkedNote:
    rel_path: str
    inbound: int


@dataclass
class CommitInfo:
    sha: str
    date: str
    subject: str


@dataclass
class GraphNode:
    id: int
    rel_path: str
    title: str
    space: str
    degree: int


@dataclass
class GraphEdge:
    source: int  # indexes into GraphData.nodes
    target: int


@dataclass
class GraphData:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool


@dataclass
class VaultStats:
    kind: str  # always "vault"; the dashboard JS dispatches on this
    vault: str
    person: str
    collected_at: str
    notes_total: int
    spaces: list[SpaceStat]
    index: IndexInfo | None  # None => no usable index (degraded mode)
    chunks_total: int
    pending_reindex: list[str]
    inbox_count: int
    open_actions: int
    top_linked: list[LinkedNote]
    unresolved_links: int
    embedded_chunks: int | None  # None => sqlite-vec unavailable, unknown
    embedding_coverage: float | None
    recent_commits: list[CommitInfo]
    graph: GraphData | None
    warnings: list[str] = field(default_factory=list)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def ro_connect(db: Path) -> sqlite3.Connection:
    # URI filenames must be percent-encoded (spaces, '?', '#'); keep '/' and
    # ':' literal so absolute paths survive. Read-only by construction: opening
    # the index read-write would create it, switch it to WAL and bump the schema.
    return sqlite3.connect(f"file:{quote(str(db), safe='/:')}?mode=ro", uri=True)


def _manifest_candidates(manifest: dict) -> dict[str, str]:
    """The person's own notes (rel_path -> sha256) from a compiled manifest —
    real ``.md`` files, excluding compiler-generated ones. This is the set that
    should be indexed, so it drives both space counts and pending-reindex."""
    generated = set(manifest["generated"])
    return {
        rel: sha
        for rel, sha in manifest["compiled"].items()
        if rel.endswith(".md") and rel not in generated
    }


def pending_reindex(vault: Path, conn: sqlite3.Connection) -> list[str]:
    """Files whose indexed sha differs from the compiled manifest, plus indexed
    files no longer in the manifest — i.e. what ``brain index`` would touch.
    `conn` must be an open read-only index connection for `vault`."""
    candidates = _manifest_candidates(_load_manifest(vault))
    indexed = dict(conn.execute("SELECT rel_path, sha256 FROM files"))
    return sorted(
        [rel for rel, sha in candidates.items() if indexed.get(rel) != sha]
        + [rel for rel in indexed if rel not in candidates]
    )


def _git_log(repo: Path, limit: int) -> tuple[list[CommitInfo], str | None]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "log", "--date=short",
             "--pretty=%h%x09%ad%x09%s", "-n", str(limit)],
            capture_output=True, text=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return [], "no git history (vault is not a git repository)"
    commits = []
    for line in proc.stdout.splitlines():
        sha, _, rest = line.partition("\t")
        date, _, subject = rest.partition("\t")
        commits.append(CommitInfo(sha=sha, date=date, subject=subject))
    return commits, None


def _count_open_actions(actions_dir: Path) -> int:
    total = 0
    if actions_dir.is_dir():
        for f in sorted(actions_dir.rglob("*.md")):
            if f.is_symlink() or not f.is_file():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            total += sum(1 for ln in text.splitlines() if ln.lstrip().startswith("- [ ]"))
    return total


def _build_graph(conn: sqlite3.Connection, cap: int) -> GraphData:
    rows = conn.execute("SELECT rel_path, space FROM files ORDER BY rel_path").fetchall()
    pairs = conn.execute(
        "SELECT l.src_rel_path, l.target_rel_path FROM links l "
        "JOIN files f ON f.rel_path = l.target_rel_path "
        "WHERE l.src_rel_path != l.target_rel_path"
    ).fetchall()

    degree: dict[str, int] = {rel: 0 for rel, _ in rows}
    for src, tgt in pairs:
        degree[src] = degree.get(src, 0) + 1
        degree[tgt] = degree.get(tgt, 0) + 1

    truncated = len(rows) > cap
    if truncated:
        keep = sorted(rows, key=lambda r: (-degree[r[0]], r[0]))[:cap]
    else:
        keep = rows
    ids = {rel: i for i, (rel, _) in enumerate(keep)}

    nodes = [
        GraphNode(id=i, rel_path=rel, title=Path(rel).stem,
                  space=space, degree=degree[rel])
        for (rel, space), i in zip(keep, range(len(keep)))
    ]
    edges = [
        GraphEdge(source=ids[src], target=ids[tgt])
        for src, tgt in pairs
        if src in ids and tgt in ids
    ]
    return GraphData(nodes=nodes, edges=edges, truncated=truncated)


def collect_vault_stats(
    vault: Path,
    *,
    include_graph: bool = False,
    git_limit: int = 20,
    graph_cap: int = 300,
) -> VaultStats:
    vault = Path(vault)
    manifest = _load_manifest(vault)  # raises ManifestError if uncompiled
    person = manifest.get("person", "")
    candidates = _manifest_candidates(manifest)

    warnings: list[str] = []
    notes_by_space: dict[str, int] = {}
    for rel in candidates:
        space = space_of_path(rel) or "(outside spaces)"
        notes_by_space[space] = notes_by_space.get(space, 0) + 1

    index_info: IndexInfo | None = None
    chunks_total = 0
    chunks_by_space: dict[str, int] = {}
    pending = sorted(candidates)  # until an index proves otherwise
    top_linked: list[LinkedNote] = []
    unresolved = 0
    embedded: int | None = None
    coverage: float | None = None
    graph: GraphData | None = None

    db = vault / ".brain" / "index.db"
    conn: sqlite3.Connection | None = None
    if db.is_file():
        try:
            conn = ro_connect(db)
            meta = dict(conn.execute("SELECT key, value FROM index_meta"))
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            index_info = IndexInfo(
                built_at=meta.get("built_at", ""),
                model=meta.get("model", ""),
                dim=meta.get("dim", ""),
                schema_version=version,
            )
            pending = pending_reindex(vault, conn)
            chunks_by_space = dict(conn.execute(
                "SELECT space, count(*) FROM chunks GROUP BY space"))
            chunks_total = sum(chunks_by_space.values())
            top_linked = [
                LinkedNote(rel_path=rel, inbound=n)
                for rel, n in conn.execute(
                    "SELECT l.target_rel_path, count(*) AS n FROM links l "
                    "JOIN files f ON f.rel_path = l.target_rel_path "
                    "GROUP BY l.target_rel_path ORDER BY n DESC, l.target_rel_path "
                    "LIMIT 10")
            ]
            unresolved = conn.execute(
                "SELECT count(*) FROM links WHERE resolved = 0").fetchone()[0]

            has_vec = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'chunks_vec'").fetchone()
            if not has_vec:
                embedded = 0  # never embedded (keyword-only build)
            else:
                try:
                    import sqlite_vec

                    conn.enable_load_extension(True)
                    sqlite_vec.load(conn)
                    conn.enable_load_extension(False)
                    embedded = conn.execute(
                        "SELECT count(*) FROM chunks_vec").fetchone()[0]
                except Exception:
                    embedded = None
                    warnings.append("sqlite-vec unavailable — embedding coverage unknown")
            if embedded is not None and chunks_total:
                coverage = embedded / chunks_total

            if include_graph:
                graph = _build_graph(conn, graph_cap)
        except sqlite3.Error as e:
            index_info = None
            warnings.append(f"index at {db} is unreadable ({e})")
        finally:
            if conn is not None:
                conn.close()
    else:
        warnings.append(f"no index at {db} — run: brain index --vault {vault}")

    spaces = [
        SpaceStat(space=s, notes=n, chunks=chunks_by_space.get(s, 0))
        for s, n in sorted(notes_by_space.items())
    ]

    commits, git_warning = _git_log(vault, git_limit)
    if git_warning:
        warnings.append(git_warning)

    people_dir = f"People/{person}" if person else None
    inbox = _count_files(vault / people_dir / "Inbox") if people_dir else 0
    actions = _count_open_actions(vault / people_dir / "Actions") if people_dir else 0

    return VaultStats(
        kind="vault",
        vault=str(vault),
        person=person,
        collected_at=_utcnow(),
        notes_total=len(candidates),
        spaces=spaces,
        index=index_info,
        chunks_total=chunks_total,
        pending_reindex=pending,
        inbox_count=inbox,
        open_actions=actions,
        top_linked=top_linked,
        unresolved_links=unresolved,
        embedded_chunks=embedded,
        embedding_coverage=coverage,
        recent_commits=commits,
        graph=graph,
        warnings=warnings,
    )


def _count_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(
        1 for f in directory.rglob("*")
        if f.is_file() and not f.is_symlink()
    )


# ---- master (admin lens) -----------------------------------------------------

@dataclass
class PersonVaultStats:
    person_id: str
    name: str
    compiled: bool
    disk_bytes: int  # content size; .git history is machine-local, excluded
    notes: int
    index_built_at: str | None
    drift: int | None  # edits awaiting writeback; None if manifest unreadable
    drift_error: str | None


@dataclass
class PromotionSummary:
    id: str
    person_id: str
    target_path: str
    created: str


@dataclass
class SpacePermission:
    space: str
    readers: list[str]  # person ids
    writers: list[str]


@dataclass
class MasterStats:
    kind: str  # always "master"
    master: str
    out_root: str | None
    collected_at: str
    people_count: int
    spaces: list[str]
    permissions: list[SpacePermission]
    uncovered_spaces: list[str]
    people: list[PersonVaultStats]
    promotions_pending: list[PromotionSummary]
    findings: list[Finding]
    webhook_sources: int | None = None  # None = no _meta/webhook.yaml (or unreadable)
    warnings: list[str] = field(default_factory=list)


def _vault_disk_bytes(vault: Path) -> int:
    # Content only: dot-entries (.git, .brain index, .obsidian) are
    # machine-local state, outside every space — the same rule diff_vault
    # applies when deciding what counts as a person's content.
    return sum(
        f.stat().st_size
        for f in vault.rglob("*")
        if f.is_file() and not f.is_symlink()
        and not any(part.startswith(".") for part in f.relative_to(vault).parts)
    )


def _read_index_built_at(vault: Path) -> str | None:
    db = vault / ".brain" / "index.db"
    if not db.is_file():
        return None
    try:
        conn = ro_connect(db)
        try:
            row = conn.execute(
                "SELECT value FROM index_meta WHERE key = 'built_at'").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def collect_master_stats(master: Path, out_root: Path | None = None) -> MasterStats:
    master = Path(master)
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")

    warnings: list[str] = []
    spaces = enumerate_spaces(master)
    uncovered = [s for s in spaces if _match_rule(s, rules)[0] is None]
    permissions = [
        SpacePermission(
            space=s,
            readers=[p.id for p in org.people.values() if can_read(s, p, rules)],
            writers=[p.id for p in org.people.values()
                     if can_write_path(f"{s}/x.md", p, rules)],
        )
        for s in spaces
    ]

    people: list[PersonVaultStats] = []
    for person in org.people.values():
        entry = PersonVaultStats(
            person_id=person.id, name=person.name, compiled=False,
            disk_bytes=0, notes=0, index_built_at=None,
            drift=None, drift_error=None,
        )
        if out_root is not None:
            vault = Path(out_root) / person.id
            if vault.is_dir():
                entry.compiled = True
                entry.disk_bytes = _vault_disk_bytes(vault)
                entry.index_built_at = _read_index_built_at(vault)
                try:
                    manifest = _load_manifest(vault)
                    generated = set(manifest["generated"])
                    entry.notes = sum(
                        1 for rel in manifest["compiled"]
                        if rel.endswith(".md") and rel not in generated)
                    entry.drift = len(diff_vault(vault))
                except ManifestError as e:
                    entry.drift_error = str(e)
        people.append(entry)

    promotions = [
        PromotionSummary(id=p.id, person_id=p.person_id,
                         target_path=p.target_path, created=p.created)
        for p in list_pending(master)
    ]

    # A count, not a health check: doctor (included in findings below) already
    # reports a broken webhook.yaml, so an unreadable config just stays None.
    webhook_sources: int | None = None
    webhook_cfg = master / "_meta" / "webhook.yaml"
    if webhook_cfg.is_file():
        from brain.webhook import WebhookConfigError, load_webhook_config

        try:
            webhook_sources = len(load_webhook_config(webhook_cfg))
        except (WebhookConfigError, OSError):
            pass

    return MasterStats(
        kind="master",
        master=str(master),
        out_root=str(out_root) if out_root is not None else None,
        collected_at=_utcnow(),
        people_count=len(org.people),
        spaces=spaces,
        permissions=permissions,
        uncovered_spaces=uncovered,
        people=people,
        promotions_pending=promotions,
        findings=run_doctor(master, Path(out_root) if out_root else None),
        webhook_sources=webhook_sources,
        warnings=warnings,
    )


# ---- plain-text rendering ----------------------------------------------------

def _table(rows: list[tuple[str, ...]], indent: str = "  ") -> list[str]:
    if not rows:
        return []
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [
        indent + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip()
        for row in rows
    ]


def format_vault_status(s: VaultStats) -> str:
    lines = [f"vault {s.vault}" + (f"  (person: {s.person})" if s.person else "")]
    lines.append(
        f"notes: {s.notes_total} across {len(s.spaces)} space(s); "
        f"chunks: {s.chunks_total}")
    if s.index is None:
        lines.append("index: none")
    else:
        cov = ("unknown" if s.embedding_coverage is None
               else f"{s.embedding_coverage:.0%}")
        lines.append(
            f"index: built {s.index.built_at or '?'}"
            + (f", model {s.index.model}" if s.index.model else "")
            + f", embedding coverage {cov}")
        lines.append(f"pending reindex: {len(s.pending_reindex)} file(s)")
    lines.append(f"inbox: {s.inbox_count} item(s); open actions: {s.open_actions}")
    if s.spaces:
        lines.append("spaces:")
        lines += _table([
            (st.space, f"{st.notes} note(s)", f"{st.chunks} chunk(s)")
            for st in s.spaces
        ])
    if s.top_linked:
        lines.append("top linked:")
        lines += _table([
            (ln.rel_path, f"{ln.inbound} inbound") for ln in s.top_linked
        ])
    if s.unresolved_links:
        lines.append(f"unresolved links: {s.unresolved_links}")
    if s.recent_commits:
        lines.append("recent activity:")
        lines += _table([
            (c.sha, c.date, c.subject) for c in s.recent_commits[:5]
        ])
    for w in s.warnings:
        lines.append(f"warning: {w}")
    return "\n".join(lines)


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"  # unreachable


def format_master_status(s: MasterStats) -> str:
    lines = [f"master {s.master}"]
    lines.append(
        f"people: {s.people_count}; spaces: {len(s.spaces)}; "
        f"promotions pending: {len(s.promotions_pending)}")
    if s.webhook_sources is None:
        lines.append("webhook intake: not configured "
                     "(_meta/webhook.yaml.example shows how)")
    else:
        lines.append(f"webhook intake: {s.webhook_sources} source(s)")
    if s.uncovered_spaces:
        lines.append("uncovered spaces: " + ", ".join(s.uncovered_spaces))

    if s.out_root is not None:
        lines.append("vaults:")
        rows = [("person", "size", "notes", "index built", "drift")]
        for p in s.people:
            if not p.compiled:
                rows.append((p.person_id, "-", "-", "-", "not compiled"))
            else:
                drift = p.drift_error if p.drift_error else str(p.drift)
                rows.append((
                    p.person_id,
                    _human_bytes(p.disk_bytes),
                    str(p.notes),
                    p.index_built_at or "never",
                    drift,
                ))
        lines += _table(rows)

    if s.promotions_pending:
        lines.append("promotions pending:")
        lines += _table([
            (p.id, f"from={p.person_id}", p.target_path)
            for p in s.promotions_pending
        ])

    lines.append("permissions:")
    lines += _table(
        [("space", "read", "write")]
        + [
            (perm.space,
             ",".join(perm.readers) or "-",
             ",".join(perm.writers) or "-")
            for perm in s.permissions
        ])

    errors = sum(1 for f in s.findings if f.severity == "error")
    warns = sum(1 for f in s.findings if f.severity == "warn")
    if s.findings:
        lines.append("doctor:")
        lines += _table([
            (f"[{f.severity.upper()}]", f.check, f.message) for f in s.findings
        ])
    lines.append(f"{errors} error(s), {warns} warning(s) from doctor")
    for w in s.warnings:
        lines.append(f"warning: {w}")
    return "\n".join(lines)
