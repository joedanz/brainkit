import subprocess
from pathlib import Path

from brain.compiler import compile_vault
from brain.writeback import apply_writeback, diff_vault
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
