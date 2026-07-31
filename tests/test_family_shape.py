"""Family-shape E2E: the promotion loop, admin approval, and isolation under
a renamed shared top. Mirrors the fixture style of tests/test_e2e_lifecycle.py."""

from pathlib import Path


def _family_master(tmp_path: Path):
    from brain.schemas import make_config
    from brain.templates import scaffold_master
    master = tmp_path / "master"
    cfg = make_config("Projects", "project", "Family")
    scaffold_master(master, "The Family", cfg)
    (master / "_meta/org.yaml").write_text(
        "people:\n"
        "  dad:  {name: Dad,  roles: [admin, lead], teams: [parents]}\n"
        "  mom:  {name: Mom,  roles: [lead],        teams: [parents]}\n"
        "  kid1: {name: Kid One, teams: [kids]}\n"
        "  kid2: {name: Kid Two, teams: [kids]}\n")
    (master / "Teams/kids").mkdir(parents=True)
    (master / "Teams/kids/Chores Roster.md").write_text("# Roster\n")
    for pid, secret in [("mom", "MOMSECRET"), ("dad", "DADSECRET"),
                        ("kid1", "KID1SECRET"), ("kid2", "KID2SECRET")]:
        d = master / f"People/{pid}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Memory.md").write_text(f"# Memory\n{secret}\n")
    return master, cfg


def test_family_promotion_loop_and_isolation(tmp_path: Path):
    from brain.cycle import run_cycle
    from brain.promotions import approve, draft_into_space
    master, _cfg = _family_master(tmp_path)
    out = tmp_path / "out"
    run_cycle(master, out, today="2026-07-31")               # cycle 1: compile

    kid1 = out / "kid1"
    draft_into_space(kid1, "kid1", "Family/Playbook/Chores.md",
                     "test", "# Chores\nrotating wheel\n", "2026-07-31",
                     shared="Family")
    draft_into_space(kid1, "kid1", "Family/Playbook/Allowance.md",
                     "test", "NEVERAPPROVED-marker\n", "2026-07-31",
                     shared="Family")
    run_cycle(master, out, today="2026-07-31")               # cycle 2: sweep
    approve(master, "kid1-2026-07-31-chores", approver="dad", date="2026-07-31")
    run_cycle(master, out, today="2026-07-31")               # cycle 3: deliver

    # landed and delivered to the sibling and a parent:
    assert (master / "Family/Playbook/Chores.md").is_file()
    assert (out / "kid2/Family/Playbook/Chores.md").is_file()
    assert (out / "mom/Family/Playbook/Chores.md").is_file()
    # negative control: the unapproved draft reached no vault
    for pid in ("dad", "mom", "kid1", "kid2"):
        hits = [p for p in (out / pid).rglob("*.md")
                if "NEVERAPPROVED-marker" in p.read_text()]
        assert hits == [], (pid, hits)
    # isolation: no secret crosses vaults
    for reader, banned in [("kid1", ("MOMSECRET", "DADSECRET", "KID2SECRET")),
                           ("kid2", ("MOMSECRET", "DADSECRET", "KID1SECRET"))]:
        text = "\n".join(p.read_text() for p in (out / reader).rglob("*.md"))
        for marker in banned:
            assert marker not in text, (reader, marker)


def test_personal_shared_home(tmp_path: Path):
    from brain.cycle import run_cycle
    from brain.schemas import make_config
    from brain.templates import scaffold_master
    master = tmp_path / "m"
    scaffold_master(master, "Just Me", make_config("Clients", "client", "Home"))
    (master / "_meta/org.yaml").write_text(
        "people:\n  me: {name: Just Me, roles: [admin]}\n")
    out = tmp_path / "out"
    run_cycle(master, out, today="2026-07-31")
    assert (out / "me/Home/Home.md").is_file()
    agents = (out / "me/AGENTS.md").read_text()
    assert "Home/Decisions/" in agents and "Company/" not in agents
