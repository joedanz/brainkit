"""The Pages tab's folder tree, built from the sqlite index alone.

Nothing walks the filesystem: the index holds exactly the notes the lens may
read (the compiler put them there), so a tree over `files` cannot list a
page the reader would refuse — and cannot list one the index has not seen.
"""
from dataclasses import asdict

from brain.compiler import compile_vault
from brain.indexer import build_index
from brain.tree import build_tree
from tests.conftest import ALICE, RULES


def _indexed(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    return vault


def _dir(parent, name):
    return next(d for d in parent.dirs if d.name == name)


def test_tree_shape_order_and_counts(master, tmp_path):
    (master / "Company/Decisions/Amendment No. 3.md").write_text("x\n")
    (master / "Company/Decisions/an early one.md").write_text("x\n")
    vault = _indexed(master, tmp_path)
    root = build_tree(vault)
    assert (root.name, root.path, root.pages) == ("", "", [])
    assert [d.name for d in root.dirs] == ["Clients", "Company", "People", "Teams"]   # casefold order
    company = _dir(root, "Company")
    decisions = _dir(company, "Decisions")
    assert decisions.path == "Company/Decisions"
    # casefold sort, and a period in a title is not an extension
    assert [p.title for p in decisions.pages] == ["Amendment No. 3", "an early one", "Big Deal Decision"]
    assert [p.rel_path for p in company.pages] == ["Company/Home.md"]   # dirs before pages, in the payload shape
    assert decisions.count == 3 and company.count == 4
    assert root.count == 7   # 4 Company + acme Overview + alice Memory + sales Q3
    assert all(len(p.mtime) == 10 for p in decisions.pages)
    d = asdict(root)
    assert set(d) == {"name", "path", "dirs", "pages", "count"}
    assert set(d["dirs"][0]["dirs"][0]["pages"][0]) == {"rel_path", "title", "mtime"}


def test_tree_shows_only_what_the_index_holds(master, tmp_path):
    vault = _indexed(master, tmp_path)
    (vault / "Company" / "Unindexed.md").write_text("on disk, not indexed\n")
    root = build_tree(vault)
    rels = [p.rel_path for p in _dir(root, "Company").pages]
    assert "Company/Unindexed.md" not in rels
    # The compiled vault carries protocol files on disk (AGENTS.md, Map.md,
    # per-space AGENTS.md) that the index never held; none of them appear.
    flat = []
    def walk(d):
        flat.extend(p.rel_path for p in d.pages)
        for c in d.dirs:
            walk(c)
    walk(root)
    assert not any(r.endswith(("AGENTS.md", "CLAUDE.md", "Map.md")) for r in flat)
    # And nothing bob-only: the lens is alice's compiled slice.
    assert not any(r.startswith("People/bob") for r in flat)


def test_tree_without_index_is_an_empty_root(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    assert asdict(build_tree(vault)) == {"name": "", "path": "", "dirs": [], "pages": [], "count": 0}
    assert not (vault / ".brain" / "index.db").exists()   # building the tree created nothing
