from pathlib import Path

import pytest

from brain.schemas import Org, Person, SpaceRule

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
