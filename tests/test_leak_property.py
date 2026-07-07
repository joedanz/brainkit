"""Property test: no compiled vault ever contains a file outside the person's
readable spaces. This is the product's core security claim.

The skip-list is manifest-based, not name-based: only files the compiler
itself declares as generated (plus the manifest) are exempt from the space
check. A REAL AGENTS.md leaked from an unreadable space is NOT in the
manifest's generated list, so it trips the assertion — the honeypots planted
by random_world in every People/<pid> and one Teams/<t> space prove this
path is armed.
"""

import json
import random
import subprocess
from pathlib import Path

from brain.compiler import MANIFEST_NAME, compile_all, compile_vault
from brain.resolver import readable_spaces, space_of_path
from brain.schemas import Org, Person, SpaceRule

RULES = (
    SpaceRule("Company", read=("everyone",), write=("role:admin",)),
    SpaceRule("Teams/*", read=("team:{name}",), write=("team:{name}",)),
    SpaceRule("People/*", read=("person:{name}",), write=("person:{name}",)),
    SpaceRule("Clients/*", read=("everyone",), write=("role:admin",)),
)


def random_world(rng: random.Random, root: Path) -> Org:
    teams = [f"team{i}" for i in range(rng.randint(1, 4))]
    people = {}
    for i in range(rng.randint(2, 8)):
        pid = f"p{i}"
        people[pid] = Person(
            id=pid,
            name=f"Person {i}",
            roles=("admin",) if rng.random() < 0.3 else (),
            teams=tuple(rng.sample(teams, k=rng.randint(0, len(teams)))),
        )
    spaces = ["Company"] + [f"Teams/{t}" for t in teams]
    spaces += [f"People/{pid}" for pid in people]
    spaces += [f"Clients/c{i}" for i in range(rng.randint(0, 3))]
    for space in spaces:
        for j in range(rng.randint(1, 4)):
            f = root / space / f"note{j}.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"content of {space}/note{j}\n")
    # Honeypots: a REAL AGENTS.md in every person's space and one team space.
    # In the vault owner's own People space it is overwritten by generation
    # (and listed as generated) — correct. In OTHER people's spaces and
    # unjoined teams it must simply never appear; if it leaked, the
    # manifest-based skip would NOT exempt it and the assertion trips.
    for pid in people:
        (root / "People" / pid / "AGENTS.md").write_text("leaked server note\n")
    (root / "Teams" / teams[0] / "AGENTS.md").write_text("leaked server note\n")
    (root / "_meta").mkdir(exist_ok=True)
    (root / "_meta/secret.yaml").write_text("secret: true\n")
    return Org(people=people)


def assert_vault_has_no_leaks(master: Path, person: Person, out: Path) -> None:
    """Every file in the vault must live in a readable space, unless the
    compiler's own manifest declares it generated (or it is the manifest)."""
    allowed = set(readable_spaces(master, person, RULES))
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    skip = set(manifest["generated"]) | {MANIFEST_NAME}
    for f in out.rglob("*"):
        if not f.is_file() or ".git" in f.parts:
            continue
        rel = f.relative_to(out).as_posix()
        if rel in skip:
            continue
        space = space_of_path(rel)
        assert space in allowed, (
            f"LEAK person={person.id}: {rel} (space={space})"
        )


def test_no_leak_across_random_worlds(tmp_path: Path):
    for seed in range(10):
        rng = random.Random(seed)
        master = tmp_path / f"master{seed}"
        master.mkdir()
        org = random_world(rng, master)
        for person in org.people.values():
            out = tmp_path / f"out{seed}" / person.id
            compile_vault(master, person, RULES, out)
            assert_vault_has_no_leaks(master, person, out)


def test_compile_all_creates_git_repos(tmp_path: Path):
    rng = random.Random(42)
    master = tmp_path / "master"
    master.mkdir()
    org = random_world(rng, master)
    out_root = tmp_path / "compiled"
    results = compile_all(master, org, RULES, out_root)
    assert {r.person_id for r in results} == set(org.people)
    # The product-level entry point must uphold the leak boundary too.
    for person in org.people.values():
        assert_vault_has_no_leaks(master, person, out_root / person.id)
    some = out_root / next(iter(org.people))
    assert (some / ".git").is_dir()
    log = subprocess.run(
        ["git", "-C", str(some), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "compile:" in log
    # Second compile with no master changes: no new commit
    compile_all(master, org, RULES, out_root)
    log2 = subprocess.run(
        ["git", "-C", str(some), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert log.count("\n") == log2.count("\n")
