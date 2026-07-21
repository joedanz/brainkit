from pathlib import Path

from brain.compiler import MANIFEST_NAME, compile_vault
from brain.contextgen import ROOT_LIMIT, SPACE_LIMIT, render_root_protocol
from brain.schemas import Person
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
    assert "Actions/Tracker" in text  # routing rules documented
    assert "Company/Frameworks" in text   # standards have a named home
    assert "must not already exist" in text  # new-file-only promotions


def test_root_protocol_mentions_shares_note():
    person = Person(id="bob", name="Bob Rivera", roles=(), teams=("ops",))
    text = render_root_protocol(person, [("Company", False), ("People/bob", True)])
    assert "People/bob/Shares.md" in text


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
