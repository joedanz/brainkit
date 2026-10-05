"""Retrieval regression guard: known questions scored against a vault's index.

A case says "this question should find this note". The harness runs each case
through the same search path agents use and reports where the expected note
landed. It answers one question — did a change make known-good queries worse —
and is not a tuning signal: nothing here compares averages, and no model is
called. See docs/superpowers/specs/2026-10-05-brain-eval-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from brain.errors import BrainError

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
