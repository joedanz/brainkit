"""Typed-edge extraction, mining, inverses, and traversal (see edges.py)."""

from pathlib import Path

from brain.compiler import compile_vault
from brain.edges import (
    INVERSE, RELATION_KEYS, W_EXPLICIT, W_MINED, date_edges, entity_edges,
    explicit_edges, folder_edges, note_date, with_inverses,
)
from brain.indexer import build_index
from brain.store import IndexStore
from tests.conftest import ALICE, RULES


def _resolve(known: dict[str, str]):
    """Test resolver: raw target -> rel path via `known`, else unresolved."""
    return lambda targets: [
        (known[t], 1) if t in known else (t, 0) for t in targets
    ]


def test_relation_constants():
    assert RELATION_KEYS == ("down", "next", "prev", "same", "up")
    assert INVERSE == {"up": "down", "down": "up", "same": "same",
                       "prev": "next", "next": "prev"}
    assert (W_EXPLICIT, W_MINED) == (2.0, 0.5)


def test_explicit_edges_multi_target_and_unresolved():
    meta = {
        "title": "PPR Retrieval",
        "up": "[[Retrieval]]",
        "same": "[[HippoRAG notes]], [[Nowhere]]",
        "author": "[[Retrieval]]",  # not a relation key: ignored
    }
    resolve = _resolve({"Retrieval": "Company/Retrieval.md",
                        "HippoRAG notes": "Company/HippoRAG notes.md"})
    assert explicit_edges("Company/PPR.md", meta, resolve) == [
        ("Company/PPR.md", "Company/HippoRAG notes.md", "same", "explicit", 2.0),
        ("Company/PPR.md", "Company/Retrieval.md", "up", "explicit", 2.0),
    ]


def test_explicit_edges_skip_self_and_empty_values():
    meta = {"up": "[[Me]]", "next": "", "prev": "no wikilink here"}
    resolve = _resolve({"Me": "Company/Me.md"})
    assert explicit_edges("Company/Me.md", meta, resolve) == []


def test_note_date_filename_wins_over_frontmatter():
    assert note_date("Inbox/2026-07-21-standup.md", {"date": "2025-01-01"}) == "2026-07-21"
    assert note_date("Inbox/standup.md", {"date": "2026-07-21T09:00"}) == "2026-07-21"
    assert note_date("Inbox/standup.md", {"captured": "2026-07-20"}) == "2026-07-20"
    assert note_date("Inbox/standup.md", {}) is None
    assert note_date("Inbox/standup.md", {"date": "July 21"}) is None


def test_folder_edges_index_note_convention():
    files = ["Clients/Acme/Acme.md", "Clients/Acme/Kickoff.md",
             "Clients/Acme/Notes.md", "Clients/Beta/Kickoff.md", "Top.md"]
    assert folder_edges(files) == [
        ("Clients/Acme/Kickoff.md", "Clients/Acme/Acme.md", "up", "folder", 0.5),
        ("Clients/Acme/Notes.md", "Clients/Acme/Acme.md", "up", "folder", 0.5),
    ]  # Beta has no index note; the index note itself and root files get nothing


def test_date_edges_chain_per_folder_sorted():
    dated = {
        "Inbox/2026-07-22-b.md": "2026-07-22",
        "Inbox/2026-07-20-a.md": "2026-07-20",
        "Other/2026-07-21-x.md": "2026-07-21",  # different folder: own chain
    }
    assert date_edges(dated) == [
        ("Inbox/2026-07-20-a.md", "Inbox/2026-07-22-b.md", "next", "date", 0.5),
    ]


def test_entity_edges_pairwise_canonical():
    entities = [("Clients/Beta.md", "client"), ("Clients/Acme.md", "client"),
                ("People/Jo.md", "person"), ("Notes/x.md", "")]
    assert entity_edges(entities) == [
        ("Clients/Acme.md", "Clients/Beta.md", "same", "entity", 0.5),
    ]  # singleton and empty-type groups produce nothing


def test_with_inverses_mirrors_every_edge():
    edges = [("a.md", "b.md", "up", "explicit", 2.0),
             ("c.md", "d.md", "next", "date", 0.5)]
    assert with_inverses(edges) == edges + [
        ("b.md", "a.md", "down", "inverse", 2.0),
        ("d.md", "c.md", "prev", "inverse", 0.5),
    ]


def test_with_inverses_dedupes_colliding_mirrors_keeping_max_weight():
    # explicit a-up->b and folder a-up->b both mirror onto (b, a, down):
    # one inverse row survives, with the higher weight.
    edges = [("a.md", "b.md", "up", "explicit", 2.0),
             ("a.md", "b.md", "up", "folder", 0.5)]
    assert with_inverses(edges) == edges + [
        ("b.md", "a.md", "down", "inverse", 2.0),
    ]


def _projects_vault(master: Path) -> None:
    (master / "Company/Projects").mkdir(parents=True, exist_ok=True)
    (master / "Company/Projects/Projects.md").write_text("# Projects\n")
    (master / "Company/Projects/Alpha.md").write_text(
        "---\nup: [[Beta]]\n---\n# Alpha\n")
    (master / "Company/Projects/Beta.md").write_text(
        "---\nentity: project\n---\n# Beta\n")
    (master / "Company/Projects/Gamma.md").write_text(
        "---\nentity: project\n---\n# Gamma\n")


def test_edges_rebuild_is_deterministic(master, tmp_path):
    _projects_vault(master)
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)

    def _rows():
        s = IndexStore.open_readonly(vault / ".brain/index.db", want_vectors=False)
        rows = s.conn.execute(
            "SELECT src_rel_path, dst_rel_path, rel, provenance, weight FROM edges "
            "ORDER BY src_rel_path, dst_rel_path, rel, provenance"
        ).fetchall()
        s.close()
        return rows

    first = _rows()
    assert first  # sanity: edges actually exist

    build_index(vault, provider=None, cache=None)  # second run, nothing changed
    second = _rows()
    assert first == second
