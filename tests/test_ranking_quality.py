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
    assert report.headline["n"] == 14


def _competitors(vault, query):
    """Notes whose text holds every word of `query` (what keyword search ranks)."""
    from brain.search import _run_search

    hits = _run_search(vault, query, k=200, provider=None, keyword_only=True).hits
    return {h.rel_path for h in hits}


def test_the_guard_has_must_cases_and_real_contention(fixture_vault):
    # Without these a fixture where every query matches exactly one note would
    # pass whatever fusion and graph reranking did: nothing could push the
    # answer out of the top k.
    cases = {c.id: c for c in load_cases(FIXTURE / "cases.yaml")}
    assert sum(c.must for c in cases.values()) >= 9
    crowded = cases["tasting-debrief"]
    assert crowded.must
    assert len(_competitors(fixture_vault, crowded.query)) >= 10


def test_the_fixture_has_a_note_that_spans_several_chunks(fixture_vault):
    from brain.search import _index_db
    from brain.store import IndexStore

    store = IndexStore.open_readonly(_index_db(fixture_vault), want_vectors=False)
    try:
        (n,) = store.conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE rel_path = ?",
            ("Company/Operations/Staffing Guide.md",)).fetchone()
    finally:
        store.close()
    assert n >= 2
