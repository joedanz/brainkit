
from brain.compiler import compile_vault
from brain.embeddings import FakeEmbeddingProvider
from brain.facts import (
    Fact,
    lint_facts,
    normalize_from,
    normalize_until,
    parse_entity,
    parse_facts,
    query_facts,
)
from brain.indexer import build_index
from tests.conftest import ALICE, RULES

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


ACME = (
    "---\nentity: client\naliases: [Acme Corp, ACME]\n---\n# Acme\n\n"
    "- Sarah Kim is our main contact [from:: 2026-01] [source:: [[Q3 Pipeline]]]\n"
    "- Dana Ortiz was our main contact [from:: 2024-06] [until:: 2026-01]\n"
)


def _facts_vault(master, tmp_path):
    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme.md").write_text(ACME)
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)
    return vault


def test_query_current_excludes_ended(master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    hits, warnings = query_facts(vault)
    assert warnings == []
    assert [h.statement for h in hits] == ["Sarah Kim is our main contact"]
    # entities = statement wikilinks (none here) + the implicit page subject
    assert hits[0].entities == ["Company/Intel/Acme.md"]


def test_query_as_of_returns_the_then_current_fact(master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    hits, _ = query_facts(vault, as_of="2025-06")
    assert [h.statement for h in hits] == ["Dana Ortiz was our main contact"]
    # boundary month: until:: 2026-01 is inclusive through month end
    hits, _ = query_facts(vault, as_of="2026-01-31")
    assert {h.statement for h in hits} == {
        "Sarah Kim is our main contact", "Dana Ortiz was our main contact"}


def test_query_entity_and_type_filters(master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    for selector in ("Company/Intel/Acme.md", "Acme", "acme corp"):
        hits, _ = query_facts(vault, entity=selector)
        assert hits, selector
    hits, _ = query_facts(vault, etype="client")
    assert hits
    hits, _ = query_facts(vault, etype="person")
    assert hits == []
    hits, warnings = query_facts(vault, entity="Unknown Co")
    assert hits == [] and any("no entity" in w for w in warnings)


def test_query_include_ended(master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    hits, _ = query_facts(vault, include_ended=True)
    assert len(hits) == 2


def test_query_missing_index_warns(tmp_path):
    hits, warnings = query_facts(tmp_path / "no-vault")
    assert hits == [] and any("no index" in w for w in warnings)


def test_query_pre_v3_index_warns_instead_of_crashing(master, tmp_path):
    """A vault indexed before schema v3 (facts/entities/fact_entities) must
    degrade gracefully instead of raising sqlite3.OperationalError."""
    import sqlite3

    vault = _facts_vault(master, tmp_path)
    conn = sqlite3.connect(vault / ".brain" / "index.db")
    conn.execute("DROP TABLE fact_entities")
    conn.execute("DROP TABLE facts")
    conn.execute("DROP TABLE entities")
    conn.commit()
    conn.close()

    hits, warnings = query_facts(vault)
    assert hits == []
    assert len(warnings) == 1
    assert "run:" in warnings[0] and "index" in warnings[0]
    assert str(vault) in warnings[0]


import subprocess

from brain.facts import query_facts_at


def _git(vault, *argv, env_date=None):
    env = {"GIT_AUTHOR_DATE": env_date, "GIT_COMMITTER_DATE": env_date} if env_date else {}
    import os
    subprocess.run(["git", "-C", str(vault), "-c", "user.name=t",
                    "-c", "user.email=t@t", *argv],
                   check=True, capture_output=True, env={**os.environ, **env})


def test_believed_on_reads_history(master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    acme = vault / "Company/Intel/Acme.md"
    _git(vault, "init", "-q")

    # state 1 (2025-01-15): only Dana, still current
    acme.write_text(
        "---\nentity: client\n---\n# Acme\n\n"
        "- Dana Ortiz is our main contact [from:: 2024-06]\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "state1", env_date="2025-01-15T12:00:00 +0000")

    # state 2 (2026-01-20): Dana closed, Sarah current — one commit
    acme.write_text(
        "---\nentity: client\n---\n# Acme\n\n"
        "- Sarah Kim is our main contact [from:: 2026-01]\n"
        "- Dana Ortiz was our main contact [from:: 2024-06] [until:: 2026-01]\n")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "state2", env_date="2026-01-20T12:00:00 +0000")

    # believed on 2025-06-01 → state1's view: Dana, open-ended
    hits, warnings = query_facts_at(vault, "2025-06-01")
    assert warnings == []
    assert [h.statement for h in hits] == ["Dana Ortiz is our main contact"]
    assert hits[0].until_date is None

    # believed on 2026-02-01 → state2's view: Sarah current, Dana closed
    hits, _ = query_facts_at(vault, "2026-02-01")
    assert [h.statement for h in hits] == ["Sarah Kim is our main contact"]
    hits, _ = query_facts_at(vault, "2026-02-01", include_ended=True)
    assert len(hits) == 2

    # before any commit → clear warning, no crash
    hits, warnings = query_facts_at(vault, "2020-01-01")
    assert hits == [] and any("no commit" in w for w in warnings)


def test_believed_on_without_git_warns(master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    hits, warnings = query_facts_at(vault, "2026-01-01")
    assert hits == [] and any("no git history" in w for w in warnings)


# ---------------------------------------------------------------------------
# find_fact_conflicts — duplicate and contradicting open facts (issue #79)

from brain.facts import find_fact_conflicts


def _entry(rel, line, stmt, keys, from_date="2026-01-01", until=None):
    return (rel, Fact(line=line, statement=stmt, from_date=from_date,
                      until_date=until, sources=[], targets=[]),
            frozenset(keys))


def test_dup_same_statement_same_keys_both_open():
    a = _entry("a.md", 3, "Acme is on the Enterprise plan", {"Clients/Acme.md"},
               from_date="2025-03-01")
    b = _entry("b.md", 8, "Acme is on the Enterprise plan", {"Clients/Acme.md"},
               from_date="2026-01-01")
    assert find_fact_conflicts([b, a]) == [("dup", a, b)]


def test_dup_fires_on_identical_from_dates_too():
    a = _entry("a.md", 3, "Acme is on the Enterprise plan", {"Clients/Acme.md"})
    b = _entry("a.md", 9, "Acme is on the Enterprise plan", {"Clients/Acme.md"})
    assert find_fact_conflicts([a, b]) == [("dup", a, b)]


def test_dup_is_casefolded():
    a = _entry("a.md", 3, "ACME is on the Enterprise Plan", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "Acme is on the enterprise plan", {"Clients/Acme.md"})
    assert [k for k, *_ in find_fact_conflicts([a, b])] == ["dup"]


def test_dup_requires_equal_key_sets():
    # Same statement but different key sets: not a dup, and identical
    # statements have an empty divergence so not a conflict either.
    a = _entry("a.md", 3, "Acme is on the Enterprise plan", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "Acme is on the Enterprise plan",
               {"Clients/Acme.md", "Company/Plans.md"})
    assert find_fact_conflicts([a, b]) == []


def test_conflict_copula_is():
    a = _entry("a.md", 3, "Acme's plan is Enterprise", {"Clients/Acme.md"},
               from_date="2025-03-01")
    b = _entry("b.md", 8, "Acme's plan is Growth", {"Clients/Acme.md"},
               from_date="2026-01-01")
    assert find_fact_conflicts([a, b]) == [("conflict", a, b)]


def test_conflict_shared_key_is_enough():
    # Key sets need only intersect — diverging tails may carry different links.
    a = _entry("a.md", 3, "Acme's plan is [[Enterprise]]",
               {"Clients/Acme.md", "Plans/Enterprise.md"})
    b = _entry("b.md", 8, "Acme's plan is [[Growth]]",
               {"Clients/Acme.md", "Plans/Growth.md"})
    assert [k for k, *_ in find_fact_conflicts([a, b])] == ["conflict"]


def test_conflict_colon_marker():
    a = _entry("a.md", 3, "Acme plan: Enterprise", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "Acme plan: Growth", {"Clients/Acme.md"})
    assert [k for k, *_ in find_fact_conflicts([a, b])] == ["conflict"]


def test_additive_verbs_stay_silent():
    # Both can be true at once — "hired" is not a single-valued attribute.
    a = _entry("a.md", 3, "Acme hired [[Bob]]", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "Acme hired [[Carol]]", {"Clients/Acme.md"})
    assert find_fact_conflicts([a, b]) == []


def test_closed_facts_never_participate():
    a = _entry("a.md", 3, "Acme's plan is Enterprise", {"Clients/Acme.md"},
               until="2026-01-31")
    b = _entry("b.md", 8, "Acme's plan is Growth", {"Clients/Acme.md"})
    assert find_fact_conflicts([a, b]) == []


def test_prefix_of_pair_is_skipped():
    # One tail empty — a prefix-of relationship, not a conflict.
    a = _entry("a.md", 3, "Acme's plan is", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "Acme's plan is Growth", {"Clients/Acme.md"})
    assert find_fact_conflicts([a, b]) == []


def test_bare_marker_needs_a_preceding_token():
    a = _entry("a.md", 3, "is Enterprise", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "is Growth", {"Clients/Acme.md"})
    assert find_fact_conflicts([a, b]) == []


def test_disjoint_keys_stay_silent():
    a = _entry("a.md", 3, "the plan is Enterprise", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "the plan is Growth", {"Clients/Initech.md"})
    assert find_fact_conflicts([a, b]) == []


def test_pairs_are_deterministic_and_ordered():
    a = _entry("a.md", 3, "Acme's plan is Enterprise", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "Acme's plan is Growth", {"Clients/Acme.md"})
    c = _entry("c.md", 2, "Acme's plan is Starter", {"Clients/Acme.md"})
    out = find_fact_conflicts([c, b, a])
    assert out == [("conflict", a, b), ("conflict", a, c), ("conflict", b, c)]


def test_subject_copula_pairs_stay_silent():
    # "<Entity> is X" facts are predications that accumulate — both true.
    a = _entry("a.md", 3, "Acme is a client", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "Acme is headquartered in Boston", {"Clients/Acme.md"})
    assert find_fact_conflicts([a, b]) == []


def test_terse_copula_conflict_is_a_known_recall_trade():
    # Cost of the predication guard: two-token "X is A"/"X is B" goes silent.
    a = _entry("a.md", 3, "Acme is Enterprise", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "Acme is Growth", {"Clients/Acme.md"})
    assert find_fact_conflicts([a, b]) == []


def test_equals_marker_needs_only_one_preceding_token():
    # "=" is an unambiguous slot marker — the predication guard is for copulas.
    a = _entry("a.md", 3, "renewal = 2026-03", {"Clients/Acme.md"})
    b = _entry("b.md", 8, "renewal = 2027-03", {"Clients/Acme.md"})
    assert [k for k, *_ in find_fact_conflicts([a, b])] == ["conflict"]
