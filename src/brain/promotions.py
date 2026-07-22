"""Human-gated promotion queue: the only path from private to shared spaces."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path
from brain.schemas import load_org


class PromotionError(ValueError):
    """Invalid promotion target or unknown promotion id."""


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def _commit(master: Path, rel_paths: list[str], message: str, name: str, email: str) -> bool:
    """Commit exactly ``rel_paths`` (adds and deletions) under the given identity.

    Every queue decision must be on the record like ingest and writeback are.
    Staging and committing are pathspec-scoped so a dirty master (e.g. an edit
    awaiting the next writeback) is never swept into a promotion commit. A
    master without ``.git`` (scratch dirs, tests) skips silently — ``brain
    init`` always creates the repo, so production masters are never in that
    state. A real git failure is loud: the files are already moved on disk,
    and the operator should commit by hand rather than lose the audit trail.
    """
    if not (master / ".git").exists():
        return False
    try:
        # An untracked file that was already unlinked (e.g. a swept draft that
        # never made it into a commit) matches no pathspec and would make
        # `git add` fatal — there is nothing to record for it, so drop it.
        tracked = set(_git(master, "ls-files", "--", *rel_paths).stdout.splitlines())
        rel_paths = [p for p in rel_paths if p in tracked or (master / p).exists()]
        if not rel_paths:
            return False
        _git(master, "add", "-A", "--", *rel_paths)
        if not _git(master, "status", "--porcelain", "--", *rel_paths).stdout.strip():
            return False
        _git(
            master,
            "-c", f"user.name={name}",
            "-c", f"user.email={email}",
            "commit", "-m", message, "--", *rel_paths,
        )
        return True
    except subprocess.CalledProcessError as e:
        raise PromotionError(f"git commit failed: {e.stderr.strip()}") from e


@dataclass
class Promotion:
    id: str
    person_id: str
    target_path: str
    source: str
    created: str
    body: str
    mode: str = "create"
    base_hash: str = ""


def _pending_dir(master: Path) -> Path:
    return master / "_meta/promotions/pending"


def _resolved_ids(master: Path) -> set[str]:
    """Promotion ids already decided — approved or rejected. Sweep must never
    re-queue these: their drafts get written back into a person's space on the
    next cycle, and without this guard an approved item would resurface in the
    queue (and a rejected one would come back for reconsideration) every cycle."""
    ids: set[str] = set()
    for state in ("approved", "rejected"):
        d = master / "_meta/promotions" / state
        if d.is_dir():
            ids.update(f.stem for f in d.glob("*.md"))
    return ids


def _validate_target(target_path: str) -> None:
    space = space_of_path(target_path)
    if space is None:
        raise PromotionError(f"target {target_path!r} is not inside any space")
    if space.startswith("People/"):
        raise PromotionError("promotions must target a shared space, not People/")
    if len(PurePosixPath(target_path).parts) <= len(space.split("/")):
        raise PromotionError(f"target {target_path!r} names a space root, not a file in it")


_MODES = ("create", "append", "patch")


def _validate_mode(mode: str) -> None:
    if mode not in _MODES:
        raise PromotionError(
            f"unknown promotion mode {mode!r} — expected one of: {', '.join(_MODES)}"
        )


def draft_promotion(
    master: Path,
    person_id: str,
    target_path: str,
    source: str,
    body: str,
    promo_id: str,
    created: str,
    mode: str = "create",
    base_hash: str = "",
) -> Path:
    _validate_target(target_path)
    _validate_mode(mode)
    if "\n" in base_hash or "\r" in base_hash:
        raise PromotionError("base-hash must be a single line")
    dest = _pending_dir(master) / f"{promo_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    hash_line = f"base-hash: {base_hash}\n" if base_hash else ""
    dest.write_text(
        "---\n"
        f"promotion-id: {promo_id}\n"
        f"from: {person_id}\n"
        f"target-path: {target_path}\n"
        f"mode: {mode}\n"
        f"{hash_line}"
        f"source: {source}\n"
        f"created: {created}\n"
        "---\n"
        f"{body}"
    )
    return dest


def draft_into_space(
    root: Path,
    person_id: str,
    target_path: str,
    source: str,
    body: str,
    created: str,
) -> str:
    """Write a promotion *draft* into the person's own ``Promotions/`` space and
    return its vault-relative path.

    This is the employee-side half of the flow: an agent or the dashboard drops a
    draft here (a writable space they own), and ``sweep`` later moves it into the
    human-gated pending queue. Unlike ``draft_promotion`` (which writes the queue
    entry directly in master), ``root`` may be a compiled slice — write-back
    carries the draft to master, where the next cycle's sweep picks it up. The
    target must be a real shared-space file; single-line fields only, no
    frontmatter injection; symlinked ancestors refused.
    """
    for field, value in (("target-path", target_path), ("source", source)):
        if "\n" in value or "\r" in value:
            raise PromotionError(f"{field} must be a single line")
    _validate_target(target_path)
    if not body.strip():
        raise PromotionError("empty promotion — nothing to share")

    promo_rel = f"People/{person_id}/Promotions"
    ancestor = root
    for part in PurePosixPath(promo_rel).parts:
        ancestor = ancestor / part
        if ancestor.is_symlink():
            raise PromotionError(f"{promo_rel} contains a symlink — refusing to write")

    dir_ = root / promo_rel
    base = _slug(PurePosixPath(target_path).stem) or "promotion"
    fname = f"{created}-{base}.md"
    n = 2
    while (dir_ / fname).exists() or (dir_ / fname).is_symlink():
        fname = f"{created}-{base}-{n}.md"
        n += 1
    rel_path = f"{promo_rel}/{fname}"
    if space_of_path(rel_path) != f"People/{person_id}":
        raise PromotionError(f"refusing to write outside {promo_rel}")

    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "---\n"
        f"target-path: {target_path}\n"
        f"source: {source}\n"
        f"created: {created}\n"
        "---\n"
        f"{body}"
    )
    return rel_path


def _parse(path: Path) -> Promotion:
    meta, body = split_frontmatter(path.read_text())
    mode = meta.get("mode", "create")
    _validate_mode(mode)
    return Promotion(
        id=meta["promotion-id"],
        person_id=meta["from"],
        target_path=meta["target-path"],
        source=meta["source"],
        created=meta["created"],
        body=body,
        mode=mode,
        base_hash=str(meta.get("base-hash", "")),
    )


def list_pending(master: Path) -> list[Promotion]:
    d = _pending_dir(master)
    if not d.exists():
        return []
    pending: list[Promotion] = []
    for p in sorted(d.glob("*.md")):
        try:
            pending.append(_parse(p))
        except (KeyError, ValueError):
            continue  # malformed file stays on disk for manual inspection
    return pending


def _find_pending(master: Path, promo_id: str) -> Path:
    p = _pending_dir(master) / f"{promo_id}.md"
    if not p.exists():
        raise PromotionError(f"no pending promotion {promo_id!r}")
    return p


def approve(master: Path, promo_id: str, approver: str, date: str) -> Path:
    # Attribution must be real: approved-by is machine-resolved against the
    # org roster, same as promoted-by.
    if not approver.strip():
        raise PromotionError("an approver is required")
    people = load_org(master / "_meta/org.yaml").people
    if approver not in people:
        raise PromotionError(f"unknown approver {approver!r} — not a person in the org")
    pending = _find_pending(master, promo_id)
    promo = _parse(pending)
    # Pending files sit on disk between draft and approve; re-validate so a
    # hand-edited target can't escape the master root.
    _validate_target(promo.target_path)
    target = master / promo.target_path
    # Promotions only ever add knowledge. An existing target means approval
    # would replace a shared note wholesale — including curated files like
    # Company/Memory.md, whose history is the whole point. Fail closed; the
    # fix is to edit the pending file's target-path to a fresh filename.
    if target.exists() or target.is_symlink():
        raise PromotionError(
            f"target {promo.target_path!r} already exists — promotions create new "
            "files, never overwrite; edit the pending file's target-path and retry"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        f"promoted-by: {promo.person_id}\n"
        f"approved-by: {approver}\n"
        f"source: {promo.source}\n"
        f"date: {date}\n"
        "---\n"
        f"{promo.body}"
    )
    archived = master / "_meta/promotions/approved" / pending.name
    archived.parent.mkdir(parents=True, exist_ok=True)
    _, fm, promo_body = pending.read_text().split("---\n", 2)
    archived.write_text(
        f"---\n{fm}approved-on: {date}\napproved-by: {approver}\n---\n{promo_body}"
    )
    pending.unlink()
    # The publish is a commit under the approver's own identity — the moment a
    # note crosses private -> shared is exactly the history entry that matters.
    _commit(
        master,
        [promo.target_path,
         archived.relative_to(master).as_posix(),
         pending.relative_to(master).as_posix()],
        f"promotions: approve {promo_id} -> {promo.target_path}",
        people[approver].name,
        f"{approver}@brain.local",
    )
    return target


def reject(master: Path, promo_id: str, reason: str, date: str) -> Path:
    pending = _find_pending(master, promo_id)
    _, fm, body = pending.read_text().split("---\n", 2)
    rejected = master / "_meta/promotions/rejected" / pending.name
    rejected.parent.mkdir(parents=True, exist_ok=True)
    rejected.write_text(
        f"---\n{fm}rejected-reason: {reason}\nrejected-on: {date}\n---\n{body}"
    )
    pending.unlink()
    _commit(
        master,
        [rejected.relative_to(master).as_posix(),
         pending.relative_to(master).as_posix()],
        f"promotions: reject {promo_id} ({reason})",
        "Brain Promotions",
        "promotions@brain.local",
    )
    return rejected


def _slug(text: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def sweep(master: Path, today: str) -> list[Path]:
    """Move agent-drafted promotions from People/*/Promotions/ into the queue.

    Personal agents cannot write _meta/, so their drafts land in their own
    writable space; the server sweeps them here. Files without a valid
    target-path are left in place — never guessed at.
    """
    moved: list[Path] = []
    changed: list[str] = []  # rel paths for the single audit commit at the end
    resolved = _resolved_ids(master)
    for f in sorted(master.glob("People/*/Promotions/*.md")):
        if f.is_symlink():
            continue  # never read through links out of the person's space
        rel = f.relative_to(master)
        person_id = rel.parts[1]
        meta, body = split_frontmatter(f.read_text())
        if not meta:
            continue
        target = meta.get("target-path", "")
        promo_id = f"{person_id}-{_slug(f.stem)}"
        if promo_id in resolved:
            f.unlink()  # already approved/rejected: clear the stale draft, don't re-queue
            changed.append(rel.as_posix())
            continue
        if (_pending_dir(master) / f"{promo_id}.md").exists():
            continue
        try:
            draft_promotion(
                master,
                person_id=person_id,
                target_path=target,
                source=meta.get("source", str(rel)),
                body=body,
                promo_id=promo_id,
                created=today,
            )
        except PromotionError:
            continue
        f.unlink()
        pending = _pending_dir(master) / f"{promo_id}.md"
        moved.append(pending)
        changed += [rel.as_posix(), pending.relative_to(master).as_posix()]
    if changed:
        _commit(
            master, changed, f"promotions: sweep {len(moved)} draft(s)",
            "Brain Promotions", "promotions@brain.local",
        )
    return moved


SHARES_NOTE_REL = "People/{person_id}/Shares.md"
_DECIDED_WINDOW_DAYS = 30
_DECIDED_CAP = 20


def generate_shares_note(master: Path, person_id: str, today: str) -> str | None:
    """Render one person's promotion-status note, or ``None`` if empty.

    The only user-visible window into ``_meta/promotions``: everything of
    theirs still pending, plus decisions from the last 30 days (newest
    first, max 20). Compiled into the slice as a *generated* file — the
    queue stays the single source of truth and edits are discarded.
    """
    from datetime import date as _date, timedelta

    base = master / "_meta/promotions"

    def _entries(state: str) -> list[dict]:
        d = base / state
        if not d.is_dir():
            return []
        metas: list[dict] = []
        for f in sorted(d.glob("*.md")):
            try:
                meta, _ = split_frontmatter(f.read_text())
            except (KeyError, ValueError):
                continue  # malformed stays on disk for manual inspection
            if not meta or meta.get("from") != person_id:
                continue
            metas.append(meta)
        return metas

    cutoff = _date.fromisoformat(today) - timedelta(days=_DECIDED_WINDOW_DAYS)

    def _when(meta: dict, key: str) -> _date | None:
        try:
            return _date.fromisoformat(meta.get(key) or meta.get("created", ""))
        except ValueError:
            return None

    decided: list[tuple[_date, str]] = []
    for meta in _entries("approved"):
        d = _when(meta, "approved-on")
        if d is None or d < cutoff:
            continue
        by = meta.get("approved-by", "")
        decided.append((d, f"- ✅ `{meta.get('target-path', '?')}` — approved "
                           f"{d.isoformat()}{f' by {by}' if by else ''}; now live"))
    for meta in _entries("rejected"):
        d = _when(meta, "rejected-on")
        if d is None or d < cutoff:
            continue
        reason = meta.get("rejected-reason", "no reason recorded")
        decided.append((d, f"- ❌ `{meta.get('target-path', '?')}` — rejected "
                           f"{d.isoformat()}: {reason}"))
    decided.sort(key=lambda t: t[0], reverse=True)
    decided = decided[:_DECIDED_CAP]

    pending = _entries("pending")
    if not pending and not decided:
        return None

    lines = [
        "---",
        "generated: true",
        "---",
        "# My Shares",
        "",
        "Status of knowledge you have proposed to share. This file is",
        "regenerated on every compile — edits here are discarded.",
    ]
    if pending:
        lines += ["", "## Awaiting approval", ""]
        lines += [f"- `{m.get('target-path', '?')}` — submitted {m.get('created', '?')}"
                  for m in pending]
    if decided:
        lines += ["", "## Recently decided", ""]
        lines += [line for _, line in decided]
    return "\n".join(lines) + "\n"
