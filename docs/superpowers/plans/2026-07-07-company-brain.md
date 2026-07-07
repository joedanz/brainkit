# Multi-Tenant Company Brain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v1 backbone of the multi-tenant AI Chief-of-Staff company brain: spaces model, vault compiler (security boundary), write-back service, agent context-file generation, promotion queue, CLI, master-vault scaffolding, and the Hermes `company-brain` profile distribution.

**Architecture:** Hub-and-spoke. One master git vault per company; every note lives in exactly one space (`Company/`, `Teams/<t>/`, `People/<p>/`, `Clients/<c>/`) with visibility rules in `_meta/spaces.yaml`. A deterministic compiler materializes a filtered, self-describing vault per person (structural privacy — the ONLY security boundary). A write-back service validates edits server-side; a human-gated promotion queue moves knowledge private → shared. The chief-of-staff and personal agents are agent runtimes (Hermes/Claude Code) driven by generated context files and a provisioned profile — not Python LLM code.

**Tech Stack:** Python 3.12, PyYAML, pytest; `git` via subprocess. No other dependencies (YAGNI).

**Spec:** `docs/superpowers/specs/2026-07-07-multitenant-vault-design.md`

## Global Constraints

- Privacy is **structural**: a compiled vault physically contains only spaces the person may read. Never rely on agent instructions for privacy.
- `_meta/` is server-only: it must never appear in any compiled vault, and no one can write to it via write-back.
- Compiler **fails closed**: on any error the previous compiled output stands untouched.
- Write-back rejects the **whole** change set if any changed path is outside the person's write scope.
- Promotions are the only mechanism moving content from a more-private space to a less-private one; approval is human.
- All vault content is plain markdown, Obsidian-compatible (wikilinks, frontmatter).
- Generated root context files ≤ 20,000 chars; per-space context files ≤ 8,000 chars (Hermes load limits).
- Generated context files are declarative (no imperative override phrasing) so they pass Hermes's prompt-injection scan.
- Hermes provisioning: `memory.write_approval: true`, `skills.write_approval: true`, **no** external memory provider configured.
- Repo root already contains Hermes-engagement HTML deliverables — leave them untouched. All new code under `src/brain/`, `tests/`, `templates/`, `docs/`.

## File Structure

```
pyproject.toml                     # package metadata, pytest config
src/brain/__init__.py
src/brain/schemas.py               # org.yaml / spaces.yaml loading + validation
src/brain/resolver.py              # permission resolution (person → spaces; path → allowed?)
src/brain/compiler.py              # (master, person) → filtered vault; link stubbing; fail-closed; manifest
src/brain/contextgen.py            # AGENTS.md / CLAUDE.md generation for compiled vaults
src/brain/writeback.py             # diff person vault vs master; validate; apply; git commit
src/brain/promotions.py            # draft / list / approve / reject promotion notes
src/brain/cli.py                   # `brain` CLI: init, compile, writeback, promotions
src/brain/templates.py             # master-vault scaffolding content (init)
templates/company-brain-profile/   # Hermes profile distribution (SOUL.md, config.yaml, skills/, README)
tests/test_schemas.py
tests/test_resolver.py
tests/test_compiler.py
tests/test_contextgen.py
tests/test_writeback.py
tests/test_promotions.py
tests/test_cli.py
tests/test_leak_property.py        # randomized no-leak property test
tests/test_profile_distribution.py
tests/conftest.py                  # shared fixture: build a sample master vault
docs/onboarding.md                 # pilot onboarding guide
```

---

### Task 1: Project scaffold + schema loading (`org.yaml`, `spaces.yaml`)

**Files:**
- Create: `pyproject.toml`
- Create: `src/brain/__init__.py`
- Create: `src/brain/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Person(id, name, roles: tuple[str,...], teams: tuple[str,...])`, `Org(people: dict[str, Person])`, `SpaceRule(path, read: tuple[str,...], write: tuple[str,...])`, `SchemaError(ValueError)`, `load_org(path: Path) -> Org`, `load_spaces(path: Path) -> tuple[SpaceRule, ...]`. Subject grammar: `everyone`, `person:<id>`, `team:<name>`, `role:<name>`, with `{name}` allowed inside subjects of wildcard rules (binds the `*` segment).

- [ ] **Step 1: Create the package scaffold**

`pyproject.toml`:

```toml
[project]
name = "brainkit"
version = "0.1.0"
description = "Multi-tenant AI Chief-of-Staff company brain on Obsidian-compatible vaults"
requires-python = ">=3.12"
dependencies = ["pyyaml>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
brain = "brain.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/brain"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`src/brain/__init__.py`:

```python
"""brainkit — multi-tenant company brain on Obsidian-compatible vaults."""
```

`tests/__init__.py` (empty file — makes `from tests.conftest import ...` work in later tasks):

```python
```

Then install: `cd /Users/danziger/code/brain && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
Add `.venv/` to `.gitignore` (append a line `\n.venv/`). All test commands in this plan run from the repo root as `.venv/bin/python -m pytest ...` (the `-m` form puts the repo root on `sys.path`, which the `tests.conftest` imports rely on).

- [ ] **Step 2: Write the failing tests**

`tests/test_schemas.py`:

```python
from pathlib import Path

import pytest

from brain.schemas import Org, Person, SchemaError, SpaceRule, load_org, load_spaces

ORG_YAML = """\
people:
  alice: {name: Alice Nguyen, roles: [admin], teams: [sales]}
  bob:   {name: Bob Rivera, teams: [ops]}
"""

SPACES_YAML = """\
spaces:
  - {path: Company,     read: [everyone],        write: ["role:admin"]}
  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}
  - {path: "Clients/*", read: [everyone],        write: ["role:admin"]}
"""


def test_load_org(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text(ORG_YAML)
    org = load_org(f)
    assert org.people["alice"] == Person(
        id="alice", name="Alice Nguyen", roles=("admin",), teams=("sales",)
    )
    assert org.people["bob"].roles == ()


def test_load_spaces(tmp_path: Path):
    f = tmp_path / "spaces.yaml"
    f.write_text(SPACES_YAML)
    rules = load_spaces(f)
    assert rules[0] == SpaceRule(path="Company", read=("everyone",), write=("role:admin",))
    assert rules[1].read == ("team:{name}",)


def test_unknown_subject_rejected(tmp_path: Path):
    f = tmp_path / "spaces.yaml"
    f.write_text('spaces:\n  - {path: Company, read: ["group:staff"], write: []}\n')
    with pytest.raises(SchemaError, match="group:staff"):
        load_spaces(f)


def test_duplicate_rule_path_rejected(tmp_path: Path):
    f = tmp_path / "spaces.yaml"
    f.write_text(
        "spaces:\n"
        "  - {path: Company, read: [everyone], write: []}\n"
        "  - {path: Company, read: [everyone], write: []}\n"
    )
    with pytest.raises(SchemaError, match="duplicate"):
        load_spaces(f)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.schemas'`

- [ ] **Step 4: Write the implementation**

`src/brain/schemas.py`:

```python
"""Load and validate _meta/org.yaml and _meta/spaces.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class SchemaError(ValueError):
    """Invalid org.yaml or spaces.yaml content."""


SUBJECT_PREFIXES = ("person:", "team:", "role:")


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    roles: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()


@dataclass(frozen=True)
class Org:
    people: dict[str, Person]


@dataclass(frozen=True)
class SpaceRule:
    path: str  # "Company", "Teams/*", "People/*", or exact like "Clients/acme"
    read: tuple[str, ...]
    write: tuple[str, ...]


def _validate_subject(subject: str, rule_path: str) -> None:
    if subject == "everyone":
        return
    if not subject.startswith(SUBJECT_PREFIXES):
        raise SchemaError(f"rule {rule_path!r}: unknown subject {subject!r}")
    if "{name}" in subject and "*" not in rule_path:
        raise SchemaError(f"rule {rule_path!r}: {{name}} requires a wildcard path")


def load_org(path: Path) -> Org:
    data = yaml.safe_load(path.read_text()) or {}
    people_raw = data.get("people")
    if not isinstance(people_raw, dict) or not people_raw:
        raise SchemaError("org.yaml must define a non-empty 'people' mapping")
    people: dict[str, Person] = {}
    for pid, attrs in people_raw.items():
        attrs = attrs or {}
        people[pid] = Person(
            id=pid,
            name=attrs.get("name", pid),
            roles=tuple(attrs.get("roles", ())),
            teams=tuple(attrs.get("teams", ())),
        )
    return Org(people=people)


def load_spaces(path: Path) -> tuple[SpaceRule, ...]:
    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("spaces")
    if not isinstance(entries, list) or not entries:
        raise SchemaError("spaces.yaml must define a non-empty 'spaces' list")
    rules: list[SpaceRule] = []
    seen: set[str] = set()
    for entry in entries:
        rule_path = entry.get("path")
        if not rule_path:
            raise SchemaError("every spaces entry needs a 'path'")
        if rule_path in seen:
            raise SchemaError(f"duplicate rule path {rule_path!r}")
        seen.add(rule_path)
        read = tuple(entry.get("read", ()))
        write = tuple(entry.get("write", ()))
        for subject in (*read, *write):
            _validate_subject(subject, rule_path)
        rules.append(SpaceRule(path=rule_path, read=read, write=write))
    return tuple(rules)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_schemas.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/brain tests/__init__.py tests/test_schemas.py
git commit -m "feat: project scaffold + org/spaces schema loading"
```

---

### Task 2: Permission resolver

**Files:**
- Create: `src/brain/resolver.py`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Person`, `Org`, `SpaceRule` from `brain.schemas`.
- Produces: `enumerate_spaces(master: Path) -> list[str]` (concrete spaces like `"Company"`, `"Teams/sales"`; never `_meta`), `space_of_path(rel_path: str) -> str | None`, `can_read(space: str, person: Person, rules: tuple[SpaceRule, ...]) -> bool`, `can_write_path(rel_path: str, person: Person, rules: tuple[SpaceRule, ...]) -> bool`, `readable_spaces(master: Path, person: Person, rules) -> list[str]`. Matching: exact rule beats wildcard; no matching rule ⇒ deny; `{name}` in a subject is replaced with the `*` segment.

- [ ] **Step 1: Write the failing tests**

`tests/test_resolver.py`:

```python
from pathlib import Path

from brain.resolver import (
    can_read,
    can_write_path,
    enumerate_spaces,
    readable_spaces,
    space_of_path,
)
from brain.schemas import Person, SpaceRule

RULES = (
    SpaceRule("Company", read=("everyone",), write=("role:admin",)),
    SpaceRule("Teams/*", read=("team:{name}",), write=("team:{name}",)),
    SpaceRule("People/*", read=("person:{name}",), write=("person:{name}",)),
    SpaceRule("Clients/*", read=("everyone",), write=("role:admin",)),
    SpaceRule("Clients/private-co", read=("person:alice",), write=("person:alice",)),
)

ALICE = Person(id="alice", name="Alice", roles=("admin",), teams=("sales",))
BOB = Person(id="bob", name="Bob", roles=(), teams=("ops",))


def make_master(tmp_path: Path) -> Path:
    for d in ("Company", "Teams/sales", "Teams/ops", "People/alice",
              "People/bob", "Clients/acme", "Clients/private-co", "_meta"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def test_enumerate_spaces_skips_meta(tmp_path: Path):
    master = make_master(tmp_path)
    spaces = enumerate_spaces(master)
    assert "Company" in spaces
    assert "Teams/sales" in spaces
    assert not any(s.startswith("_meta") for s in spaces)


def test_space_of_path():
    assert space_of_path("Company/Decisions/d1.md") == "Company"
    assert space_of_path("Teams/sales/notes.md") == "Teams/sales"
    assert space_of_path("People/alice/Memory.md") == "People/alice"
    assert space_of_path("_meta/org.yaml") is None
    assert space_of_path("stray-root-file.md") is None


def test_can_read_wildcard_binding():
    assert can_read("People/alice", ALICE, RULES)
    assert not can_read("People/alice", BOB, RULES)
    assert can_read("Teams/ops", BOB, RULES)
    assert not can_read("Teams/sales", BOB, RULES)


def test_exact_rule_beats_wildcard():
    assert can_read("Clients/private-co", ALICE, RULES)
    assert not can_read("Clients/private-co", BOB, RULES)  # exact rule overrides Clients/*
    assert can_read("Clients/acme", BOB, RULES)


def test_can_write_path():
    assert can_write_path("People/bob/Actions/todo.md", BOB, RULES)
    assert not can_write_path("Company/Memory.md", BOB, RULES)      # not admin
    assert can_write_path("Company/Memory.md", ALICE, RULES)        # role:admin
    assert not can_write_path("_meta/spaces.yaml", ALICE, RULES)    # _meta locked for all
    assert not can_write_path("stray-root-file.md", ALICE, RULES)   # outside any space


def test_readable_spaces(tmp_path: Path):
    master = make_master(tmp_path)
    spaces = set(readable_spaces(master, BOB, RULES))
    assert spaces == {"Company", "Teams/ops", "People/bob", "Clients/acme"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.resolver'`

- [ ] **Step 3: Write the implementation**

`src/brain/resolver.py`:

```python
"""Permission resolution: which spaces a person can read, which paths they can write."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from brain.schemas import Person, SpaceRule

# Top-level dirs whose children are each a space; Company is itself a space.
NESTED_TOPS = ("Teams", "People", "Clients")
RESERVED = ("_meta", ".git")


def enumerate_spaces(master: Path) -> list[str]:
    spaces: list[str] = []
    for top in sorted(p for p in master.iterdir() if p.is_dir()):
        if top.name in RESERVED or top.name.startswith("."):
            continue
        if top.name == "Company":
            spaces.append("Company")
        elif top.name in NESTED_TOPS:
            spaces.extend(
                f"{top.name}/{child.name}"
                for child in sorted(top.iterdir())
                if child.is_dir()
            )
    return spaces


def space_of_path(rel_path: str) -> str | None:
    parts = PurePosixPath(rel_path).parts
    if not parts or parts[0] in RESERVED or parts[0].startswith("."):
        return None
    if parts[0] == "Company":
        return "Company"
    if parts[0] in NESTED_TOPS and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return None


def _match_rule(space: str, rules: tuple[SpaceRule, ...]) -> tuple[SpaceRule | None, str | None]:
    """Return (rule, wildcard_binding). Exact match wins over wildcard."""
    parts = space.split("/")
    wildcard_hit: tuple[SpaceRule, str] | None = None
    for rule in rules:
        if rule.path == space:
            return rule, None
        rparts = rule.path.split("/")
        if len(rparts) == len(parts) and rparts[-1] == "*" and rparts[:-1] == parts[:-1]:
            wildcard_hit = (rule, parts[-1])
    if wildcard_hit:
        return wildcard_hit
    return None, None


def _subject_matches(subject: str, person: Person, binding: str | None) -> bool:
    if binding is not None:
        subject = subject.replace("{name}", binding)
    if subject == "everyone":
        return True
    kind, _, value = subject.partition(":")
    if kind == "person":
        return person.id == value
    if kind == "team":
        return value in person.teams
    if kind == "role":
        return value in person.roles
    return False


def _allowed(space: str, person: Person, rules: tuple[SpaceRule, ...], mode: str) -> bool:
    rule, binding = _match_rule(space, rules)
    if rule is None:
        return False
    subjects = rule.read if mode == "read" else rule.write
    return any(_subject_matches(s, person, binding) for s in subjects)


def can_read(space: str, person: Person, rules: tuple[SpaceRule, ...]) -> bool:
    return _allowed(space, person, rules, "read")


def can_write_path(rel_path: str, person: Person, rules: tuple[SpaceRule, ...]) -> bool:
    space = space_of_path(rel_path)
    if space is None:
        return False
    return _allowed(space, person, rules, "write")


def readable_spaces(master: Path, person: Person, rules: tuple[SpaceRule, ...]) -> list[str]:
    return [s for s in enumerate_spaces(master) if can_read(s, person, rules)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_resolver.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/brain/resolver.py tests/test_resolver.py
git commit -m "feat: permission resolver (spaces, wildcard binding, exact-over-wildcard)"
```

---

### Task 3: Compiler — filtered materialization, fail-closed, manifest

**Files:**
- Create: `src/brain/compiler.py`
- Create: `tests/conftest.py`
- Test: `tests/test_compiler.py`

**Interfaces:**
- Consumes: `readable_spaces`, `space_of_path` from `brain.resolver`; `Person`, `Org`, `SpaceRule` from `brain.schemas`.
- Produces: `CompileResult(person_id: str, files: list[str])` (rel paths of copied source files), `compile_vault(master: Path, person: Person, rules: tuple[SpaceRule, ...], out: Path) -> CompileResult`, manifest file `.brain-manifest.json` at vault root with keys `{"person": str, "compiled": {rel path: sha256 of shipped bytes}, "generated": [rel paths]}` (in this task `generated` is `[]`; Task 5 populates it). Hashes are computed AFTER post-processing, so the manifest records exactly what was shipped — Task 7's diff compares against this baseline, never against live master bytes. Constant `MANIFEST_NAME = ".brain-manifest.json"`. Fail-closed: builds into a temp sibling dir and atomically swaps; preserves existing `out/.git`.
- Note: link stubbing (Task 4) and context files (Task 5) plug into `compile_vault` later.

- [ ] **Step 1: Write the shared master-vault fixture**

`tests/conftest.py`:

```python
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
        "AGENTS.md": "# Chief-of-staff protocol (server only)\n",
    }
    for rel, content in files.items():
        p = m / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (m / "_meta/promotions/pending").mkdir(parents=True)
    return m
```

- [ ] **Step 2: Write the failing tests**

`tests/test_compiler.py`:

```python
import json
from pathlib import Path

import pytest

from brain.compiler import MANIFEST_NAME, compile_vault
from tests.conftest import ALICE, BOB, RULES


def test_compiles_only_readable_spaces(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    result = compile_vault(master, BOB, RULES, out)
    assert (out / "Company/Home.md").exists()
    assert (out / "Teams/ops/Runbook.md").exists()
    assert (out / "People/bob/Memory.md").exists()
    assert (out / "Clients/acme/Overview.md").exists()
    # Structural privacy: not readable → not on disk
    assert not (out / "Teams/sales").exists()
    assert not (out / "People/alice").exists()
    assert not (out / "_meta").exists()
    # Master-root files (server chief-of-staff protocol) are never copied
    assert "AGENTS.md" not in {p.name for p in out.iterdir()}
    assert "Company/Home.md" in result.files


def test_manifest_written(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert manifest["person"] == "bob"
    assert "People/bob/Memory.md" in manifest["compiled"]
    assert isinstance(manifest["generated"], list)


def test_recompile_replaces_stale_files(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    (master / "Teams/ops/Runbook.md").write_text("Updated runbook.\n")
    # Simulate access removal: bob loses acme
    import shutil
    shutil.rmtree(master / "Clients/acme")
    compile_vault(master, BOB, RULES, out)
    assert (out / "Teams/ops/Runbook.md").read_text() == "Updated runbook.\n"
    assert not (out / "Clients/acme").exists()


def test_fail_closed_preserves_previous_output(master: Path, tmp_path: Path, monkeypatch):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    before = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())

    import brain.compiler as compiler_mod

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(compiler_mod.shutil, "copy2", boom)
    with pytest.raises(RuntimeError):
        compile_vault(master, BOB, RULES, out)
    after = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert before == after  # previous output stands untouched


def test_git_dir_preserved_across_recompile(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    (out / ".git").mkdir()
    (out / ".git/HEAD").write_text("ref: refs/heads/main\n")
    compile_vault(master, BOB, RULES, out)
    assert (out / ".git/HEAD").read_text() == "ref: refs/heads/main\n"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.compiler'`

- [ ] **Step 4: Write the implementation**

`src/brain/compiler.py`:

```python
"""Vault compiler: (master, person) -> filtered vault. THE security boundary.

Builds into a temp sibling directory and atomically swaps into place, so any
failure leaves the previous compiled output untouched (fail closed). A person
can only ever temporarily see LESS than they are allowed, never more.

The manifest records the sha256 of every shipped file AFTER post-processing
(link stubbing, context generation). Write-back diffs against this baseline,
so per-person rewrites (stubbed links) never show up as phantom user edits.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from brain.resolver import readable_spaces
from brain.schemas import Person, SpaceRule

MANIFEST_NAME = ".brain-manifest.json"


@dataclass
class CompileResult:
    person_id: str
    files: list[str]  # rel paths of copied source files


def _iter_space_files(master: Path, space: str):
    root = master / space
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield str(p.relative_to(master))


def compile_vault(
    master: Path, person: Person, rules: tuple[SpaceRule, ...], out: Path
) -> CompileResult:
    spaces = readable_spaces(master, person, rules)
    building = out.parent / f".{out.name}.building"
    if building.exists():
        shutil.rmtree(building)
    building.mkdir(parents=True)

    compiled: list[str] = []
    try:
        for space in spaces:
            for rel in _iter_space_files(master, space):
                dest = building / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(master / rel, dest)
                compiled.append(rel)

        generated = _post_process(building, master, person, spaces, rules, compiled)

        # Hash what was actually shipped (post-stubbing); generated files are
        # tracked separately and never counted as user-editable baseline.
        compiled_hashes = {
            rel: hashlib.sha256((building / rel).read_bytes()).hexdigest()
            for rel in compiled
            if rel not in set(generated)
        }
        manifest = {
            "person": person.id,
            "compiled": compiled_hashes,
            "generated": generated,
        }
        (building / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))

        # Atomic-ish swap; preserve the per-person git history across recompiles.
        if out.exists():
            git_dir = out / ".git"
            if git_dir.exists():
                shutil.move(str(git_dir), str(building / ".git"))
            shutil.rmtree(out)
        shutil.move(str(building), str(out))
    finally:
        if building.exists():
            shutil.rmtree(building)

    return CompileResult(person_id=person.id, files=compiled)


def _post_process(
    building: Path,
    master: Path,
    person: Person,
    spaces: list[str],
    rules: tuple[SpaceRule, ...],
    compiled: list[str],
) -> list[str]:
    """Hook for link stubbing (Task 4) and context-file generation (Task 5).

    Returns the list of generated rel paths for the manifest.
    """
    return []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_compiler.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/brain/compiler.py tests/conftest.py tests/test_compiler.py
git commit -m "feat: vault compiler with fail-closed swap and manifest"
```

---

### Task 4: Compiler — wikilink stubbing

**Files:**
- Modify: `src/brain/compiler.py` (add `stub_links`, call it from `_post_process`)
- Test: `tests/test_compiler.py` (append tests)

**Interfaces:**
- Consumes: `compile_vault` internals from Task 3.
- Produces: `stub_links(text: str, included_stems: set[str], master_stems: set[str]) -> str`. Behavior: a `[[Target]]` or `[[Target|Alias]]` (and embed `![[Target]]`) whose note-name stem exists in the master but NOT in this compiled vault is replaced by its display text (`Alias` if present, else `Target`); links to included notes and to notes that don't exist anywhere are left untouched (unresolved links are normal in Obsidian).
- **Stubbing applies only to files in spaces the person cannot write.** Writable files must round-trip byte-identical through write-back, so they are never rewritten; a link to an invisible note there just renders as an unresolved link, which is harmless.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compiler.py`:

```python
from brain.compiler import stub_links


def test_stub_links_unit():
    included = {"big deal decision"}
    master = {"big deal decision", "bob private note"}
    text = "See [[Big Deal Decision]], [[Bob Private Note|his note]], and [[Future Idea]]. ![[Bob Private Note]]"
    out = stub_links(text, included, master)
    assert "[[Big Deal Decision]]" in out          # included → untouched
    assert "[[Bob Private Note" not in out         # invisible → stubbed
    assert "his note" in out                       # alias used as display text
    assert "[[Future Idea]]" in out                # nonexistent anywhere → untouched
    assert "![[" not in out                        # embed of invisible note stubbed too


def test_compile_stubs_invisible_links_in_readonly_spaces(master, tmp_path):
    from brain.compiler import compile_vault
    from tests.conftest import BOB, RULES

    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    # Company is read-only for bob → stubbing applies there
    home = (out / "Company/Home.md").read_text()
    assert "[[Big Deal Decision]]" in home   # included in bob's vault → untouched
    assert "[[Q3 Pipeline]]" not in home     # Teams/sales invisible to bob → stubbed
    assert "Q3 Pipeline" in home             # display text remains


def test_writable_spaces_never_stubbed(master, tmp_path):
    from brain.compiler import compile_vault
    from tests.conftest import BOB, RULES

    (master / "People/bob/Notes.md").write_text("See [[Q3 Pipeline]].\n")
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    # People/bob is writable for bob → byte-identical copy, link untouched
    assert (out / "People/bob/Notes.md").read_text() == "See [[Q3 Pipeline]].\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compiler.py -v -k stub`
Expected: FAIL — `ImportError: cannot import name 'stub_links'`

- [ ] **Step 3: Implement**

Add to `src/brain/compiler.py` (top-level, plus wire into `_post_process`):

```python
import re
from pathlib import PurePosixPath

WIKILINK_RE = re.compile(
    r"!?\[\[([^\][|#]+)(#[^\][|]*)?(\|([^\][]+))?\]\]"
)


def _stem(target: str) -> str:
    return PurePosixPath(target.strip()).stem.lower()


def stub_links(text: str, included_stems: set[str], master_stems: set[str]) -> str:
    def repl(m: re.Match) -> str:
        target, alias = m.group(1), m.group(4)
        stem = _stem(target)
        if stem in included_stems or stem not in master_stems:
            return m.group(0)
        return (alias or target).strip()

    return WIKILINK_RE.sub(repl, text)
```

Replace `_post_process` body's stubbing portion (note: only read-only files are rewritten):

```python
def _post_process(building, master, person, spaces, rules, compiled):
    from brain.resolver import can_write_path

    included_stems = {
        PurePosixPath(rel).stem.lower() for rel in compiled if rel.endswith(".md")
    }
    master_stems = {
        p.stem.lower()
        for p in master.rglob("*.md")
        if ".git" not in p.parts and "_meta" not in p.parts
    }
    for rel in compiled:
        if rel.endswith(".md") and not can_write_path(rel, person, rules):
            f = building / rel
            f.write_text(stub_links(f.read_text(), included_stems, master_stems))
    return []
```

- [ ] **Step 4: Run the full compiler suite**

Run: `.venv/bin/python -m pytest tests/test_compiler.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/brain/compiler.py tests/test_compiler.py
git commit -m "feat: stub wikilinks to notes outside the compiled vault"
```

---

### Task 5: Context-file generation (AGENTS.md / CLAUDE.md)

**Files:**
- Create: `src/brain/contextgen.py`
- Modify: `src/brain/compiler.py` (`_post_process` calls `generate_context_files`)
- Test: `tests/test_contextgen.py`

**Interfaces:**
- Consumes: `Person`, `SpaceRule`; `can_write_path` from `brain.resolver`.
- Produces: `ROOT_LIMIT = 20_000`, `SPACE_LIMIT = 8_000`, `render_root_protocol(person: Person, spaces_rw: list[tuple[str, bool]]) -> str` (`spaces_rw` = [(space, writable)]), `render_space_note(space: str, writable: bool, owner: bool) -> str`, `generate_context_files(vault: Path, person: Person, spaces: list[str], rules: tuple[SpaceRule, ...]) -> list[str]` (writes root `AGENTS.md` + `CLAUDE.md`, plus per-space pairs for the person's own `People/` space and every `Clients/` space; returns rel paths written). Raises `ValueError` if a rendered file exceeds its limit. All copy is declarative — no phrasing like "ignore previous instructions".

- [ ] **Step 1: Write the failing tests**

`tests/test_contextgen.py`:

```python
from pathlib import Path

from brain.compiler import MANIFEST_NAME, compile_vault
from brain.contextgen import ROOT_LIMIT, SPACE_LIMIT, render_root_protocol
from tests.conftest import ALICE, BOB, RULES


def test_root_protocol_content():
    text = render_root_protocol(
        BOB, [("Company", False), ("Teams/ops", True), ("People/bob", True)]
    )
    assert len(text) <= ROOT_LIMIT
    assert "Bob Rivera" in text
    assert "People/bob" in text
    assert "read-only" in text            # Company marked read-only for bob
    assert "promotion" in text.lower()    # promotion protocol documented
    assert "Actions/Action Tracker" in text  # routing rules documented


def test_compile_writes_context_files(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    assert (out / "AGENTS.md").exists()
    assert (out / "CLAUDE.md").read_text() == (out / "AGENTS.md").read_text()
    person_note = (out / "People/bob/AGENTS.md").read_text()
    assert "private" in person_note.lower()
    assert len(person_note) <= SPACE_LIMIT
    client_note = out / "Clients/acme/AGENTS.md"
    assert client_note.exists()


def test_generated_files_listed_in_manifest(master: Path, tmp_path: Path):
    import json

    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert "AGENTS.md" in manifest["generated"]
    assert "CLAUDE.md" in manifest["generated"]
    assert "People/bob/AGENTS.md" in manifest["generated"]
    assert "AGENTS.md" not in manifest["compiled"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_contextgen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.contextgen'`

- [ ] **Step 3: Implement**

`src/brain/contextgen.py`:

```python
"""Generate AGENTS.md / CLAUDE.md so every compiled vault is self-describing.

Limits follow Hermes Agent context-file loading: ~20K chars for the root file,
~8K for progressively-discovered per-directory files. Copy is declarative so it
passes Hermes's prompt-injection scan.
"""

from __future__ import annotations

from pathlib import Path

from brain.schemas import Person, SpaceRule

ROOT_LIMIT = 20_000
SPACE_LIMIT = 8_000

_ROOT_TEMPLATE = """\
# Brain Protocol — vault of {name} ({pid})

This vault is {name}'s slice of the company brain. It is compiled: it contains
only the spaces {name} may read. Anything not present here is not accessible.

## Spaces in this vault

{space_lines}

Read-only spaces are maintained by the company chief-of-staff. Edits belong in
writable spaces; the write-back service rejects changes to read-only paths.

## Routing rules (apply when processing new information)

- Action items (owner + deadline) -> `People/{pid}/Actions/Action Tracker.md`
- Session/meeting summaries -> `People/{pid}/Sessions/`
- Raw transcripts land in `People/{pid}/Inbox/` and are processed, then archived to `People/{pid}/Sessions/`
- Personal durable facts, preferences, lessons -> `People/{pid}/Memory.md`
- Client facts you may share -> draft a promotion targeting the client's space
- Decisions of company-wide relevance -> draft a promotion targeting `Company/Decisions/`
- If unsure where something belongs, add it to `People/{pid}/Needs-Routing.md`

## Promotion protocol (moving knowledge to shared spaces)

Nothing in `People/{pid}/` is shared automatically. To share knowledge:
1. Draft a sanitized note (no private context beyond what is being shared).
2. Save it under `People/{pid}/Promotions/` with frontmatter:
   `target-path: <shared space path>` and `source: <originating note>`.
3. {name} reviews and approves via `brain promotions approve`; only then does
   the note reach the shared space.

## Privacy rules

- Content in `People/{pid}/` is private to {pid}.
- Never copy content from a private space into a shared space directly; use a
  promotion.
- When drafting anything client-facing, cite the source note.
"""


def render_root_protocol(person: Person, spaces_rw: list[tuple[str, bool]]) -> str:
    space_lines = "\n".join(
        f"- `{space}/` — {'writable' if writable else 'read-only'}"
        for space, writable in spaces_rw
    )
    text = _ROOT_TEMPLATE.format(name=person.name, pid=person.id, space_lines=space_lines)
    if len(text) > ROOT_LIMIT:
        raise ValueError(f"root protocol exceeds {ROOT_LIMIT} chars")
    return text


def render_space_note(space: str, writable: bool, owner: bool) -> str:
    if owner:
        text = (
            f"# {space} — private space\n\n"
            "Everything here is private to the vault owner. Nothing leaves this\n"
            "space without an approved promotion. Keep Memory.md curated;\n"
            "archive processed Inbox items into Sessions/.\n"
        )
    else:
        mode = "writable" if writable else "read-only"
        text = (
            f"# {space}\n\n"
            f"This space is {mode} for the vault owner. Follow the routing and\n"
            "promotion rules in the vault root AGENTS.md. Cite sources for\n"
            "facts recorded here.\n"
        )
    if len(text) > SPACE_LIMIT:
        raise ValueError(f"space note for {space} exceeds {SPACE_LIMIT} chars")
    return text


def _writable(space: str, person: Person, rules: tuple[SpaceRule, ...]) -> bool:
    from brain.resolver import can_write_path

    return can_write_path(f"{space}/x.md", person, rules)


def generate_context_files(
    vault: Path, person: Person, spaces: list[str], rules: tuple[SpaceRule, ...]
) -> list[str]:
    written: list[str] = []
    spaces_rw = [(s, _writable(s, person, rules)) for s in spaces]

    root_text = render_root_protocol(person, spaces_rw)
    for fname in ("AGENTS.md", "CLAUDE.md"):
        (vault / fname).write_text(root_text)
        written.append(fname)

    for space, writable in spaces_rw:
        owner = space == f"People/{person.id}"
        if not owner and not space.startswith("Clients/"):
            continue
        note = render_space_note(space, writable, owner)
        for fname in ("AGENTS.md", "CLAUDE.md"):
            rel = f"{space}/{fname}"
            target = vault / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(note)
            written.append(rel)
    return written
```

Update `_post_process` in `src/brain/compiler.py` — replace `return []` at the end with:

```python
    from brain.contextgen import generate_context_files

    return generate_context_files(building, person, spaces, rules)
```

(If a compiled space contained its own `AGENTS.md`/`CLAUDE.md`, generation overwrites it in the vault copy. No manifest bookkeeping is needed here — Task 3's `compile_vault` already excludes every path in `generated` when building `compiled_hashes`, so each file is tracked exactly once.)

- [ ] **Step 4: Run the affected suites**

Run: `.venv/bin/python -m pytest tests/test_contextgen.py tests/test_compiler.py -v`
Expected: all passed (context tests + compiler tests still green)

- [ ] **Step 5: Commit**

```bash
git add src/brain/contextgen.py src/brain/compiler.py tests/test_contextgen.py
git commit -m "feat: generate self-describing AGENTS.md/CLAUDE.md in compiled vaults"
```

---

### Task 6: Randomized no-leak property test + `compile_all`

**Files:**
- Modify: `src/brain/compiler.py` (add `compile_all`)
- Test: `tests/test_leak_property.py`

**Interfaces:**
- Consumes: `compile_vault`, `readable_spaces`, `Org`.
- Produces: `compile_all(master: Path, org: Org, rules: tuple[SpaceRule, ...], out_root: Path) -> list[CompileResult]` — compiles every person to `out_root/<person_id>/`, initializes a git repo there if absent, and commits when anything changed (author `Brain Compiler <compiler@brain.local>`, message `compile: <short master state>`).

- [ ] **Step 1: Write the failing tests**

`tests/test_leak_property.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_leak_property.py -v`
Expected: `test_no_leak_across_random_worlds` PASSES already (the boundary exists); `test_compile_all_creates_git_repos` FAILS — `ImportError: cannot import name 'compile_all'`

- [ ] **Step 3: Implement `compile_all`**

Add to `src/brain/compiler.py`:

```python
import subprocess

from brain.schemas import Org


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def compile_all(
    master: Path, org: Org, rules: tuple[SpaceRule, ...], out_root: Path
) -> list[CompileResult]:
    results = []
    for person in org.people.values():
        out = out_root / person.id
        result = compile_vault(master, person, rules, out)
        if not (out / ".git").exists():
            _git(out, "init", "-b", "main")
        _git(out, "add", "-A")
        status = _git(out, "status", "--porcelain").stdout
        if status.strip():
            _git(
                out,
                "-c", "user.name=Brain Compiler",
                "-c", "user.email=compiler@brain.local",
                "commit", "-m", f"compile: refresh vault for {person.id}",
            )
        results.append(result)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_leak_property.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -v` — all tests pass.

```bash
git add src/brain/compiler.py tests/test_leak_property.py
git commit -m "feat: compile_all with per-person git repos + randomized no-leak property test"
```

---

### Task 7: Write-back service

**Files:**
- Create: `src/brain/writeback.py`
- Test: `tests/test_writeback.py`

**Interfaces:**
- Consumes: `MANIFEST_NAME` from `brain.compiler`; `can_write_path` from `brain.resolver`; `Person`, `SpaceRule`.
- Produces: `Change(path: str, kind: str)` with kind ∈ `{"add", "modify", "delete"}`; `WritebackResult(applied: list[Change], violations: list[str])` (`applied` is empty whenever `violations` is non-empty); `diff_vault(vault: Path) -> list[Change]` — diffs the vault against the **manifest hash baseline** (what the compiler shipped), never against live master bytes, so compiler rewrites (stubbed links) can't surface as phantom edits and master moving on since compile resolves last-write-wins per the spec. Skips `.git`, the manifest, and every path in the manifest's `generated` list; `modify` = file's sha256 differs from its manifest entry; `delete` = manifest entry missing from the vault; `add` = file not in the manifest. Also `apply_writeback(master: Path, vault: Path, person: Person, rules: tuple[SpaceRule, ...]) -> WritebackResult` (validates first — one violation rejects everything; on success applies changes and commits to master with author `<person.name> <person.id@brain.local>`).

- [ ] **Step 1: Write the failing tests**

`tests/test_writeback.py`:

```python
import subprocess
from pathlib import Path

from brain.compiler import compile_vault
from brain.writeback import apply_writeback, diff_vault
from tests.conftest import ALICE, BOB, RULES


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def setup_master_git(master: Path) -> None:
    git(master, "init", "-b", "main")
    git(master, "add", "-A")
    git(master, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed")


def test_diff_detects_add_modify_delete(master: Path, tmp_path: Path):
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Actions").mkdir(parents=True, exist_ok=True)
    (vault / "People/bob/Actions/Todo.md").write_text("- [ ] call acme\n")
    (vault / "People/bob/Memory.md").write_text("Bob updated memory.\n")
    (vault / "People/bob/Sessions/Bob Private Note.md").unlink()
    changes = {(c.kind, c.path) for c in diff_vault(vault)}
    assert ("add", "People/bob/Actions/Todo.md") in changes
    assert ("modify", "People/bob/Memory.md") in changes
    assert ("delete", "People/bob/Sessions/Bob Private Note.md") in changes
    # Generated context files are not treated as user changes
    assert not any(p.endswith("AGENTS.md") or p.endswith("CLAUDE.md") for _, p in changes)


def test_out_of_scope_change_rejects_everything(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("legit change\n")
    (vault / "Company/Home.md").write_text("bob defaces the homepage\n")  # not writable
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.applied == []
    assert any("Company/Home.md" in v for v in result.violations)
    # Nothing applied — master untouched, including the legit change
    assert (master / "People/bob/Memory.md").read_text() == "Bob private memory.\n"


def test_valid_writeback_applies_and_commits(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("Bob updated memory.\n")
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.violations == []
    assert [c.kind for c in result.applied] == ["modify"]
    assert (master / "People/bob/Memory.md").read_text() == "Bob updated memory.\n"
    log = git(master, "log", "-1", "--format=%an %ae %s")
    assert "Bob Rivera" in log and "bob@brain.local" in log


def test_noop_writeback_makes_no_commit(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    before = git(master, "rev-parse", "HEAD")
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.applied == [] and result.violations == []
    assert git(master, "rev-parse", "HEAD") == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_writeback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.writeback'`

- [ ] **Step 3: Implement**

`src/brain/writeback.py`:

```python
"""Write-back: validate a person's vault edits server-side and apply to master.

Client trust is never assumed: every changed path is checked against the
person's write permissions here, regardless of what the sync client allowed.
One out-of-scope change rejects the whole change set.

Diffs run against the manifest's hash baseline (what the compiler shipped),
never live master bytes: compiler rewrites such as stubbed links would
otherwise appear as phantom user edits, and a master that moved on since
compile resolves last-write-wins per the spec.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from brain.compiler import MANIFEST_NAME
from brain.resolver import can_write_path
from brain.schemas import Person, SpaceRule


@dataclass
class Change:
    path: str
    kind: str  # "add" | "modify" | "delete"


@dataclass
class WritebackResult:
    applied: list[Change] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


def _load_manifest(vault: Path) -> dict:
    return json.loads((vault / MANIFEST_NAME).read_text())


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diff_vault(vault: Path) -> list[Change]:
    manifest = _load_manifest(vault)
    baseline: dict[str, str] = manifest["compiled"]  # rel path -> sha256
    generated = set(manifest["generated"]) | {MANIFEST_NAME}

    changes: list[Change] = []
    present: set[str] = set()
    for f in sorted(vault.rglob("*")):
        if not f.is_file() or ".git" in f.parts:
            continue
        rel = str(f.relative_to(vault))
        if rel in generated:
            continue
        present.add(rel)
        if rel not in baseline:
            changes.append(Change(rel, "add"))
        elif _sha(f.read_bytes()) != baseline[rel]:
            changes.append(Change(rel, "modify"))
    for rel in sorted(set(baseline) - present):
        changes.append(Change(rel, "delete"))
    return changes


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def apply_writeback(
    master: Path, vault: Path, person: Person, rules: tuple[SpaceRule, ...]
) -> WritebackResult:
    changes = diff_vault(vault)
    violations = [
        f"{c.kind} {c.path}: outside write scope for {person.id}"
        for c in changes
        if not can_write_path(c.path, person, rules)
    ]
    if violations:
        return WritebackResult(applied=[], violations=violations)
    if not changes:
        return WritebackResult()

    for c in changes:
        target = master / c.path
        if c.kind == "delete":
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((vault / c.path).read_bytes())

    _git(master, "add", "-A")
    _git(
        master,
        "-c", f"user.name={person.name}",
        "-c", f"user.email={person.id}@brain.local",
        "commit", "-m", f"writeback: {person.id} ({len(changes)} change(s))",
    )
    return WritebackResult(applied=changes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_writeback.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/brain/writeback.py tests/test_writeback.py
git commit -m "feat: write-back service with server-side validation and whole-set rejection"
```

---

### Task 8: Promotion queue

**Files:**
- Create: `src/brain/promotions.py`
- Test: `tests/test_promotions.py`

**Interfaces:**
- Consumes: `space_of_path` from `brain.resolver`.
- Produces: `PromotionError(ValueError)`; `Promotion(id: str, person_id: str, target_path: str, source: str, created: str, body: str)`; `draft_promotion(master: Path, person_id: str, target_path: str, source: str, body: str, promo_id: str, created: str) -> Path` (writes `_meta/promotions/pending/<id>.md`; rejects targets in `People/` or `_meta` or outside any space); `list_pending(master: Path) -> list[Promotion]`; `approve(master: Path, promo_id: str, approver: str, date: str) -> Path` (writes target with provenance frontmatter `promoted-by/approved-by/source/date`, moves promo to `approved/`); `reject(master: Path, promo_id: str, reason: str) -> Path` (moves to `rejected/`, appends `rejected-reason`); `sweep(master: Path, today: str) -> list[Path]` — the bridge between agents and the queue: personal agents cannot write `_meta/`, so they draft into their own `People/<pid>/Promotions/` (which write-back accepts); the server then sweeps those drafts into `_meta/promotions/pending/`. Each swept file needs `target-path:` (and optionally `source:`) in its frontmatter; the promo id is `<pid>-<filename-stem-slug>`; files with a missing/invalid target are left in place (skipped, never guessed). Timestamps/ids are passed in by callers (CLI supplies real ones) so the library stays deterministic and testable.

- [ ] **Step 1: Write the failing tests**

`tests/test_promotions.py`:

```python
from pathlib import Path

import pytest

from brain.promotions import (
    PromotionError,
    approve,
    draft_promotion,
    list_pending,
    reject,
)


def test_draft_and_list(master: Path):
    p = draft_promotion(
        master,
        person_id="bob",
        target_path="Company/Frameworks/Onboarding-Call-SOP.md",
        source="People/bob/Sessions/2026-07-01-call.md",
        body="## Onboarding call SOP\n1. Confirm goals.\n",
        promo_id="p-001",
        created="2026-07-07",
    )
    assert p == master / "_meta/promotions/pending/p-001.md"
    pending = list_pending(master)
    assert len(pending) == 1
    assert pending[0].person_id == "bob"
    assert pending[0].target_path == "Company/Frameworks/Onboarding-Call-SOP.md"
    assert "Confirm goals" in pending[0].body


@pytest.mark.parametrize(
    "bad_target",
    ["People/alice/Memory.md", "_meta/org.yaml", "loose-root-note.md"],
)
def test_draft_rejects_bad_targets(master: Path, bad_target: str):
    with pytest.raises(PromotionError):
        draft_promotion(
            master, person_id="bob", target_path=bad_target,
            source="x", body="b", promo_id="p-002", created="2026-07-07",
        )


def test_approve_writes_target_with_provenance(master: Path):
    draft_promotion(
        master, person_id="bob",
        target_path="Company/Frameworks/SOP.md",
        source="People/bob/Sessions/call.md",
        body="Step one.\n", promo_id="p-003", created="2026-07-07",
    )
    target = approve(master, "p-003", approver="alice", date="2026-07-08")
    text = target.read_text()
    assert text.startswith("---\n")
    assert "promoted-by: bob" in text
    assert "approved-by: alice" in text
    assert "source: People/bob/Sessions/call.md" in text
    assert "Step one." in text
    assert not (master / "_meta/promotions/pending/p-003.md").exists()
    assert (master / "_meta/promotions/approved/p-003.md").exists()


def test_reject_records_reason(master: Path):
    draft_promotion(
        master, person_id="bob", target_path="Company/Frameworks/SOP2.md",
        source="s", body="b", promo_id="p-004", created="2026-07-07",
    )
    rejected = reject(master, "p-004", reason="too client-specific")
    assert "rejected-reason: too client-specific" in rejected.read_text()
    assert not (master / "Company/Frameworks/SOP2.md").exists()
    assert list_pending(master) == []


def test_sweep_moves_agent_drafts_into_queue(master: Path):
    from brain.promotions import sweep

    d = master / "People/bob/Promotions"
    d.mkdir(parents=True)
    (d / "Onboarding SOP.md").write_text(
        "---\n"
        "target-path: Company/Frameworks/Onboarding-SOP.md\n"
        "source: People/bob/Sessions/call.md\n"
        "---\n"
        "Step one.\n"
    )
    (d / "broken.md").write_text("no frontmatter, no target\n")
    moved = sweep(master, today="2026-07-07")
    assert len(moved) == 1
    pending = list_pending(master)
    assert pending[0].id == "bob-onboarding-sop"
    assert pending[0].target_path == "Company/Frameworks/Onboarding-SOP.md"
    assert not (d / "Onboarding SOP.md").exists()   # swept
    assert (d / "broken.md").exists()               # skipped, left in place
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_promotions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.promotions'`

- [ ] **Step 3: Implement**

`src/brain/promotions.py`:

```python
"""Human-gated promotion queue: the only path from private to shared spaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from brain.resolver import space_of_path


class PromotionError(ValueError):
    """Invalid promotion target or unknown promotion id."""


@dataclass
class Promotion:
    id: str
    person_id: str
    target_path: str
    source: str
    created: str
    body: str


def _pending_dir(master: Path) -> Path:
    return master / "_meta/promotions/pending"


def _validate_target(target_path: str) -> None:
    space = space_of_path(target_path)
    if space is None:
        raise PromotionError(f"target {target_path!r} is not inside any space")
    if space.startswith("People/"):
        raise PromotionError("promotions must target a shared space, not People/")


def draft_promotion(
    master: Path,
    person_id: str,
    target_path: str,
    source: str,
    body: str,
    promo_id: str,
    created: str,
) -> Path:
    _validate_target(target_path)
    dest = _pending_dir(master) / f"{promo_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "---\n"
        f"promotion-id: {promo_id}\n"
        f"from: {person_id}\n"
        f"target-path: {target_path}\n"
        f"source: {source}\n"
        f"created: {created}\n"
        "---\n"
        f"{body}"
    )
    return dest


def _parse(path: Path) -> Promotion:
    text = path.read_text()
    _, fm, body = text.split("---\n", 2)
    meta = {}
    for line in fm.strip().splitlines():
        key, _, value = line.partition(": ")
        meta[key] = value
    return Promotion(
        id=meta["promotion-id"],
        person_id=meta["from"],
        target_path=meta["target-path"],
        source=meta["source"],
        created=meta["created"],
        body=body,
    )


def list_pending(master: Path) -> list[Promotion]:
    d = _pending_dir(master)
    if not d.exists():
        return []
    return [_parse(p) for p in sorted(d.glob("*.md"))]


def _find_pending(master: Path, promo_id: str) -> Path:
    p = _pending_dir(master) / f"{promo_id}.md"
    if not p.exists():
        raise PromotionError(f"no pending promotion {promo_id!r}")
    return p


def approve(master: Path, promo_id: str, approver: str, date: str) -> Path:
    pending = _find_pending(master, promo_id)
    promo = _parse(pending)
    target = master / promo.target_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        f"promoted-by: {promo.person_id}\n"
        f"approved-by: {approver}\n"
        f"source: {promo.source}\n"
        f"date: {date}\n"
        "---\n"
        f"{promo.body}"
    )
    archived = master / "_meta/promotions/approved" / pending.name
    archived.parent.mkdir(parents=True, exist_ok=True)
    pending.rename(archived)
    return target


def reject(master: Path, promo_id: str, reason: str) -> Path:
    pending = _find_pending(master, promo_id)
    _, fm, body = pending.read_text().split("---\n", 2)
    rejected = master / "_meta/promotions/rejected" / pending.name
    rejected.parent.mkdir(parents=True, exist_ok=True)
    rejected.write_text(f"---\n{fm}rejected-reason: {reason}\n---\n{body}")
    pending.unlink()
    return rejected


def _slug(text: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def sweep(master: Path, today: str) -> list[Path]:
    """Move agent-drafted promotions from People/*/Promotions/ into the queue.

    Personal agents cannot write _meta/, so their drafts land in their own
    writable space; the server sweeps them here. Files without a valid
    target-path are left in place — never guessed at.
    """
    moved: list[Path] = []
    for f in sorted(master.glob("People/*/Promotions/*.md")):
        rel = f.relative_to(master)
        person_id = rel.parts[1]
        text = f.read_text()
        if text.count("---\n") < 2:
            continue
        _, fm, body = text.split("---\n", 2)
        meta = {}
        for line in fm.strip().splitlines():
            key, _, value = line.partition(": ")
            meta[key] = value
        target = meta.get("target-path", "")
        promo_id = f"{person_id}-{_slug(f.stem)}"
        if (_pending_dir(master) / f"{promo_id}.md").exists():
            continue
        try:
            draft_promotion(
                master,
                person_id=person_id,
                target_path=target,
                source=meta.get("source", str(rel)),
                body=body,
                promo_id=promo_id,
                created=today,
            )
        except PromotionError:
            continue
        f.unlink()
        moved.append(_pending_dir(master) / f"{promo_id}.md")
    return moved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_promotions.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/brain/promotions.py tests/test_promotions.py
git commit -m "feat: human-gated promotion queue with provenance frontmatter"
```

---

### Task 9: CLI — compile, writeback, promotions

**Files:**
- Create: `src/brain/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above; `load_org`, `load_spaces` read from `<master>/_meta/`.
- Produces: `main(argv: list[str] | None = None) -> int` (console script `brain`). Subcommands:
  - `brain compile --master M --out O [--person ID]` — compile one or all
  - `brain writeback --master M --vault V --person ID` — exit 1 + violations on stderr when rejected
  - `brain promotions list --master M`
  - `brain promotions sweep --master M` — pull agent drafts from `People/*/Promotions/` into the pending queue
  - `brain promotions approve <id> --master M --approver WHO`
  - `brain promotions reject <id> --master M --reason TEXT`
  - (`brain init` is added in Task 10.)

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
import subprocess
from pathlib import Path

from brain.cli import main

ORG_YAML = """\
people:
  alice: {name: Alice Nguyen, roles: [admin], teams: [sales]}
  bob:   {name: Bob Rivera, teams: [ops]}
"""

SPACES_YAML = """\
spaces:
  - {path: Company,     read: [everyone],        write: ["role:admin"]}
  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}
  - {path: "Clients/*", read: [everyone],        write: ["role:admin"]}
"""


def seed_meta(master: Path) -> None:
    (master / "_meta").mkdir(exist_ok=True)
    (master / "_meta/org.yaml").write_text(ORG_YAML)
    (master / "_meta/spaces.yaml").write_text(SPACES_YAML)
    subprocess.run(["git", "-C", str(master), "init", "-b", "main"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(master), "add", "-A"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(master), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-m", "seed"],
                   capture_output=True, check=True)


def test_compile_all_and_single(master: Path, tmp_path: Path, capsys):
    seed_meta(master)
    out_root = tmp_path / "compiled"
    assert main(["compile", "--master", str(master), "--out", str(out_root)]) == 0
    assert (out_root / "bob/People/bob/Memory.md").exists()
    assert not (out_root / "bob/People/alice").exists()
    assert main(["compile", "--master", str(master), "--out", str(out_root),
                 "--person", "alice"]) == 0


def test_writeback_rejection_exit_code(master: Path, tmp_path: Path, capsys):
    seed_meta(master)
    out_root = tmp_path / "compiled"
    main(["compile", "--master", str(master), "--out", str(out_root)])
    vault = out_root / "bob"
    (vault / "Company/Home.md").write_text("defaced\n")
    code = main(["writeback", "--master", str(master),
                 "--vault", str(vault), "--person", "bob"])
    assert code == 1
    assert "Company/Home.md" in capsys.readouterr().err


def test_promotions_flow(master: Path, tmp_path: Path, capsys):
    seed_meta(master)
    from brain.promotions import draft_promotion
    draft_promotion(master, "bob", "Company/Frameworks/SOP.md",
                    "People/bob/Sessions/x.md", "Body.\n", "p-1", "2026-07-07")
    assert main(["promotions", "list", "--master", str(master)]) == 0
    assert "p-1" in capsys.readouterr().out
    assert main(["promotions", "approve", "p-1", "--master", str(master),
                 "--approver", "alice"]) == 0
    assert (master / "Company/Frameworks/SOP.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'brain.cli'`

- [ ] **Step 3: Implement**

`src/brain/cli.py`:

```python
"""brain — CLI for the multi-tenant company brain."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from brain.compiler import compile_all, compile_vault
from brain.promotions import PromotionError, approve, list_pending, reject, sweep
from brain.schemas import load_org, load_spaces
from brain.writeback import apply_writeback


def _load(master: Path):
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    return org, rules


def cmd_compile(args) -> int:
    master, out = Path(args.master), Path(args.out)
    org, rules = _load(master)
    if args.person:
        person = org.people.get(args.person)
        if person is None:
            print(f"unknown person: {args.person}", file=sys.stderr)
            return 1
        compile_vault(master, person, rules, out / person.id)
        print(f"compiled {person.id} -> {out / person.id}")
    else:
        results = compile_all(master, org, rules, out)
        for r in results:
            print(f"compiled {r.person_id}: {len(r.files)} files")
    return 0


def cmd_writeback(args) -> int:
    master, vault = Path(args.master), Path(args.vault)
    org, rules = _load(master)
    person = org.people.get(args.person)
    if person is None:
        print(f"unknown person: {args.person}", file=sys.stderr)
        return 1
    result = apply_writeback(master, vault, person, rules)
    if result.violations:
        print("REJECTED — nothing applied:", file=sys.stderr)
        for v in result.violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print(f"applied {len(result.applied)} change(s)")
    return 0


def cmd_promotions(args) -> int:
    master = Path(args.master)
    try:
        if args.action == "list":
            for p in list_pending(master):
                print(f"{p.id}  from={p.person_id}  target={p.target_path}")
        elif args.action == "sweep":
            moved = sweep(master, today=date.today().isoformat())
            print(f"swept {len(moved)} draft(s) into the pending queue")
        elif args.action == "approve":
            target = approve(master, args.id, approver=args.approver,
                             date=date.today().isoformat())
            print(f"approved {args.id} -> {target}")
        elif args.action == "reject":
            reject(master, args.id, reason=args.reason)
            print(f"rejected {args.id}")
    except PromotionError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brain")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compile", help="compile per-person vaults from master")
    c.add_argument("--master", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--person")
    c.set_defaults(func=cmd_compile)

    w = sub.add_parser("writeback", help="validate and apply a person's edits")
    w.add_argument("--master", required=True)
    w.add_argument("--vault", required=True)
    w.add_argument("--person", required=True)
    w.set_defaults(func=cmd_writeback)

    p = sub.add_parser("promotions", help="manage the promotion queue")
    p.add_argument("action", choices=["list", "sweep", "approve", "reject"])
    p.add_argument("id", nargs="?")
    p.add_argument("--master", required=True)
    p.add_argument("--approver", default="")
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_promotions)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/brain/cli.py tests/test_cli.py
git commit -m "feat: brain CLI (compile, writeback, promotions)"
```

---

### Task 10: `brain init` — master vault scaffolding + chief-of-staff protocol

**Files:**
- Create: `src/brain/templates.py`
- Modify: `src/brain/cli.py` (add `init` subcommand)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: CLI parser from Task 9.
- Produces: `scaffold_master(dest: Path, company: str) -> list[str]` in `brain.templates` (returns rel paths created); CLI `brain init <dir> --company NAME` (creates structure, git-inits, seed commit). The scaffold creates: `Company/{Home.md,Memory.md}`, `Company/{Decisions,Frameworks,Templates}/.gitkeep`, `Teams/.gitkeep`, `People/.gitkeep`, `Clients/.gitkeep`, `_meta/{org.yaml,spaces.yaml}`, `_meta/promotions/{pending,approved,rejected}/.gitkeep`, and a master-root `AGENTS.md` (the server chief-of-staff protocol — never compiled into personal vaults, per Task 3).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_init_scaffolds_master(tmp_path: Path):
    dest = tmp_path / "acme-brain"
    assert main(["init", str(dest), "--company", "Acme Co"]) == 0
    assert (dest / "Company/Home.md").exists()
    assert "Acme Co" in (dest / "Company/Memory.md").read_text()
    assert (dest / "_meta/spaces.yaml").exists()
    assert (dest / "_meta/promotions/pending/.gitkeep").exists()
    protocol = (dest / "AGENTS.md").read_text()
    assert "chief-of-staff" in protocol.lower()
    assert "Needs-Routing" in protocol
    assert (dest / ".git").is_dir()
    # org/spaces parse cleanly
    from brain.schemas import load_org, load_spaces
    load_org(dest / "_meta/org.yaml")
    load_spaces(dest / "_meta/spaces.yaml")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py::test_init_scaffolds_master -v`
Expected: FAIL — argparse error `invalid choice: 'init'` (exit code 2 raises SystemExit; pytest reports the failure)

- [ ] **Step 3: Implement**

`src/brain/templates.py`:

```python
"""Scaffolding content for a new company master vault."""

from __future__ import annotations

import subprocess
from pathlib import Path

ORG_YAML = """\
people:
  # id: {name: Full Name, roles: [admin], teams: [sales]}
  founder: {name: Founder, roles: [admin], teams: []}
"""

SPACES_YAML = """\
spaces:
  - {path: Company,     read: [everyone],        write: ["role:admin"]}
  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}
  - {path: "Clients/*", read: [everyone],        write: ["role:admin"]}
"""

CHIEF_OF_STAFF_PROTOCOL = """\
# Chief-of-Staff Protocol (server — full master vault)

This is the master vault. You are the company chief-of-staff agent with full
access. Personal agents see only their compiled slice; you maintain the whole.

## Transcript pipeline

When a transcript appears in any `People/<person>/Inbox/`:
1. Summarize it.
2. Extract decisions, action items (owner + deadline), and context updates.
3. Route:
   - Action items -> that person's `People/<person>/Actions/Action Tracker.md`
   - Client facts -> the matching `Clients/<client>/` file
   - Company-wide decisions -> `Company/Decisions/`
   - Session summary -> `People/<person>/Sessions/`
   - General insights -> `Company/Memory.md`
4. Archive the processed transcript into `People/<person>/Sessions/`.

If you cannot place an item confidently, add it to `Company/Needs-Routing.md`
instead of guessing. Doing nothing is always safer than routing wrongly.

## Dashboards

Keep `Company/Home.md` current as the priority dashboard: open actions by
owner, pending promotions, recent decisions.

## Boundaries

- Never move content out of a `People/` space except via an approved
  promotion in `_meta/promotions/`.
- `_meta/` is operational state; never surface its contents in shared notes.
- Drafts only for anything outward-facing: a human sends every message and
  approves every commitment.
"""


def _home_md(company: str) -> str:
    return (
        f"# {company} — Home\n\n"
        "## Priorities\n\n(maintained by the chief-of-staff agent)\n\n"
        "## Links\n\n- [[Memory]]\n- Decisions/\n- Frameworks/\n- Templates/\n"
    )


def _memory_md(company: str) -> str:
    return (
        f"# {company} — Company Memory\n\n"
        "Business overview, positioning, offers, team structure. Maintained by\n"
        "the chief-of-staff agent; substantive changes arrive via promotions or\n"
        "admin edits.\n"
    )


def scaffold_master(dest: Path, company: str) -> list[str]:
    files: dict[str, str] = {
        "AGENTS.md": CHIEF_OF_STAFF_PROTOCOL,
        "Company/Home.md": _home_md(company),
        "Company/Memory.md": _memory_md(company),
        "Company/Decisions/.gitkeep": "",
        "Company/Frameworks/.gitkeep": "",
        "Company/Templates/.gitkeep": "",
        "Teams/.gitkeep": "",
        "People/.gitkeep": "",
        "Clients/.gitkeep": "",
        "_meta/org.yaml": ORG_YAML,
        "_meta/spaces.yaml": SPACES_YAML,
        "_meta/promotions/pending/.gitkeep": "",
        "_meta/promotions/approved/.gitkeep": "",
        "_meta/promotions/rejected/.gitkeep": "",
    }
    created = []
    for rel, content in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        created.append(rel)
    subprocess.run(["git", "-C", str(dest), "init", "-b", "main"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(dest), "-c", "user.name=Brain Init",
                    "-c", "user.email=init@brain.local",
                    "commit", "-m", f"init: {company} master vault"],
                   capture_output=True, check=True)
    return created
```

Add to `src/brain/cli.py` — new command function and parser wiring:

```python
def cmd_init(args) -> int:
    from brain.templates import scaffold_master

    dest = Path(args.dir)
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} exists and is not empty", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    created = scaffold_master(dest, args.company)
    print(f"initialized {args.company} master vault at {dest} ({len(created)} files)")
    return 0
```

In `build_parser()`, before `return parser`:

```python
    i = sub.add_parser("init", help="scaffold a new company master vault")
    i.add_argument("dir")
    i.add_argument("--company", required=True)
    i.set_defaults(func=cmd_init)
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -v`
Expected: all tests pass (including the new init test)

- [ ] **Step 5: Commit**

```bash
git add src/brain/templates.py src/brain/cli.py tests/test_cli.py
git commit -m "feat: brain init scaffolds master vault with chief-of-staff protocol"
```

---

### Task 11: Hermes `company-brain` profile distribution + onboarding doc

**Files:**
- Create: `templates/company-brain-profile/README.md`
- Create: `templates/company-brain-profile/SOUL.md`
- Create: `templates/company-brain-profile/config.yaml`
- Create: `templates/company-brain-profile/skills/brain-protocol/SKILL.md`
- Create: `docs/onboarding.md`
- Test: `tests/test_profile_distribution.py`

**Interfaces:**
- Consumes: nothing from the Python package (static provisioning assets).
- Produces: a directory that a company copies into its own git repo and installs per employee with `hermes profile install <repo> --alias`. Constraints verified by test: `memory.write_approval: true`, `skills.write_approval: true`, no `memory.provider` key (external providers off by policy), `terminal.cwd` present as an explicit replace-me marker.

- [ ] **Step 1: Write the failing test**

`tests/test_profile_distribution.py`:

```python
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "templates/company-brain-profile"


def test_required_files_exist():
    for rel in ("README.md", "SOUL.md", "config.yaml",
                "skills/brain-protocol/SKILL.md"):
        assert (ROOT / rel).exists(), rel


def test_config_enforces_policies():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert cfg["memory"]["write_approval"] is True
    assert "provider" not in cfg["memory"]          # external memory OFF by policy
    assert cfg["skills"]["write_approval"] is True
    assert "REPLACE_WITH_VAULT_PATH" in cfg["terminal"]["cwd"]


def test_soul_and_skill_reference_the_vault_protocol():
    soul = (ROOT / "SOUL.md").read_text()
    assert "AGENTS.md" in soul
    skill = (ROOT / "skills/brain-protocol/SKILL.md").read_text()
    assert "promotion" in skill.lower()
    assert "Inbox" in skill
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_profile_distribution.py -v`
Expected: FAIL — missing files

- [ ] **Step 3: Create the distribution files**

`templates/company-brain-profile/README.md`:

```markdown
# company-brain — Hermes profile distribution

Provisions an employee's personal Chief-of-Staff agent against their compiled
brain vault.

## Install (per employee)

1. Fork/copy this directory into your company's provisioning repo.
2. Edit `config.yaml`: set `terminal.cwd` to the employee's synced vault path.
3. On the employee's machine:

   hermes profile install github.com/<your-org>/company-brain --alias

Credentials, memories, and sessions stay on the employee's machine.

## Deployment rule (do not skip)

Hermes profiles do NOT sandbox the filesystem. Run this agent only:
- on the employee's own device against their synced vault, or
- server-side in a container that mounts ONLY that person's compiled vault.

Never run multiple employees' profiles side-by-side on one uncontained host.
```

`templates/company-brain-profile/SOUL.md`:

```markdown
# Identity

You are a personal chief of staff. Your knowledge base is the brain vault in
your working directory — read its root AGENTS.md first; it defines your
spaces, routing rules, and the promotion protocol. The vault is the single
source of truth: keep knowledge there, not in chat history.

## Posture
- Draft, don't send: a human sends every outward message and approves every
  commitment or promotion.
- Route new information immediately (actions, sessions, client facts) per the
  vault protocol; when unsure, use Needs-Routing rather than guessing.
- Cite the source note for any fact you assert.
- Private means private: content in the People/ space is never summarized or
  quoted into shared spaces except through an approved promotion.

## Voice
- Concise, warm, professional. Lead with the answer, then the detail, then
  the ask.
```

`templates/company-brain-profile/config.yaml`:

```yaml
# company-brain profile — provisioned defaults
# Set terminal.cwd to the employee's synced vault before install.

terminal:
  backend: local
  cwd: "REPLACE_WITH_VAULT_PATH"

memory:
  memory_enabled: true
  user_profile_enabled: true
  write_approval: true
  # No external memory provider: the vault is the single source of truth.

skills:
  write_approval: true

display:
  tool_progress: new
```

`templates/company-brain-profile/skills/brain-protocol/SKILL.md`:

```markdown
---
name: brain-protocol
description: Operate the company brain vault — ingest, route, and promote knowledge. Use whenever processing new information (transcripts, meeting notes, emails, decisions) or when asked to share knowledge with the team.
---

# Brain Protocol

Your working directory is a compiled brain vault. The root AGENTS.md defines
your spaces and permissions — read it before acting.

## Ingest (new transcript or note in People/<you>/Inbox/)

1. Summarize the content.
2. Extract: decisions; action items (owner + deadline); context updates.
3. Route per the vault's routing rules (actions -> Actions/Action Tracker.md,
   summary -> Sessions/, durable facts -> Memory.md).
4. Archive the processed item into Sessions/. Unplaceable items go to
   Needs-Routing.md — never guess.

## Promote (share knowledge with the team)

1. Draft a sanitized note containing only what is being shared.
2. Save under People/<you>/Promotions/ with frontmatter:
   target-path: <shared space path>
   source: <originating note path>
3. Tell the owner it awaits their approval (`brain promotions list`). Never
   write directly into a read-only space; the write-back service rejects it.

## Maintain

- Keep People/<you>/Memory.md curated: durable facts only, consolidated.
- Surface stale actions and unprocessed Inbox items when asked for status.
```

`docs/onboarding.md`:

```markdown
# Pilot Onboarding — Company Brain

## 0. One-time company setup (operator)

    brain init /srv/brain/master --company "Acme Co"
    # edit /srv/brain/master/_meta/org.yaml  — add people, roles, teams
    # edit /srv/brain/master/_meta/spaces.yaml — adjust visibility if needed
    brain compile --master /srv/brain/master --out /srv/brain/compiled

Re-run `brain compile` on every master change (cron or a git post-commit hook).

## 1. Per-employee setup

1. Give the employee sync access to `/srv/brain/compiled/<person-id>` ONLY
   (e.g., per-person deploy key or per-person remote). Never to the master.
2. On their machine, clone it and open the folder in Obsidian:

       git clone <their-vault-remote> ~/brain

3. Install the agent profile (Hermes users):

       hermes profile install github.com/<your-org>/company-brain --alias
       # set terminal.cwd to ~/brain in the profile config before first run

   Claude Code users need no install — the vault's CLAUDE.md carries the
   same protocol.

## 2. Daily flow

- Transcripts and notes drop into `People/<you>/Inbox/`; the agent ingests
  and routes them.
- Edits sync back; the server runs `brain writeback` per person, then
  `brain promotions sweep` (agent drafts -> pending queue), then
  `brain compile` to refresh everyone.
- Sharing: the agent drafts promotions; approve with
  `brain promotions approve <id> --master ... --approver <you>`.

## Deployment rule

Personal agents run on the employee's device, or in a container mounting
only that person's vault. Hermes profiles do not sandbox the filesystem —
never co-host multiple employees' agents uncontained.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_profile_distribution.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the complete suite, then commit**

Run: `.venv/bin/python -m pytest -v`
Expected: all tests pass

```bash
git add templates/ docs/onboarding.md tests/test_profile_distribution.py
git commit -m "feat: Hermes company-brain profile distribution + pilot onboarding guide"
```
