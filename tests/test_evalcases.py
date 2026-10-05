import json

import pytest

from brain.compiler import compile_vault
from brain.evalcases import (
    INVALID_REASON,
    Case,
    CaseResult,
    EvalCaseError,
    EvalError,
    _assemble,
    compare,
    load_cases,
    run_eval,
)
from brain.indexer import build_index
from brain.retrieval import RAW_NAME, SENTINEL_NAME
from brain.search import search_index
from tests.conftest import ALICE, RULES


def _write(tmp_path, text):
    p = tmp_path / "cases.yaml"
    p.write_text(text, encoding="utf-8")
    return p


GOOD = """\
- id: lease-renewal
  query: when is the lease due
  expect: [Company/Lease.md]
  origin: human
  must: true
- id: seeded
  query: Lease
  expect: [Company/Lease.md, Company/Other.md]
  origin: synthetic
"""


def test_load_cases_reads_every_field(tmp_path):
    cases = load_cases(_write(tmp_path, GOOD))
    assert cases == [
        Case("lease-renewal", "when is the lease due", ("Company/Lease.md",),
             "human", True),
        Case("seeded", "Lease", ("Company/Lease.md", "Company/Other.md"),
             "synthetic", False),
    ]


def test_load_cases_strips_stray_whitespace(tmp_path):
    p = _write(tmp_path, "- id: ' a '\n  query: '  q  '\n"
                         "  expect: [' Company/x.md ']\n  origin: human\n")
    (c,) = load_cases(p)
    assert (c.id, c.query, c.expect) == ("a", "q", ("Company/x.md",))


@pytest.mark.parametrize("body, needle", [
    ("[]\n", "non-empty list"),
    ("not: a list\n", "non-empty list"),
    ("- just a string\n", "case #1"),
    ("- {id: a, query: q, expect: [x.md], origin: human, extra: 1}\n", "extra"),
    ("- {id: a, query: '  ', expect: [x.md], origin: human}\n", "query"),
    ("- {id: a, query: q, expect: [], origin: human}\n", "expect"),
    ("- {id: a, query: q, expect: [x.md], origin: robot}\n", "origin"),
    ("- {id: a, query: q, expect: [/etc/passwd], origin: human}\n", "absolute"),
    ("- {id: a, query: q, expect: ['../x.md'], origin: human}\n", ".."),
    ("- {id: a, query: q, expect: [x.md], origin: human, must: maybe}\n", "must"),
    (("- {id: a, query: q, expect: [x.md], origin: human}\n"
      "- {id: a, query: r, expect: [y.md], origin: human}\n"), "duplicate"),
])
def test_load_cases_rejects_malformed_files(tmp_path, body, needle):
    with pytest.raises(EvalCaseError) as exc:
        load_cases(_write(tmp_path, body))
    assert needle in str(exc.value)


def test_load_cases_names_the_case_in_the_error(tmp_path):
    p = _write(tmp_path, "- {id: lease-renewal, query: '', expect: [x.md], origin: human}\n")
    with pytest.raises(EvalCaseError, match="lease-renewal"):
        load_cases(p)


def test_load_cases_missing_file_is_a_handled_error(tmp_path):
    with pytest.raises(EvalCaseError):
        load_cases(tmp_path / "nope.yaml")


def _case(cid, origin="human", must=False, expect=("Company/a.md",), query="q"):
    return Case(cid, query, expect, origin, must)


def _result(cid, rank, **kw):
    status = "hit" if rank is not None else "miss"
    return CaseResult(_case(cid, **kw), status, rank, ())


def test_metrics_from_hand_computed_ranks():
    report = _assemble(
        [_result("a", 1), _result("b", 2), _result("c", None), _result("d", 4)],
        k=8, mode="keyword-only", degraded=False, warnings=())
    h = report.headline
    assert h["n"] == 4
    assert h["hit_at_1"] == 0.25
    assert h["hit_at_k"] == 0.75
    assert h["mrr"] == round((1 + 1 / 2 + 0 + 1 / 4) / 4, 4)


def test_synthetic_cases_stay_out_of_the_headline_but_show_by_origin():
    report = _assemble(
        [_result("h", 1), _result("s", 1, origin="synthetic"),
         _result("s2", 1, origin="synthetic")],
        k=8, mode="keyword-only", degraded=False, warnings=())
    assert report.headline["n"] == 1
    assert report.by_origin["synthetic"]["n"] == 2
    assert report.by_origin["human"]["n"] == 1


def test_zero_valid_cases_is_zeros_not_a_crash():
    invalid = CaseResult(_case("a"), "invalid", None, ())
    report = _assemble([invalid], k=8, mode="", degraded=False, warnings=())
    assert report.headline == {"n": 0, "hit_at_1": 0.0, "hit_at_k": 0.0, "mrr": 0.0}
    assert report.invalid == 1
    assert report.ok is False


def test_ok_fails_only_for_a_must_miss_or_an_invalid_case():
    base = {"k": 8, "mode": "keyword-only", "degraded": False, "warnings": ()}
    assert _assemble([_result("a", 3, must=True), _result("b", None)], **base).ok
    assert not _assemble([_result("a", None, must=True)], **base).ok


@pytest.fixture
def alice_vault(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    return vault


def test_run_eval_scores_a_real_search(alice_vault):
    cases = [Case("decision", "chose option", ("Company/Decisions/Big Deal Decision.md",),
                  "human", True)]
    report = run_eval(alice_vault, cases)
    assert report.results[0].status == "hit"
    assert report.results[0].rank == 1
    assert report.ok
    assert report.mode.startswith("keyword-only")


def test_two_chunks_of_one_note_count_once(alice_vault):
    # rank is over notes: a note that fills two result slots still ranks once
    cases = [Case("c", "chose option", ("Company/Decisions/Big Deal Decision.md",),
                  "human")]
    (res,) = run_eval(alice_vault, cases).results
    assert len(res.top) == len(set(res.top))


def test_invalid_case_reason_is_identical_for_hidden_and_missing(alice_vault):
    hidden = "People/bob/Sessions/Bob Private Note.md"
    cases = [_case("hidden", expect=(hidden,)),
             _case("missing", expect=("Company/Nope.md",))]
    report = run_eval(alice_vault, cases)
    assert [r.status for r in report.results] == ["invalid", "invalid"]
    out = report.to_dict(detail=True)["cases"]
    assert out[0]["reason"] == out[1]["reason"] == INVALID_REASON
    assert not report.ok
    blob = json.dumps(report.to_dict())
    assert "Bob" not in blob and "bob" not in blob


def test_punctuation_only_query_is_a_miss_not_a_crash(alice_vault):
    cases = [_case("p", query='" \' ) (', expect=("Company/Home.md",))]
    (res,) = run_eval(alice_vault, cases).results
    assert res.status == "miss"


def test_k_below_one_is_a_handled_error(alice_vault):
    with pytest.raises(EvalError, match="k"):
        run_eval(alice_vault, [_case("a")], k=0)


def test_missing_index_is_a_handled_error_naming_the_fix(tmp_path):
    with pytest.raises(EvalError, match="brain index"):
        run_eval(tmp_path, [_case("a")])


def test_default_report_has_no_query_text_or_paths(alice_vault):
    cases = [Case("decision", "chose option", ("Company/Decisions/Big Deal Decision.md",),
                  "human")]
    report = run_eval(alice_vault, cases)
    blob = json.dumps(report.to_dict())
    assert "chose option" not in blob and "Big Deal" not in blob
    detail = json.dumps(report.to_dict(detail=True))
    assert "chose option" in detail and "Big Deal Decision.md" in detail
    assert report.to_dict()["schema"] == 1


def test_run_eval_leaves_the_retrieval_log_and_stats_untouched(alice_vault):
    brain = alice_vault / ".brain"
    (brain / SENTINEL_NAME).write_text("")
    search_index(alice_vault, "pipeline", keyword_only=True)  # proves logging is live
    assert (brain / RAW_NAME).exists()

    def snapshot():
        return {p.name: p.read_bytes() for p in sorted(brain.iterdir())
                if p.is_file() and not p.name.startswith("index.db")}

    before = snapshot()
    run_eval(alice_vault, [_case("a", query="pipeline",
                                 expect=("Teams/sales/Q3 Pipeline.md",))])
    assert snapshot() == before


def test_hybrid_requested_without_a_provider_is_marked_degraded(alice_vault):
    report = run_eval(alice_vault, [_case("a", query="pipeline",
                                          expect=("Teams/sales/Q3 Pipeline.md",))],
                      keyword_only=False, provider=None)
    assert report.degraded is True
    assert any("without vectors" in w for w in report.warnings)
    assert report.to_dict()["degraded"] is True


def test_keyword_only_run_is_not_degraded(alice_vault):
    report = run_eval(alice_vault, [_case("a", query="pipeline",
                                          expect=("Teams/sales/Q3 Pipeline.md",))])
    assert report.degraded is False and report.warnings == ()


def test_title_copy_is_flagged_for_human_but_not_synthetic(alice_vault):
    expect = ("Teams/sales/Q3 Pipeline.md",)
    cases = [_case("h", query="q3 pipeline", expect=expect),
             _case("s", origin="synthetic", query="Q3 Pipeline", expect=expect)]
    report = run_eval(alice_vault, cases)
    assert report.warnings == ("h: copies-title",)


def test_compare_reports_each_outcome():
    before = _assemble(
        [_result("up", 5), _result("down", 1), _result("same", 2),
         _result("gone", 1), _result("lost", 1)],
        k=8, mode="keyword-only", degraded=False, warnings=()).to_dict()
    now = _assemble(
        [_result("up", 2), _result("down", None), _result("same", 2),
         _result("fresh", 1), _result("lost", 3)],
        k=8, mode="keyword-only", degraded=False, warnings=())
    assert compare(before, now) == {
        "up": "improved", "down": "regressed", "same": "unchanged",
        "fresh": "new", "gone": "removed", "lost": "regressed"}


def test_k_counts_notes_not_chunks(alice_vault, monkeypatch):
    # Search allows two chunks per note, so a window of k chunks holds as few
    # as k/2 notes. The top-k cut belongs on notes: a chunker change that
    # splits every note in two must not push a note out of "the top 8".
    from brain import evalcases
    from brain.search import Hit, SearchReport

    order = ["Company/Decisions/Big Deal Decision.md", "Teams/sales/Q3 Pipeline.md",
             "X.md", "Y.md", "Company/Home.md"]
    chunks = [Hit(rel, "Company", "", "", 1.0) for rel in order for _ in range(2)]

    def fake(vault, query, *, k, provider, keyword_only, center=None):
        return SearchReport(query, "keyword-only", chunks[:k])

    monkeypatch.setattr(evalcases, "_run_search", fake)
    (res,) = run_eval(alice_vault, [_case("c", expect=("Company/Home.md",))], k=8).results
    assert res.rank == 5
    assert len(res.top) <= 8
