"""Retrieval regression guard: known questions scored against a vault's index.

A case says "this question should find this note". The harness runs each case
through the same search path agents use and reports where the expected note
landed. It answers one question — did a change make known-good queries worse —
and is not a tuning signal: nothing here compares averages, and no model is
called. See docs/superpowers/specs/2026-10-05-brain-eval-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from brain.embeddings import EmbeddingProvider
from brain.errors import BrainError
from brain.search import _index_db, _run_search
from brain.store import IndexStore

SCHEMA = 1
ORIGINS = ("human", "agent", "synthetic")
_FIELDS = frozenset({"id", "query", "expect", "origin", "must"})


class EvalError(BrainError, ValueError):
    """An eval run could not start: no index, a bad `k`, or a bad case file."""


class EvalCaseError(EvalError):
    """A malformed case file. The message names the case."""


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    expect: tuple[str, ...]  # vault-relative note paths; any one counts as a hit
    origin: str  # "human" | "agent" | "synthetic"
    must: bool = False  # falling out of the top k fails the run


def _text(item: dict, key: str, label: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvalCaseError(f"{label}: '{key}' must be a non-empty string")
    return value.strip()


def _expect(item: dict, label: str) -> tuple[str, ...]:
    raw = item.get("expect")
    if not isinstance(raw, list) or not raw:
        raise EvalCaseError(f"{label}: 'expect' must be a non-empty list of note paths")
    paths = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise EvalCaseError(f"{label}: 'expect' entries must be non-empty strings")
        path = entry.strip()
        parts = PurePosixPath(path).parts
        if path.startswith("/") or PurePosixPath(path).is_absolute():
            raise EvalCaseError(f"{label}: expected path must not be absolute")
        if ".." in parts:
            raise EvalCaseError(f"{label}: expected path must not contain '..'")
        paths.append(path)
    return tuple(paths)


def load_cases(path: Path) -> list[Case]:
    """Read a YAML case file. Every problem is an EvalCaseError naming the case."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise EvalCaseError(f"cannot read case file: {e}") from e
    if not isinstance(raw, list) or not raw:
        raise EvalCaseError("case file must be a non-empty list of cases")
    cases: list[Case] = []
    seen: set[str] = set()
    for n, item in enumerate(raw, 1):
        label = f"case #{n}"
        if not isinstance(item, dict):
            raise EvalCaseError(f"{label}: each case must be a mapping")
        if isinstance(item.get("id"), str) and item["id"].strip():
            label = f"case {item['id'].strip()!r}"
        unknown = sorted(set(item) - _FIELDS)
        if unknown:
            raise EvalCaseError(f"{label}: unknown field(s): {', '.join(map(str, unknown))}")
        cid = _text(item, "id", label)
        if cid in seen:
            raise EvalCaseError(f"{label}: duplicate id")
        seen.add(cid)
        query = _text(item, "query", label)
        origin = _text(item, "origin", label)
        if origin not in ORIGINS:
            raise EvalCaseError(
                f"{label}: 'origin' must be one of {', '.join(ORIGINS)}")
        must = item.get("must", False)
        if not isinstance(must, bool):
            raise EvalCaseError(f"{label}: 'must' must be true or false")
        cases.append(Case(cid, query, _expect(item, label), origin, must))
    return cases


INVALID_REASON = "expected path not in this vault's index"


@dataclass(frozen=True)
class CaseResult:
    case: Case
    status: str  # "hit" | "miss" | "invalid"
    rank: int | None  # 1-based rank of the best expected note within the top k
    top: tuple[str, ...]  # the notes returned, in order, one entry per note


def _metrics(results: list[CaseResult]) -> dict:
    n = len(results)
    if n == 0:
        return {"n": 0, "hit_at_1": 0.0, "hit_at_k": 0.0, "mrr": 0.0}
    ranks = [r.rank for r in results]
    return {
        "n": n,
        "hit_at_1": round(sum(1 for x in ranks if x == 1) / n, 4),
        "hit_at_k": round(sum(1 for x in ranks if x is not None) / n, 4),
        "mrr": round(sum(1 / x for x in ranks if x is not None) / n, 4),
    }


@dataclass(frozen=True)
class EvalReport:
    k: int
    mode: str
    degraded: bool
    warnings: tuple[str, ...]
    results: tuple[CaseResult, ...]

    @property
    def _valid(self) -> list[CaseResult]:
        return [r for r in self.results if r.status != "invalid"]

    @property
    def headline(self) -> dict:
        """Everything but synthetic cases: a query lifted from a note's own
        title always wins on keyword search, so those say nothing about quality."""
        return _metrics([r for r in self._valid if r.case.origin != "synthetic"])

    @property
    def by_origin(self) -> dict[str, dict]:
        return {o: _metrics([r for r in self._valid if r.case.origin == o])
                for o in ORIGINS if any(r.case.origin == o for r in self._valid)}

    @property
    def invalid(self) -> int:
        return sum(1 for r in self.results if r.status == "invalid")

    @property
    def ok(self) -> bool:
        """No average threshold: a `must` case out of the top k, or any case
        pointing at a note this vault doesn't have, is the only failure."""
        return self.invalid == 0 and all(
            r.rank is not None for r in self.results if r.case.must)

    def to_dict(self, *, detail: bool = False) -> dict:
        """Stable, JSON-safe. Counts, ids, ranks and statuses only unless
        `detail`: query text and note paths are a map of the vault's private
        structure, so they are opt-in."""
        cases = []
        for r in self.results:
            row = {"id": r.case.id, "origin": r.case.origin, "must": r.case.must,
                   "status": r.status, "rank": r.rank}
            if r.status == "invalid":
                row["reason"] = INVALID_REASON
            if detail:
                row.update(query=r.case.query, expect=list(r.case.expect),
                           top=list(r.top))
            cases.append(row)
        return {
            "schema": SCHEMA, "k": self.k, "mode": self.mode,
            "degraded": self.degraded, "ok": self.ok,
            "headline": self.headline, "by_origin": self.by_origin,
            "invalid": self.invalid, "warnings": list(self.warnings),
            "cases": cases,
        }


def _assemble(results: list[CaseResult], *, k: int, mode: str, degraded: bool,
              warnings: tuple[str, ...]) -> EvalReport:
    return EvalReport(k, mode, degraded, tuple(warnings), tuple(results))


def run_eval(vault: Path, cases: list[Case], *, k: int = 8,
             keyword_only: bool = True,
             provider: EmbeddingProvider | None = None) -> EvalReport:
    """Score `cases` against a compiled vault's index.

    Calls `_run_search`, never `search_index`: the latter records every search
    in the retrieval stats and raw query log, and eval queries must not land
    there. keyword_only defaults on so a run is deterministic and needs no
    provider.
    """
    if k < 1:
        raise EvalError("k must be at least 1")
    vault = Path(vault)
    db = _index_db(vault)
    if not db.is_file():
        raise EvalError(f"no index at {db} — run: brain index --vault {vault}")
    store = IndexStore.open_readonly(db, want_vectors=False)
    try:
        present = {p for c in cases for p in c.expect if store.has_file(p)}
    finally:
        store.close()

    results: list[CaseResult] = []
    modes: set[str] = set()
    for case in cases:
        if not all(p in present for p in case.expect):
            results.append(CaseResult(case, "invalid", None, ()))
            continue
        report = _run_search(vault, case.query, k=k, provider=provider,
                             keyword_only=keyword_only)
        modes.add(report.mode)
        top: list[str] = []
        for hit in report.hits:
            if hit.rel_path not in top:
                top.append(hit.rel_path)
        rank = next((i for i, rel in enumerate(top, 1) if rel in case.expect), None)
        results.append(CaseResult(case, "hit" if rank else "miss", rank, tuple(top)))

    mode = next(iter(modes)) if len(modes) == 1 else ("mixed" if modes else "")
    degraded = not keyword_only and any(not m.startswith("hybrid") for m in modes)
    warnings = [f"{r.case.id}: copies-title" for r in results
                if _copies_title(r.case)]
    if degraded:
        warnings.append("hybrid search was requested but ran without vectors")
    return _assemble(results, k=k, mode=mode, degraded=degraded,
                     warnings=tuple(warnings))


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.lower()).split())


def _copies_title(case: Case) -> bool:
    """A query equal to an expected note's file name gives the answer away."""
    if case.origin == "synthetic":
        return False
    return any(_norm(case.query) == _norm(PurePosixPath(p).stem) for p in case.expect)


_MISS = 10**9


def compare(baseline: dict, current: EvalReport) -> dict[str, str]:
    """Case-by-case movement against an earlier `to_dict()`. Averages hide a
    pair of cases swapping places; this does not. A miss and an invalid case
    both rank worse than any hit."""
    before = {c["id"]: c.get("rank") for c in baseline.get("cases", [])}
    now = {r.case.id: r.rank for r in current.results}

    def key(rank: int | None) -> int:
        return _MISS if rank is None else rank

    out: dict[str, str] = {}
    for cid, rank in now.items():
        if cid not in before:
            out[cid] = "new"
            continue
        a, b = key(before[cid]), key(rank)
        out[cid] = "improved" if b < a else "regressed" if b > a else "unchanged"
    for cid in before:
        if cid not in now:
            out[cid] = "removed"
    return out
