from pathlib import Path

from brain.compiler import MANIFEST_NAME, compile_vault
from brain.contextgen import ROOT_LIMIT, SPACE_LIMIT, render_root_protocol
from brain.schemas import Person, VaultConfig
from tests.conftest import ALICE, BOB, RULES

FAM = VaultConfig(entities="Families", entity="family")


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
    assert "Company/Playbook" in text   # standards have a named home
    assert "must not already exist" in text  # new-file-only promotions
    # personal Memory.md is a lean map: fat topics split into Notes/
    assert "lean overview" in text
    assert "People/bob/Notes/<Topic>.md" in text
    # shared travel wiki: distill articles into Intel entity pages
    assert "Company/Intel/" in text
    assert "distill, never archive" in text
    assert "as of YYYY-MM" in text                    # provenance on every claim
    assert "captured YYYY-MM" in text                 # today's-date fallback, labelled
    assert "uploaded filename" in text                # non-URL sources (PDF/screenshot)
    assert "mode: append" in text                     # additive page updates
    assert "mode: patch" in text                      # full-page revisions
    assert "never build a wiki outside it" in text    # blocks off-vault ~/wiki
    # typed-relation authoring guidance (up/down/same/prev/next frontmatter)
    assert "## Typed relations" in text
    assert "brain graph" in text


def test_root_protocol_mentions_shares_note():
    person = Person(id="bob", name="Bob Rivera", roles=(), teams=("ops",))
    text = render_root_protocol(person, [("Company", False), ("People/bob", True)])
    assert "People/bob/Shares.md" in text


def test_root_protocol_routes_third_parties_to_clients():
    from brain.contextgen import render_root_protocol
    from brain.schemas import Person

    joe = Person(id="joe", name="Joe Danziger", roles=(), teams=())
    text = render_root_protocol(joe, [("People/joe", True), ("Company", False)])

    low = text.lower()
    # third-party vs self
    assert "clientrequests" in low
    assert "third part" in low  # "third party"/"third parties"
    # owner-identity disambiguation (surname collision)
    assert "surname" in low or "same last name" in low
    # adaptive ask
    assert "ask" in low and "distinguishing" in low


def test_assistant_protocol_mentions_client_requests():
    from brain.templates import ASSISTANT_PROTOCOL
    assert "ClientRequests" in ASSISTANT_PROTOCOL or "Clients/<client>" in ASSISTANT_PROTOCOL
    assert "third part" in ASSISTANT_PROTOCOL.lower()


def test_assistant_protocol_has_multi_entity_capture():
    from brain.templates import ASSISTANT_PROTOCOL
    low = ASSISTANT_PROTOCOL.lower()
    assert "two homes" in low
    assert "intel/events" in low and "cross-link" in low


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


def test_root_protocol_carries_share_mechanic():
    import re
    from brain.contextgen import render_root_protocol
    from brain.schemas import Person

    joe = Person(id="joe", name="Joe Danziger", roles=(), teams=())
    text = render_root_protocol(joe, [("People/joe", True), ("Company", False)])
    low = text.lower()
    # frontmatter keys
    assert "share-with" in low and "access" in low and "action" in low
    # file structure
    assert "sharerequests" in low
    # revoke action
    assert "revoke" in low
    # keep-writing guarantee
    assert "keep writing" in low or "never blocks" in low
    # no-self-revoke rule: "you cannot" + "revoke" pattern (may have whitespace/newlines between)
    assert re.search(r"you\s+cannot.*revoke.*own", low, re.DOTALL) is not None


def test_assistant_protocol_carries_share_mechanic():
    import re
    from brain.templates import ASSISTANT_PROTOCOL

    low = ASSISTANT_PROTOCOL.lower()
    # frontmatter keys
    assert "share-with" in low and "access" in low and "action" in low
    # file structure
    assert "sharerequests" in low
    # revoke action
    assert "revoke" in low
    # keep-writing guarantee
    assert "keep writing" in low or "never blocks" in low
    # no-self-revoke rule: "you cannot" + "revoke" pattern (may have whitespace/newlines between)
    assert re.search(r"you\s+cannot.*revoke.*own", low, re.DOTALL) is not None


def test_skill_carries_share_mechanic():
    import re
    from pathlib import Path

    skill = Path("templates/company-brain-profile/skills/brain-protocol/SKILL.md").read_text().lower()
    # frontmatter keys
    assert "share-with" in skill and "access" in skill and "action" in skill
    # file structure
    assert "sharerequests" in skill
    # revoke action
    assert "revoke" in skill
    # keep-writing guarantee
    assert "keep writing" in skill or "never blocked" in skill
    # no-self-revoke rule: "cannot revoke" or "you cannot revoke" pattern
    assert "cannot revoke" in skill or re.search(r"you\s+cannot.*revoke", skill, re.DOTALL) is not None


def test_assistant_protocol_defaults_are_canonical():
    from brain.templates import ASSISTANT_PROTOCOL, assistant_protocol
    assert assistant_protocol() == ASSISTANT_PROTOCOL
    assert "ClientRequests" in ASSISTANT_PROTOCOL
    assert "Clients/<client>" in ASSISTANT_PROTOCOL


def test_assistant_protocol_custom_noun_has_no_client_literals():
    from brain.templates import assistant_protocol
    text = assistant_protocol(FAM)
    assert "FamilyRequests" in text
    assert "Families/<family>" in text
    assert "Clients/" not in text and "ClientRequests" not in text


def test_spaces_yaml_custom_noun_parses_with_custom_wildcard():
    import yaml
    from brain.templates import spaces_yaml
    text = spaces_yaml(FAM)
    data = yaml.safe_load(text)
    paths = [e["path"] for e in data["spaces"]]
    assert "Families/*" in paths and "Clients/*" not in paths
    assert "Clients" not in text


def test_brain_protocol_skill_is_noun_neutral():
    from pathlib import Path
    skill = (Path(__file__).resolve().parents[1] /
             "templates/company-brain-profile/skills/brain-protocol/SKILL.md").read_text()
    for literal in ("Clients/", "ClientRequests", "client-name"):
        assert literal not in skill, literal


def test_root_protocol_custom_noun():
    from brain.contextgen import render_root_protocol
    from brain.schemas import Person
    person = Person(id="joe", name="Joe")
    text = render_root_protocol(person, [("People/joe", True)], config=FAM)
    assert "FamilyRequests" in text
    assert "family-name: <full" in text
    assert "`Families/<name>/` space" in text
    assert "ClientRequests" not in text and "Clients/" not in text


def test_root_protocol_default_matches_current_text():
    from brain.contextgen import render_root_protocol
    from brain.schemas import Person
    person = Person(id="joe", name="Joe")
    text = render_root_protocol(person, [("People/joe", True)])
    assert "ClientRequests" in text and "client-name: <full" in text


def test_space_notes_generated_for_custom_tree(tmp_path):
    from brain.contextgen import generate_context_files
    from brain.schemas import Person, SpaceRule
    person = Person(id="joe", name="Joe")
    rules = (SpaceRule(path="Families/Danziger", read=("person:joe",), write=("person:joe",)),)
    written = generate_context_files(
        tmp_path, person, ["Families/Danziger"], rules, config=FAM)
    assert "Families/Danziger/AGENTS.md" in written


def test_root_template_carries_decider_guidance():
    from brain.contextgen import render_root_protocol
    from brain.schemas import Person
    text = render_root_protocol(Person(id="joe", name="Joe"), [("People/joe", True)])
    for needle in ("Awaiting your decision", "Approvals/",
                   "decision: approve", "explicitly", "everyone"):
        assert needle in text, needle


def test_assistant_protocol_carries_decider_guidance():
    from brain.templates import ASSISTANT_PROTOCOL
    for needle in ("Awaiting your decision", "Approvals/", "explicitly"):
        assert needle in ASSISTANT_PROTOCOL, needle


def test_skill_carries_decider_guidance_noun_neutral():
    from pathlib import Path
    skill = (Path(__file__).resolve().parents[1] /
             "templates/company-brain-profile/skills/brain-protocol/SKILL.md").read_text()
    for needle in ("Awaiting your decision", "Approvals/", "explicitly"):
        assert needle in skill, needle
    for literal in ("Clients/", "ClientRequests", "client-name"):
        assert literal not in skill, literal   # noun-neutral pin still holds


def test_root_protocol_points_at_the_map():
    text = render_root_protocol(
        BOB, [("Company", False), ("People/bob", True)])
    assert "Map.md" in text
    assert "brain_search" in text  # map orients, search looks up
    assert len(text) <= ROOT_LIMIT
