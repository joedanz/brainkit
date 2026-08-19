import os
from pathlib import Path

import pytest

from brain.corrections import (
    CORRECTIONS_LIMIT,
    Correction,
    load_corrections,
    render_corrections,
)


def _write(vault: Path, pid: str, slug: str, text: str) -> None:
    d = vault / "People" / pid / "Corrections"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(text)


def _correction(rule: str, from_date: str | None = "2026-08-19", body: str = "") -> str:
    fm = f"rule: {rule}\n"
    if from_date is not None:
        fm += f"from: {from_date}\n"
    return f"---\n{fm}---\n{body}"


def test_parses_rule_and_date(tmp_path):
    _write(tmp_path, "alice", "no-filler", _correction("Never open with filler."))
    cs = load_corrections(tmp_path, "alice")
    assert cs.rendered == (Correction("no-filler", "Never open with filler.", "2026-08-19"),)
    assert cs.omitted == () and cs.unusable == () and cs.undated == ()


def test_missing_directory_is_empty_not_an_error(tmp_path):
    cs = load_corrections(tmp_path, "alice")
    assert cs.rendered == () and cs.unusable == ()


def test_a_record_without_a_rule_is_unusable_and_never_rendered(tmp_path):
    _write(tmp_path, "alice", "broken", "---\nfrom: 2026-08-19\n---\nI meant to write a rule.")
    cs = load_corrections(tmp_path, "alice")
    assert cs.rendered == ()
    assert cs.unusable == ("broken",)


def test_a_bad_date_still_renders_but_sorts_last(tmp_path):
    # Losing a rule to a typo would be the silent drop this design exists to
    # prevent — so it renders, but it cannot outrank a well-formed rule.
    _write(tmp_path, "alice", "undated", _correction("Rule U.", from_date="last tuesday"))
    _write(tmp_path, "alice", "dated", _correction("Rule D.", from_date="2026-01-01"))
    cs = load_corrections(tmp_path, "alice")
    assert [c.rule for c in cs.rendered] == ["Rule D.", "Rule U."]
    assert cs.undated == ("undated",)


def test_a_missing_date_is_treated_as_undated(tmp_path):
    _write(tmp_path, "alice", "nodate", _correction("Rule N.", from_date=None))
    cs = load_corrections(tmp_path, "alice")
    assert [c.rule for c in cs.rendered] == ["Rule N."]
    assert cs.undated == ("nodate",)


def test_newest_first_with_slug_breaking_ties(tmp_path):
    _write(tmp_path, "alice", "old", _correction("Rule O.", "2026-01-01"))
    _write(tmp_path, "alice", "b-same", _correction("Rule B.", "2026-08-19"))
    _write(tmp_path, "alice", "a-same", _correction("Rule A.", "2026-08-19"))
    cs = load_corrections(tmp_path, "alice")
    assert [c.rule for c in cs.rendered] == ["Rule A.", "Rule B.", "Rule O."]


def test_the_budget_omits_whole_rules_and_never_truncates(tmp_path):
    # Each rule is ~60 chars; a 200-char budget fits some and not others.
    for i in range(10):
        _write(tmp_path, "alice", f"r{i:02d}", _correction(f"Rule number {i} " + "x" * 40, "2026-08-19"))
    cs = load_corrections(tmp_path, "alice", limit=200)
    assert cs.rendered, "some rules should fit"
    assert cs.omitted, "some rules should not fit"
    assert len(cs.rendered) + len(cs.omitted) == 10
    block = render_corrections(cs)
    assert len(block) <= 200
    # Every rendered rule appears in full — no fragment of an omitted one.
    for c in cs.rendered:
        assert c.rule in block
    for c in cs.omitted:
        assert c.rule not in block


def test_render_is_empty_when_there_is_nothing_to_say(tmp_path):
    cs = load_corrections(tmp_path, "alice")
    assert render_corrections(cs) == ""


def test_the_body_never_reaches_the_rendered_block(tmp_path):
    # Structural guard: if a future change starts rendering bodies, this fails
    # rather than quietly tripling the size of every protocol.
    _write(tmp_path, "alice", "voice", _correction(
        "Keep client mail direct.", body="Joe rewrote the Acme draft; SECRETBODY."))
    block = render_corrections(load_corrections(tmp_path, "alice"))
    assert "Keep client mail direct." in block
    assert "SECRETBODY" not in block
    assert "Acme" not in block


requires_nonroot = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses file permissions, so an unreadable file can't be staged")


def test_a_byte_that_is_not_utf8_still_renders_and_never_raises(tmp_path):
    # A Windows-1252 smart quote pasted out of a document. Bare read_text()
    # raised UnicodeDecodeError here, which aborted the whole compile for this
    # person — one bad byte taking down every rule they ever wrote.
    d = tmp_path / "People/alice/Corrections"
    d.mkdir(parents=True)
    (d / "quote.md").write_bytes(
        b"---\nrule: Never say \x93maybe\x94 to a client.\nfrom: 2026-08-19\n---\nwhy\n")
    cs = load_corrections(tmp_path, "alice")
    assert len(cs.rendered) == 1
    assert cs.unreadable == () and cs.unusable == ()
    # The rule survives around the undecodable byte rather than vanishing.
    assert "Never say" in cs.rendered[0].rule and "to a client." in cs.rendered[0].rule
    assert "Never say" in render_corrections(cs)


@requires_nonroot
def test_a_record_the_os_refuses_is_reported_not_fatal(tmp_path):
    _write(tmp_path, "alice", "good", _correction("Keep it direct."))
    _write(tmp_path, "alice", "locked", _correction("Never do that."))
    locked = tmp_path / "People/alice/Corrections/locked.md"
    locked.chmod(0o000)
    try:
        cs = load_corrections(tmp_path, "alice")
    finally:
        locked.chmod(0o644)
    assert cs.unreadable == ("locked",)
    # The readable rule is unaffected: one bad file drops one rule, not all.
    assert [c.rule for c in cs.rendered] == ["Keep it direct."]


def test_default_limit_is_the_documented_one():
    assert CORRECTIONS_LIMIT == 4000
