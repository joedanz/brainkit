"""One-shot server cycle: writeback -> materialize clients -> sweep -> compile-all.

Ordering is load-bearing: writebacks land person edits (including freshly
synced promotion drafts) in master BEFORE the sweep reads People/*/Promotions,
and compile runs last so every vault reflects the post-writeback master.

A rejected writeback never halts the cycle. Rejected edits are reverted
server-side by the fresh compile commit (fail closed); the rejection is
reported and flips CycleReport.ok so cron alerts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from brain.compiler import MANIFEST_NAME, compile_all
from brain.promotions import list_pending, sweep
from brain.schemas import load_org, load_spaces
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
    indexed: int = 0
    index_warnings: list[str] = field(default_factory=list)

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
        )


def _refresh_indexes(master: Path, out_root: Path, org) -> tuple[int, list[str]]:
    from brain.embeddings import EmbeddingCache, default_cache_path, provider_from_config
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
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")

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

    provisioned = materialize_clients(master, org, today=today)
    # materialize_clients appended grants to spaces.yaml; the compile below must
    # see them, so reload the rules it was given at the top of the cycle.
    rules = load_spaces(master / "_meta/spaces.yaml")

    swept = len(sweep(master, today=today))
    compiled = len(compile_all(master, org, rules, out_root, today=today))
    pending = len(list_pending(master))

    indexed = 0
    index_warnings: list[str] = []
    if index:
        indexed, index_warnings = _refresh_indexes(master, out_root, org)

    return CycleReport(
        writebacks=writebacks, swept=swept, compiled=compiled, pending=pending,
        clients_created=sum(1 for p in provisioned if p.status == "created"),
        clients_rejected=sum(1 for p in provisioned if p.status == "rejected"),
        clients_tampering=sum(
            1 for p in provisioned
            if p.status == "rejected" and p.reason == "owner mismatch"
        ),
        indexed=indexed, index_warnings=index_warnings,
    )
