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
