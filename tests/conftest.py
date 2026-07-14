import sqlite3
from pathlib import Path

import pytest

from brain import store
from brain.schemas import Org, Person, SpaceRule


def _vectors_available() -> bool:
    """True iff sqlite-vec actually loads on this interpreter. On a Python built
    without loadable-extension support (e.g. the python.org macOS installer) this
    is False and the store degrades to keyword-only — the same seam production
    uses (``store._try_load_vec``), so the guard tracks real behavior."""
    conn = sqlite3.connect(":memory:")
    try:
        return store._try_load_vec(conn)
    finally:
        conn.close()


VECTORS_AVAILABLE = _vectors_available()

requires_vectors = pytest.mark.skipif(
    not VECTORS_AVAILABLE,
    reason="sqlite-vec extension unavailable — vector/semantic behavior can't be exercised",
)


@pytest.fixture(autouse=True)
def _no_ambient_provider(monkeypatch, tmp_path):
    """No test should pick up a real embedding provider from the ambient env.
    Hoisted here so every test runs against a known keyword-or-configured state;
    tests that want a provider set the vars explicitly after this runs."""
    for var in ("BRAIN_EMBED_BASE_URL", "BRAIN_EMBED_API_KEY", "BRAIN_EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "no-config.yaml"))

RULES = (
    SpaceRule("Company", read=("everyone",), write=("role:admin",)),
    SpaceRule("Teams/*", read=("team:{name}",), write=("team:{name}",)),
    SpaceRule("People/*", read=("person:{name}",), write=("person:{name}",)),
    SpaceRule("Clients/*", read=("everyone",), write=("role:admin",)),
)

ALICE = Person(id="alice", name="Alice Nguyen", roles=("admin",), teams=("sales",))
BOB = Person(id="bob", name="Bob Rivera", roles=(), teams=("ops",))
ORG = Org(people={"alice": ALICE, "bob": BOB})


@pytest.fixture
def master(tmp_path: Path) -> Path:
    m = tmp_path / "master"
    files = {
        "Company/Home.md": "# Home\nSee [[Big Deal Decision]] and [[Q3 Pipeline]].\n",
        "Company/Decisions/Big Deal Decision.md": "We chose option A.\n",
        "Teams/sales/Q3 Pipeline.md": "Q3 pipeline.\n",
        "Teams/ops/Runbook.md": "Ops runbook.\n",
        "People/alice/Memory.md": "Alice private memory.\n",
        "People/bob/Memory.md": "Bob private memory.\n",
        "People/bob/Sessions/Bob Private Note.md": "Bob only.\n",
        "Clients/acme/Overview.md": "Acme overview.\n",
        "_meta/org.yaml": "people: {}\n",
        "AGENTS.md": "# Assistant protocol (server only)\n",
    }
    for rel, content in files.items():
        p = m / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (m / "_meta/promotions/pending").mkdir(parents=True)
    return m
