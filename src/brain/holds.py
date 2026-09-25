"""Held edits: what write-back could not apply, kept findable and told to
the person.

A hold never copies content. The vault repo already has it: the record in
master (People/<id>/.held.json) names the vault commit and the paths, and
`brain held show` reads them back from there. The person hears about it
through People/<id>/Inbox/held-edits.md and dismisses it by deleting that
note; write-back then removes the record in the same commit.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from brain.compiler import MANIFEST_NAME
from brain.errors import BrainError
from brain.schemas import Person, SpaceRule
from brain.writeback import (
    HELD_REL,
    NOTICE_REL,
    Held,
    WritebackResult,
    _git,
    apply_writeback,
    commit_paths,
)

CYCLE_NAME = "Brain Cycle"
CYCLE_EMAIL = "cycle@brain.local"

_KIND_WORDS = {"add": "added", "modify": "changed", "delete": "deleted"}


class HoldError(BrainError, ValueError):
    """A hold record exists but cannot be read."""


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def vault_head(vault: Path) -> str | None:
    if not (vault / ".git").exists():
        return None
    try:
        return _git(vault, "rev-parse", "HEAD").stdout.strip() or None
    except subprocess.CalledProcessError:
        return None


def commit_vault_strays(vault: Path) -> bool:
    """Commit uncommitted edits in the vault repo so every held path is
    reachable by SHA. The manifest is excluded: during a cycle it carries
    the busy marker, which must stay uncommitted."""
    if not (vault / ".git").exists():
        return False
    spec = ("--", ".", f":(exclude){MANIFEST_NAME}")
    if not _git(vault, "status", "--porcelain", *spec).stdout.strip():
        return False
    _git(vault, "add", "-A", *spec)
    _git(vault, "-c", f"user.name={CYCLE_NAME}", "-c", f"user.email={CYCLE_EMAIL}",
         "commit", "-q", "-m", "cycle: keep uncommitted vault edits")
    return True


def load_hold(master: Path, person_id: str) -> dict | None:
    path = master / HELD_REL.format(person_id=person_id)
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise HoldError(f"{path}: unreadable hold record ({e})") from e
    if not isinstance(rec, dict) or not isinstance(rec.get("paths"), list):
        raise HoldError(f"{path}: hold record is the wrong shape")
    return rec


def render_notice(held: list[Held]) -> str:
    lines = [
        "# Edits that were held back",
        "",
        "Your last sync changed files in folders you can't write to, so those",
        "changes were not saved to the shared brain. Everything else in that",
        "sync was saved, and your copy of these files was put back to match",
        "the shared brain.",
        "",
        "Held back:",
        "",
        *(f"- {_KIND_WORDS.get(h.kind, h.kind)} `{h.path}`" for h in held),
        "",
        "Nothing is lost: an admin can still see these edits. To keep one, move",
        "the edit into a folder you can write to, or ask an admin to add it.",
        "",
        "Delete this note once you have dealt with it.",
        "",
    ]
    return "\n".join(lines)


def record_hold(master: Path, person_id: str, sha: str | None, held: list[Held],
                *, now: str) -> bool:
    """Write (or replace) the person's one open hold and its notice, and
    commit both. A re-detection of the same hold (same paths, same vault
    commit, notice still there) is a no-op, so a cycle's two write-back
    passes make one commit, not two."""
    record_rel = HELD_REL.format(person_id=person_id)
    notice_rel = NOTICE_REL.format(person_id=person_id)
    paths = [{"kind": h.kind, "path": h.path, "reason": h.reason} for h in held]
    try:
        existing = load_hold(master, person_id)
    except HoldError:
        existing = None  # a corrupt record is replaced by the new one
    if (existing is not None and existing.get("paths") == paths
            and existing.get("sha") == sha and (master / notice_rel).is_file()):
        return False
    record = json.dumps({"sha": sha, "paths": paths, "at": now}, indent=2) + "\n"
    for rel, text in ((record_rel, record), (notice_rel, render_notice(held))):
        target = master / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    commit_paths(master, [record_rel, notice_rel], name=CYCLE_NAME, email=CYCLE_EMAIL,
                 message=f"held: {len(held)} edit(s) from {person_id}")
    return True


def held_content(vault: Path, sha: str | None, entry: dict) -> str:
    """What a held path looked like in the vault, for an admin to copy by
    hand. Never raises: a gone commit or path is said, not thrown."""
    if entry.get("kind") == "delete":
        return "(deleted in the vault)"
    if not sha:
        return "(no vault history was recorded, so the content is not available)"
    if not (vault / ".git").exists():
        return f"(no vault repository at {vault})"
    probe = subprocess.run(["git", "-C", str(vault), "cat-file", "-e", f"{sha}^{{commit}}"],
                           capture_output=True)
    if probe.returncode != 0:
        return (f"(vault commit {sha[:12]} is no longer available — the vault was "
                "re-cloned or purged)")
    shown = subprocess.run(["git", "-C", str(vault), "show", f"{sha}:{entry.get('path', '')}"],
                           capture_output=True)
    if shown.returncode != 0:
        return f"(not found at vault commit {sha[:12]})"
    return shown.stdout.decode("utf-8", errors="replace")


def writeback_person(master: Path, vault: Path, person: Person,
                     rules: tuple[SpaceRule, ...], *, now: str | None = None
                     ) -> WritebackResult:
    """Write-back plus everything that makes a hold findable: commit vault
    strays first so the held bytes have a SHA, then record the hold."""
    commit_vault_strays(vault)
    result = apply_writeback(master, vault, person, rules)
    if result.held:
        record_hold(master, person.id, vault_head(vault), result.held,
                    now=now or utc_now_iso())
    return result
