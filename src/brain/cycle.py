"""One-shot server cycle: writeback -> materialize clients -> sweep shares ->
sweep promotions -> sweep promotion decisions -> compile-all -> triage.

Ordering is load-bearing: writebacks land person edits (including freshly
synced promotion drafts) in master BEFORE the sweep reads People/*/Promotions,
and compile runs last so every vault reflects the post-writeback master.
Triage runs last, after the compile, so doctor's compiled-vault check sees
fresh vaults; the digests it lands in master compile into vaults on the next
cycle.

A write-back never halts the cycle. Out-of-scope changes are held (never
applied) while the person's in-scope changes still land; a git or disk
failure for one person is reported as that person's `error`. Any hold or
error flips CycleReport.ok so cron alerts.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from brain.compiler import MANIFEST_NAME, CompileError, compile_all, write_manifest
from brain.corrections import grandfather
from brain.errors import HANDLED, BrainError, describe
from brain.holds import utc_now_iso as _utc_now_iso
from brain.holds import writeback_person
from brain.promotions import list_pending, sweep
from brain.schemas import load_config, load_org, load_spaces
from brain.writeback import ManifestError


@dataclass
class PersonWriteback:
    person_id: str
    status: str  # "applied" | "partial" | "held" | "skipped" | "error"
    applied: int = 0
    held: list[str] = field(default_factory=list)  # "<kind> <path>: <reason>"
    error: str = ""
    violations: list[str] = field(default_factory=list)  # why a "skipped" person was skipped


@dataclass
class CycleReport:
    writebacks: list[PersonWriteback]
    swept: int
    compiled: int
    pending: int
    clients_created: int = 0
    clients_rejected: int = 0
    clients_tampering: int = 0  # owner-mismatch client rejections — a tamper signal
    # People whose vault failed to build this cycle, "<pid>: <reason>". Each
    # keeps their previous vault; everyone else is refreshed, and the cycle
    # still indexes, triages, and writes its health snapshot.
    compile_failures: list[str] = field(default_factory=list)
    shares_queued: int = 0
    shares_revoked: int = 0
    shares_tampering: int = 0  # non-owner share/revoke requests — a tamper signal
    share_decisions_applied: int = 0
    share_decisions_refused: int = 0
    promotion_decisions_applied: int = 0
    promotion_decisions_refused: int = 0
    promotion_tampering: int = 0  # forged owner: on an in-vault decision — a tamper signal
    indexed: int = 0
    index_warnings: list[str] = field(default_factory=list)
    triage_findings: int = 0
    triage_digests: int = 0     # digest notes written or removed
    triage_unrouted: int = 0
    triage_warnings: list[str] = field(default_factory=list)
    doctor_counts: dict[str, int] = field(default_factory=dict)
    # Why this cycle published no health snapshot, if it published none.
    # Empty on a normal run. Same list-of-strings shape as index_warnings and
    # triage_warnings above, for the same reason: a best-effort step that
    # failed must still be SAYABLE. Fleet reads a missing or ageing snapshot
    # as "not reporting"/"stale" and cannot tell an operator why, so the only
    # place the reason can surface is the cycle's own output.
    health_warnings: list[str] = field(default_factory=list)
    # Grandfathering could not record this box's existing corrections; they
    # stay pending until a later cycle records them. Never fails the cycle.
    corrections_warnings: list[str] = field(default_factory=list)
    # Wall time for the whole cycle, in milliseconds.
    #
    # A cycle that outgrows its own cron interval is the failure mode this
    # measures, and it arrives gradually: one fleet's went 13m, 20m, 30m26s as
    # its index outgrew the box's RAM, and nothing recorded any of it. The
    # first anyone knew was five overlapping runs and a box in swap.
    #
    # Monotonic, so a clock adjustment mid-cycle cannot produce a negative or
    # wildly large duration — this number gets compared against a cron
    # interval, where a wrong value is worse than none.
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        # Retrieval is a convenience layer; a failed index warns but never fails
        # the cycle. A hold or a write-back error fails it: someone's edit did
        # not land. As does an owner-mismatch client request (a tamper
        # signal). Routine "name taken" client rejections do NOT — they're a
        # normal user outcome surfaced via the requester's inbox note. A
        # person whose vault failed to compile fails it too: that agent is
        # working from a stale vault.
        return (
            all(w.status not in ("partial", "held", "error") for w in self.writebacks)
            and self.clients_tampering == 0
            and self.shares_tampering == 0
            and self.promotion_tampering == 0
            and not self.compile_failures
        )



def _refresh_indexes(master: Path, out_root: Path, org) -> tuple[int, list[str]]:
    import sqlite3

    from brain.embeddings import EmbeddingCache, provider_from_config
    from brain.indexer import build_index

    provider = provider_from_config()
    indexed = 0
    warnings: list[str] = []
    cache = None
    if provider:
        try:
            cache = EmbeddingCache.for_master(master)
        except (OSError, sqlite3.Error) as e:
            warnings.append(f"embedding cache not used: {e}")
        else:
            if cache is None:
                warnings.append(
                    "embedding cache not used: master/.gitignore does not cover "
                    "_meta/cache/ — add that line (brain init writes it) or the "
                    "cache would be committable")
    for person in org.people.values():
        vault = out_root / person.id
        if not (vault / MANIFEST_NAME).is_file():
            continue
        try:
            rep = build_index(vault, provider=provider, cache=cache)
        except Exception as e:  # never let indexing abort the cycle
            warnings.append(f"{person.id}: index failed: {e}")
            continue
        indexed += 1
        warnings.extend(f"{person.id}: {w}" for w in rep.warnings)
    if cache is not None:
        warnings.extend(cache.warnings)
        cache.close()
    return indexed, warnings


# A key in the vault's tracked manifest, set (uncommitted) while the cycle
# works on that person. The worktree is then dirty, and with
# receive.denyCurrentBranch=updateInstead git refuses agent pushes until the
# compile commits a fresh manifest without it; vault-sync keeps the commit and
# retries next run. Nothing reads the value to decide anything (flock already
# serializes cycles), so a marker a crash left behind is simply overwritten.
BUSY_KEY = "busy"


def _rewrite_manifest(vault: Path, edit) -> None:
    path = vault / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(manifest, dict) or "compiled" not in manifest:
        return  # write-back skips this person anyway; leave it byte-for-byte
    if edit(manifest):
        # Same serialization as compile_vault, so clearing the key restores
        # the committed bytes exactly and the worktree is clean again.
        write_manifest(path, manifest)


def _set_busy(vault: Path, now: str) -> None:
    def edit(m: dict) -> bool:
        m[BUSY_KEY] = now
        return True
    _rewrite_manifest(vault, edit)


def _clear_busy(vault: Path) -> None:
    _rewrite_manifest(vault, lambda m: m.pop(BUSY_KEY, None) is not None)


class WritebackFailed(BrainError):
    """A person's final write-back failed; their compile is skipped so the
    edits it could not apply stay in their vault for the next cycle."""


def _status(applied: int, held: list[str], error: str) -> str:
    if error:
        return "error"
    if held:
        return "partial" if applied else "held"
    return "applied"


def _writeback_if_present(master: Path, vault: Path, person, rules, *, now: str,
                          prior: PersonWriteback | None = None,
                          already: dict[str, str | None] | None = None,
                          ) -> PersonWriteback | None:
    """Run a write-back pass, unless this person has no compiled vault yet
    (nothing to diff against). Returns None in that case."""
    if not (vault / MANIFEST_NAME).is_file():
        return None
    return _writeback_one(master, vault, person, rules, now=now, prior=prior,
                          already=already)


def _writeback_one(master: Path, vault: Path, person, rules, *, now: str,
                   prior: PersonWriteback | None = None,
                   already: dict[str, str | None] | None = None) -> PersonWriteback:
    """One write-back pass. A second pass (`prior` set) adds its applied
    count to the first's; its own held list and error replace the first's,
    because it re-diffs everything the first pass held or failed on."""
    applied_before = prior.applied if prior else 0
    try:
        result = writeback_person(master, vault, person, rules, now=now, already=already)
    except ManifestError as e:
        # A present-but-corrupt manifest means no trustworthy diff baseline
        # for this person. Skip them (their edits, if any, wait for the next
        # cycle) rather than aborting everyone else's refresh — the recompile
        # rewrites a clean manifest, so the next cycle self-heals.
        return prior or PersonWriteback(person.id, "skipped", violations=[str(e)])
    except HANDLED as e:
        # One person's disk or git failure is theirs alone.
        return PersonWriteback(person.id, "error", applied=applied_before, error=describe(e))
    if already is not None:
        already.update({c.path: c.sha for c in result.applied})
    held = [f"{h.kind} {h.path}: {h.reason}" for h in result.held]
    applied = applied_before + len(result.applied)
    # A later pass that ran cleanly clears an earlier pass's error: a failed
    # pass records nothing in `already`, so everything it failed on was
    # re-diffed and retried just now.
    error = result.error
    return PersonWriteback(person.id, _status(applied, held, error),
                           applied=applied, held=held, error=error)


def run_cycle(master: Path, out_root: Path, today: str, *, index: bool = False) -> CycleReport:
    # First statement, so the measurement covers the whole run rather than
    # whatever part of it someone remembers to include.
    _started = time.monotonic()
    now = _utc_now_iso()
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    config = load_config(master)

    # Before any write-back, so only rules already in master on upgrade day
    # are kept in force; anything an agent pushes from now on waits for its
    # person. A no-op once every person has a record.
    corrections_warnings: list[str] = []
    try:
        grandfather(master, org.people, now=now)
    except HANDLED as e:
        corrections_warnings.append(
            f"existing corrections not recorded ({describe(e)}); they stay "
            "pending until the next cycle records them")

    wb: dict[str, PersonWriteback] = {}
    already: dict[str, dict[str, str | None]] = {}
    vaults = {p.id: out_root / p.id for p in org.people.values()}
    for vault in vaults.values():
        if (vault / MANIFEST_NAME).is_file():
            _set_busy(vault, now)

    def final_writeback(person) -> None:
        # Runs right before this person's compile: anything that landed
        # between the first pass and the busy marker taking effect is applied
        # now instead of being overwritten by the compile. `rules` is read at
        # call time, so this sees the post-sweep reload below.
        vault = vaults[person.id]
        result = _writeback_if_present(master, vault, person, rules, now=now,
                                       prior=wb.get(person.id),
                                       already=already.setdefault(person.id, {}))
        if result is None:
            return
        wb[person.id] = result
        if wb[person.id].status == "error":
            raise WritebackFailed(
                f"write-back failed ({wb[person.id].error}); vault left as it was "
                "so the edits are retried next cycle")

    compile_failures: list[str] = []
    try:
        for person in org.people.values():
            vault = vaults[person.id]
            result = _writeback_if_present(master, vault, person, rules, now=now,
                                           already=already.setdefault(person.id, {}))
            wb[person.id] = result or PersonWriteback(person.id, "skipped")

        from brain.clients import materialize_clients
        from brain.shares import sweep_approvals, sweep_shares

        provisioned = materialize_clients(master, org, today=today, config=config)
        share_outcomes = sweep_shares(master, org, today=today, shared=config.shared)
        decision_outcomes = sweep_approvals(master, org, today=today,
                                            shared=config.shared)
        # sweep_shares/sweep_approvals may have modified spaces.yaml (revokes,
        # delegated approvals); materialize_clients appended grants too. The
        # compile below must see all of it, so reload.
        rules = load_spaces(master / "_meta/spaces.yaml")

        swept = len(sweep(master, today=today, shared=config.shared))
        # Decisions can only apply to something already queued, and a lead's
        # decision file and the draft it decides may land in the same
        # write-back — so this runs after the draft sweep, before compile.
        from brain.promotions import sweep_promotion_approvals

        promo_decisions = sweep_promotion_approvals(master, org, today=today,
                                                    shared=config.shared)
        # The queue is settled only now: sweep() queued this cycle's drafts
        # and sweep_promotion_approvals() consumed the ones just decided. One
        # parse from here serves both the fleet compile and the report count.
        pending_promotions = list_pending(master)
        try:
            compiled = len(compile_all(master, org, rules, out_root, today=today,
                                       config=config, pending=pending_promotions,
                                       before_each=final_writeback))
        except CompileError as e:
            # One person's failure is theirs alone: they keep their last good
            # vault, and indexing, triage and the health snapshot still run.
            compiled = len(e.completed)
            compile_failures = [f"{pid}: {why}" for pid, why in e.failures]
    finally:
        # A successful compile already wrote a manifest without the marker;
        # this covers a failed compile and a crash anywhere above.
        for vault in vaults.values():
            _clear_busy(vault)
    writebacks = list(wb.values())
    pending = len(pending_promotions)

    indexed = 0
    index_warnings: list[str] = []
    if index:
        indexed, index_warnings = _refresh_indexes(master, out_root, org)

    from brain.triage import TriageReport, run_triage

    # `measured` is the one fact the health write below cannot recover from the
    # report itself: a crashed triage and a genuinely clean brain BOTH arrive
    # here with empty finding_counts, so `counts == {}` cannot tell them apart.
    measured = True
    try:
        triage = run_triage(master, out_root, today=today)
    except Exception as e:  # never let triage abort the cycle — mirrors
        # the indexing posture (_refresh_indexes above): everything before
        # this point (writeback, sweeps, compile) already succeeded, so a
        # broken triage run should warn, not throw that work away.
        triage = TriageReport(0, 0, 0, 0, [f"triage failed: {e}"])
        measured = False

    clients_tampering = sum(
        1 for p in provisioned
        if p.status == "rejected" and p.reason == "owner mismatch"
    )
    shares_tampering = (
        sum(1 for o in share_outcomes if o.status == "tampering")
        + sum(1 for o in decision_outcomes if o.status == "tampering")
    )
    promotion_tampering = sum(1 for o in promo_decisions if o.status == "tampering")

    from brain.health import write_health

    # Stopped here, before the snapshot write, so the number the snapshot
    # carries is the cycle's real work rather than a value that also includes
    # the write it appears in.
    duration_ms = int((time.monotonic() - _started) * 1000)

    # An unmeasured cycle publishes NOTHING. Writing the crash arm's empty
    # counts would overwrite a true snapshot with {"ok": true, "counts": {}} —
    # which Fleet reads as a reporting, finding-free brain, manufacturing the
    # exact false green this telemetry exists to remove. Leaving the previous
    # file untouched lets it age into `stale` instead, and "these are the last
    # findings we could measure" is the honest answer to a triage that died.
    health_warnings: list[str] = []
    if not measured:
        health_warnings.append(
            "health snapshot not published: triage did not run, so this cycle "
            "measured no findings"
        )
    else:
        # Best-effort: telemetry must never fail a cycle that already did its
        # real work, the same posture as indexing and triage above. Silent,
        # though, is a different thing from harmless — a skip Fleet can only
        # see as "not reporting" needs a reason SOMEWHERE, and this is the
        # only output that has one.
        try:
            counts = dict(triage.finding_counts)
            if compile_failures:
                # Doctor cannot see a failed compile (a broken vault repo, a
                # disk error), so without this the snapshot Fleet reads would
                # say ok while the cycle itself says otherwise. A count only:
                # the names stay in the cycle's own output.
                counts["error:compile-failed"] = len(compile_failures)
            written = write_health(
                master,
                counts,
                {
                    "clients": clients_tampering,
                    "shares": shares_tampering,
                    "promotions": promotion_tampering,
                },
                now=_utc_now_iso(),
                duration_ms=duration_ms,
            )
            if not written:
                health_warnings.append(
                    "health snapshot not published: master/.gitignore does not "
                    "cover _meta/cache/ — add that line (brain init writes it) "
                    "or the snapshot would be committable"
                )
        except OSError as e:
            health_warnings.append(f"health snapshot not written: {e}")

    return CycleReport(
        duration_ms=duration_ms,
        writebacks=writebacks, swept=swept, compiled=compiled,
        compile_failures=compile_failures, pending=pending,
        clients_created=sum(1 for p in provisioned if p.status == "created"),
        clients_rejected=sum(1 for p in provisioned if p.status == "rejected"),
        clients_tampering=clients_tampering,
        shares_queued=sum(1 for o in share_outcomes if o.status == "queued"),
        shares_revoked=sum(1 for o in share_outcomes if o.status == "revoked"),
        shares_tampering=shares_tampering,
        share_decisions_applied=sum(1 for o in decision_outcomes if o.status == "applied"),
        share_decisions_refused=sum(1 for o in decision_outcomes if o.status == "refused"),
        promotion_decisions_applied=sum(1 for o in promo_decisions if o.status == "applied"),
        promotion_decisions_refused=sum(1 for o in promo_decisions if o.status == "refused"),
        promotion_tampering=promotion_tampering,
        indexed=indexed, index_warnings=index_warnings,
        triage_findings=triage.routed,
        triage_digests=triage.digests_written + triage.digests_removed,
        triage_unrouted=triage.unrouted,
        triage_warnings=triage.warnings,
        doctor_counts=triage.finding_counts,
        health_warnings=health_warnings,
        corrections_warnings=corrections_warnings,
    )
