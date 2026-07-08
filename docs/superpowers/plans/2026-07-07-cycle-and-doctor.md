# brain cycle + brain doctor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `brain cycle` (one-shot server-side orchestration: writeback for every person → promotions sweep → compile-all, cron-friendly with JSON output) and `brain doctor` (integrity checker that surfaces silent failure modes: bad meta files, dangling subjects, unreachable spaces, symlinks, crashed compiles, `_meta` leakage, stuck promotion drafts).

**Architecture:** Two new pure-orchestration modules (`cycle.py`, `doctor.py`) that only compose the existing primitives (`apply_writeback`, `sweep`, `compile_all`, `list_pending`, resolver helpers) — no new state, no daemon, no database. Both are one-shot commands designed to run from cron or a git hook; `cycle` exits nonzero when any writeback was rejected, `doctor` exits nonzero when any error-severity finding exists.

**Tech Stack:** Python 3.12, PyYAML, pytest; `git` via subprocess. No new dependencies (YAGNI).

**Spec:** No separate spec — scope was agreed in conversation (gap analysis vs gbrain, 2026-07-07). Design decisions locked below.

## Global Constraints

- Privacy is **structural**; neither command may weaken it. `doctor` is read-only against master and compiled vaults. `cycle` only calls existing primitives.
- `_meta/` is server-only: its presence inside any compiled vault is a **security error** finding.
- Compiler fails closed; `cycle` must not change that. A rejected writeback does NOT halt the cycle: sweep and compile still run (the fresh compile commit reverts rejected edits server-side — fail-closed, matches `docs/onboarding.md` daily flow). Rejections surface via exit code 1 and the report.
- Writeback drift in a compiled vault (file hash ≠ manifest baseline) is the NORMAL "edits awaiting writeback" state → `doctor` reports it as `info`, never `error`.
- One person's failure never blocks another's: `cycle` isolates per-person writeback results; a vault missing its manifest is `skipped`, not fatal.
- All new code under `src/brain/`, `tests/`. Follow existing style: frozen dataclasses where immutable, argparse subcommands in `cli.py`, module docstrings stating the invariant the module protects.
- `--json` output must be a single JSON object on stdout (cron/scripting contract); human output goes to stdout, violations/errors summary to stderr where the existing CLI already does so.

## File Structure

```
src/brain/cycle.py        # run_cycle(master, out_root, today) -> CycleReport
src/brain/doctor.py       # run_doctor(master, out_root|None) -> list[Finding]
src/brain/cli.py          # add cmd_cycle, cmd_doctor + parsers   (modify)
tests/test_cycle.py       # cycle unit + CLI tests
tests/test_doctor.py      # doctor unit + CLI tests
docs/onboarding.md        # replace 3-command daily flow with `brain cycle`, add doctor (modify)
```

Existing interfaces consumed (do not modify them):

- `brain.schemas.load_org(path) -> Org`, `load_spaces(path) -> tuple[SpaceRule, ...]`, `SchemaError`
- `brain.writeback.apply_writeback(master, vault, person, rules) -> WritebackResult` (`.applied: list[Change]`, `.violations: list[str]`)
- `brain.promotions.sweep(master, today: str) -> list[Path]`, `list_pending(master) -> list[Promotion]`, `_pending_dir`, `_parse`, `_validate_target`, `PromotionError`
- `brain.compiler.compile_all(master, org, rules, out_root) -> list[CompileResult]`, `MANIFEST_NAME`
- `brain.resolver.enumerate_spaces(master)`, `_match_rule(space, rules)`, `RESERVED`

---

### Task 1: `cycle.py` — run_cycle orchestration

**Files:**
- Create: `src/brain/cycle.py`
- Test: `tests/test_cycle.py`

**Interfaces:**
- Consumes: `load_org`, `load_spaces`, `apply_writeback`, `sweep`, `compile_all`, `list_pending`, `MANIFEST_NAME`
- Produces (Task 2 relies on these exact names):
  - `PersonWriteback(person_id: str, status: str, applied: int, violations: list[str])` — status ∈ `"applied" | "rejected" | "skipped"`
  - `CycleReport(writebacks: list[PersonWriteback], swept: int, compiled: int, pending: int)` with property `ok: bool` (True iff no writeback has status `"rejected"`)
  - `run_cycle(master: Path, out_root: Path, today: str) -> CycleReport`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cycle.py
from pathlib import Path

from brain.cli import main
from brain.cycle import run_cycle

from .test_cli import seed_meta  # ORG/SPACES yaml + git init helper


def _first_compile(master: Path, tmp_path: Path) -> Path:
    out = tmp_path / "compiled"
    main(["compile", "--master", str(master), "--out", str(out)])
    return out


def test_cycle_applies_writebacks_sweeps_and_recompiles(master, tmp_path):
    seed_meta(master)
    out = _first_compile(master, tmp_path)

    # bob edits his own space (valid) and drafts a promotion
    (out / "bob/People/bob/Memory.md").write_text("Bob learned a thing.\n")
    promo = out / "bob/People/bob/Promotions/share-sop.md"
    promo.parent.mkdir(parents=True, exist_ok=True)
    promo.write_text(
        "---\ntarget-path: Company/Frameworks/SOP.md\n"
        "source: People/bob/Memory.md\n---\nThe SOP body.\n"
    )
    # promotion draft must reach master before sweep can see it
    report = run_cycle(master, out, today="2026-07-07")

    assert report.ok
    bob = next(w for w in report.writebacks if w.person_id == "bob")
    assert bob.status == "applied" and bob.applied == 2
    assert (master / "People/bob/Memory.md").read_text() == "Bob learned a thing.\n"
    assert report.swept == 1
    assert (master / "_meta/promotions/pending/bob-share-sop.md").exists()
    assert report.pending == 1
    assert report.compiled == 2  # alice + bob recompiled
    # recompile refreshed bob's vault from master (draft was swept out)
    assert not (out / "bob/People/bob/Promotions/share-sop.md").exists()


def test_cycle_rejection_isolated_and_reported(master, tmp_path):
    seed_meta(master)
    out = _first_compile(master, tmp_path)

    (out / "bob/Company/Home.md").write_text("defaced\n")          # out of scope
    (out / "alice/People/alice/Memory.md").write_text("ok edit\n")  # valid

    report = run_cycle(master, out, today="2026-07-07")

    assert not report.ok
    bob = next(w for w in report.writebacks if w.person_id == "bob")
    alice = next(w for w in report.writebacks if w.person_id == "alice")
    assert bob.status == "rejected" and bob.violations
    assert alice.status == "applied" and alice.applied == 1
    # master never took the defaced file; alice's edit landed
    assert (master / "Company/Home.md").read_text() != "defaced\n"
    assert (master / "People/alice/Memory.md").read_text() == "ok edit\n"
    # compile still ran for everyone: bob's vault was refreshed from master
    assert (out / "bob/Company/Home.md").read_text() != "defaced\n"


def test_cycle_skips_vault_without_manifest(master, tmp_path):
    seed_meta(master)
    out = tmp_path / "compiled"          # never compiled: no vaults yet
    report = run_cycle(master, out, today="2026-07-07")
    assert report.ok
    assert all(w.status == "skipped" for w in report.writebacks)
    assert report.compiled == 2          # first compile creates the vaults
    assert (out / "bob/People/bob/Memory.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cycle.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'brain.cycle'`

- [ ] **Step 3: Write the implementation**

```python
# src/brain/cycle.py
"""One-shot server cycle: writeback (all people) -> sweep -> compile-all.

Ordering is load-bearing: writebacks land person edits (including freshly
synced promotion drafts) in master BEFORE the sweep reads People/*/Promotions,
and compile runs last so every vault reflects the post-writeback master.

A rejected writeback never halts the cycle. Rejected edits are reverted
server-side by the fresh compile commit (fail closed); the rejection is
reported and flips CycleReport.ok so cron alerts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from brain.compiler import MANIFEST_NAME, compile_all
from brain.promotions import list_pending, sweep
from brain.schemas import load_org, load_spaces
from brain.writeback import apply_writeback


@dataclass
class PersonWriteback:
    person_id: str
    status: str  # "applied" | "rejected" | "skipped"
    applied: int = 0
    violations: list[str] = field(default_factory=list)


@dataclass
class CycleReport:
    writebacks: list[PersonWriteback]
    swept: int
    compiled: int
    pending: int

    @property
    def ok(self) -> bool:
        return all(w.status != "rejected" for w in self.writebacks)


def run_cycle(master: Path, out_root: Path, today: str) -> CycleReport:
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")

    writebacks: list[PersonWriteback] = []
    for person in org.people.values():
        vault = out_root / person.id
        if not (vault / MANIFEST_NAME).is_file():
            writebacks.append(PersonWriteback(person.id, "skipped"))
            continue
        result = apply_writeback(master, vault, person, rules)
        if result.violations:
            writebacks.append(
                PersonWriteback(person.id, "rejected", violations=result.violations)
            )
        else:
            writebacks.append(
                PersonWriteback(person.id, "applied", applied=len(result.applied))
            )

    swept = len(sweep(master, today=today))
    compiled = len(compile_all(master, org, rules, out_root))
    pending = len(list_pending(master))
    return CycleReport(
        writebacks=writebacks, swept=swept, compiled=compiled, pending=pending
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cycle.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `uv run pytest`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/brain/cycle.py tests/test_cycle.py
git commit -m "feat: run_cycle — one-shot writeback→sweep→compile orchestration"
```

---

### Task 2: CLI `brain cycle` + onboarding doc

**Files:**
- Modify: `src/brain/cli.py` (imports, `cmd_cycle`, parser wiring in `build_parser`)
- Modify: `docs/onboarding.md` (daily-flow section)
- Test: `tests/test_cycle.py` (append CLI tests)

**Interfaces:**
- Consumes: `run_cycle`, `CycleReport`, `PersonWriteback` from Task 1 (exact names above).
- Produces: `brain cycle --master M --out O [--json]`; exit 0 iff `report.ok`, else 1.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cycle.py`)

```python
import json


def test_cli_cycle_json_and_exit_codes(master, tmp_path, capsys):
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    capsys.readouterr()  # drop compile output

    (out / "alice/People/alice/Memory.md").write_text("note\n")
    code = main(["cycle", "--master", str(master), "--out", str(out), "--json"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert {w["person_id"]: w["status"] for w in report["writebacks"]} == {
        "alice": "applied", "bob": "applied",
    }

    (out / "bob/Company/Home.md").write_text("defaced\n")
    code = main(["cycle", "--master", str(master), "--out", str(out), "--json"])
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False


def test_cli_cycle_human_output(master, tmp_path, capsys):
    seed_meta(master)
    out = _first_compile(master, tmp_path)
    capsys.readouterr()
    code = main(["cycle", "--master", str(master), "--out", str(out)])
    assert code == 0
    text = capsys.readouterr().out
    assert "swept" in text and "compiled" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cycle.py -v -k cli_cycle`
Expected: FAIL — argparse error `invalid choice: 'cycle'` (SystemExit)

- [ ] **Step 3: Implement `cmd_cycle` and wire the parser**

In `src/brain/cli.py` add imports:

```python
import json
from dataclasses import asdict

from brain.cycle import run_cycle
```

Add the command function (after `cmd_init`):

```python
def cmd_cycle(args) -> int:
    report = run_cycle(Path(args.master), Path(args.out),
                       today=date.today().isoformat())
    if args.json:
        payload = asdict(report)
        payload["ok"] = report.ok
        print(json.dumps(payload, indent=2))
    else:
        for w in report.writebacks:
            line = f"writeback {w.person_id}: {w.status}"
            if w.status == "applied":
                line += f" ({w.applied} change(s))"
            print(line)
            for v in w.violations:
                print(f"  {v}", file=sys.stderr)
        print(f"swept {report.swept} draft(s); "
              f"compiled {report.compiled} vault(s); "
              f"{report.pending} promotion(s) pending")
    return 0 if report.ok else 1
```

Wire into `build_parser()` (after the `init` block):

```python
    y = sub.add_parser("cycle", help="writeback all, sweep promotions, recompile")
    y.add_argument("--master", required=True)
    y.add_argument("--out", required=True)
    y.add_argument("--json", action="store_true")
    y.set_defaults(func=cmd_cycle)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cycle.py -v`
Expected: 5 passed

- [ ] **Step 5: Update `docs/onboarding.md`**

Replace the second bullet of "## 2. Daily flow" (the one starting "Edits sync back; the server runs `brain writeback` per person...") with:

```markdown
- Edits sync back; the server runs one command per interval (cron or
  post-receive hook):

      brain cycle --master /srv/brain/master --out /srv/brain/compiled --json

  It applies every person's writeback (rejections are reported and revert
  on the next compile), sweeps agent drafts into the pending queue, and
  recompiles all vaults. Nonzero exit = at least one rejected writeback.
```

And append to "## 0. One-time company setup (operator)" after the compile line:

```markdown
    brain doctor --master /srv/brain/master --out /srv/brain/compiled
    # run after setup and from cron; nonzero exit = integrity error
```

- [ ] **Step 6: Full suite + commit**

Run: `uv run pytest`
Expected: all pass

```bash
git add src/brain/cli.py tests/test_cycle.py docs/onboarding.md
git commit -m "feat: brain cycle CLI with --json and cron-friendly exit codes"
```

---

### Task 3: `doctor.py` — Finding model + meta/subject/space checks

**Files:**
- Create: `src/brain/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `load_org`, `load_spaces`, `SchemaError`, `enumerate_spaces`, `_match_rule`
- Produces (Tasks 4–6 rely on these exact names):
  - `Finding(severity: str, check: str, message: str)` — frozen dataclass, severity ∈ `"error" | "warn" | "info"`
  - `run_doctor(master: Path, out_root: Path | None = None) -> list[Finding]`
  - internal check functions each returning `list[Finding]`: `_check_meta`, `_check_subjects`, `_check_rule_paths`, `_check_space_coverage`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doctor.py
from pathlib import Path

from brain.doctor import run_doctor

from .test_cli import ORG_YAML, SPACES_YAML, seed_meta


def _severities(findings, check):
    return [f.severity for f in findings if f.check == check]


def test_clean_master_has_no_errors(master):
    seed_meta(master)
    findings = run_doctor(master)
    assert not [f for f in findings if f.severity == "error"]


def test_broken_org_yaml_is_error_and_stops_dependent_checks(master):
    seed_meta(master)
    (master / "_meta/org.yaml").write_text("people: []\n")  # list, not mapping
    findings = run_doctor(master)
    assert _severities(findings, "meta") == ["error"]
    assert not [f for f in findings if f.check == "subjects"]  # skipped


def test_unknown_person_subject_is_error(master):
    seed_meta(master)
    (master / "_meta/spaces.yaml").write_text(
        SPACES_YAML + '  - {path: "Clients/acme", read: ["person:ghost"], write: []}\n'
    )
    findings = run_doctor(master)
    assert "error" in _severities(findings, "subjects")


def test_unused_team_subject_is_warn(master):
    seed_meta(master)
    (master / "_meta/spaces.yaml").write_text(
        SPACES_YAML + '  - {path: "Clients/acme", read: ["team:phantom"], write: []}\n'
    )
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "subjects")


def test_rule_path_matching_nothing_is_warn(master):
    seed_meta(master)
    (master / "_meta/spaces.yaml").write_text(
        SPACES_YAML + "  - {path: Handbook, read: [everyone], write: []}\n"
    )
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "rule-paths")


def test_space_with_no_rule_is_warn(master):
    seed_meta(master)
    (master / "Projects").mkdir()  # not a space; ignored by enumerate_spaces
    (master / "Teams/newteam/Notes.md").parent.mkdir(parents=True)
    (master / "Teams/newteam/Notes.md").write_text("x\n")
    # Teams/* rule covers it -> no warning expected for newteam
    findings = run_doctor(master)
    assert "warn" not in _severities(findings, "space-coverage")
    # now remove the wildcard rule so sales/ops/newteam become unreachable
    (master / "_meta/spaces.yaml").write_text(
        'spaces:\n  - {path: Company, read: [everyone], write: ["role:admin"]}\n'
    )
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "space-coverage")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'brain.doctor'`

- [ ] **Step 3: Write the implementation**

```python
# src/brain/doctor.py
"""Integrity checks for a company brain: surface what otherwise fails silently.

Read-only by design: doctor never mutates master or any compiled vault.
Severity contract: "error" = invariant broken (exit 1), "warn" = probably a
mistake but nothing leaks (fail-closed side), "info" = normal state worth
seeing (e.g. edits awaiting writeback).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from brain.resolver import _match_rule, enumerate_spaces
from brain.schemas import Org, SchemaError, SpaceRule, load_org, load_spaces


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" | "warn" | "info"
    check: str
    message: str


def _check_meta(master: Path) -> tuple[list[Finding], Org | None, tuple[SpaceRule, ...] | None]:
    findings: list[Finding] = []
    org = rules = None
    try:
        org = load_org(master / "_meta/org.yaml")
    except (SchemaError, FileNotFoundError, OSError) as e:
        findings.append(Finding("error", "meta", f"org.yaml: {e}"))
    try:
        rules = load_spaces(master / "_meta/spaces.yaml")
    except (SchemaError, FileNotFoundError, OSError) as e:
        findings.append(Finding("error", "meta", f"spaces.yaml: {e}"))
    return findings, org, rules


def _check_subjects(org: Org, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    teams = {t for p in org.people.values() for t in p.teams}
    roles = {r for p in org.people.values() for r in p.roles}
    for rule in rules:
        for subject in (*rule.read, *rule.write):
            if subject == "everyone" or "{name}" in subject:
                continue
            kind, _, value = subject.partition(":")
            if kind == "person" and value not in org.people:
                findings.append(Finding(
                    "error", "subjects",
                    f"rule {rule.path!r}: person {value!r} not in org.yaml"))
            elif kind == "team" and value not in teams:
                findings.append(Finding(
                    "warn", "subjects",
                    f"rule {rule.path!r}: no one is on team {value!r}"))
            elif kind == "role" and value not in roles:
                findings.append(Finding(
                    "warn", "subjects",
                    f"rule {rule.path!r}: no one holds role {value!r}"))
    return findings


def _check_rule_paths(master: Path, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in rules:
        base = rule.path[:-2] if rule.path.endswith("/*") else rule.path
        if not (master / base).is_dir():
            findings.append(Finding(
                "warn", "rule-paths",
                f"rule {rule.path!r}: {base!r} does not exist in master"))
    return findings


def _check_space_coverage(master: Path, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for space in enumerate_spaces(master):
        rule, _ = _match_rule(space, rules)
        if rule is None:
            findings.append(Finding(
                "warn", "space-coverage",
                f"space {space!r} matches no rule — unreachable by everyone"))
    return findings


def run_doctor(master: Path, out_root: Path | None = None) -> list[Finding]:
    findings, org, rules = _check_meta(master)
    if org is None or rules is None:
        return findings  # dependent checks are meaningless on broken meta
    findings += _check_subjects(org, rules)
    findings += _check_rule_paths(master, rules)
    findings += _check_space_coverage(master, rules)
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/brain/doctor.py tests/test_doctor.py
git commit -m "feat: brain doctor core — meta, subject, rule-path, coverage checks"
```

---

### Task 4: doctor — symlink + compiled-vault checks

**Files:**
- Modify: `src/brain/doctor.py`
- Test: `tests/test_doctor.py` (append)

**Interfaces:**
- Consumes: `Finding`, `run_doctor` from Task 3; `MANIFEST_NAME` from `brain.compiler`; `hashlib`, `json` stdlib.
- Produces: `_check_symlinks(master) -> list[Finding]`, `_check_compiled(master, org, out_root) -> list[Finding]`; `run_doctor` gains the optional `out_root` behavior (compiled checks only run when `out_root` is given).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_doctor.py`)

```python
from brain.cli import main


def _compile(master, tmp_path):
    out = tmp_path / "compiled"
    main(["compile", "--master", str(master), "--out", str(out)])
    return out


def test_symlink_in_master_is_error(master):
    seed_meta(master)
    (master / "Company/evil.md").symlink_to(master / "People/bob/Memory.md")
    findings = run_doctor(master)
    assert "error" in _severities(findings, "symlinks")


def test_compiled_checks_clean_and_missing_vault(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    findings = run_doctor(master, out)
    assert not [f for f in findings if f.severity == "error"]

    import shutil
    shutil.rmtree(out / "bob")
    findings = run_doctor(master, out)
    assert "warn" in _severities(findings, "compiled")  # bob never compiled


def test_meta_inside_vault_is_security_error(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    (out / "alice/_meta").mkdir()
    (out / "alice/_meta/org.yaml").write_text("people: {}\n")
    findings = run_doctor(master, out)
    assert "error" in _severities(findings, "compiled")


def test_crashed_compile_tombstone_is_error(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    (out / ".bob.old").mkdir()
    findings = run_doctor(master, out)
    assert "error" in _severities(findings, "compiled")


def test_drift_is_info_not_error(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    (out / "bob/People/bob/Memory.md").write_text("edited, not yet written back\n")
    findings = run_doctor(master, out)
    drift = [f for f in findings if f.check == "compiled" and "awaiting writeback" in f.message]
    assert drift and all(f.severity == "info" for f in drift)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_doctor.py -v -k "symlink or compiled or meta_inside or tombstone or drift"`
Expected: FAIL — no `symlinks`/`compiled` findings produced yet

- [ ] **Step 3: Implement the two checks**

Add to `src/brain/doctor.py` (imports at top):

```python
import hashlib
import json

from brain.compiler import MANIFEST_NAME
```

New functions:

```python
def _check_symlinks(master: Path) -> list[Finding]:
    findings: list[Finding] = []
    for p in sorted(master.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_symlink():
            findings.append(Finding(
                "error", "symlinks",
                f"{p.relative_to(master)} is a symlink — compiler and writeback "
                "skip links, so this content is dead weight or an escape attempt"))
    return findings


def _check_compiled(master: Path, org, out_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for person in org.people.values():
        vault = out_root / person.id
        for tomb in (out_root / f".{person.id}.old", out_root / f".{person.id}.building"):
            if tomb.exists():
                findings.append(Finding(
                    "error", "compiled",
                    f"{tomb.name}: leftover from a crashed compile — "
                    "next compile will attempt recovery; investigate first"))
        if not vault.is_dir():
            findings.append(Finding(
                "warn", "compiled", f"{person.id}: no compiled vault yet"))
            continue
        if (vault / "_meta").exists():
            findings.append(Finding(
                "error", "compiled",
                f"{person.id}: _meta/ present inside compiled vault — "
                "SECURITY: server-only data leaked to a person"))
        manifest_path = vault / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text())
        except (FileNotFoundError, ValueError) as e:
            findings.append(Finding(
                "error", "compiled", f"{person.id}: unreadable manifest ({e})"))
            continue
        drifted = 0
        for rel, sha in manifest["compiled"].items():
            f = vault / rel
            if not f.is_file():
                drifted += 1
            elif hashlib.sha256(f.read_bytes()).hexdigest() != sha:
                drifted += 1
        if drifted:
            findings.append(Finding(
                "info", "compiled",
                f"{person.id}: {drifted} file(s) awaiting writeback"))
    return findings
```

Extend `run_doctor` (replace its final lines):

```python
    findings += _check_subjects(org, rules)
    findings += _check_rule_paths(master, rules)
    findings += _check_space_coverage(master, rules)
    findings += _check_symlinks(master)
    if out_root is not None:
        findings += _check_compiled(master, org, out_root)
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/brain/doctor.py tests/test_doctor.py
git commit -m "feat: doctor symlink and compiled-vault checks (tombstones, _meta leak, drift)"
```

---

### Task 5: doctor — promotion-queue checks

**Files:**
- Modify: `src/brain/doctor.py`
- Test: `tests/test_doctor.py` (append)

**Interfaces:**
- Consumes: `_pending_dir`, `_parse`, `_validate_target`, `PromotionError` from `brain.promotions`.
- Produces: `_check_promotions(master) -> list[Finding]`, wired into `run_doctor` after `_check_symlinks`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_doctor.py`)

```python
def test_malformed_pending_promotion_is_warn(master):
    seed_meta(master)
    (master / "_meta/promotions/pending/broken.md").write_text("no frontmatter\n")
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "promotions")


def test_stuck_draft_without_target_is_warn(master):
    seed_meta(master)
    d = master / "People/bob/Promotions"
    d.mkdir(parents=True)
    (d / "no-target.md").write_text("---\nsource: x\n---\nBody.\n")
    findings = run_doctor(master)
    assert any(
        f.check == "promotions" and f.severity == "warn" and "no-target.md" in f.message
        for f in findings
    )


def test_pending_count_is_info(master):
    seed_meta(master)
    from brain.promotions import draft_promotion
    draft_promotion(master, "bob", "Company/Frameworks/SOP.md",
                    "People/bob/x.md", "Body.\n", "p-1", "2026-07-07")
    findings = run_doctor(master)
    assert any(f.check == "promotions" and f.severity == "info" for f in findings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_doctor.py -v -k promotion`
Expected: FAIL — no `promotions` findings produced yet

- [ ] **Step 3: Implement the check**

Add to `src/brain/doctor.py` (import at top):

```python
from brain.promotions import PromotionError, _parse, _pending_dir, _validate_target
```

New function:

```python
def _check_promotions(master: Path) -> list[Finding]:
    findings: list[Finding] = []
    pending_dir = _pending_dir(master)
    valid_pending = 0
    if pending_dir.is_dir():
        for f in sorted(pending_dir.glob("*.md")):
            try:
                promo = _parse(f)
                _validate_target(promo.target_path)
                valid_pending += 1
            except (KeyError, ValueError, PromotionError) as e:
                findings.append(Finding(
                    "warn", "promotions",
                    f"pending/{f.name}: malformed, will never be approvable ({e})"))
    if valid_pending:
        findings.append(Finding(
            "info", "promotions",
            f"{valid_pending} promotion(s) awaiting approval"))

    # Drafts sweep() will silently skip forever: missing/invalid target-path.
    for f in sorted(master.glob("People/*/Promotions/*.md")):
        if f.is_symlink():
            continue
        text = f.read_text()
        rel = f.relative_to(master)
        if text.count("---\n") < 2:
            findings.append(Finding(
                "warn", "promotions", f"{rel}: draft has no frontmatter, sweep skips it"))
            continue
        _, fm, _ = text.split("---\n", 2)
        meta = dict(
            (line.partition(": ")[0], line.partition(": ")[2])
            for line in fm.strip().splitlines()
        )
        try:
            _validate_target(meta.get("target-path", ""))
        except PromotionError as e:
            findings.append(Finding(
                "warn", "promotions", f"{rel}: sweep will never move it ({e})"))
    return findings
```

Wire into `run_doctor` after `_check_symlinks(master)`:

```python
    findings += _check_promotions(master)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/brain/doctor.py tests/test_doctor.py
git commit -m "feat: doctor promotion checks — malformed pending, stuck drafts"
```

---

### Task 6: CLI `brain doctor`

**Files:**
- Modify: `src/brain/cli.py`
- Test: `tests/test_doctor.py` (append CLI tests)

**Interfaces:**
- Consumes: `run_doctor`, `Finding` from Tasks 3–5.
- Produces: `brain doctor --master M [--out O] [--json]`; exit 1 iff any `error` finding, else 0.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_doctor.py`)

```python
import json as jsonlib


def test_cli_doctor_clean_exits_zero(master, tmp_path, capsys):
    seed_meta(master)
    out = _compile(master, tmp_path)
    capsys.readouterr()
    code = main(["doctor", "--master", str(master), "--out", str(out)])
    assert code == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_cli_doctor_error_exits_one_and_json(master, tmp_path, capsys):
    seed_meta(master)
    (master / "Company/evil.md").symlink_to(master / "People/bob/Memory.md")
    code = main(["doctor", "--master", str(master), "--json"])
    assert code == 1
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(f["check"] == "symlinks" for f in payload["findings"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_doctor.py -v -k cli_doctor`
Expected: FAIL — argparse error `invalid choice: 'doctor'` (SystemExit)

- [ ] **Step 3: Implement `cmd_doctor` and wire the parser**

In `src/brain/cli.py` add the import:

```python
from brain.doctor import run_doctor
```

Add the command function (after `cmd_cycle`):

```python
def cmd_doctor(args) -> int:
    out_root = Path(args.out) if args.out else None
    findings = run_doctor(Path(args.master), out_root)
    errors = [f for f in findings if f.severity == "error"]
    if args.json:
        print(json.dumps({
            "ok": not errors,
            "findings": [asdict(f) for f in findings],
        }, indent=2))
    else:
        for f in findings:
            print(f"[{f.severity.upper():5}] {f.check}: {f.message}")
        warns = sum(1 for f in findings if f.severity == "warn")
        print(f"{len(errors)} error(s), {warns} warning(s), "
              f"{len(findings)} finding(s) total")
    return 1 if errors else 0
```

Wire into `build_parser()` (after the `cycle` block):

```python
    d = sub.add_parser("doctor", help="check master and compiled vaults for integrity issues")
    d.add_argument("--master", required=True)
    d.add_argument("--out")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: 16 passed

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest`
Expected: all pass

```bash
git add src/brain/cli.py tests/test_doctor.py
git commit -m "feat: brain doctor CLI with --json and error-gated exit code"
```

---

## Self-Review Notes

- **Coverage:** cycle (orchestration, rejection isolation, first-run, CLI, JSON, exit codes, docs) — Tasks 1–2. doctor (meta, subjects, rule paths, space coverage, symlinks, tombstones, `_meta` leak, drift-as-info, promotions, CLI, JSON, exit codes) — Tasks 3–6. Both onboarding.md updates land in Task 2.
- **Type consistency:** `PersonWriteback`/`CycleReport`/`run_cycle` names match between Tasks 1–2; `Finding`/`run_doctor` and all `_check_*` names match between Tasks 3–6. `asdict` works because both report types are plain dataclasses (`CycleReport.ok` is a property, added to the JSON payload explicitly in `cmd_cycle`).
- **Private-import caveat:** doctor imports `_match_rule`, `_pending_dir`, `_parse`, `_validate_target` — private helpers from sibling modules. Acceptable inside the same package (mirrors how `compiler` uses `resolver`); promoting them to public names is deliberate non-scope (YAGNI).
