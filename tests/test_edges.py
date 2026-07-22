"""Typed-edge extraction, mining, inverses, and traversal (see edges.py)."""

from brain.edges import (
    INVERSE, RELATION_KEYS, W_EXPLICIT, W_MINED, explicit_edges,
)


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
