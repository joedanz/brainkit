import json
from pathlib import Path

import pytest

from brain.compiler import MANIFEST_NAME, compile_vault
from tests.conftest import BOB, RULES, familyize, rules_for


def test_compiles_only_readable_spaces(master: Path, tmp_path: Path):
    # Marker for the master-root invariant: context generation never writes a
    # file with this name, so unlike AGENTS.md (which generation overwrites),
    # its presence in the vault can only mean the copy loop regressed and
    # copied master-root files.
    (master / "SERVER-NOTES.md").write_text("server-only marker\n")
    out = tmp_path / "bob-vault"
    result = compile_vault(master, BOB, RULES, out)
    assert (out / "Company/Home.md").exists()
    assert (out / "Teams/ops/Runbook.md").exists()
    assert (out / "People/bob/Memory.md").exists()
    assert (out / "Clients/acme/Overview.md").exists()
    # Structural privacy: not readable → not on disk
    assert not (out / "Teams/sales").exists()
    assert not (out / "People/alice").exists()
    assert not (out / "_meta").exists()
    # Master-root files are never copied: the marker appears nowhere in the
    # vault, and the root AGENTS.md is exactly the generated protocol (not
    # master's server-only file).
    assert not list(out.rglob("SERVER-NOTES.md"))
    from brain.contextgen import render_root_protocol
    from brain.resolver import can_write_path, readable_spaces

    spaces_rw = [
        (s, can_write_path(f"{s}/x.md", BOB, RULES))
        for s in readable_spaces(master, BOB, RULES)
    ]
    assert (out / "AGENTS.md").read_text() == render_root_protocol(BOB, spaces_rw)
    assert "Company/Home.md" in result.files


def test_manifest_written(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert manifest["person"] == "bob"
    assert "People/bob/Memory.md" in manifest["compiled"]
    assert isinstance(manifest["generated"], list)


def test_recompile_replaces_stale_files(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    (master / "Teams/ops/Runbook.md").write_text("Updated runbook.\n")
    # Simulate access removal: bob loses acme
    import shutil
    shutil.rmtree(master / "Clients/acme")
    compile_vault(master, BOB, RULES, out)
    assert (out / "Teams/ops/Runbook.md").read_text() == "Updated runbook.\n"
    assert not (out / "Clients/acme").exists()


def test_fail_closed_preserves_previous_output(master: Path, tmp_path: Path, monkeypatch):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    before = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())

    import brain.compiler as compiler_mod

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(compiler_mod.shutil, "copy2", boom)
    with pytest.raises(RuntimeError):
        compile_vault(master, BOB, RULES, out)
    after = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert before == after  # previous output stands untouched


def test_git_dir_preserved_across_recompile(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    (out / ".git").mkdir()
    (out / ".git/HEAD").write_text("ref: refs/heads/main\n")
    compile_vault(master, BOB, RULES, out)
    assert (out / ".git/HEAD").read_text() == "ref: refs/heads/main\n"


def test_symlinks_never_cross_tenant_boundary(master: Path, tmp_path: Path):
    # A symlink planted inside a readable space pointing at a file (or dir)
    # in an unreadable space must not materialize the target's content.
    (master / "People/bob/leak.md").symlink_to(master / "People/alice/Memory.md")
    (master / "People/bob/leakdir").symlink_to(
        master / "People/alice", target_is_directory=True
    )
    out = tmp_path / "bob-vault"
    result = compile_vault(master, BOB, RULES, out)
    assert not (out / "People/bob/leak.md").exists(follow_symlinks=False)
    assert not (out / "People/bob/leakdir").exists(follow_symlinks=False)
    leaked = [
        p
        for p in out.rglob("*")
        if p.is_file() and "Alice private memory" in p.read_text()
    ]
    assert leaked == []
    assert "People/bob/leak.md" not in result.files


def test_symlinked_space_root_materializes_nothing(master: Path, tmp_path: Path):
    # Turning a whole readable space into a symlink must not copy its target in:
    # the space root itself is symlink-checked, not just files within it.
    secret_dir = tmp_path / "outside_client"
    secret_dir.mkdir()
    (secret_dir / "secret.md").write_text("SENTINEL client data\n")
    (master / "Clients" / "leak").symlink_to(secret_dir, target_is_directory=True)
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)  # Clients/* is everyone-readable
    # Fail-closed: the space compiles to empty scaffolding (generated context
    # files only), never the symlink target's content, and never as a symlink.
    leaked = [p for p in out.rglob("*.md") if "SENTINEL" in p.read_text()]
    assert leaked == []
    assert not (out / "Clients/leak/secret.md").exists()
    assert not (out / "Clients/leak").is_symlink()


def test_crashed_swap_with_missing_out_fully_restored(master: Path, tmp_path: Path):
    # Simulate a crash between `out.rename(old)` and promoting the new tree:
    # `out` is gone entirely and the previous vault (content + .git) sits at
    # `.old`. The next compile must restore it — preserving git history —
    # then proceed normally, replacing content via the two-phase swap.
    out = tmp_path / "bob-vault"
    old = out.parent / f".{out.name}.old"
    (old / ".git").mkdir(parents=True)
    (old / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (old / "marker.md").write_text("stale content from previous vault\n")
    compile_vault(master, BOB, RULES, out)
    assert out.exists()
    assert (out / "People/bob/Memory.md").exists()  # fresh compiled content
    assert (out / ".git/HEAD").read_text() == "ref: refs/heads/main\n"
    assert not old.exists()


def test_crashed_swap_recovered_on_next_compile(master: Path, tmp_path: Path):
    # Simulate a crash mid-swap: the new tree landed at `out` but the process
    # died before moving .git back and removing the `.old` sibling. The next
    # compile must recover the git history and clean up the tombstone.
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    old = out.parent / f".{out.name}.old"
    (old / ".git").mkdir(parents=True)
    (old / ".git/HEAD").write_text("ref: refs/heads/main\n")
    compile_vault(master, BOB, RULES, out)
    assert (out / ".git/HEAD").read_text() == "ref: refs/heads/main\n"
    assert not old.exists()


from brain.compiler import stub_links


def test_stub_links_unit():
    included = {"big deal decision"}
    master = {"big deal decision", "bob private note"}
    text = "See [[Big Deal Decision]], [[Bob Private Note|his note]], and [[Future Idea]]. ![[Bob Private Note]]"
    out = stub_links(text, included, master)
    assert "[[Big Deal Decision]]" in out          # included → untouched
    assert "[[Bob Private Note" not in out         # invisible → stubbed
    assert "his note" in out                       # alias used as display text
    assert "[[Future Idea]]" in out                # nonexistent anywhere → untouched
    assert "![[" not in out                        # embed of invisible note stubbed too


def test_compile_stubs_invisible_links_in_readonly_spaces(master, tmp_path):
    from brain.compiler import compile_vault
    from tests.conftest import BOB, RULES

    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    # Company is read-only for bob → stubbing applies there
    home = (out / "Company/Home.md").read_text()
    assert "[[Big Deal Decision]]" in home   # included in bob's vault → untouched
    assert "[[Q3 Pipeline]]" not in home     # Teams/sales invisible to bob → stubbed
    assert "Q3 Pipeline" in home             # display text remains


def test_writable_spaces_never_stubbed(master, tmp_path):
    from brain.compiler import compile_vault
    from tests.conftest import BOB, RULES

    (master / "People/bob/Notes.md").write_text("See [[Q3 Pipeline]].\n")
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    # People/bob is writable for bob → byte-identical copy, link untouched
    assert (out / "People/bob/Notes.md").read_text() == "See [[Q3 Pipeline]].\n"


def test_brain_index_dir_preserved_across_recompile(master: Path, tmp_path: Path):
    """The local search index at <vault>/.brain survives a recompile, exactly
    like .git — it is machine-local state, not compiled output."""
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    (out / ".brain").mkdir()
    (out / ".brain/index.db").write_bytes(b"\x00sqlite-index\x00")
    compile_vault(master, BOB, RULES, out)
    assert (out / ".brain/index.db").read_bytes() == b"\x00sqlite-index\x00"


def test_gitignore_generated_and_in_manifest(master: Path, tmp_path: Path):
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    gi = out / ".gitignore"
    assert gi.is_file()
    body = gi.read_text()
    assert ".brain/" in body and ".obsidian/" in body
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert ".gitignore" in manifest["generated"]
    # generated files are never counted as user-editable baseline
    assert ".gitignore" not in manifest["compiled"]


def test_compile_all_never_tracks_brain_index(master: Path, tmp_path: Path):
    import subprocess

    from brain.compiler import compile_all
    from tests.conftest import ORG
    out_root = tmp_path / "compiled"
    compile_all(master, ORG, RULES, out_root)
    bob = out_root / "bob"
    (bob / ".brain").mkdir()
    (bob / ".brain/index.db").write_bytes(b"\x00")
    compile_all(master, ORG, RULES, out_root)
    tracked = subprocess.run(
        ["git", "-C", str(bob), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    # .brain-manifest.json IS tracked (compiled baseline); the .brain/ index dir
    # must not be.
    assert not any(p.startswith(".brain/") for p in tracked)
    assert (bob / ".brain/index.db").exists()


def test_compile_generates_shares_note_for_pending_promotion(master: Path, tmp_path: Path):
    from brain.promotions import draft_promotion

    draft_promotion(
        master, person_id="bob", target_path="Company/Playbook/S.md",
        source="s", body="x", promo_id="p-s", created="2026-07-18",
    )
    out = tmp_path / "out" / "bob"
    compile_vault(master, BOB, RULES, out, today="2026-07-20")
    note = out / "People/bob/Shares.md"
    assert note.is_file()
    assert "Awaiting approval" in note.read_text()
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert "People/bob/Shares.md" in manifest["generated"]
    assert "People/bob/Shares.md" not in manifest["compiled"]


def test_compile_omits_shares_note_when_no_activity(master: Path, tmp_path: Path):
    out = tmp_path / "out" / "bob"
    compile_vault(master, BOB, RULES, out, today="2026-07-20")
    assert not (out / "People/bob/Shares.md").exists()


def test_shares_note_includes_space_shares_section(master, tmp_path):
    from brain.compiler import compile_vault
    from brain.shares import request_share, sweep_shares
    from tests.conftest import BOB

    with (master / "_meta/spaces.yaml").open("w") as fh:
        fh.write(
            "spaces:\n"
            '  - {path: Company,     read: [everyone],        write: ["role:admin"]}\n'
            '  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}\n'
            '  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}\n'
            '  - {path: "Clients/*", read: ["role:admin"],    write: ["role:admin"]}\n'
            '  - {path: "Clients/acme", read: ["role:admin", "person:bob"], write: ["role:admin", "person:bob"]}\n'
        )
    (master / "_meta/org.yaml").write_text(
        "people:\n  alice: {name: Alice, roles: [admin]}\n"
        "  bob: {name: Bob, teams: [ops]}\n")
    import subprocess
    subprocess.run(["git", "-C", str(master), "init", "-b", "main"],
                   capture_output=True)
    request_share(master, "bob", "Clients/acme", "person:alice", "read", "2026-07-22")
    from brain.schemas import load_org, load_spaces
    sweep_shares(master, load_org(master / "_meta/org.yaml"), today="2026-07-22")
    dest = tmp_path / "bob"
    compile_vault(master, BOB, load_spaces(master / "_meta/spaces.yaml"), dest,
                  today="2026-07-22")
    text = (dest / "People/bob/Shares.md").read_text()
    assert "## Space shares" in text and "Clients/acme" in text


def test_decider_section_compiles_into_shares_md(master, tmp_path):
    from brain.compiler import compile_vault
    from brain.schemas import Person, load_org, load_spaces
    from brain.shares import request_share, sweep_shares

    with (master / "_meta/spaces.yaml").open("w") as fh:
        fh.write(
            "spaces:\n"
            '  - {path: Company,     read: [everyone],        write: ["role:admin"]}\n'
            '  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}\n'
            '  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}\n'
            '  - {path: "Clients/*", read: ["role:admin"],    write: ["role:admin"]}\n'
            '  - {path: "Clients/acme", read: ["role:admin", "person:bob"], write: ["role:admin", "person:bob"]}\n'
        )
    (master / "_meta/org.yaml").write_text(
        "people:\n  alice: {name: Alice, roles: [admin]}\n"
        "  bob: {name: Bob, teams: [ops]}\n"
        "  mary: {name: Mary Ops}\n")
    import subprocess
    subprocess.run(["git", "-C", str(master), "init", "-b", "main"],
                   capture_output=True)
    request_share(master, "bob", "Clients/acme", "person:mary", "read", "2026-07-22")
    sweep_shares(master, load_org(master / "_meta/org.yaml"), today="2026-07-22")
    mary = Person(id="mary", name="Mary Ops")
    dest = tmp_path / "mary"
    compile_vault(master, mary, load_spaces(master / "_meta/spaces.yaml"), dest,
                  today="2026-07-22")
    text = (dest / "People/mary/Shares.md").read_text()
    assert "Awaiting your decision" in text


def test_promotion_decider_section_compiles_into_lead_shares_md(master, tmp_path):
    from brain.compiler import compile_vault
    from brain.promotions import draft_promotion
    from brain.schemas import Person, load_spaces

    with (master / "_meta/spaces.yaml").open("w") as fh:
        fh.write(
            "spaces:\n"
            '  - {path: Company,     read: [everyone],        write: ["role:admin"]}\n'
            '  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}\n'
            '  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}\n'
        )
    (master / "_meta/org.yaml").write_text(
        "people:\n  alice: {name: Alice, roles: [admin]}\n"
        "  bob: {name: Bob, teams: [ops]}\n"
        "  lead_ops: {name: Lead Ops, roles: [lead], teams: [ops]}\n")
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Escalation.md",
                    source="s", body="Restart it.\n", promo_id="p-ops",
                    created="2026-08-17")
    lead = Person(id="lead_ops", name="Lead Ops", roles=("lead",), teams=("ops",))
    dest = tmp_path / "lead_ops"
    compile_vault(master, lead, load_spaces(master / "_meta/spaces.yaml"), dest,
                  today="2026-08-17")
    text = (dest / "People/lead_ops/Shares.md").read_text()
    assert "Promotions awaiting your decision" in text
    assert "Restart it." in text
    # and it is a generated file: in the manifest's generated list
    import json
    manifest = json.loads((dest / ".brain-manifest.json").read_text())
    assert "People/lead_ops/Shares.md" in manifest["generated"]


def test_promotion_decider_section_hidden_when_lead_cannot_read_target(
    master: Path, tmp_path: Path
):
    """Approval authority is path-shaped; read access is rule-shaped. An exact
    ``Teams/ops`` rule shadows the ``Teams/*`` wildcard, so mary leads ops but
    cannot read it — ``Teams/ops`` is (correctly) absent from her vault. The
    review section must be absent too: a patch promotion renders a diff against
    the *current* target, which would otherwise carry those bytes into her
    Shares.md, i.e. into a vault the rules say may not hold them."""
    from brain.promotions import draft_promotion
    from brain.schemas import Person, load_spaces

    with (master / "_meta/spaces.yaml").open("w") as fh:
        fh.write(
            "spaces:\n"
            '  - {path: Company,     read: [everyone],        write: ["role:admin"]}\n'
            '  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}\n'
            '  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}\n'
            '  - {path: "Teams/ops", read: ["person:bob"],    write: ["person:bob"]}\n'
        )
    (master / "_meta/org.yaml").write_text(
        "people:\n  alice: {name: Alice, roles: [admin]}\n"
        "  bob: {name: Bob, teams: [ops]}\n"
        "  mary: {name: Mary Ops, roles: [lead], teams: [ops]}\n")
    (master / "Teams/ops/Runbook.md").write_text("SECRET-OPS-BYTES\nline two\n")
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Runbook.md",
                    source="s", body="SECRET-OPS-BYTES\nline two changed\n",
                    promo_id="p-patch", created="2026-08-17", mode="patch")
    mary = Person(id="mary", name="Mary Ops", roles=("lead",), teams=("ops",))
    dest = tmp_path / "mary"
    compile_vault(master, mary, load_spaces(master / "_meta/spaces.yaml"), dest,
                  today="2026-08-17")
    assert not (dest / "Teams/ops").exists()
    note = dest / "People/mary/Shares.md"
    text = note.read_text() if note.exists() else ""
    assert "SECRET-OPS-BYTES" not in text
    assert "Promotions awaiting your decision" not in text
    # and nowhere else in the vault either
    assert not [f for f in dest.rglob("*.md") if "SECRET-OPS-BYTES" in f.read_text()]


def test_promotion_body_reaches_only_the_deciding_leads_vault(
    master: Path, tmp_path: Path
):
    """The leak property, at the level that matters: compile the whole org and
    prove the pending body exists in exactly one compiled vault — the lead's.
    Everyone else, including the requester and an admin who decides at the
    dashboard, gets nothing of it anywhere in their tree."""
    from brain.promotions import draft_promotion
    from brain.schemas import Person, load_spaces

    with (master / "_meta/spaces.yaml").open("w") as fh:
        fh.write(
            "spaces:\n"
            '  - {path: Company,     read: [everyone],        write: ["role:admin"]}\n'
            '  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}\n'
            '  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}\n'
        )
    (master / "_meta/org.yaml").write_text(
        "people:\n  alice: {name: Alice, roles: [admin], teams: [ops]}\n"
        "  bob: {name: Bob, teams: [ops]}\n"
        "  carol: {name: Carol, teams: [sales]}\n"
        "  lead_ops: {name: Lead Ops, roles: [lead], teams: [ops]}\n")
    body = "UNIQUE-PROMOTION-BODY-MARKER\n"
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Escalation.md",
                    source="s", body=body, promo_id="p-ops", created="2026-08-17")
    rules = load_spaces(master / "_meta/spaces.yaml")
    people = [
        Person(id="alice", name="Alice", roles=("admin",), teams=("ops",)),
        Person(id="bob", name="Bob", teams=("ops",)),
        Person(id="carol", name="Carol", teams=("sales",)),
        Person(id="lead_ops", name="Lead Ops", roles=("lead",), teams=("ops",)),
    ]
    vaults = {}
    for person in people:
        dest = tmp_path / person.id
        compile_vault(master, person, rules, dest, today="2026-08-17")
        vaults[person.id] = dest

    lead_note = vaults["lead_ops"] / "People/lead_ops/Shares.md"
    assert body.strip() in lead_note.read_text()
    for pid, dest in vaults.items():
        if pid == "lead_ops":
            continue
        hits = [
            f.relative_to(dest).as_posix()
            for f in dest.rglob("*")
            if f.is_file() and ".git" not in f.parts
            and body.strip() in f.read_text(errors="ignore")
        ]
        assert hits == [], f"promotion body leaked into {pid}'s vault: {hits}"


def test_map_note_generated_and_in_manifest(master: Path, tmp_path: Path):
    from brain.vaultmap import MAP_NAME

    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    text = (out / MAP_NAME).read_text()
    assert text.startswith("---\ngenerated: true\n---\n")
    assert "## Spaces" in text
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    # Generated => excluded from the write-back baseline, so a regenerated
    # Map.md never shows up as a phantom user edit.
    assert MAP_NAME in manifest["generated"]
    assert MAP_NAME not in manifest["compiled"]


def test_map_degree_never_counts_a_target_outside_this_vault(
    master: Path, tmp_path: Path
):
    """The conftest master's Company/Home.md links to [[Big Deal Decision]]
    (Company — bob can read it) and [[Q3 Pipeline]] (Teams/sales — bob is in
    ops, so he cannot). Home's degree must be 1, not 2: a hub's number never
    reflects notes the person cannot open.

    NOTE: this holds whether the text is scanned pre- or post-stub, because
    link_degree resolves only against this vault's own notes. Do not rewrite
    this as a test of stub_links — measured, stubbing does not change the
    count."""
    from brain.vaultmap import MAP_NAME

    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    text = (out / MAP_NAME).read_text()
    assert "[[Home]] — 1 link(s)" in text
    # The unreadable target is not a note in this vault, so it is never a hub.
    assert "[[Q3 Pipeline]]" not in text


def test_map_survives_a_vault_with_no_entities(master: Path, tmp_path: Path):
    import shutil

    from brain.vaultmap import MAP_LIMIT, MAP_NAME

    shutil.rmtree(master / "Clients")
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    text = (out / MAP_NAME).read_text()
    assert "## Entities" not in text
    assert len(text) <= MAP_LIMIT


def test_map_edit_is_discarded_by_the_next_compile(master: Path, tmp_path: Path):
    from brain.vaultmap import MAP_NAME

    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)
    (out / MAP_NAME).write_text("hand-edited nonsense\n")
    compile_vault(master, BOB, RULES, out)
    assert (out / MAP_NAME).read_text() != "hand-edited nonsense\n"


def test_undecodable_writable_note_degrades_instead_of_failing(
    master: Path, tmp_path: Path
):
    """scan_vault reads EVERY .md, including writable ones the stub loop
    never touched, so a stray byte must not fail a compile. It is read with
    errors="replace" and never written back — the file ships byte-for-byte."""
    raw = b"# Memory\n\xff\xfe not utf-8\nSee [[Runbook]].\n"
    (master / "People/bob/Memory.md").write_bytes(raw)
    out = tmp_path / "bob-vault"
    compile_vault(master, BOB, RULES, out)  # must not raise
    assert (out / "People/bob/Memory.md").read_bytes() == raw


def test_undecodable_read_only_note_still_fails_closed(
    master: Path, tmp_path: Path
):
    """Unchanged from before this feature: a note that cannot be decoded
    cannot be link-stubbed, and shipping it unstubbed could leak a live
    cross-boundary link. Company is read-only for bob, so it gets stubbed."""
    (master / "Company/Home.md").write_bytes(b"# Home\n\xff\xfe\n")
    out = tmp_path / "bob-vault"
    with pytest.raises(UnicodeDecodeError):
        compile_vault(master, BOB, RULES, out)


def test_manifest_shared_key_only_when_nondefault(master: Path, tmp_path: Path):


    # Default compile: no "shared" key, so a default vault's manifest is
    # byte-unchanged from before the setting existed.
    out = tmp_path / "bob-default"
    compile_vault(master, BOB, RULES, out)
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert "shared" not in manifest

    # A Family-shaped master: shared tree renamed, config declares it.
    fam = familyize(master, tmp_path / "family-master")
    fam_out = tmp_path / "bob-family"
    compile_vault(fam, BOB, rules_for("Family"), fam_out)
    manifest = json.loads((fam_out / MANIFEST_NAME).read_text())
    assert manifest["shared"] == "Family"
    assert (fam_out / "Family/Home.md").exists()
