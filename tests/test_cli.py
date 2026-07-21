import io
import json
import subprocess
from pathlib import Path

import pytest

from brain.cli import main

ORG_YAML = """\
people:
  alice: {name: Alice Nguyen, roles: [admin], teams: [sales], email: alice@acme.com}
  bob:   {name: Bob Rivera, teams: [ops], email: bob@acme.com}
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
    draft_promotion(master, "bob", "Company/Playbook/SOP.md",
                    "People/bob/Sessions/x.md", "Body.\n", "p-1", "2026-07-07")
    assert main(["promotions", "list", "--master", str(master)]) == 0
    assert "p-1" in capsys.readouterr().out
    assert main(["promotions", "approve", "p-1", "--master", str(master),
                 "--approver", "alice"]) == 0
    assert (master / "Company/Playbook/SOP.md").exists()


def test_promotions_approve_requires_approver(master: Path, capsys):
    seed_meta(master)
    from brain.promotions import draft_promotion
    draft_promotion(master, "bob", "Company/Playbook/SOP2.md",
                    "People/bob/Sessions/x.md", "Body.\n", "p-2", "2026-07-07")
    assert main(["promotions", "approve", "p-2", "--master", str(master)]) == 2
    assert "--approver" in capsys.readouterr().err
    assert main(["promotions", "approve", "p-2", "--master", str(master),
                 "--approver", "mallory"]) == 1
    assert "mallory" in capsys.readouterr().err
    assert not (master / "Company/Playbook/SOP2.md").exists()


def test_ingest_cli_by_person_stdin(master: Path, monkeypatch, capsys):
    seed_meta(master)
    monkeypatch.setattr("sys.stdin", io.StringIO("decided X\n"))
    code = main(["ingest", "--master", str(master), "--person", "bob",
                 "--title", "Standup", "--source", "chat"])
    assert code == 0
    out = capsys.readouterr().out
    assert "People/bob/Inbox/" in out
    assert list((master / "People/bob/Inbox").glob("*.md"))


def test_ingest_cli_by_email(master: Path, monkeypatch):
    seed_meta(master)
    monkeypatch.setattr("sys.stdin", io.StringIO("from the field\n"))
    code = main(["ingest", "--master", str(master),
                 "--from", "bob@acme.com", "--source", "email"])
    assert code == 0
    notes = list((master / "People/bob/Inbox").glob("*.md"))
    assert len(notes) == 1
    assert "from: bob@acme.com" in notes[0].read_text()


def test_ingest_cli_unknown_sender_fails_closed(master: Path, monkeypatch, capsys):
    seed_meta(master)
    monkeypatch.setattr("sys.stdin", io.StringIO("intruder\n"))
    code = main(["ingest", "--master", str(master),
                 "--from", "stranger@evil.com"])
    assert code == 1
    assert "refusing to ingest" in capsys.readouterr().err
    # Nothing written anywhere under People/.
    assert not list((master / "People").rglob("Inbox/*.md"))


def test_ingest_cli_unknown_person(master: Path, monkeypatch, capsys):
    seed_meta(master)
    monkeypatch.setattr("sys.stdin", io.StringIO("x\n"))
    code = main(["ingest", "--master", str(master), "--person", "nobody"])
    assert code == 1
    assert "unknown person" in capsys.readouterr().err


def test_ingest_cli_requires_exactly_one_identity(master: Path):
    seed_meta(master)
    with pytest.raises(SystemExit) as none:
        main(["ingest", "--master", str(master)])
    assert none.value.code == 2
    with pytest.raises(SystemExit) as both:
        main(["ingest", "--master", str(master),
              "--person", "bob", "--from", "bob@acme.com"])
    assert both.value.code == 2


def test_ingest_cli_json(master: Path, monkeypatch, capsys):
    seed_meta(master)
    monkeypatch.setattr("sys.stdin", io.StringIO("payload\n"))
    code = main(["ingest", "--master", str(master), "--person", "bob",
                 "--title", "Note", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["person_id"] == "bob"
    assert payload["rel_path"].startswith("People/bob/Inbox/")


def test_ingest_cli_file_input(master: Path, tmp_path: Path, capsys):
    seed_meta(master)
    src = tmp_path / "meeting-notes.md"
    src.write_text("# Kickoff\nWe start Monday.\n")
    code = main(["ingest", "--master", str(master), "--person", "bob",
                 "--file", str(src), "--source", "upload"])
    assert code == 0
    note = next((master / "People/bob/Inbox").glob("*.md"))
    text = note.read_text()
    assert "original-name: meeting-notes.md" in text
    assert "title: meeting-notes" in text  # default title from file stem


def test_init_scaffolds_master(tmp_path: Path):
    dest = tmp_path / "acme-brain"
    assert main(["init", str(dest), "--company", "Acme Co"]) == 0
    assert (dest / "Company/Home.md").exists()
    assert "Acme Co" in (dest / "Company/Memory.md").read_text()
    assert (dest / "_meta/spaces.yaml").exists()
    assert (dest / "_meta/promotions/pending/.gitkeep").exists()
    protocol = (dest / "AGENTS.md").read_text()
    assert "assistant" in protocol.lower()
    assert "Needs-Routing" in protocol
    # shared travel wiki scaffolded and wired into the assistant protocol
    assert (dest / "Company/Intel/Home.md").exists()
    assert (dest / "Company/Intel/Destinations/.gitkeep").exists()
    assert "Company/Intel/" in protocol
    assert "as of YYYY-MM" in protocol
    assert (dest / ".git").is_dir()
    # org/spaces parse cleanly
    from brain.schemas import load_org, load_spaces
    load_org(dest / "_meta/org.yaml")
    load_spaces(dest / "_meta/spaces.yaml")
