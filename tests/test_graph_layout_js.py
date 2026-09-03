"""The 3D graph layout must stay inside the range a GPU can hold.

This is the one browser asset with a numerical failure mode, and it had one:
the integrator placed no limit on how far a node may travel in a single pass,
so the layout ran away. Measured against a real 300-note vault, reach reached
1.2e47 — every value FINITE, so `layout()`'s own non-finite guard never fired.
three.js then narrows positions to float32 for the GPU
(Float32BufferAttribute), where anything past 3.4e38 becomes Infinity; 850 of
900 coordinates did, and the 3D tab rendered an empty frame with nothing in the
console.

Testing it needs a JS engine, and this repo has no JS runner — so node imports
the shipped module and calls the shipped function, which asserts against the
code that actually ships rather than a Python re-implementation of it. Node is
preinstalled on GitHub's ubuntu-latest runners, so this gate runs in CI rather
than quietly skipping there.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from functools import cache

import pytest

from tests.conftest import ASSETS

GRAPH3D = ASSETS / "js" / "graph" / "layout3d.js"
FLOAT32_MAX = 3.4028235e38

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped JS")


def _run(n: int, *, spokes: bool, report: str) -> dict:
    """Settle an n-node graph with the shipped layout() and report on it.

    `report` is a JS object literal evaluated with `pos` and `reach` in scope,
    so each test asks its own question of the same run.
    """
    driver = f"""
const {{ layout }} = await import({json.dumps(GRAPH3D.as_uri())});
const n = {n};
const nodes = Array.from({{length: n}}, (_, i) => ({{id: i, degree: 0}}));
const edges = [];
// A hub with spokes, which is what a real vault looks like: the busiest note in
// embark's carries 192 of 892 links. Uniform synthetic degrees never reproduced
// the runaway, which is why nothing caught it.
for (let i = 1; i < n; i++) edges.push({{source: 0, target: i}});
{"for (let i = 2; i < n; i++) edges.push({source: i, target: i % 7});" if spokes else ""}
const pos = layout({{nodes, edges}});
const reach = pos.reduce((m, p) => Math.max(m, Math.hypot(p.x, p.y, p.z)), 0);
console.log(JSON.stringify({report}));
"""
    out = subprocess.run(
        ["node", "--input-type=module", "-e", driver],
        capture_output=True, text=True, check=True, timeout=180,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@cache
def _reach(n: int) -> float:
    """The layout is deterministic (the seed is Math.sin, no RNG), so the two
    tests below asking about the same size are one node run, not two."""
    result = _run(n, spokes=True, report="{reach, count: pos.length}")
    assert result["count"] == n
    return float(result["reach"])


@pytest.mark.parametrize("n", [200, 300, 1000])
def test_layout_stays_inside_float32(n: int) -> None:
    """Reach measured before the fix: 1.1e47 at 200, 1.2e47 at 300, 4.6e72 at 1000."""
    reach = _reach(n)
    assert reach < FLOAT32_MAX, f"{n} nodes settle to {reach:.2e}; float32 caps at {FLOAT32_MAX:.2e}"


@pytest.mark.parametrize("n", [200, 300, 1000])
def test_layout_is_bounded_by_construction(n: int) -> None:
    """Not merely under the float32 ceiling — bounded.

    Every node starts inside the seed spread (about 200 from the origin) and may
    move at most a capped step per pass, so reach is bounded by the iteration
    count whatever the graph. A value anywhere near the ceiling would mean the
    cap had been removed and the layout was merely getting lucky.
    """
    assert _reach(n) < 1e5


def test_layout_still_produces_a_shape() -> None:
    """A bound that collapsed every node onto one point would pass the above."""
    result = _run(60, spokes=False, report="{distinct: new Set(pos.map(p => p.x.toFixed(3))).size}")
    assert result["distinct"] > 1


def test_displacement_cap_is_preserved_verbatim() -> None:
    """The cap is the only bound in the integrator (see the module docstring).
    Moving the function must carry the line and the reasoning above it."""
    src = GRAPH3D.read_text(encoding="utf-8")
    assert "const step = 50 * alpha + 1;" in src
    assert "It is NOT the repulsion running away, which is the natural guess." in src
    assert "reach becomes bounded by construction" in src
