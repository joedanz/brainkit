import subprocess
from pathlib import Path

import pytest

from brain.rename import RenameError, rename_entities
from brain.schemas import SchemaError, load_config, load_spaces


def _master(tmp_path: Path) -> Path:
    m = tmp_path / "master"
    (m / "_meta").mkdir(parents=True)
    (m / "_meta/org.yaml").write_text(
        "people:\n  joe: {name: Joe}\n  mary: {name: Mary}\n")
    (m / "_meta/spaces.yaml").write_text(
        "spaces:\n"
        '  - {path: Company,     read: [everyone],        write: ["role:admin"]}\n'
        '  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}\n'
        "\n"
        "  # Clients are deny-by-default. An exact per-client rule overrides:\n"
        '  #   - {path: "Clients/Acme", read: ["role:admin", "person:alice"], write: ["role:admin"]}\n'
        '  - {path: "Clients/*", read: ["role:admin"], write: ["role:admin"]}\n'
        '  - {path: "Clients/Acme", read: ["role:admin", "person:joe"], write: ["role:admin", "person:joe"]}\n')
    (m / "Clients/Acme").mkdir(parents=True)
    (m / "Clients/Acme/Acme.md").write_text("# Acme\n")
    (m / "People/joe/ClientRequests").mkdir(parents=True)
    (m / "People/joe/ClientRequests/2026-07-23-smith.md").write_text(
        "---\nclient-name: Smith\nowner: joe\nentity: client\n"
        "source: t\ncreated: 2026-07-23\n---\nbody\n")
    (m / "_meta/shares/pending").mkdir(parents=True)
    (m / "_meta/shares/pending/req1.md").write_text(
        "---\nspace: Clients/Acme\nshare-with: person:mary\naccess: read\n"
        "action: share\nfrom: joe\ncreated: 2026-07-23\n---\n")
    (m / "_meta/promotions/pending").mkdir(parents=True)
    (m / "_meta/promotions/pending/p1.md").write_text(
        "---\ntarget-path: Clients/Acme/Plan.md\nfrom: joe\nmode: create\n"
        "created: 2026-07-23\n---\nplan\n")
    subprocess.run(["git", "-C", str(m), "init", "-b", "main"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(m), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(m), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-m", "seed"], check=True, capture_output=True)
    return m


def test_rename_moves_tree_and_rewrites_everything(tmp_path):
    m = _master(tmp_path)
    rep = rename_entities(m, "Vendors", "vendor")
    assert rep.moved_tree
    assert (m / "Vendors/Acme/Acme.md").is_file()
    assert not (m / "Clients").exists()
    rules = load_spaces(m / "_meta/spaces.yaml")           # still parses
    paths = [r.path for r in rules]
    assert "Vendors/*" in paths and "Vendors/Acme" in paths
    assert not any(p.startswith("Clients") for p in paths)
    text = (m / "_meta/spaces.yaml").read_text()
    assert "# Vendors are deny-by-default" in text          # comments preserved+renamed
    req = (m / "People/joe/VendorRequests/2026-07-23-smith.md").read_text()
    assert "vendor-name: Smith" in req and "entity: vendor" in req
    assert "client-name" not in req
    share = (m / "_meta/shares/pending/req1.md").read_text()
    assert "space: Vendors/Acme" in share
    promo = (m / "_meta/promotions/pending/p1.md").read_text()
    assert "target-path: Vendors/Acme/Plan.md" in promo
    assert load_config(m).entities == "Vendors"
    assert rep.committed
    log = subprocess.run(["git", "-C", str(m), "log", "--oneline"],
                         check=True, capture_output=True, text=True).stdout
    assert "rename-entities: Clients -> Vendors" in log


def test_rename_same_name_is_a_noop(tmp_path):
    m = _master(tmp_path)
    rep = rename_entities(m, "Clients", "client")
    assert not rep.moved_tree and rep.rules_rewritten == 0 and not rep.committed


def test_rename_refuses_existing_target_dir(tmp_path):
    m = _master(tmp_path)
    (m / "Vendors").mkdir()
    with pytest.raises(RenameError):
        rename_entities(m, "Vendors")


def test_rename_validates_names(tmp_path):
    m = _master(tmp_path)
    with pytest.raises(SchemaError):
        rename_entities(m, "People")


def test_rename_leaves_malformed_pending_file_untouched(tmp_path):
    m = _master(tmp_path)
    poison = m / "People/joe/ClientRequests/poison.md"
    poison.write_bytes(b"\xff\xfe not utf8")
    before = poison.read_bytes()
    rename_entities(m, "Vendors")
    after = (m / "People/joe/VendorRequests/poison.md").read_bytes()
    assert after == before


def test_rename_is_rerunnable_after_partial_completion(tmp_path):
    m = _master(tmp_path)
    # simulate a crash after the tree move but before anything else
    (m / "Clients").rename(m / "Vendors")
    rename_entities(m, "Vendors", "vendor")
    assert (m / "Vendors/Acme").is_dir()
    assert load_config(m).entities == "Vendors"
    assert "vendor-name: Smith" in \
        (m / "People/joe/VendorRequests/2026-07-23-smith.md").read_text()


def test_rename_then_cycle_processes_all_pending_state(tmp_path):
    from brain.cycle import run_cycle
    m = _master(tmp_path)
    rename_entities(m, "Vendors", "vendor")
    out = tmp_path / "out"
    report = run_cycle(m, out, "2026-07-24")
    assert report.ok
    # the pending entity request provisioned under the NEW tree
    assert report.clients_created == 1
    assert (m / "Vendors/Smith/Smith.md").is_file()
    # the rewritten pending share is still valid: approving it grants mary.
    # mary is the recipient (person:mary) — she consents to her own share;
    # joe (the requester, with no admin role) may not decide it.
    from brain.shares import approve_share, list_pending_shares
    pending = list_pending_shares(m)
    assert len(pending) == 1 and pending[0]["space"] == "Vendors/Acme"
    approve_share(m, pending[0]["id"], approver="mary", date="2026-07-24")
    rules = load_spaces(m / "_meta/spaces.yaml")
    acme = next(r for r in rules if r.path == "Vendors/Acme")
    assert "person:mary" in acme.read


def _family_master(tmp_path):
    from brain.schemas import make_config
    from brain.templates import scaffold_master
    master = tmp_path / "family-master"
    scaffold_master(master, "Fam",
                    make_config("Projects", "project", "Family"))
    return master


def test_rename_entities_preserves_shared(tmp_path):
    master = _family_master(tmp_path)
    rename_entities(master, "Ventures", "venture")
    cfg = load_config(master)
    assert (cfg.entities, cfg.entity, cfg.shared) == ("Ventures", "venture", "Family")


def test_rename_entities_to_shared_name_rejected(tmp_path):
    master = _family_master(tmp_path)
    with pytest.raises(SchemaError):
        rename_entities(master, "Family")   # policy, not dir-collision


def test_rename_preserves_the_charter(tmp_path):
    """Renaming the tree must not quietly erase the one config value a human
    wrote in prose — nobody would think to re-check it after a rename."""
    from brain.rename import rename_entities
    from brain.schemas import load_config, make_config
    from brain.templates import config_yaml, scaffold_master

    cfg = make_config("Clients", None, "Company", "Bespoke luxury travel.")
    scaffold_master(tmp_path, "Acme", cfg)
    (tmp_path / "_meta/config.yaml").write_text(config_yaml(cfg))

    rename_entities(tmp_path, "Vendors", None)

    after = load_config(tmp_path)
    assert after.entities == "Vendors"
    assert after.charter == "Bespoke luxury travel."
