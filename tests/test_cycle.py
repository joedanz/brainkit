import json
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
