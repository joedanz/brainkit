"""Full-lifecycle end-to-end test across multiple users.

Unlike the focused tests in test_cli.py, this chains the whole operator loop in
one scenario — init -> populate -> compile -> writeback (accept + reject) ->
promotions (sweep/approve/reject) -> recompile — with five users whose roles and
teams exercise every read/write path, and asserts the isolation invariants that
are the product's core security claim.
"""

import json
import subprocess
from pathlib import Path

from brain.cli import main
from brain.resolver import readable_spaces
from brain.schemas import load_org, load_spaces

ORG_YAML = """\
people:
  dana:  {name: Dana Ops, roles: [admin], teams: []}
  alice: {name: Alice Nguyen, teams: [sales]}
  bob:   {name: Bob Rivera, teams: [ops]}
  carol: {name: Carol Vale, teams: [sales, ops]}
  evan:  {name: Evan Poole, teams: []}
"""

PEOPLE = ["dana", "alice", "bob", "carol", "evan"]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def _commit(master: Path, msg: str) -> None:
    _git(master, "add", "-A")
    subprocess.run(
        ["git", "-C", str(master), "-c", "user.name=Operator",
         "-c", "user.email=op@brain.local", "commit", "-q", "-m", msg],
        capture_output=True, check=True,
    )


def _commit_count(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


def _write(base: Path, rel: str, content: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _content_files(vault: Path) -> set[str]:
    return {
        p.relative_to(vault).as_posix()
        for p in vault.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }


def _populate(master: Path) -> None:
    (master / "_meta/org.yaml").write_text(ORG_YAML)
    # Explicit spaces so this lifecycle test owns its permission model rather than
    # inheriting the scaffold default (which is deny-by-default for Clients). Here
    # Clients are everyone-readable — the isolation asserts below depend on it.
    (master / "_meta/spaces.yaml").write_text(
        "spaces:\n"
        '  - {path: Company,     read: [everyone],        write: ["role:admin"]}\n'
        '  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}\n'
        '  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}\n'
        '  - {path: "Clients/*", read: [everyone],        write: ["role:admin"]}\n'
    )
    _write(master, "Company/Home.md",
           "# Northwind — Home\n\nSee [[Q3 Strategy]] and the [[Ops Runbook]].\n")
    _write(master, "Company/Decisions/Q3 Strategy.md", "Ship the pilot in Q3.\n")
    _write(master, "Company/Playbook/Sales.md", "Qualify, then close.\n")
    _write(master, "Teams/sales/Pipeline.md", "Acme renewal in progress.\n")
    _write(master, "Teams/sales/Deals/Acme.md", "Acme — $50k ARR.\n")
    _write(master, "Teams/ops/Ops Runbook.md", "How we run oncall.\n")
    _write(master, "Teams/ops/Oncall.md", "Primary: Bob.\n")
    for pid in PEOPLE:
        _write(master, f"People/{pid}/Memory.md", f"{pid} private memory.\n")
        _write(master, f"People/{pid}/Inbox/2026-07-07 standup.md",
               f"Raw standup transcript for {pid}.\n")
        _write(master, f"People/{pid}/Actions/Tracker.md", "- [ ] nothing yet\n")
    _write(master, "People/carol/Notes/café ☕ 计划.md", "Carol's unicode plan.\n")
    _write(master, "Clients/acme/Overview.md", "Acme Corp overview.\n")
    _write(master, "Clients/globex/Overview.md", "Globex Inc overview.\n")
    _commit(master, "populate: 5 users + varied data")


def test_full_multiuser_lifecycle(tmp_path: Path, capsys):
    master = tmp_path / "master"
    compiled = tmp_path / "compiled"

    # 1. init + populate --------------------------------------------------- #
    assert main(["init", str(master), "--company", "Northwind Traders"]) == 0
    assert (master / ".git").is_dir()
    _populate(master)
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    assert len(org.people) == 5

    # 2. compile all + isolation ------------------------------------------ #
    assert main(["compile", "--master", str(master), "--out", str(compiled)]) == 0
    exempt = {"AGENTS.md", "CLAUDE.md", ".brain-manifest.json", ".gitignore"}
    for pid in PEOPLE:
        vault = compiled / pid
        expected = set(readable_spaces(master, org.people[pid], rules))
        for rel in _content_files(vault):
            if rel in exempt or rel.endswith(("/AGENTS.md", "/CLAUDE.md")):
                continue
            top = rel.split("/")[0]
            space = "Company" if top == "Company" else "/".join(rel.split("/")[:2])
            assert space in expected, f"LEAK {pid}: {rel} (space {space})"
            assert not rel.startswith("_meta"), f"{pid} leaked _meta: {rel}"
        assert (vault / "AGENTS.md").exists() and (vault / "CLAUDE.md").exists()
        assert (vault / ".brain-manifest.json").exists()
        assert (vault / ".git").is_dir()

    # cross-tenant spot checks
    assert (compiled / "alice/Teams/sales/Pipeline.md").exists()
    assert not (compiled / "alice/Teams/ops").exists()
    assert not (compiled / "alice/People/bob").exists()
    assert (compiled / "carol/Teams/sales").exists()
    assert (compiled / "carol/Teams/ops").exists()
    assert not (compiled / "evan/Teams").exists()
    assert (compiled / "evan/Clients/acme/Overview.md").exists()

    # link stubbing: read-only Company/Home.md degrades the unreadable link
    alice_home = (compiled / "alice/Company/Home.md").read_text()
    assert "[[Ops Runbook]]" not in alice_home and "Ops Runbook" in alice_home
    assert "[[Q3 Strategy]]" in alice_home
    # dana writes Company -> her copy is not stubbed
    assert "[[Ops Runbook]]" in (compiled / "dana/Company/Home.md").read_text()

    # 3. writeback happy path --------------------------------------------- #
    av = compiled / "alice"
    _write(av, "People/alice/Sessions/summary.md", "Summary of the standup.\n")
    (av / "People/alice/Memory.md").write_text("alice updated memory.\n")
    _write(av, "Teams/sales/Deals/Newco.md", "Newco — new lead.\n")
    assert main(["writeback", "--master", str(master), "--vault", str(av),
                 "--person", "alice"]) == 0
    assert (master / "People/alice/Sessions/summary.md").exists()
    assert (master / "Teams/sales/Deals/Newco.md").exists()
    assert "alice updated memory" in (master / "People/alice/Memory.md").read_text()
    assert _git(master, "log", "-1", "--format=%ae").strip() == "alice@brain.local"

    # admin writes a read-only-for-others space
    dv = compiled / "dana"
    (dv / "Company/Home.md").write_text("# Northwind — Home (curated by Dana)\n")
    assert main(["writeback", "--master", str(master), "--vault", str(dv),
                 "--person", "dana"]) == 0
    assert "curated by Dana" in (master / "Company/Home.md").read_text()

    # 4. writeback rejection (whole changeset) ---------------------------- #
    bv = compiled / "bob"
    before_home = (master / "Company/Home.md").read_text()
    before_mem = (master / "People/bob/Memory.md").read_text()
    (bv / "Company/Home.md").write_text("bob defaced this\n")
    (bv / "People/bob/Memory.md").write_text("bob legit edit\n")
    assert main(["writeback", "--master", str(master), "--vault", str(bv),
                 "--person", "bob"]) == 1
    assert "REJECTED" in capsys.readouterr().err
    assert (master / "Company/Home.md").read_text() == before_home
    assert (master / "People/bob/Memory.md").read_text() == before_mem  # all-or-nothing

    # 5. promotions: sweep / approve / reject ----------------------------- #
    cv = compiled / "carol"
    _write(cv, "People/carol/Promotions/acme-note.md",
           "---\ntarget-path: Clients/acme/Meeting Notes.md\n"
           "source: People/carol/Sessions/acme-call.md\n---\nAcme asked for SSO by Q4.\n")
    _write(cv, "People/carol/Promotions/new-sop.md",
           "---\ntarget-path: Company/Decisions/New SOP.md\n"
           "source: People/carol/Memory.md\n---\nProposed onboarding SOP.\n")
    _write(cv, "People/carol/Promotions/bad-target.md",
           "---\ntarget-path: People/dana/Secret.md\n"
           "source: People/carol/Memory.md\n---\nShould never be promotable.\n")
    assert main(["writeback", "--master", str(master), "--vault", str(cv),
                 "--person", "carol"]) == 0

    assert main(["promotions", "sweep", "--master", str(master)]) == 0
    pending = master / "_meta/promotions/pending"
    assert (pending / "carol-acme-note.md").exists()
    assert (pending / "carol-new-sop.md").exists()
    assert (master / "People/carol/Promotions/bad-target.md").exists()  # not swept

    assert main(["promotions", "list", "--master", str(master)]) == 0
    listing = capsys.readouterr().out
    assert "carol-acme-note" in listing and "carol-new-sop" in listing

    assert main(["promotions", "approve", "carol-acme-note",
                 "--master", str(master), "--approver", "dana"]) == 0
    note = master / "Clients/acme/Meeting Notes.md"
    assert note.exists() and "SSO by Q4" in note.read_text()
    assert "approved-by: dana" in note.read_text()
    assert (master / "_meta/promotions/approved/carol-acme-note.md").exists()

    assert main(["promotions", "reject", "carol-new-sop",
                 "--master", str(master), "--reason", "not this quarter"]) == 0
    rejected = master / "_meta/promotions/rejected/carol-new-sop.md"
    assert rejected.exists() and "not this quarter" in rejected.read_text()
    assert not (master / "Company/Decisions/New SOP.md").exists()
    _commit(master, "promotions applied")

    # 6. recompile: convergence + idempotency ----------------------------- #
    assert main(["compile", "--master", str(master), "--out", str(compiled)]) == 0
    for pid in PEOPLE:  # Clients read: everyone -> approved note reaches all
        assert (compiled / pid / "Clients/acme/Meeting Notes.md").exists()
    assert (compiled / "alice/People/alice/Sessions/summary.md").exists()  # round-trip

    before = _commit_count(compiled / "alice")
    assert main(["compile", "--master", str(master), "--out", str(compiled)]) == 0
    assert _commit_count(compiled / "alice") == before  # no-op compile, no new commit


def test_cycle_and_doctor_e2e(tmp_path: Path, capsys):
    """The scheduled operator loop (`brain cycle`) plus the `brain doctor` gate,
    driven end-to-end through the CLI exactly as cron/a git hook would.
    """
    master = tmp_path / "master"
    compiled = tmp_path / "compiled"

    assert main(["init", str(master), "--company", "Northwind Traders"]) == 0
    _populate(master)

    # First compile materializes every vault.
    assert main(["compile", "--master", str(master), "--out", str(compiled)]) == 0

    # doctor on a clean master + compiled root: no error findings, exit 0. ---- #
    capsys.readouterr()  # drop buffered compile output
    assert main(["doctor", "--master", str(master), "--out", str(compiled),
                 "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert not [f for f in report["findings"] if f["severity"] == "error"]

    # One cycle with a valid edit + a promotion draft, and one out-of-scope
    # edit that must be rejected without blocking anyone else. ---------------- #
    av = compiled / "alice"
    (av / "People/alice/Memory.md").write_text("alice updated via cycle.\n")
    _write(av, "People/alice/Promotions/acme-sso.md",
           "---\ntarget-path: Clients/acme/Meeting Notes.md\n"
           "source: People/alice/Memory.md\n---\nAcme wants SSO by Q4.\n")
    bv = compiled / "bob"
    (bv / "Company/Home.md").write_text("bob defaced this\n")  # read-only for bob
    before_home = (master / "Company/Home.md").read_text()

    # writeback-all -> sweep -> recompile in one command; bob's rejection -> 1.
    capsys.readouterr()
    assert main(["cycle", "--master", str(master), "--out", str(compiled),
                 "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    statuses = {w["person_id"]: w["status"] for w in report["writebacks"]}
    assert statuses["alice"] == "applied"
    assert statuses["bob"] == "rejected"
    assert report["swept"] == 1
    assert report["compiled"] == len(PEOPLE)
    assert report["pending"] == 1

    # alice's edit and draft landed in master; bob's deface never did.
    assert "alice updated via cycle" in (master / "People/alice/Memory.md").read_text()
    assert (master / "_meta/promotions/pending/alice-acme-sso.md").exists()
    assert (master / "Company/Home.md").read_text() == before_home
    # Recompile refreshed the vaults: alice's swept draft is gone, and bob's
    # out-of-scope deface was reverted from his slice.
    assert not (compiled / "alice/People/alice/Promotions/acme-sso.md").exists()
    assert "defaced" not in (compiled / "bob/Company/Home.md").read_text()

    # doctor catches injected corruption across all three surfaces. ---------- #
    (compiled / "alice/_meta").mkdir()
    (compiled / "alice/_meta/org.yaml").write_text("people: {}\n")   # _meta leak
    (master / "Company/evil.md").symlink_to(master / "People/bob/Memory.md")
    (compiled / "bob/.brain-manifest.json").write_text("{}")  # valid JSON, wrong shape

    capsys.readouterr()
    assert main(["doctor", "--master", str(master), "--out", str(compiled),
                 "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    checks = {(f["check"], f["severity"]) for f in report["findings"]}
    assert ("symlinks", "error") in checks
    assert ("compiled", "error") in checks
    messages = " ".join(f["message"] for f in report["findings"])
    assert "_meta/ present" in messages          # security leak surfaced
    assert "unreadable manifest" in messages     # wrong-shape manifest, not a crash

    # Without --out, doctor skips compiled-vault checks but still flags the
    # master-side symlink.
    capsys.readouterr()
    assert main(["doctor", "--master", str(master), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(f["check"] == "symlinks" for f in report["findings"])
    assert not [f for f in report["findings"] if f["check"] == "compiled"]
