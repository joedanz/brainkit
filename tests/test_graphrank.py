import random

import pytest

from brain.graphrank import build_graph, extract_seeds, ppr
from brain.store import IndexStore

ALPHA = 0.85


def test_ppr_two_node_analytic():
    # A—B, seed A. Stationary solution: p_A = 1/(1+α), p_B = α/(1+α).
    adj = {"A": {"B": 1.0}, "B": {"A": 1.0}}
    ranked = dict(ppr(adj, {"A": 1.0}))
    assert ranked["A"] == pytest.approx(1 / (1 + ALPHA), abs=1e-6)
    assert ranked["B"] == pytest.approx(ALPHA / (1 + ALPHA), abs=1e-6)


def test_ppr_path_multiplicity_beats_single_path():
    # Diamond: S—B—D and S—C—D (two paths S→D) vs chain S—E—F (one path S→F).
    # D and F are both two hops from the seed; D must outscore F.
    adj = {}
    for a, b in [("S", "B"), ("S", "C"), ("B", "D"), ("C", "D"),
                 ("S", "E"), ("E", "F")]:
        adj.setdefault(a, {})[b] = 1.0
        adj.setdefault(b, {})[a] = 1.0
    scores = dict(ppr(adj, {"S": 1.0}))
    assert scores["D"] > scores["F"]


def test_ppr_deterministic_under_insertion_order():
    edges = [("A", "B"), ("B", "C"), ("C", "D"), ("A", "D"), ("B", "D")]
    seed_items = [("A", 1.0), ("C", 0.5)]
    results = []
    for seed in (0, 1, 2):
        rng = random.Random(seed)
        shuffled = edges[:]
        rng.shuffle(shuffled)
        adj = {}
        for a, b in shuffled:
            adj.setdefault(a, {})[b] = 1.0
            adj.setdefault(b, {})[a] = 1.0
        # Also shuffle the seeds dict insertion order
        seed_shuffled = seed_items[:]
        rng.shuffle(seed_shuffled)
        seeds = {n: w for n, w in seed_shuffled}
        results.append(ppr(adj, seeds))
    assert results[0] == results[1] == results[2]  # bit-identical


def test_ppr_dangling_mass_returns_to_seeds():
    # Orphan is an isolated seed; Linked shares a component with the main
    # seed. Retention would let Orphan hoard its 0.25 of seed mass and
    # outrank Linked; teleport-to-seeds must keep Linked well above it.
    adj = {"Home": {"Linked": 1.0, "Other": 1.0},
           "Linked": {"Home": 1.0}, "Other": {"Home": 1.0}}
    scores = dict(ppr(adj, {"Home": 1.0, "Linked": 0.5, "Orphan": 0.5}))
    assert scores["Linked"] > scores["Orphan"]


def test_ppr_zero_score_nodes_excluded():
    # B's component is unreachable from the seed and unseeded → absent.
    adj = {"A": {}, "B": {"C": 1.0}, "C": {"B": 1.0}}
    ranked = ppr(adj, {"A": 1.0})
    names = [n for n, _ in ranked]
    assert "B" not in names and "C" not in names and "A" in names


def test_ppr_empty_seeds_returns_empty():
    assert ppr({"A": {"B": 1.0}, "B": {"A": 1.0}}, {}) == []


def test_build_graph_edges_weights_and_no_self_loops(tmp_path):
    from brain.chunker import Chunk
    from brain.facts import Fact

    store = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    mk = lambda rel: [Chunk(rel_path=rel, space="Company", heading_path="",
                            pos=0, text="body")]
    fact = lambda line, targets: (Fact(line=line, statement="s",
                                       from_date="2026-01-01", until_date=None,
                                       sources=[], targets=[]), targets)
    # A wikilinks B, itself, and a note missing from the vault; A's facts
    # co-mention (A,B) once, (B,C) twice, and C with itself; A also has a
    # typed edge to D (a mined co-provenance pair) to prove typed sources
    # participate in the same self-loop/symmetry invariant.
    store.add_file("A.md", "s1", "Company", mk("A.md"), ["c1"], None,
                   links=[("B.md", 1), ("A.md", 1), ("Missing.md", 0)],
                   facts=[fact(1, ["A.md", "B.md"]),
                          fact(2, ["B.md", "C.md"]),
                          fact(3, ["B.md", "C.md"]),
                          fact(4, ["C.md", "C.md"])])
    store.add_file("B.md", "s2", "Company", mk("B.md"), ["c2"], None)
    store.add_file("C.md", "s3", "Company", mk("C.md"), ["c3"], None)
    store.add_file("D.md", "s4", "Company", mk("D.md"), ["c4"], None)
    store.replace_edges([("A.md", "D.md", "same", "entity", 0.5)])
    adj = build_graph(store)
    store.close()

    # link + fact co-mention on the same pair sum; undirected both ways
    assert adj["A.md"]["B.md"] == 2.0 and adj["B.md"]["A.md"] == 2.0
    # co-mention counts accumulate across facts
    assert adj["B.md"]["C.md"] == 2.0 and adj["C.md"]["B.md"] == 2.0
    # typed edge is undirected too
    assert adj["A.md"]["D.md"] == 0.5 and adj["D.md"]["A.md"] == 0.5
    # self-link, self-co-mention, and missing-target link produce no edges
    assert all(node not in row for node, row in adj.items())
    assert "Missing.md" not in adj and "Missing.md" not in adj["A.md"]


def test_build_graph_stacks_typed_weight_on_link_weight(tmp_path):
    from brain.chunker import Chunk

    # a.md wikilinks b.md (links table) AND declares an explicit typed edge
    # to b.md (edges table, weight 2.0, mirroring what a frontmatter `up:
    # [[b]]` relation produces). Undirected pair weight must be 3.0 both
    # directions: 1.0 for the wikilink plus 2.0 for the explicit edge.
    store = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    mk = lambda rel: [Chunk(rel_path=rel, space="Company", heading_path="",
                            pos=0, text="body")]
    store.add_file("a.md", "s1", "Company", mk("a.md"), ["c1"], None,
                   links=[("b.md", 1)])
    store.add_file("b.md", "s2", "Company", mk("b.md"), ["c2"], None)
    store.replace_edges([("a.md", "b.md", "up", "explicit", 2.0)])
    adj = build_graph(store)
    store.close()

    assert adj["a.md"]["b.md"] == 3.0
    assert adj["b.md"]["a.md"] == 3.0


def test_build_graph_ignores_inverse_rows(tmp_path):
    from brain.chunker import Chunk

    # edges: (a, b, up, explicit, 2.0) and its mirror (b, a, down, inverse,
    # 2.0); no wikilinks. Pair weight must be 2.0, not 4.0 — inverse rows
    # are mirrors already counted via the forward row, not new information.
    store = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    mk = lambda rel: [Chunk(rel_path=rel, space="Company", heading_path="",
                            pos=0, text="body")]
    store.add_file("a.md", "s1", "Company", mk("a.md"), ["c1"], None)
    store.add_file("b.md", "s2", "Company", mk("b.md"), ["c2"], None)
    store.replace_edges([
        ("a.md", "b.md", "up", "explicit", 2.0),
        ("b.md", "a.md", "down", "inverse", 2.0),
    ])
    adj = build_graph(store)
    store.close()

    assert adj["a.md"]["b.md"] == 2.0
    assert adj["b.md"]["a.md"] == 2.0


def test_build_graph_mined_weight(tmp_path):
    from brain.chunker import Chunk

    # A single (a, b, same, entity, 0.5) mined row: pair weight 0.5 both ways.
    store = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    mk = lambda rel: [Chunk(rel_path=rel, space="Company", heading_path="",
                            pos=0, text="body")]
    store.add_file("a.md", "s1", "Company", mk("a.md"), ["c1"], None)
    store.add_file("b.md", "s2", "Company", mk("b.md"), ["c2"], None)
    store.replace_edges([("a.md", "b.md", "same", "entity", 0.5)])
    adj = build_graph(store)
    store.close()

    assert adj["a.md"]["b.md"] == 0.5
    assert adj["b.md"]["a.md"] == 0.5


def _seed_store(tmp_path):
    from brain.chunker import Chunk

    store = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    mk = lambda rel: Chunk(rel_path=rel, space="Company", heading_path="",
                           pos=0, text="body")
    store.add_file("Company/Intel/Acme.md", "s1", "Company",
                   [mk("Company/Intel/Acme.md")], ["c1"], None,
                   entity=("client", ["Acme Corp", "ACME"]))
    store.add_file("Company/Ace.md", "s2", "Company",
                   [mk("Company/Ace.md")], ["c2"], None)
    return store


def test_seeds_word_boundary_and_case(tmp_path):
    store = _seed_store(tmp_path)
    # "ACME" matches the "ACME" alias case-insensitively, anchored to word
    # boundaries within the query text; Ace.md has no registered alias here,
    # so "Ace" as a separate stem doesn't fire and only Acme.md is seeded.
    seeds = extract_seeds("what's the latest ACME news?", store)
    assert seeds == {"Company/Intel/Acme.md": 1.0}
    store.close()


def test_seeds_longest_match_wins(tmp_path):
    store = _seed_store(tmp_path)
    # "acme corp" overlaps "acme": only the longer term claims the span.
    seeds = extract_seeds("update on acme corp pricing", store)
    assert seeds == {"Company/Intel/Acme.md": 1.0}
    # standalone shorter term still matches elsewhere in the query
    seeds2 = extract_seeds("ace vs acme", store)
    assert seeds2 == {"Company/Ace.md": 1.0, "Company/Intel/Acme.md": 1.0}
    store.close()


def test_seeds_center_and_text_hits(tmp_path):
    store = _seed_store(tmp_path)
    seeds = extract_seeds("no entities here", store,
                          center="Company/Home.md",
                          text_hit_files=["Company/Ace.md", "Company/Home.md"])
    # center at 1.0 beats its own 0.5 text-hit weight; plain text hit at 0.5
    assert seeds == {"Company/Home.md": 1.0, "Company/Ace.md": 0.5}
    store.close()


def test_seeds_empty_when_nothing_matches(tmp_path):
    store = _seed_store(tmp_path)
    assert extract_seeds("zzz qqq", store) == {}
    store.close()
