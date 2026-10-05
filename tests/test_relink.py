import random

import pytest

from brain.compiler import WIKILINK_RE
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


STEMS = ["alpha", "beta", "gamma", "alpha beta"]
DIRS = ["Company", "Company/Sub", "Teams/ops", "People/zed"]


def _random_tree(rng):
    paths: set[str] = set()
    for _ in range(rng.randint(3, 7)):
        paths.add(f"{rng.choice(DIRS)}/{rng.choice(STEMS)}.md")
    paths = sorted(paths)

    def link():
        target = rng.choice(paths)
        stem = target.rsplit("/", 1)[1][:-3]
        form = rng.choice(["stem", "stem#h", "stem|l", "embed", "path",
                           "pathmd", "alias", "missing", "padded"])
        return {
            "stem": f"[[{stem}]]", "stem#h": f"[[{stem}#Sec]]",
            "stem|l": f"[[{stem}|lbl]]", "embed": f"![[{stem}]]",
            "path": f"[[{target[:-3]}]]", "pathmd": f"[[{target}]]",
            "alias": "[[Zed Corp]]", "missing": "[[Nowhere]]",
            "padded": f"[[ {stem} ]]",
        }[form]

    texts = {p: " ".join(link() for _ in range(rng.randint(0, 4))) + "\n"
             for p in paths}
    texts[paths[0]] = ENTITY + texts[paths[0]]
    return texts, paths


def _resolutions(texts, maps):
    return {p: [resolve(m.group(1).strip(), *maps) for m in WIKILINK_RE.finditer(t)]
            for p, t in texts.items()}


def _assert_same_notes(before_texts, after_texts, old, new, rename):
    before = _resolutions(before_texts, build_maps(before_texts))
    after_maps = build_maps(after_texts)
    for path in before_texts:
        post = after_texts[rename(path)]
        links = list(WIKILINK_RE.finditer(post))
        assert len(links) == len(before[path])  # no link added or dropped
        for was, m in zip(before[path], links, strict=True):
            if was is None:
                continue
            assert resolve(m.group(1).strip(), *after_maps) == (new if was == old else was)


@pytest.mark.parametrize("mode", ["move", "heal"])
def test_every_link_that_resolved_still_reaches_the_same_note(mode):
    rng = random.Random(20261005)
    checked = 0
    for _ in range(400):
        texts, paths = _random_tree(rng)
        old = rng.choice(paths)
        new = f"{rng.choice(DIRS)}/{rng.choice([*STEMS, 'delta', 'delta two'])}.md"
        if new in texts:
            continue
        rename = lambda p, old=old, new=new: new if p == old else p
        current = ({rename(p): t for p, t in texts.items()}
                   if mode == "heal" else dict(texts))
        try:
            plan = plan_relink(current, old, new)
        except RelinkError:
            continue  # a refusal is a correct answer for a colliding name
        applied = ({rename(p): t for p, t in texts.items()}
                   if mode == "move" else dict(current))
        applied.update(plan.edits)
        _assert_same_notes(texts, applied, old, new, rename)
        checked += 1
    assert checked > 100, "the generator refused too often to prove anything"


def test_a_link_is_not_rewritten_when_some_reader_cannot_see_its_target():
    # master-wide, bare [[X]] reaches alpha/X; in beta's vault it reaches
    # beta/X. Rewriting beta's note to alpha's new name would break that link
    # and name a note beta's readers cannot see.
    texts = {
        "Teams/alpha/X.md": "# alpha\n",
        "Teams/beta/X.md": "# beta\n",
        "Teams/beta/Note.md": "see [[X]]\n",
        "Teams/alpha/Hub.md": "see [[X]]\n",
    }

    def can_see(source, target):
        return source.rsplit("/", 1)[0] == target.rsplit("/", 1)[0]

    plan = plan_relink(texts, "Teams/alpha/X.md", "Teams/alpha/Q.md", can_see=can_see)
    assert plan.edits == {"Teams/alpha/Hub.md": "see [[Q]]\n"}
    assert plan.skipped == 1


def test_a_heal_is_allowed_when_the_other_owner_of_the_old_name_sorts_first():
    # bare [[X]] never reached the old note (Company/a/X.md sorts before it),
    # so there is nothing ambiguous to heal and a re-run is not refused
    texts = {
        "Company/a/X.md": "# a\n", "Company/c/Y.md": "# y\n",
        "Company/Hub.md": "[[X]] [[Company/b/X]]\n",
    }
    plan = _plan(texts, "Company/b/X.md", "Company/c/Y.md")
    assert plan.edits == {"Company/Hub.md": "[[X]] [[Company/c/Y]]\n"}
