import json
import subprocess
from pathlib import Path

from brain.compiler import MANIFEST_NAME, compile_vault
from brain.writeback import Change, apply_writeback, diff_vault
from tests.conftest import ALICE, BOB, RULES


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def setup_master_git(master: Path) -> None:
    git(master, "init", "-b", "main")
    git(master, "add", "-A")
    git(master, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed")


def test_diff_detects_add_modify_delete(master: Path, tmp_path: Path):
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Actions").mkdir(parents=True, exist_ok=True)
    (vault / "People/bob/Actions/Todo.md").write_text("- [ ] call acme\n")
    (vault / "People/bob/Memory.md").write_text("Bob updated memory.\n")
    (vault / "People/bob/Sessions/Bob Private Note.md").unlink()
    changes = {(c.kind, c.path) for c in diff_vault(vault)}
    assert ("add", "People/bob/Actions/Todo.md") in changes
    assert ("modify", "People/bob/Memory.md") in changes
    assert ("delete", "People/bob/Sessions/Bob Private Note.md") in changes
    # Generated context files are not treated as user changes
    assert not any(p.endswith("AGENTS.md") or p.endswith("CLAUDE.md") for _, p in changes)


def test_out_of_scope_change_rejects_everything(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("legit change\n")
    (vault / "Company/Home.md").write_text("bob defaces the homepage\n")  # not writable
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.applied == []
    assert any("Company/Home.md" in v for v in result.violations)
    # Nothing applied — master untouched, including the legit change
    assert (master / "People/bob/Memory.md").read_text() == "Bob private memory.\n"


def test_out_of_scope_delete_rejects_everything(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("legit change\n")
    # Bob can read Company but not write it — deleting the file from his
    # vault copy must be rejected server-side like any other change.
    (vault / "Company/Decisions/Big Deal Decision.md").unlink()
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.applied == []
    assert any(
        "delete" in v and "Company/Decisions/Big Deal Decision.md" in v
        for v in result.violations
    )
    # Master untouched: the read-only file survives with original content,
    # and the legit edit is NOT applied either.
    master_file = master / "Company/Decisions/Big Deal Decision.md"
    assert master_file.read_text() == "We chose option A.\n"
    assert (master / "People/bob/Memory.md").read_text() == "Bob private memory.\n"


def test_valid_writeback_applies_and_commits(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("Bob updated memory.\n")
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.violations == []
    assert [c.kind for c in result.applied] == ["modify"]
    assert (master / "People/bob/Memory.md").read_text() == "Bob updated memory.\n"
    log = git(master, "log", "-1", "--format=%an %ae %s")
    assert "Bob Rivera" in log and "bob@brain.local" in log


def test_noop_writeback_makes_no_commit(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    before = git(master, "rev-parse", "HEAD")
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.applied == [] and result.violations == []
    assert git(master, "rev-parse", "HEAD") == before


def test_modify_converged_with_master_no_crash_no_commit(master: Path, tmp_path: Path):
    """Last-write-wins converged case: vault differs from the compile-time
    baseline but its bytes already equal master's current bytes. git add -A
    stages nothing; apply_writeback must not crash and must not commit."""
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    # Master moved on after compile...
    (master / "People/bob/Memory.md").write_text("converged\n")
    git(master, "add", "-A")
    git(master, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "moved on")
    # ...and bob independently made the identical edit in his vault.
    (vault / "People/bob/Memory.md").write_text("converged\n")
    before = git(master, "rev-parse", "HEAD")
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.violations == []
    assert [c.kind for c in result.applied] == ["modify"]
    assert (master / "People/bob/Memory.md").read_text() == "converged\n"
    assert git(master, "rev-parse", "HEAD") == before  # nothing new to record


def test_forged_baseline_delete_of_absent_file_no_crash(master: Path, tmp_path: Path):
    """A forged manifest baseline entry for an in-scope path absent from both
    vault and master yields a 'delete' change that nets to zero delta.
    apply_writeback must not crash and must not commit."""
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    manifest_path = vault / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest["compiled"]["People/bob/Ghost.md"] = "0" * 64  # bogus entry
    manifest_path.write_text(json.dumps(manifest))
    before = git(master, "rev-parse", "HEAD")
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.violations == []
    assert [(c.kind, c.path) for c in result.applied] == [
        ("delete", "People/bob/Ghost.md")
    ]
    assert git(master, "rev-parse", "HEAD") == before


def test_symlink_planted_in_writable_space_never_applied(master: Path, tmp_path: Path):
    """A client-planted symlink at an in-scope path must never have its
    TARGET bytes committed into master (server-side arbitrary file read)."""
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    secret = master / "Teams/sales/Q3 Pipeline.md"  # bob cannot read this
    (vault / "People/bob/leak.md").symlink_to(secret)
    # The symlink never surfaces from the diff at all
    assert not any(c.path == "People/bob/leak.md" for c in diff_vault(vault))
    result = apply_writeback(master, vault, BOB, RULES)
    assert not any("leak.md" in c.path for c in result.applied)
    assert not (master / "People/bob/leak.md").exists()


def test_baseline_file_replaced_by_symlink_is_a_delete(master: Path, tmp_path: Path):
    """If a shipped file is replaced by a symlink, the real file is gone:
    diff must report a delete, never a modify that reads through the link."""
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    mem = vault / "People/bob/Memory.md"
    mem.unlink()
    mem.symlink_to(master / "Teams/sales/Q3 Pipeline.md")
    changes = {(c.kind, c.path) for c in diff_vault(vault)}
    assert ("delete", "People/bob/Memory.md") in changes
    assert ("modify", "People/bob/Memory.md") not in changes
    assert ("add", "People/bob/Memory.md") not in changes


def test_local_dot_dirs_do_not_reject_writeback(master: Path, tmp_path: Path):
    """A vault carrying machine-local state (.brain index, .obsidian config)
    plus a legitimate edit: the dot-dirs are outside every space, so they must
    be ignored, not treated as out-of-scope changes that reject the whole set."""
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / ".brain").mkdir()
    (vault / ".brain/index.db").write_bytes(b"\x00sqlite\x00")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian/app.json").write_text("{}\n")
    (vault / "People/bob/Memory.md").write_text("Bob updated memory.\n")
    # Neither dot-dir appears in the diff at all.
    changes = {c.path for c in diff_vault(vault)}
    assert not any(p.startswith(".brain") or p.startswith(".obsidian") for p in changes)
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.violations == []
    assert any(c.path == "People/bob/Memory.md" for c in result.applied)


def test_apply_skips_symlink_appearing_after_diff(master: Path, tmp_path: Path, monkeypatch):
    # diff_vault never emits symlinks, but if the vault changed between diff and
    # apply, the apply phase must still refuse to copy a symlink's target into
    # master (arbitrary-file-read defense in depth).
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    secret = master / "Teams/sales/Q3 Pipeline.md"  # sales space — not Bob's to read
    (vault / "People/bob/leak.md").symlink_to(secret)  # in Bob's writable scope
    monkeypatch.setattr(
        "brain.writeback.diff_vault",
        lambda v: [Change("People/bob/leak.md", "modify")],
    )
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.violations == []  # path is in scope; the symlink is the issue
    # The secret's bytes were never written into master under Bob's path.
    assert not (master / "People/bob/leak.md").exists()
