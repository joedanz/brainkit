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
