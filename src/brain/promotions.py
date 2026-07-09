"""Human-gated promotion queue: the only path from private to shared spaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path


class PromotionError(ValueError):
    """Invalid promotion target or unknown promotion id."""


@dataclass
class Promotion:
    id: str
    person_id: str
    target_path: str
    source: str
    created: str
    body: str


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


def draft_promotion(
    master: Path,
    person_id: str,
    target_path: str,
    source: str,
    body: str,
    promo_id: str,
    created: str,
) -> Path:
    _validate_target(target_path)
    dest = _pending_dir(master) / f"{promo_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "---\n"
        f"promotion-id: {promo_id}\n"
        f"from: {person_id}\n"
        f"target-path: {target_path}\n"
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
    return Promotion(
        id=meta["promotion-id"],
        person_id=meta["from"],
        target_path=meta["target-path"],
        source=meta["source"],
        created=meta["created"],
        body=body,
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
    pending = _find_pending(master, promo_id)
    promo = _parse(pending)
    # Pending files sit on disk between draft and approve; re-validate so a
    # hand-edited target can't escape the master root.
    _validate_target(promo.target_path)
    target = master / promo.target_path
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
    pending.rename(archived)
    return target


def reject(master: Path, promo_id: str, reason: str) -> Path:
    pending = _find_pending(master, promo_id)
    _, fm, body = pending.read_text().split("---\n", 2)
    rejected = master / "_meta/promotions/rejected" / pending.name
    rejected.parent.mkdir(parents=True, exist_ok=True)
    rejected.write_text(f"---\n{fm}rejected-reason: {reason}\n---\n{body}")
    pending.unlink()
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
        moved.append(_pending_dir(master) / f"{promo_id}.md")
    return moved
