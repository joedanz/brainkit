"""Notes captured over remote MCP, waiting for the cycle to file them.

The per-person MCP process must not write into the compiled vault: a raw write
dirties its git tree (the person's agent pushes bounce until the next cycle),
and a capture landing between the cycle's final write-back and the compile's
directory swap would be lost. So it drops a small JSON envelope here instead,
and ``brain cycle --spool-root`` files each one into master through
``ingest_note`` — the same path, write check and single-file commit the webhook
uses. Only the cycle ever touches master.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from brain.errors import BrainError

ENVELOPE_VERSION = 1
MAX_TEXT = 20_000
MAX_TITLE = 120
MAX_PENDING = 200
REJECTED_DIR = ".rejected"
_TMP_DIR = ".tmp"


class SpoolError(BrainError, ValueError):
    """A capture the spool refuses; the message is shown to the person."""


def pending_count(spool: Path) -> int:
    return sum(1 for p in spool.glob("*.json") if p.is_file())


def write_envelope(spool: Path, person: str, *, text: str, title: str,
                   now: datetime) -> str:
    """Queue one capture atomically; return its file name. ``now`` is UTC."""
    if not isinstance(text, str) or not text.strip():
        raise SpoolError("nothing to capture — the text is empty")
    if len(text) > MAX_TEXT:
        raise SpoolError(f"the text is {len(text)} characters; the limit is {MAX_TEXT}")
    title = (title or "").strip()
    if "\n" in title or "\r" in title:
        raise SpoolError("the title must be a single line")
    if len(title) > MAX_TITLE:
        raise SpoolError(f"the title is longer than {MAX_TITLE} characters")
    # JSON allows a lone surrogate ("\\ud800"); no note can hold one, and the
    # cycle must never meet it.
    for value in (text, title):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise SpoolError("the text is not valid UTF-8 — remove unusual characters") from None
    if pending_count(spool) >= MAX_PENDING:
        raise SpoolError("capture queue full — try again after the next sync")

    name = f"{now:%Y%m%dT%H%M%S}Z-{secrets.token_hex(4)}.json"
    envelope = {"version": ENVELOPE_VERSION, "person": person, "title": title,
                "body": text, "source": "mcp", "created": now.date().isoformat()}
    tmp_dir = spool / _TMP_DIR
    tmp_dir.mkdir(mode=0o700, exist_ok=True)
    tmp = tmp_dir / name
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(envelope, f)
    os.replace(tmp, spool / name)   # a reader never sees half a file
    return name


@dataclass
class DrainReport:
    ingested: int = 0
    rejected: int = 0
    warnings: list[str] = field(default_factory=list)


def _reject(path: Path, report: DrainReport, why: str) -> None:
    dest = path.parent / REJECTED_DIR
    dest.mkdir(mode=0o700, exist_ok=True)
    os.replace(path, dest / path.name)
    report.rejected += 1
    report.warnings.append(f"spool: rejected {path.parent.name}/{path.name}: {why}")


def _envelope_problem(env, pid: str) -> str | None:
    if not isinstance(env, dict) or env.get("version") != ENVELOPE_VERSION:
        return "unknown envelope version"
    if env.get("person") != pid:
        return "envelope names a different person"
    if not all(isinstance(env.get(k), str) for k in ("body", "title", "created")):
        return "malformed envelope"
    for key in ("body", "title"):
        try:
            env[key].encode("utf-8")
        except UnicodeEncodeError:
            return "text is not valid UTF-8"
    try:
        # `created` becomes part of a file name in build_inbox_note — only a
        # real ISO date may pass.
        date.fromisoformat(env["created"])
    except ValueError:
        return "bad created date"
    return None


def drain_spools(spool_root: Path, master: Path, org, rules, *, shared: str) -> DrainReport:
    """File every queued capture into master. Runs inside the cycle lock.

    A failure is moved to .rejected/ and reported, never retried: ingest_note
    uses one error class for bad metadata and a failed commit, and retrying a
    half-done ingest could file the note twice."""
    from brain.ingest import IngestError, ingest_note

    report = DrainReport()
    if not spool_root.is_dir():
        report.warnings.append(f"spool: {spool_root} is not a directory — nothing drained")
        return report
    folders = sorted(p for p in spool_root.iterdir()
                     if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))
    for folder in folders:
        person = org.people.get(folder.name)
        if not os.access(folder, os.R_OK | os.W_OK | os.X_OK):
            # glob() on an unreadable directory returns [] without a word, so
            # say it here: the cycle must run as the user that owns the spool.
            report.warnings.append(
                f"spool: cannot read {folder} — captures for {folder.name} are not "
                "being filed (the cycle must run as the user that owns the spool)")
            continue
        envelopes = sorted(p for p in folder.glob("*.json")
                           if p.is_file() and not p.is_symlink())
        for path in envelopes:
            if person is None:
                _reject(path, report, "not a person in org.yaml")
                continue
            try:
                env = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
                _reject(path, report, f"unreadable ({type(e).__name__})")
                continue
            problem = _envelope_problem(env, folder.name)
            if problem:
                _reject(path, report, problem)
                continue
            try:
                ingest_note(master, person, rules, env["body"],
                            title=env["title"] or "Captured from Claude", source="mcp",
                            sender=person.email or person.id, created=env["created"],
                            shared=shared)
            except (IngestError, OSError, ValueError) as e:
                # Not only IngestError: a write failure must cost one capture,
                # never the company's cycle. Nothing retries a half-done ingest
                # (it could file the note twice).
                _reject(path, report, f"{type(e).__name__}: {e}")
                continue
            # A crash between the ingest commit and this unlink files the note
            # again next cycle; the window is a few microseconds.
            path.unlink()
            report.ingested += 1
    return report
