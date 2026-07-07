"""Property test: no compiled vault ever contains a file outside the person's
readable spaces. This is the product's core security claim."""

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

GENERATED_NAMES = {"AGENTS.md", "CLAUDE.md", MANIFEST_NAME}


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
    (root / "_meta").mkdir(exist_ok=True)
    (root / "_meta/secret.yaml").write_text("secret: true\n")
    return Org(people=people)


def test_no_leak_across_random_worlds(tmp_path: Path):
    for seed in range(10):
        rng = random.Random(seed)
        master = tmp_path / f"master{seed}"
        master.mkdir()
        org = random_world(rng, master)
        for person in org.people.values():
            out = tmp_path / f"out{seed}" / person.id
            compile_vault(master, person, RULES, out)
            allowed = set(readable_spaces(master, person, RULES))
            for f in out.rglob("*"):
                if not f.is_file() or ".git" in f.parts:
                    continue
                rel = str(f.relative_to(out))
                if f.name in GENERATED_NAMES:
                    continue
                space = space_of_path(rel)
                assert space in allowed, (
                    f"LEAK seed={seed} person={person.id}: {rel} (space={space})"
                )


def test_compile_all_creates_git_repos(tmp_path: Path):
    rng = random.Random(42)
    master = tmp_path / "master"
    master.mkdir()
    org = random_world(rng, master)
    out_root = tmp_path / "compiled"
    results = compile_all(master, org, RULES, out_root)
    assert {r.person_id for r in results} == set(org.people)
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
