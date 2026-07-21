from brain.facts import (
    Fact,
    lint_facts,
    normalize_from,
    normalize_until,
    parse_entity,
    parse_facts,
)

FACT_NOTE = """\
# Acme

- Sarah Kim is our main contact
  [from:: 2026-01] [source:: [[2026-01-14-call]]]
- Dana Ortiz was our main contact
  [from:: 2024-06] [until:: 2026-01] [source:: [[2026-01-14-call]]]
- Largest client by revenue [from:: 2025-03-15] [source:: https://example.com/q1]
- A plain bullet with no fields.

Prose mentioning [from:: 2026-01] outside a bullet is not a fact.
"""


def test_parse_facts_finds_only_bullet_facts():
    facts = parse_facts(FACT_NOTE)
    assert len(facts) == 3  # the plain bullet and the prose line are not facts


def test_multiline_bullet_fields_and_statement():
    f = parse_facts(FACT_NOTE)[0]
    assert f.statement == "Sarah Kim is our main contact"
    assert f.from_date == "2026-01-01"
    assert f.until_date is None
    assert f.sources == ["[[2026-01-14-call]]"]
    assert f.targets == []  # source links are provenance, not entities
    assert f.line == 3  # 1-based line of the bullet start


def test_statement_wikilinks_are_targets():
    f = parse_facts("- Partnered with [[Globex]] on the pilot [from:: 2026-02]\n")[0]
    assert f.targets == ["Globex"]
    assert f.statement == "Partnered with [[Globex]] on the pilot"


def test_until_normalizes_to_month_end():
    f = parse_facts(FACT_NOTE)[1]
    assert f.from_date == "2024-06-01"
    assert f.until_date == "2026-01-31"


def test_day_granularity_and_url_source():
    f = parse_facts(FACT_NOTE)[2]
    assert f.from_date == "2025-03-15"
    assert f.sources == ["https://example.com/q1"]
    assert f.targets == []


def test_normalize_dates():
    assert normalize_from("2026-01") == "2026-01-01"
    assert normalize_until("2026-01") == "2026-01-31"
    assert normalize_until("2024-02") == "2024-02-29"  # leap year
    assert normalize_from("2026-01-14") == "2026-01-14"
    assert normalize_from("2026-13") is None
    assert normalize_from("garbage") is None
    assert normalize_from("2026-02-30") is None


def test_malformed_lines_are_not_facts_but_lint():
    text = (
        "- bad date [from:: 2026-99]\n"
        "- inverted [from:: 2026-05] [until:: 2026-01]\n"
        "- floating end [until:: 2026-01]\n"
        "- fine [from:: 2026-01]\n"
    )
    facts = parse_facts(text)
    assert [f.statement for f in facts] == ["fine"]
    problems = lint_facts(text)
    assert [ln for ln, _ in problems] == [1, 2, 3]
    assert "unparseable" in problems[0][1]
    assert "before" in problems[1][1]
    assert "until without from" in problems[2][1]


def test_multiple_sources_collected():
    text = "- x [from:: 2026-01] [source:: [[A]]] [source:: [[B]]]\n"
    assert parse_facts(text)[0].sources == ["[[A]]", "[[B]]"]


def test_non_ascii_digits_are_not_facts():
    assert normalize_from("2026-¹3") is None
    text = "- weird [from:: 2026-¹3]\n"
    assert parse_facts(text) == []
    assert [ln for ln, _ in lint_facts(text)] == [1]  # reported, not raised


def test_parse_entity():
    assert parse_entity({"entity": "client", "aliases": "[Acme Corp, ACME]"}) == (
        "client", ["Acme Corp", "ACME"])
    assert parse_entity({"entity": "person"}) == ("person", [])
    assert parse_entity({"entity": ""}) == ("", [])  # doctor warns on this
    assert parse_entity({"title": "x"}) is None
