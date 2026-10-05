import pytest

from brain.doctor import _resolve_target
from brain.indexer import _resolve_links
from brain.relink import build_maps, resolve

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
