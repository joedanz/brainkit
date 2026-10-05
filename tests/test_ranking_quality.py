"""The retrieval guard: pinned questions must still find their notes.

Runs the synthetic fixture vault keyword-only (deterministic, no provider). It
fails when a `must` case drops out of the top results, and deliberately pins no
exact ranks, so an unrelated ranking tweak does not fail it.
"""

import shutil
from pathlib import Path

import pytest

from brain.compiler import compile_vault
from brain.evalcases import load_cases, run_eval
from brain.indexer import build_index
from brain.templates import assistant_protocol
from tests.conftest import ALICE, RULES

FIXTURE = Path(__file__).parent / "fixtures" / "eval"


@pytest.fixture
def fixture_vault(tmp_path):
    master = tmp_path / "master"
    shutil.copytree(FIXTURE / "master", master)
    (master / "_meta").mkdir()
    (master / "_meta/org.yaml").write_text("people: {}\n")
    (master / "AGENTS.md").write_text(assistant_protocol())
    vault = tmp_path / "vault"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    return vault


def test_pinned_cases_still_find_their_notes(fixture_vault):
    report = run_eval(fixture_vault, load_cases(FIXTURE / "cases.yaml"))
    detail = report.to_dict(detail=True)
    failed = [c["id"] for c in detail["cases"] if c["must"] and c["rank"] is None]
    assert report.invalid == 0, "a fixture case points at a note that is missing"
    assert not failed, f"must cases fell out of the top {report.k}: {failed}"
    assert report.ok


def test_fixture_cases_are_not_title_copies(fixture_vault):
    report = run_eval(fixture_vault, load_cases(FIXTURE / "cases.yaml"))
    assert report.warnings == ()


def test_the_seeded_case_is_reported_apart_from_the_headline(fixture_vault):
    report = run_eval(fixture_vault, load_cases(FIXTURE / "cases.yaml"))
    assert report.by_origin["synthetic"]["n"] == 1
    assert report.headline["n"] == 12
