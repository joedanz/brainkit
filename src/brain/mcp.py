"""A minimal stdio MCP server exposing the local brain to any MCP client.

Hand-rolled JSON-RPC 2.0 over newline-delimited stdio — five message types
(initialize, initialized, ping, tools/list, tools/call). The official mcp SDK
would pull pydantic/anyio/httpx into a package whose only runtime dep is pyyaml
(+ sqlite-vec); this is ~two tools and a read loop. `serve()` takes injectable
streams so it is unit-testable in-process.

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
                       "chunks with their file path and heading.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "natural-language query"},
                "k": {"type": "integer", "description": "max results (default 8)"},
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
    report = search_index(vault, query, k=k, provider=provider)
    if not report.hits:
        return f"No results for {query!r} ({report.mode}).", False
    lines = [f"{len(report.hits)} result(s) [{report.mode}]:"]
    for i, h in enumerate(report.hits, 1):
        loc = h.rel_path + (f" — {h.heading_path}" if h.heading_path else "")
        lines.append(f"{i}. {loc}\n   {h.snippet}")
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
