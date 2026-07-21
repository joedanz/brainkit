"""A minimal stdio MCP server exposing the local brain to any MCP client.

Hand-rolled JSON-RPC 2.0 over newline-delimited stdio — five message types
(initialize, initialized, ping, tools/list, tools/call). The official mcp SDK
would pull pydantic/anyio/httpx into a package whose only runtime dep is pyyaml
(+ sqlite-vec); this is five read-only tools and a read loop. `serve()` takes
injectable streams so it is unit-testable in-process.

It runs on the employee's device against their own vault clone, so it inherits
the compiler's boundary: the vault contains only what that person may read.
brain_read adds defense-in-depth path scoping on top (no symlinks, no escaping
the vault, nothing outside a space).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "brainkit", "version": "0.1.0"}

_TOOLS = [
    {
        "name": "brain_search",
        "description": "Hybrid keyword+semantic search over this vault. Returns ranked "
                       "chunks with their file path and heading. Pass `center` (a note's "
                       "relative path, e.g. the note you are working from) to rank results "
                       "near it in the wikilink graph higher.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "natural-language query"},
                "k": {"type": "integer", "description": "max results (default 8)"},
                "center": {"type": "string",
                           "description": "optional note rel path; boosts graph-nearby results"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "brain_read",
        "description": "Read one markdown file from this vault by its relative path.",
        "inputSchema": {
            "type": "object",
            "properties": {"rel_path": {"type": "string"}},
            "required": ["rel_path"],
        },
    },
    {
        "name": "brain_links",
        "description": "Show how one note connects to the rest of the vault: backlinks "
                       "(notes linking to it) and its outgoing wikilinks.",
        "inputSchema": {
            "type": "object",
            "properties": {"rel_path": {"type": "string"}},
            "required": ["rel_path"],
        },
    },
    {
        "name": "brain_recent",
        "description": "List the most recently changed notes in this vault (from git "
                       "history), newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "k": {"type": "integer", "description": "max notes (default 10)"},
            },
        },
    },
    {
        "name": "brain_facts",
        "description": "Query time-stamped fact lines. Default: facts true today. "
                       "Filter by entity (a note's title, alias, or rel path), entity "
                       "type, or date. as_of asks what was true on a date; believed_on "
                       "asks what the vault said on a date (from git history).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "entity title, alias, or rel path"},
                "type": {"type": "string", "description": "entity type, e.g. client"},
                "as_of": {"type": "string", "description": "YYYY-MM or YYYY-MM-DD"},
                "believed_on": {"type": "string", "description": "YYYY-MM or YYYY-MM-DD"},
                "ended": {"type": "boolean", "description": "include closed facts"},
            },
        },
    },
]


def _result(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text_result(mid, text: str, is_error: bool = False) -> dict:
    return _result(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})


def _tool_search(vault: Path, args: dict, provider) -> tuple[str, bool]:
    from brain.search import search_index

    query = args.get("query", "")
    k = int(args.get("k", 8))
    center = args.get("center") or None
    report = search_index(vault, query, k=k, provider=provider, center=center)
    if not report.hits:
        return f"No results for {query!r} ({report.mode}).", False
    lines = [f"{len(report.hits)} result(s) [{report.mode}]:"]
    for i, h in enumerate(report.hits, 1):
        loc = h.rel_path + (f" — {h.heading_path}" if h.heading_path else "")
        lines.append(f"{i}. {loc}\n   {h.snippet}")
    lines.extend(f"warning: {w}" for w in report.warnings)
    return "\n".join(lines), False


def _open_index(vault: Path):
    from brain.store import IndexStore

    db = vault / ".brain" / "index.db"
    if not db.is_file():
        return None
    return IndexStore.open_readonly(db, want_vectors=False)


def _tool_links(vault: Path, args: dict) -> tuple[str, bool]:
    rel = args.get("rel_path", "")
    store = _open_index(vault)
    if store is None:
        return f"no index at {vault / '.brain' / 'index.db'} — run: brain index", True
    try:
        if not store.has_file(rel):
            return f"not in index: {rel}", True
        backlinks = store.links_to(rel)
        outgoing = store.links_from(rel)
    finally:
        store.close()
    lines = [f"{rel}", f"Backlinks ({len(backlinks)}):"]
    lines.extend(f"- {src}" for src in backlinks)
    lines.append(f"Outgoing links ({len(outgoing)}):")
    lines.extend(f"- {tgt}" + ("" if ok else " (unresolved)") for tgt, ok in outgoing)
    return "\n".join(lines), False


def _tool_facts(vault: Path, args: dict) -> tuple[str, bool]:
    from brain.facts import query_facts, query_facts_at

    kwargs = dict(entity=args.get("entity") or None,
                  etype=args.get("type") or None,
                  include_ended=bool(args.get("ended")))
    if args.get("believed_on"):
        hits, warnings = query_facts_at(vault, args["believed_on"], **kwargs)
    else:
        hits, warnings = query_facts(vault, as_of=args.get("as_of") or None, **kwargs)
    if not hits:
        return "\n".join(["no facts"] + [f"warning: {w}" for w in warnings]), False
    lines = [f"{len(hits)} fact(s):"]
    for h in hits:
        span = h.from_date + (f" → {h.until_date}" if h.until_date else " →")
        lines.append(f"- {h.statement}  ({span})  [{h.rel_path}:{h.line}]")
    lines.extend(f"warning: {w}" for w in warnings)
    return "\n".join(lines), False


def _tool_recent(vault: Path, args: dict) -> tuple[str, bool]:
    import subprocess

    k = int(args.get("k", 10))
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), "log", "--date=short",
             "--pretty=%x01%ad", "--name-only", "-n", "200", "--", "*.md"],
            capture_output=True, text=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "no git history (vault is not a git repository)", False
    seen: dict[str, str] = {}  # rel_path -> date of newest commit touching it
    date = ""
    for line in proc.stdout.splitlines():
        if line.startswith("\x01"):
            date = line[1:]
        elif line.endswith(".md") and line not in seen and (vault / line).is_file():
            seen[line] = date
            if len(seen) >= k:
                break
    if not seen:
        return "no notes in git history yet", False
    lines = [f"{len(seen)} recently changed note(s):"]
    lines.extend(f"- {rel}  ({d})" for rel, d in seen.items())
    return "\n".join(lines), False


def _tool_read(vault: Path, args: dict) -> tuple[str, bool]:
    from brain.notes import NoteAccessError, read_note

    rel = args.get("rel_path", "")
    try:
        return read_note(vault, rel), False
    except NoteAccessError as e:
        return f"refused: {e}", True


def _handle(vault: Path, provider, msg: dict):
    """Return a response dict, or None for notifications (no id)."""
    method = msg.get("method")
    mid = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        return _result(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        return _result(mid, {"tools": _TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "brain_search":
            text, is_err = _tool_search(vault, args, provider)
        elif name == "brain_read":
            text, is_err = _tool_read(vault, args)
        elif name == "brain_links":
            text, is_err = _tool_links(vault, args)
        elif name == "brain_recent":
            text, is_err = _tool_recent(vault, args)
        elif name == "brain_facts":
            text, is_err = _tool_facts(vault, args)
        else:
            return _error(mid, -32602, f"unknown tool: {name}")
        return _text_result(mid, text, is_err)

    if is_notification:
        return None  # e.g. notifications/initialized — silently accepted
    return _error(mid, -32601, f"method not found: {method}")


def serve(vault: Path, stdin=None, stdout=None) -> None:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    vault = Path(vault)

    from brain.embeddings import provider_from_config
    provider = provider_from_config()  # resolved once; None → keyword-only

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        resp = _handle(vault, provider, msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
