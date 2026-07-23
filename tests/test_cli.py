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
    assert "captured YYYY-MM" in protocol    # today's-date fallback when source undated
    # fact-line and entity-page conventions are part of the protocol
    assert "## Facts and entities" in protocol
    assert "[from:: YYYY-MM]" in protocol
    assert "[until:: date]" in protocol
    assert "entity: client" in protocol
    # typed-relation authoring: agents declare up/down/same/prev/next edges
    assert "## Typed relations" in protocol
    assert "up: [[Acme]]" in protocol
    assert "brain graph" in protocol
    # Home is the live dashboard; Memory is the overview/map — distinct jobs,
    # not two copies of the same folder index (regression: they looked alike).
    home = (dest / "Company/Home.md").read_text()
    memory = (dest / "Company/Memory.md").read_text()
    assert "dashboard" in home.lower()
    assert "## Priorities" in home
    assert "## Links" not in home           # no folder index duplicating Memory
    assert "## Priorities" not in memory
    assert home.rstrip() != memory.rstrip()
    assert (dest / ".git").is_dir()
    # org/spaces parse cleanly
    from brain.schemas import load_org, load_spaces
    load_org(dest / "_meta/org.yaml")
    load_spaces(dest / "_meta/spaces.yaml")


def test_cli_facts_json(master, tmp_path, capsys):
    from brain.compiler import compile_vault
    from brain.embeddings import FakeEmbeddingProvider
    from brain.indexer import build_index
    from tests.conftest import ALICE, RULES

    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme.md").write_text(
        "---\nentity: client\n---\n# Acme\n\n"
        "- Sarah Kim is our main contact [from:: 2026-01]\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)

    assert main(["facts", "--vault", str(vault), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["facts"][0]["statement"] == "Sarah Kim is our main contact"

    assert main(["facts", "--vault", str(tmp_path / "nope")]) == 1


def test_cli_graph_walks_typed_edges(master, tmp_path, capsys):
    from brain.compiler import compile_vault
    from brain.embeddings import FakeEmbeddingProvider
    from brain.indexer import build_index
    from tests.conftest import ALICE, RULES

    (master / "Company/Projects").mkdir(parents=True, exist_ok=True)
    (master / "Company/Projects/Kickoff.md").write_text(
        "---\nup: [[Home]]\n---\n# Kickoff\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)

    rc = main(["graph", "Company/Projects/Kickoff.md", "--vault", str(vault), "--rel", "up"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "—up→" in out and "(explicit)" in out


def test_cli_graph_resolves_bare_filename(master, tmp_path, capsys):
    # Regression for the stem-fallback bug: a filename-with-extension like
    # "Kickoff.md" must resolve via _stem(note) == _stem(rel), not a raw
    # lowercase-string comparison against the full rel path.
    from brain.compiler import compile_vault
    from brain.embeddings import FakeEmbeddingProvider
    from brain.indexer import build_index
    from tests.conftest import ALICE, RULES

    (master / "Company/Projects").mkdir(parents=True, exist_ok=True)
    (master / "Company/Projects/Kickoff.md").write_text(
        "---\nup: [[Home]]\n---\n# Kickoff\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)

    rc = main(["graph", "Kickoff.md", "--vault", str(vault)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "—up→" in out and "(explicit)" in out


def test_cli_graph_unknown_note_fails(master, tmp_path, capsys):
    from brain.compiler import compile_vault
    from brain.embeddings import FakeEmbeddingProvider
    from brain.indexer import build_index
    from tests.conftest import ALICE, RULES

    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)

    rc = main(["graph", "nope.md", "--vault", str(vault)])
    assert rc == 1
    assert "not in index" in capsys.readouterr().err


def test_promotions_show_renders_patch_diff(master: Path, capsys):
    seed_meta(master)
    from brain.promotions import draft_promotion
    import hashlib
    page = master / "Company/Intel/Portugal.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Portugal\nOld claim.\n")
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/Portugal.md",
        source="s", body="# Portugal\nNew claim.\n", promo_id="p-s1",
        created="2026-07-21", mode="patch",
        base_hash=hashlib.sha256(page.read_bytes()).hexdigest(),
    )
    assert main(["promotions", "show", "p-s1", "--master", str(master)]) == 0
    out = capsys.readouterr().out
    assert "mode: patch" in out
    assert "-Old claim." in out
    assert "+New claim." in out


def test_promotions_show_prints_body_for_create(master: Path, capsys):
    seed_meta(master)
    from brain.promotions import draft_promotion
    draft_promotion(
        master, person_id="bob", target_path="Company/Playbook/SOP.md",
        source="s", body="Step one.\n", promo_id="p-s2", created="2026-07-21",
    )
    assert main(["promotions", "show", "p-s2", "--master", str(master)]) == 0
    assert "Step one." in capsys.readouterr().out


def test_promotions_show_unknown_id_errors(master: Path, capsys):
    seed_meta(master)
    assert main(["promotions", "show", "p-nope", "--master", str(master)]) == 1


def test_promotions_show_noop_patch_prints_no_changes(master: Path, capsys):
    seed_meta(master)
    from brain.promotions import draft_promotion
    import hashlib
    page = master / "Company/Intel/Same.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("identical\n")
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/Same.md",
        source="s", body="identical\n", promo_id="p-s4", created="2026-07-21",
        mode="patch",
        base_hash=hashlib.sha256(page.read_bytes()).hexdigest(),
    )
    assert main(["promotions", "show", "p-s4", "--master", str(master)]) == 0
    out = capsys.readouterr().out
    assert "(no changes" in out
    assert out.rstrip().endswith("(no changes — proposed page is identical to the current one)")


def test_init_custom_entities_scaffolds_config_and_tree(tmp_path: Path):
    dest = tmp_path / "vault"
    rc = main(["init", str(dest), "--company", "Acme",
               "--entities", "Families", "--entity", "family"])
    assert rc == 0
    assert (dest / "Families/.gitkeep").exists()
    assert not (dest / "Clients").exists()
    assert (dest / "_meta/config.yaml").read_text() == \
        "entities: Families\nentity: family\n"
    spaces = (dest / "_meta/spaces.yaml").read_text()
    assert '"Families/*"' in spaces and "Clients" not in spaces
    agents = (dest / "AGENTS.md").read_text()
    assert "Families/<family>" in agents and "Clients" not in agents


def test_init_default_writes_default_config(tmp_path: Path):
    dest = tmp_path / "vault"
    rc = main(["init", str(dest), "--company", "Acme"])
    assert rc == 0
    assert (dest / "_meta/config.yaml").read_text() == \
        "entities: Clients\nentity: client\n"
    assert (dest / "Clients/.gitkeep").exists()


def test_init_rejects_reserved_entities_before_writing(tmp_path: Path):
    dest = tmp_path / "vault"
    rc = main(["init", str(dest), "--company", "Acme",
               "--entities", "People"])
    assert rc == 1
    assert not dest.exists()


def test_rename_entities_cli(tmp_path):
    # scaffold a real vault via init, then rename it
    dest = tmp_path / "vault"
    assert main(["init", str(dest), "--company", "Acme"]) == 0
    rc = main(["rename-entities", "--master", str(dest),
               "--entities", "Vendors"])
    assert rc == 0
    assert (dest / "Vendors").is_dir() and not (dest / "Clients").exists()
    assert "Vendors" in (dest / "_meta/config.yaml").read_text()


def test_rename_entities_cli_rejects_reserved(tmp_path):
    dest = tmp_path / "vault"
    assert main(["init", str(dest), "--company", "Acme"]) == 0
    assert main(["rename-entities", "--master", str(dest),
                 "--entities", "People"]) == 1


def test_cli_shares_list_approve(master: Path, tmp_path: Path, capsys):
    from brain.shares import amend_space_rule  # noqa: F401 (import check)
    seed_meta(master)
    (master / "Clients/acme").mkdir(exist_ok=True, parents=True)
    # give bob an exact rule so he owns Clients/acme, then queue a share
    with (master / "_meta/spaces.yaml").open("a") as fh:
        fh.write('  - {path: "Clients/acme", read: ["role:admin", "person:bob"], write: ["role:admin", "person:bob"]}\n')
    from brain.shares import request_share, sweep_shares
    from brain.schemas import load_org
    request_share(master, "bob", "Clients/acme", "person:alice", "read", "2026-07-22")
    sweep_shares(master, load_org(master / "_meta/org.yaml"), today="2026-07-22")
    assert main(["shares", "list", "--master", str(master)]) == 0
    out = capsys.readouterr().out
    assert "Clients/acme" in out and "person:alice" in out
    sid = out.split()[0]
    assert main(["shares", "approve", sid, "--master", str(master),
                 "--approver", "alice"]) == 0
    assert main(["shares", "revoke", "--master", str(master),
                 "--space", "Clients/acme", "--subject", "person:alice"]) == 0
