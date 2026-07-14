import json
from pathlib import Path

import pytest

from brain.compiler import MANIFEST_NAME, compile_vault
from tests.conftest import ALICE, BOB, RULES


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
