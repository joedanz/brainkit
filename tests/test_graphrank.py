import random

import pytest

from brain.graphrank import ppr

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
