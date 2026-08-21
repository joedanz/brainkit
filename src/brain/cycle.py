"""One-shot server cycle: writeback -> materialize clients -> sweep shares ->
sweep promotions -> sweep promotion decisions -> compile-all -> triage.

Ordering is load-bearing: writebacks land person edits (including freshly
synced promotion drafts) in master BEFORE the sweep reads People/*/Promotions,
and compile runs last so every vault reflects the post-writeback master.
Triage runs last, after the compile, so doctor's compiled-vault check sees
fresh vaults; the digests it lands in master compile into vaults on the next
cycle.

A rejected writeback never halts the cycle. Rejected edits are reverted
server-side by the fresh compile commit (fail closed); the rejection is
reported and flips CycleReport.ok so cron alerts.
"""

from __future__ import annotations

import time

from dataclasses import dataclass, field
from pathlib import Path

from brain.compiler import MANIFEST_NAME, compile_all
from brain.promotions import list_pending, sweep
from brain.schemas import load_config, load_org, load_spaces
from brain.writeback import ManifestError, apply_writeback


@dataclass
class PersonWriteback:
    person_id: str
    status: str  # "applied" | "rejected" | "skipped"
    applied: int = 0
    violations: list[str] = field(default_factory=list)


@dataclass
class CycleReport:
    writebacks: list[PersonWriteback]
    swept: int
    compiled: int
    pending: int
    clients_created: int = 0
    clients_rejected: int = 0
    clients_tampering: int = 0  # owner-mismatch client rejections — a tamper signal
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
        # the cycle. A rejected writeback (a security-relevant event) fails it,
        # as does an owner-mismatch client request (a tamper signal). Routine
        # "name taken" client rejections do NOT — they're a normal user outcome
        # surfaced via the requester's inbox note.
        return (
            all(w.status != "rejected" for w in self.writebacks)
            and self.clients_tampering == 0
            and self.shares_tampering == 0
            and self.promotion_tampering == 0
        )


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refresh_indexes(master: Path, out_root: Path, org) -> tuple[int, list[str]]:
    from brain.embeddings import EmbeddingCache, provider_from_config
    from brain.indexer import build_index

    provider = provider_from_config()
    cache = EmbeddingCache(master / "_meta/cache/embeddings.db") if provider else None
    indexed = 0
    warnings: list[str] = []
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
    return indexed, warnings


def run_cycle(master: Path, out_root: Path, today: str, *, index: bool = False) -> CycleReport:
    # First statement, so the measurement covers the whole run rather than
    # whatever part of it someone remembers to include.
    _started = time.monotonic()
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    config = load_config(master)

    writebacks: list[PersonWriteback] = []
    for person in org.people.values():
        vault = out_root / person.id
        if not (vault / MANIFEST_NAME).is_file():
            writebacks.append(PersonWriteback(person.id, "skipped"))
            continue
        try:
            result = apply_writeback(master, vault, person, rules)
        except ManifestError as e:
            # A present-but-corrupt manifest means no trustworthy diff baseline
            # for this person. Skip them (their edits, if any, wait for the next
            # cycle) rather than aborting everyone else's refresh — the recompile
            # below rewrites a clean manifest, so the next cycle self-heals.
            writebacks.append(PersonWriteback(person.id, "skipped", violations=[str(e)]))
            continue
        if result.violations:
            writebacks.append(
                PersonWriteback(person.id, "rejected", violations=result.violations)
            )
        else:
            writebacks.append(
                PersonWriteback(person.id, "applied", applied=len(result.applied))
            )

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
    # decision file and the draft it decides may land in the same write-back —
    # so this runs after the draft sweep, before compile.
    from brain.promotions import sweep_promotion_approvals

    promo_decisions = sweep_promotion_approvals(master, org, today=today,
                                                shared=config.shared)
    # The queue is settled only now: sweep() queued this cycle's drafts and
    # sweep_promotion_approvals() consumed the ones just decided. One parse
    # from here serves both the fleet compile and the report count.
    pending_promotions = list_pending(master)
    compiled = len(compile_all(master, org, rules, out_root, today=today,
                               config=config, pending=pending_promotions))
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
            written = write_health(
                master,
                triage.finding_counts,
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
        writebacks=writebacks, swept=swept, compiled=compiled, pending=pending,
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
    )
