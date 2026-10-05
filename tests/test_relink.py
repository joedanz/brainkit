import pytest

from brain.doctor import _resolve_target
from brain.indexer import _resolve_links
from brain.relink import RelinkError, build_maps, plan_relink, resolve

ENTITY = "---\nentity: client\naliases: [Zed Corp, ZED]\n---\n# Zed\n"

TEXTS = {
    "Company/Alpha.md": "# Alpha\n",
    "Company/Sub/Alpha.md": "# Alpha again\n",
    "Teams/ops/Beta Two.md": "# Beta\n",
    "Clients/zed/Zed.md": ENTITY,
}

TARGETS = [
    "Alpha", "alpha", "ALPHA.md", "Company/Alpha", "Company/Alpha.md",
    "Company/Sub/Alpha", "Nowhere/Alpha", "Beta Two", "beta two",
    "Teams/ops/Beta Two", "Zed Corp", "zed corp", "ZED", "Missing",
    "Elsewhere/Missing", "Zed",
]


def test_duplicate_stems_resolve_to_the_first_sorted_path():
    paths, by_stem, _ = build_maps(TEXTS)
    assert by_stem["alpha"] == "Company/Alpha.md"
    assert paths == set(TEXTS)


def test_entity_aliases_resolve_after_stems_miss():
    maps = build_maps(TEXTS)
    assert resolve("Zed Corp", *maps) == "Clients/zed/Zed.md"
    assert resolve("zed", *maps) == "Clients/zed/Zed.md"  # by stem
    assert resolve("Missing", *maps) is None


@pytest.mark.parametrize("target", TARGETS)
def test_resolve_agrees_with_the_indexer(target):
    paths, by_stem, by_alias = build_maps(TEXTS)
    ((hit, ok),) = _resolve_links([target], paths, by_stem, by_alias)
    assert resolve(target, paths, by_stem, by_alias) == (hit if ok else None)


@pytest.mark.parametrize("target", TARGETS)
def test_resolve_agrees_with_the_doctor_without_aliases(target):
    paths, by_stem, _ = build_maps(TEXTS)
    assert resolve(target, paths, by_stem, {}) == _resolve_target(target, paths, by_stem)


def _plan(texts, old, new):
    return plan_relink(texts, old, new)


def test_move_rewrites_name_links_and_keeps_heading_label_and_embed():
    texts = {
        "Company/Old.md": "# Old\n",
        "Company/Hub.md": "See [[Old]], [[Old#Sec|the label]] and ![[Old]].\n",
    }
    plan = _plan(texts, "Company/Old.md", "Company/New.md")
    assert plan.mode == "move"
    assert plan.edits == {
        "Company/Hub.md": "See [[New]], [[New#Sec|the label]] and ![[New]].\n"}
    assert plan.links_rewritten == 3


def test_move_keeps_the_path_style_of_each_link():
    texts = {
        "Company/Old.md": "# Old\n",
        "Company/Hub.md": "[[Company/Old]] and [[Company/Old.md]]\n",
    }
    plan = _plan(texts, "Company/Old.md", "Company/New.md")
    assert plan.edits["Company/Hub.md"] == "[[Company/New]] and [[Company/New.md]]\n"


def test_folder_move_leaves_name_links_and_updates_path_links():
    texts = {
        "Company/Old.md": "# Old\n",
        "Company/Hub.md": "[[Old]] and [[Company/Old]]\n",
    }
    plan = _plan(texts, "Company/Old.md", "Company/Sub/Old.md")
    assert plan.edits["Company/Hub.md"] == "[[Old]] and [[Company/Sub/Old]]\n"


def test_padding_inside_the_brackets_survives():
    texts = {"Company/Old.md": "# Old\n", "Company/Hub.md": "[[ Old ]]\n"}
    plan = _plan(texts, "Company/Old.md", "Company/New.md")
    assert plan.edits["Company/Hub.md"] == "[[ New ]]\n"


def test_links_that_still_reach_the_note_through_an_alias_are_left_alone():
    # the page's aliases move with it, so [[Zed Corp]] and [[Zed]] (the ZED
    # alias, once the stem is gone) keep resolving; only the exact path link
    # needs its text changed
    texts = {
        "Clients/zed/Zed.md": ENTITY,
        "Company/Hub.md": "[[Zed Corp]] [[Zed]] [[Clients/zed/Zed]]\n",
    }
    plan = _plan(texts, "Clients/zed/Zed.md", "Clients/zed/Zed Inc.md")
    assert plan.edits == {
        "Company/Hub.md": "[[Zed Corp]] [[Zed]] [[Clients/zed/Zed Inc]]\n"}


def test_the_moved_note_own_links_are_rewritten_under_its_new_path():
    texts = {"Company/Old.md": "I am [[Old]].\n"}
    plan = _plan(texts, "Company/Old.md", "Company/New.md")
    assert plan.edits == {"Company/New.md": "I am [[New]].\n"}


def test_moving_a_duplicate_stem_winner_pins_bare_links_to_it():
    texts = {
        "A/Dup.md": "# a\n", "B/Dup.md": "# b\n",
        "Hub/Hub.md": "[[Dup]]\n",
    }
    plan = _plan(texts, "A/Dup.md", "C/Dup.md")
    # bare [[Dup]] reached A/Dup; B/Dup would now win, so it is pinned to the move
    assert plan.edits == {"Hub/Hub.md": "[[C/Dup]]\n"}


def test_a_move_that_newly_wins_a_duplicate_stem_pins_the_old_winner():
    texts = {
        "B/Dup.md": "# b\n", "Z/Dup.md": "# z\n",
        "Hub/Hub.md": "[[Dup]]\n",
    }
    plan = _plan(texts, "Z/Dup.md", "A/Dup.md")
    # bare [[Dup]] reached B/Dup; A/Dup would now sort first and capture it
    assert plan.edits == {"Hub/Hub.md": "[[B/Dup]]\n"}


def test_heal_rewrites_links_that_dangle_on_the_old_name():
    texts = {
        "Company/New.md": "# New\n",
        "Company/Hub.md": "[[Old]] [[Company/Old]] [[Nowhere]]\n",
    }
    plan = _plan(texts, "Company/Old.md", "Company/New.md")
    assert plan.mode == "heal"
    assert plan.edits == {"Company/Hub.md": "[[New]] [[Company/New]] [[Nowhere]]\n"}


def test_heal_does_not_touch_links_unrelated_to_the_rename():
    texts = {"Company/New.md": "# New\n", "Company/Hub.md": "[[Other]] [[New]]\n",
             "Company/Other.md": "# o\n"}
    plan = _plan(texts, "Company/Old.md", "Company/New.md")
    assert plan.edits == {}


def test_a_fenced_link_is_rewritten_like_the_indexer_resolves_it():
    texts = {"Company/Old.md": "# Old\n",
             "Company/Hub.md": "```\n[[Old]]\n```\nplain Old text\n"}
    plan = _plan(texts, "Company/Old.md", "Company/New.md")
    assert plan.edits["Company/Hub.md"] == "```\n[[New]]\n```\nplain Old text\n"


def test_a_new_stem_that_is_taken_is_refused():
    texts = {"Company/Old.md": "# o\n", "Teams/ops/New.md": "# n\n"}
    with pytest.raises(RelinkError, match="already"):
        _plan(texts, "Company/Old.md", "Company/New.md")


def test_heal_is_refused_when_the_old_name_now_belongs_to_another_note():
    texts = {"Company/New.md": "# n\n", "Teams/ops/Old.md": "# o\n"}
    with pytest.raises(RelinkError, match="in use"):
        _plan(texts, "Company/Old.md", "Company/New.md")


@pytest.mark.parametrize("texts", [
    {"Company/Old.md": "", "Company/New.md": ""},   # both exist
    {"Company/Other.md": ""},                        # neither exists
])
def test_both_or_neither_present_is_an_error(texts):
    with pytest.raises(RelinkError):
        _plan(texts, "Company/Old.md", "Company/New.md")


def test_planning_again_after_applying_a_move_finds_nothing_to_do():
    texts = {"Company/Old.md": "# Old\n", "Company/Hub.md": "[[Old]]\n"}
    plan = _plan(texts, "Company/Old.md", "Company/New.md")
    after = {("Company/New.md" if p == "Company/Old.md" else p): t
             for p, t in texts.items()}
    after.update(plan.edits)
    again = _plan(after, "Company/Old.md", "Company/New.md")
    assert again.mode == "heal" and again.edits == {}
