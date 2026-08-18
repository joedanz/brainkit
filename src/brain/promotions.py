"""Human-gated promotion queue: the only path from private to shared spaces."""

from __future__ import annotations

import difflib
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from brain.errors import BrainError
from brain.frontmatter import split_frontmatter
from brain.resolver import can_read, space_of_path
from brain.schemas import DEFAULT_SHARED, Org, Person, SpaceRule, load_org, master_shared


class PromotionError(BrainError, ValueError):
    """Invalid promotion target or unknown promotion id."""


_ID = re.compile(r"[A-Za-z0-9._-]+")


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


def _validate_target(target_path: str, shared: str = DEFAULT_SHARED) -> None:
    space = space_of_path(target_path, shared)
    if space is None:
        raise PromotionError(f"target {target_path!r} is not inside any space")
    if space.startswith("People/"):
        raise PromotionError("promotions must target a shared space, not People/")
    if len(PurePosixPath(target_path).parts) <= len(space.split("/")):
        raise PromotionError(f"target {target_path!r} names a space root, not a file in it")


def may_approve(person: Person | None, target_path: str,
                shared: str = DEFAULT_SHARED) -> bool:
    """One authority definition for promotion approvals, mirroring
    ``shares.may_decide``. Fail closed: an unknown person approves nothing.

    Admins approve anything. A ``lead`` on team T approves promotions into
    ``Teams/T/`` — the promotions counterpart to a lead deciding a
    ``team:T`` share. Everything else (the shared space, entity spaces) is
    admin-only: a promotion publishes into a space other people read, and
    unlike a share request the recipient is implicit in ``target_path``, so
    there is no consent step to fall back on.
    """
    if person is None:
        return False
    if person.is_admin:
        return True
    space = space_of_path(target_path, shared)
    if space is None or not space.startswith("Teams/"):
        return False
    team = space.split("/", 1)[1]
    return "lead" in person.roles and team in person.teams


_MODES = ("create", "append", "patch")


def _validate_mode(mode: str) -> None:
    if mode not in _MODES:
        raise PromotionError(
            f"unknown promotion mode {mode!r} — expected one of: {', '.join(_MODES)}"
        )


def _require_existing(target: Path, rel: str) -> None:
    """append/patch update a page in place — a symlink would carry the write
    outside master, and a missing page means the agent meant mode: create."""
    if target.is_symlink():
        raise PromotionError(f"target {rel!r} is a symlink — refusing to modify")
    if not target.is_file():
        raise PromotionError(
            f"target {rel!r} does not exist — append/patch update an existing "
            "page; use mode: create for new files"
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
    shared: str | None = None,
) -> Path:
    _validate_target(target_path, master_shared(master, shared))
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
    shared: str = DEFAULT_SHARED,
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
    _validate_target(target_path, shared)
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
    if space_of_path(rel_path, shared) != f"People/{person_id}":
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


def patch_diff(master: Path, promo: Promotion,
               shared: str | None = None) -> str | None:
    """Live unified diff of a patch promotion against its current target.

    Computed at review time on purpose: the hash guards the write, the diff
    shows the reviewer exactly what changes at the moment of decision. None
    for non-patch modes and for a missing target (approval fails closed on
    that anyway)."""
    try:
        _validate_target(promo.target_path, master_shared(master, shared))
    except PromotionError:
        return None
    if promo.mode != "patch":
        return None
    target = master / promo.target_path
    if target.is_symlink() or not target.is_file():
        return None
    return "".join(difflib.unified_diff(
        target.read_text().splitlines(keepends=True),
        promo.body.splitlines(keepends=True),
        fromfile=f"{promo.target_path} (current)",
        tofile=f"{promo.target_path} (proposed)",
    ))


def _find_pending(master: Path, promo_id: str) -> Path:
    # A path/traversal-shaped id (e.g. "../../evil") must fail exactly like an
    # unknown one — the charset check happens before any filesystem lookup,
    # and the error message never reveals which reason applied.
    if not _ID.fullmatch(promo_id):
        raise PromotionError(f"no pending promotion {promo_id!r}")
    p = _pending_dir(master) / f"{promo_id}.md"
    if not p.exists():
        raise PromotionError(f"no pending promotion {promo_id!r}")
    return p


def approve(master: Path, promo_id: str, approver: str, date: str,
            shared: str | None = None, via: str = "") -> Path:
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
    shared = master_shared(master, shared)
    _validate_target(promo.target_path, shared)
    # The shared-space carve-out, re-asserted where the write happens.
    # sweep_promotion_approvals checks it against its own parse of the pending
    # file, but the dashboard server writes the same master concurrently: a
    # target-path that flips Teams/ -> Company/ in between would sail past
    # may_approve for an *admin* and publish company-wide from a delegated
    # in-vault note. A delegated decision never publishes into the shared
    # space, whoever made it.
    if via == "delegated" and space_of_path(promo.target_path, shared) == shared:
        raise PromotionError(
            f"promotions into {shared}/ are decided by an admin at the dashboard, "
            "not by an in-vault decision"
        )
    if not may_approve(people[approver], promo.target_path, shared):
        raise PromotionError(
            f"{approver!r} may not approve this promotion — the approver must "
            "be role:admin, or a lead of the team whose space it targets"
        )
    target = master / promo.target_path
    if promo.mode == "create":
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
    elif promo.mode == "append":
        _require_existing(target, promo.target_path)
        promoter = people.get(promo.person_id)
        promoter_name = promoter.name if promoter else promo.person_id
        current = target.read_text()
        target.write_text(
            current.rstrip("\n")
            + "\n\n---\n\n"
            + promo.body.strip()
            + f"\n\n*Promoted by {promoter_name}, approved by "
              f"{people[approver].name}, {date} — source: {promo.source}*\n"
        )
    else:  # patch — _parse already rejected anything outside _MODES
        _require_existing(target, promo.target_path)
        if not promo.base_hash:
            raise PromotionError(
                f"patch promotion {promo_id!r} has no base-hash — it was queued "
                "by hand; re-run sweep or redraft"
            )
        if hashlib.sha256(target.read_bytes()).hexdigest() != promo.base_hash:
            raise PromotionError(
                f"target {promo.target_path!r} changed since this was queued — "
                "redraft against the current page"
            )
        target.write_text(promo.body)
    archived = master / "_meta/promotions/approved" / pending.name
    archived.parent.mkdir(parents=True, exist_ok=True)
    _, fm, promo_body = pending.read_text().split("---\n", 2)
    via_line = f"via: {via}\n" if via else ""
    archived.write_text(
        f"---\n{fm}approved-on: {date}\napproved-by: {approver}\n{via_line}---\n{promo_body}"
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


def reject(master: Path, promo_id: str, reason: str, date: str,
           approver: str = "", via: str = "") -> Path:
    """Move a pending promotion to rejected/ with the reason.

    ``approver`` is optional so the CLI's existing shape (no identity — the
    commit is ``Brain Promotions``) still works; the in-vault seam passes it
    so a delegated rejection records who decided and commits as them.
    ``via`` is stamped verbatim when non-empty (``delegated``)."""
    pending = _find_pending(master, promo_id)
    _, fm, body = pending.read_text().split("---\n", 2)
    rejected = master / "_meta/promotions/rejected" / pending.name
    rejected.parent.mkdir(parents=True, exist_ok=True)
    by_line = f"rejected-by: {approver}\n" if approver else ""
    via_line = f"via: {via}\n" if via else ""
    rejected.write_text(
        f"---\n{fm}rejected-reason: {reason}\nrejected-on: {date}\n"
        f"{by_line}{via_line}---\n{body}"
    )
    pending.unlink()
    if approver:
        people = load_org(master / "_meta/org.yaml").people
        name = people[approver].name if approver in people else approver
        email = f"{approver}@brain.local"
    else:
        name, email = "Brain Promotions", "promotions@brain.local"
    _commit(
        master,
        [rejected.relative_to(master).as_posix(),
         pending.relative_to(master).as_posix()],
        f"promotions: reject {promo_id} ({reason})",
        name, email,
    )
    return rejected


def _slug(text: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def write_inbox_note(master: Path, person_id: str, prefix: str, slug: str,
                     text: str, today: str) -> str:
    """Write one timestamped note into a person's Inbox; return its rel path.

    Every server-side queue that has to tell a person *why* something did not
    happen lands the explanation the same way. ``prefix`` is the only thing
    that differs between them (``promotion``, ``share``, ``client-taken``), so
    it is the only thing they pass — shares.py and clients.py wrap this with
    their own prefix rather than restating the write.
    """
    rel = f"People/{person_id}/Inbox/{prefix}-{slug}.md"
    dest = master / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"---\ncreated: {today}\n---\n{text}\n")
    return rel


def sweep(master: Path, today: str, shared: str | None = None) -> list[Path]:
    """Move agent-drafted promotions from People/*/Promotions/ into the queue.

    Personal agents cannot write _meta/, so their drafts land in their own
    writable space; the server sweeps them here. Files without a valid
    target-path are left in place — never guessed at.
    """
    shared = master_shared(master, shared)
    moved: list[Path] = []
    changed: list[str] = []  # rel paths for the single audit commit at the end
    resolved = _resolved_ids(master)
    for f in sorted(master.glob("People/*/Promotions/*.md")):
        if f.is_symlink():
            continue  # never read through links out of the person's space
        rel = f.relative_to(master)
        person_id = rel.parts[1]
        try:
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
                _validate_target(target, shared)
                mode = meta.get("mode", "create")
                _validate_mode(mode)
                base_hash = ""
                if mode == "patch":
                    target_file = master / target
                    if target_file.is_symlink() or not target_file.is_file():
                        continue  # left in place; doctor flags it
                    base_hash = hashlib.sha256(target_file.read_bytes()).hexdigest()
                draft_promotion(
                    master,
                    person_id=person_id,
                    target_path=target,
                    source=meta.get("source", str(rel)),
                    body=body,
                    promo_id=promo_id,
                    created=today,
                    mode=mode,
                    base_hash=base_hash,
                    shared=shared,
                )
            except PromotionError:
                continue
            f.unlink()
            pending = _pending_dir(master) / f"{promo_id}.md"
            moved.append(pending)
            changed += [rel.as_posix(), pending.relative_to(master).as_posix()]
        except Exception:
            continue  # unexpected per-request error: leave file in place, touch nothing
    if changed:
        _commit(
            master, changed, f"promotions: sweep {len(moved)} draft(s)",
            "Brain Promotions", "promotions@brain.local",
        )
    return moved


# ---- in-vault delegated decisions --------------------------------------------

PROMOTION_APPROVALS_REL = "People/{person_id}/PromotionApprovals"


@dataclass(frozen=True)
class PromotionDecisionOutcome:
    promo_id: str
    decider: str
    decision: str
    status: str  # "applied" | "refused" | "tampering"
    reason: str = ""


def sweep_promotion_approvals(master: Path, org: Org, today: str,
                              shared: str | None = None,
                              ) -> list[PromotionDecisionOutcome]:
    """Apply delegated decisions from People/*/PromotionApprovals/*.md to the
    pending promotion queue — the promotions counterpart to
    ``shares.sweep_approvals``, line for line.

    The <pid> path segment is the authoritative decider (write-back already
    gated the write); the filename stem is the promotion id. Eligibility is
    re-checked at decision time with the *full* person via ``may_approve``.
    Targets inside the shared space are never decidable here — that is the
    dashboard's job, where the diff and audience warning are visible — so
    even an admin's in-vault decision on a ``Company/`` promotion is refused.
    Only a forged ``owner:`` is tampering; every other failure is a routine
    refusal with an inbox note explaining why. Malformed notes are left in
    place untouched.
    """
    from brain.clients import _validate_owner_id

    shared = master_shared(master, shared)
    results: list[PromotionDecisionOutcome] = []
    for note in sorted(master.glob("People/*/PromotionApprovals/*.md")):
        if note.is_symlink():
            continue
        rel = note.relative_to(master)
        decider_id = rel.parts[1]
        try:
            _validate_owner_id(decider_id)
        except Exception:
            continue  # malformed pid folder: leave in place, touch nothing
        try:
            promo_id = note.stem
            if not _ID.fullmatch(promo_id):
                continue  # malformed id: leave in place for inspection
            meta, _ = split_frontmatter(note.read_text())
            if not meta:
                continue
            decider = org.people.get(decider_id)
            name_id = decider.name if decider else decider_id
            decision = str(meta.get("decision", ""))
            reason = str(meta.get("reason", ""))

            def consume(status: str, why: str = "",
                        extra: list[str] | None = None) -> None:
                note.unlink()
                _commit(master, [rel.as_posix(), *(extra or [])],
                        f"promotions: decision {promo_id} {status}",
                        name_id, f"{decider_id}@brain.local")
                results.append(PromotionDecisionOutcome(
                    promo_id, decider_id, decision, status, why))

            def refuse(why: str, msg: str) -> None:
                """A routine refusal: say why in the decider's Inbox, then
                consume the decision note in the same commit. Only a forged
                ``owner:`` skips this and lands as tampering."""
                inbox = write_inbox_note(master, decider_id, "promotion",
                                         _slug(promo_id), msg, today)
                consume("refused", why, [inbox])

            if meta.get("owner", decider_id) != decider_id:
                consume("tampering", "owner mismatch")
                continue

            try:
                pending = _find_pending(master, promo_id)
                promo = _parse(pending)
            except (PromotionError, KeyError, ValueError):
                refuse("already decided or unknown",
                       f"Promotion {promo_id} is already decided or unknown.")
                continue

            space = space_of_path(promo.target_path, shared)
            if space == shared:
                refuse("shared space",
                       f"Promotion {promo_id} targets {shared}/ — company-wide "
                       "promotions are decided by an admin at the dashboard.")
                continue

            if not may_approve(decider, promo.target_path, shared):
                refuse("not eligible",
                       f"You are not an eligible approver for {promo_id}.")
                continue

            if decision not in ("approve", "reject"):
                refuse("bad decision",
                       f"Decision for {promo_id} must be approve or reject.")
                continue
            if decision == "reject" and not reason.strip():
                refuse("missing reason",
                       f"Rejecting {promo_id} needs a reason: line.")
                continue

            try:
                if decision == "approve":
                    approve(master, promo_id, approver=decider_id, date=today,
                            shared=shared, via="delegated")
                else:
                    reject(master, promo_id, reason=reason, date=today,
                           approver=decider_id, via="delegated")
            except PromotionError as e:
                refuse(str(e),
                       f"Could not apply your decision on {promo_id}: {e}")
                continue
            consume("applied", decision)
        except Exception:
            continue  # unexpected per-note error: leave file in place, touch nothing
    return results


SHARES_NOTE_REL = "People/{person_id}/Shares.md"
_DECIDED_WINDOW_DAYS = 30
_DECIDED_CAP = 20


def generate_shares_note(master: Path, person_id: str, today: str) -> str | None:
    """Render one person's promotion-status note, or ``None`` if empty.

    The requester's window into ``_meta/promotions``: everything of theirs
    still pending, plus decisions from the last 30 days (newest first, max
    20). The decider's window is ``generate_promotion_decider_section``.
    Compiled into the slice as a *generated* file — the queue stays the
    single source of truth and edits are discarded.
    """
    from datetime import date as _date
    from datetime import timedelta

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
        decided.append((d, (f"- ✅ `{meta.get('target-path', '?')}` — approved "
                           f"{d.isoformat()}{f' by {by}' if by else ''}; now live")))
    for meta in _entries("rejected"):
        d = _when(meta, "rejected-on")
        if d is None or d < cutoff:
            continue
        reason = meta.get("rejected-reason", "no reason recorded")
        decided.append((d, (f"- ❌ `{meta.get('target-path', '?')}` — rejected "
                           f"{d.isoformat()}: {reason}")))
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


_REVIEW_CAP = 4096  # chars of body/diff rendered per item into Shares.md


def _longest_backtick_run(text: str) -> int:
    """Longest consecutive run of backticks in ``text``, for sizing a
    CommonMark fence long enough to contain it without closing early."""
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return longest


_AUDIENCE_LIST_CAP = 8  # reader ids named inline before collapsing to a count


def _audience_line(space: str, org: Org, rules: tuple[SpaceRule, ...]) -> str:
    """One line naming who would be able to read this promotion once approved.

    The dashboard's promotion card warns that approving publishes to everyone
    who can read the target space; the in-vault reviewer decides from the same
    facts, so they get the resolved reader list — from ``can_read``, the same
    authority the compiler uses — not just the space name."""
    readers = sorted(p.id for p in org.people.values() if can_read(space, p, rules))
    if not readers:
        who = "no one else yet — the space has no readers under the current rules"
    elif len(readers) == len(org.people):
        who = f"everyone in the org ({len(readers)} people)"
    elif len(readers) > _AUDIENCE_LIST_CAP:
        who = f"{len(readers)} people"
    else:
        who = f"{len(readers)} " + ("person" if len(readers) == 1 else "people")
        who += ": " + ", ".join(readers)
    return f"⚠ Approving publishes into `{space}` — readable by {who}."


def _render_promotion_item(master: Path, promo: Promotion, space: str, org: Org,
                           rules: tuple[SpaceRule, ...], shared: str) -> list[str]:
    """One pending promotion, rendered as the lines a reviewer decides from:
    a heading, the audience warning, and the reviewable content — the body for
    create/append, a live unified diff for patch — capped at ``_REVIEW_CAP``."""
    if promo.mode == "patch":
        rendered = patch_diff(master, promo, shared)
        lang = "diff"
        if rendered is None:
            rendered = "(target missing or unchanged — approval would fail closed)"
            lang = ""
    else:
        rendered, lang = promo.body, ""
    truncated = len(rendered) > _REVIEW_CAP
    if truncated:
        rendered = rendered[:_REVIEW_CAP]
    # An untrusted promoter's body/diff is rendered raw inside a fence
    # that also frames this note's own structural markdown (including
    # the decision recipe below). A ``` (or longer) run in the content
    # would close the fence early and let it bleed into — or spoof —
    # adjacent sections an agent reads as instructions. Size the fence
    # to the content: CommonMark lets a longer backtick fence safely
    # contain any shorter run.
    fence = "`" * max(3, _longest_backtick_run(rendered) + 1)
    lines = [
        (f"### `{promo.id}` → `{promo.target_path}` ({promo.mode}) from "
         f"{promo.person_id}, drafted {promo.created}"),
        "",
        _audience_line(space, org, rules),
        "",
        f"{fence}{lang}",
        rendered.rstrip("\n"),
        fence,
    ]
    if truncated:
        lines.append(f"*Truncated at {_REVIEW_CAP} characters — see the full text with "
                     f"`brain promotions show {promo.id}` or in the dashboard.*")
    lines.append("")
    return lines


def generate_promotion_decider_section(master: Path, person_id: str, today: str,
                                       shared: str | None = None,
                                       rules: tuple[SpaceRule, ...] | None = None,
                                       org: Org | None = None,
                                       pending: list[Promotion] | None = None,
                                       ) -> str | None:
    """The 'Promotions awaiting your decision' section for one person's
    ``Shares.md``, or ``None`` when nothing is theirs to decide.

    Eligibility is computed on a *delegated view* of the person — admin
    stripped — so admins see nothing here and keep using the dashboard, and
    shared-space targets are excluded outright (they are never decidable
    in-vault). This mirrors ``shares.generate_decider_section`` exactly.

    Approval authority is path-shaped (``may_approve``: lead of the team the
    target names) and says nothing about *reading* that space. An exact rule
    can shadow the ``Teams/*`` wildcard, leaving a lead who may approve into a
    space their vault does not contain — and a patch's diff is built from the
    target's current bytes, so rendering it would put those bytes in a vault
    the rules keep them out of. ``rules`` closes that: the item must also pass
    ``can_read``, the same authority the compiler filters the vault with.
    Without rules there is no read check to run, so nothing is shown.

    Because ``_meta/`` never compiles into a vault, this is the only way a
    lead can read what they are approving: the body for create/append, a
    unified diff for patch, each capped at ``_REVIEW_CAP`` with an explicit
    truncation notice pointing at ``brain promotions show``.

    ``org`` and ``pending`` are the already-parsed roster and promotion queue
    when the caller has them (the fleet compile parses each once for the whole
    run); without them both are read here, so a lone caller still works."""
    import yaml

    from brain.schemas import SchemaError

    if rules is None:
        return None  # no read authority available: fail closed
    try:
        shared = master_shared(master, shared)
        if org is None:
            org = load_org(master / "_meta/org.yaml")
    except (SchemaError, OSError, yaml.YAMLError):
        return None
    person = org.people.get(person_id)
    if person is None:
        return None
    delegated_view = Person(
        id=person.id, name=person.name,
        roles=tuple(r for r in person.roles if r != "admin"),
        teams=person.teams, email=person.email)
    if pending is None:
        pending = list_pending(master)
    mine = [
        (p, space) for p in pending
        if (space := space_of_path(p.target_path, shared)) is not None
        # Belt-and-suspenders, not live logic today: may_approve already
        # refuses non-Teams/ targets for a non-admin delegated view, so this
        # can never fire yet. Kept explicit so "the shared space is never
        # decidable in-vault" stays visible at this display layer too — a
        # future change to may_approve's Teams/-only rule can't silently
        # start surfacing company-wide promotions into a lead's own vault.
        and space != shared
        and may_approve(delegated_view, p.target_path, shared)
        # Read access is checked on the *real* person, not the delegated view:
        # the question is what this vault already holds, and the compiler that
        # built it used the real person too.
        and can_read(space, person, rules)
    ]
    if not mine:
        return None

    lines = [
        "## Promotions awaiting your decision", "",
        "These promotions target a team you lead. Read what would be published,",
        "then record only a decision your human has explicitly made.", "",
    ]
    for p, space in mine:
        lines += _render_promotion_item(master, p, space, org, rules, shared)
    lines += [
        f"To decide, write `People/{person_id}/PromotionApprovals/<promo-id>.md`:", "",
        "```", "---", "decision: approve   # or: reject",
        "reason: required when rejecting", f"owner: {person_id}",
        f"created: {today}", "---", "```",
        "",
        f"Promotions into `{shared}/` are decided by an admin at the dashboard, not here.",
    ]
    return "\n".join(lines) + "\n"
