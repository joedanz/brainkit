import pytest

from brain.evalcases import Case, EvalCaseError, load_cases


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
