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

from brain.resolver import space_of_path
from brain.writeback import _load_manifest


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


def _ro_connect(db: Path) -> sqlite3.Connection:
    # URI filenames must be percent-encoded (spaces, '?', '#'); keep '/' and
    # ':' literal so absolute paths survive.
    return sqlite3.connect(f"file:{quote(str(db), safe='/:')}?mode=ro", uri=True)


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
    generated = set(manifest["generated"])
    candidates = {
        rel: sha
        for rel, sha in manifest["compiled"].items()
        if rel.endswith(".md") and rel not in generated
    }

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
            conn = _ro_connect(db)
            meta = dict(conn.execute("SELECT key, value FROM index_meta"))
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            index_info = IndexInfo(
                built_at=meta.get("built_at", ""),
                model=meta.get("model", ""),
                dim=meta.get("dim", ""),
                schema_version=version,
            )
            indexed = dict(conn.execute("SELECT rel_path, sha256 FROM files"))
            pending = sorted(
                [rel for rel, sha in candidates.items() if indexed.get(rel) != sha]
                + [rel for rel in indexed if rel not in candidates]
            )
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
