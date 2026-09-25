import json
import subprocess
from pathlib import Path

from brain.clients import request_client
from brain.compiler import MANIFEST_NAME, compile_vault
from brain.schemas import load_org, load_spaces
from brain.writeback import Change, apply_writeback, diff_vault
from tests.conftest import BOB, RULES
from tests.test_cli import seed_meta


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
    assert not any(p.endswith(("AGENTS.md", "CLAUDE.md")) for _, p in changes)


def test_mixed_batch_applies_in_scope_and_holds_the_rest(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("legit change\n")
    (vault / "Company/Home.md").write_text("bob defaces the homepage\n")  # not writable
    result = apply_writeback(master, vault, BOB, RULES)
    assert [(c.kind, c.path) for c in result.applied] == [("modify", "People/bob/Memory.md")]
    assert result.held == [("modify", "Company/Home.md", "outside write scope for bob")]
    assert result.error == ""
    assert (master / "People/bob/Memory.md").read_text() == "legit change\n"
    assert (master / "Company/Home.md").read_text() != "bob defaces the homepage\n"
    assert git(master, "show", "--name-only", "--format=%an", "HEAD").split() == [
        "Bob", "Rivera", "People/bob/Memory.md"]


def test_out_of_scope_delete_is_held_not_applied(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "Company/Decisions/Big Deal Decision.md").unlink()
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.applied == []
    assert result.held == [("delete", "Company/Decisions/Big Deal Decision.md",
                            "outside write scope for bob")]
    assert (master / "Company/Decisions/Big Deal Decision.md").read_text() == "We chose option A.\n"


def test_valid_writeback_applies_and_commits(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("Bob updated memory.\n")
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.held == []
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
    assert result.applied == [] and result.held == []
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
    assert result.held == []
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
    assert result.held == []
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
    assert not any(p.startswith((".brain", ".obsidian")) for p in changes)
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.held == []
    assert any(c.path == "People/bob/Memory.md" for c in result.applied)


def test_apply_skips_symlink_appearing_after_diff(master: Path, tmp_path: Path, monkeypatch):
    # A change the diff hands over without captured bytes is never applied:
    # apply writes only bytes the diff hashed, and never re-reads the vault.
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    secret = master / "Teams/sales/Q3 Pipeline.md"  # sales space — not Bob's to read
    (vault / "People/bob/leak.md").symlink_to(secret)  # in Bob's writable scope
    monkeypatch.setattr(
        "brain.writeback.diff_vault",
        lambda v, manifest=None: [Change("People/bob/leak.md", "modify")],
    )
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.held == []  # path is in scope; the symlink is the issue
    # The secret's bytes were never written into master under Bob's path.
    assert not (master / "People/bob/leak.md").exists()


def test_client_request_subdir_survives_writeback(master: Path, tmp_path: Path):
    seed_meta(master)
    out = tmp_path / "compiled"
    compile_vault(master, BOB, RULES, out / "bob")

    # brand-new subdir the compile never shipped
    request_client(out / "bob", "bob", "Danziger Family", "body\n", "2026-07-22")

    person = load_org(master / "_meta/org.yaml").people["bob"]
    rules = load_spaces(master / "_meta/spaces.yaml")
    result = apply_writeback(master, out / "bob", person, rules)

    assert not result.held
    assert any(c.path.startswith("People/bob/ClientRequests/") for c in result.applied)
    assert list((master / "People/bob/ClientRequests").glob("*.md"))


def test_shared_of_semantics():
    from brain.writeback import shared_of
    assert shared_of({}) == "Company"
    assert shared_of({"shared": "Family"}) == "Family"
    assert shared_of({"shared": 7}) == "Company"      # non-string -> default
    assert shared_of({"shared": ""}) == "Company"     # empty -> default


def test_vault_shared_missing_manifest(tmp_path):
    from brain.writeback import vault_shared
    assert vault_shared(tmp_path) == "Company"  # naming lookup, never raises


import subprocess as _sp


def test_junk_at_any_depth_is_ignored(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    for rel in ("People/bob/.DS_Store", "People/bob/Notes/._x.md", "People/bob/a.md~",
                "People/bob/.Memory.md.swp", "Company/Thumbs.db", "Company/Decisions/desktop.ini"):
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_bytes(b"junk")
    (vault / "People/bob/Memory.md").write_text("real edit\n")
    assert {c.path for c in diff_vault(vault)} == {"People/bob/Memory.md"}
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.held == []
    assert [c.path for c in result.applied] == ["People/bob/Memory.md"]
    assert not (master / "People/bob/.DS_Store").exists()


def test_committed_bytes_are_the_hashed_bytes(master: Path, tmp_path: Path, monkeypatch):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("version one\n")
    import brain.writeback as wb
    real = wb.diff_vault

    def racing(v, manifest=None):
        changes = real(v, manifest)
        (v / "People/bob/Memory.md").write_text("version two, written after the diff\n")
        return changes

    monkeypatch.setattr(wb, "diff_vault", racing)
    result = apply_writeback(master, vault, BOB, RULES)
    assert result.applied[0].sha == wb._sha(b"version one\n")
    assert (master / "People/bob/Memory.md").read_text() == "version one\n"
    assert git(master, "show", "HEAD:People/bob/Memory.md") == "version one\n"


def test_unrelated_dirty_master_file_stays_out_of_the_commit(master: Path, tmp_path: Path):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (master / "Company/Home.md").write_text("admin draft, not committed\n")
    (master / "Teams/ops/Runbook.md").write_text("admin staged this\n")
    git(master, "add", "Teams/ops/Runbook.md")
    (vault / "People/bob/Memory.md").write_text("bob edit\n")
    apply_writeback(master, vault, BOB, RULES)
    assert git(master, "show", "--name-only", "--format=", "HEAD").split() == ["People/bob/Memory.md"]
    status = git(master, "status", "--porcelain")
    assert " M Company/Home.md" in status and "M  Teams/ops/Runbook.md" in status


def test_git_commit_failure_restores_master_and_does_not_raise(master: Path, tmp_path: Path,
                                                                monkeypatch):
    setup_master_git(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("bob edit\n")
    (vault / "People/bob/New.md").write_text("new note\n")
    (vault / "Company/Home.md").write_text("held\n")
    import brain.writeback as wb
    real = wb._git

    def failing(cwd, *args):
        if "commit" in args:
            raise _sp.CalledProcessError(1, ["git", *args], stderr="disk full")
        return real(cwd, *args)

    monkeypatch.setattr(wb, "_git", failing)
    result = apply_writeback(master, vault, BOB, RULES)
    assert "disk full" in result.error
    assert result.applied == []
    assert [h.path for h in result.held] == ["Company/Home.md"]
    assert (master / "People/bob/Memory.md").read_text() == "Bob private memory.\n"
    assert not (master / "People/bob/New.md").exists()
    assert git(master, "status", "--porcelain") == ""


def test_glob_characters_in_paths_commit_literally(master: Path, tmp_path: Path):
    setup_master_git(master)
    (master / "People/bob/Notes").mkdir(parents=True)
    (master / "People/bob/Notes/other.md").write_text("untouched\n")
    git(master, "add", "-A")
    git(master, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "other")
    (master / "People/bob/Notes/other.md").write_text("dirty, must stay out\n")
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Notes/[draft] *.md").write_text("literal name\n")
    result = apply_writeback(master, vault, BOB, RULES)
    assert [c.path for c in result.applied] == ["People/bob/Notes/[draft] *.md"]
    assert git(master, "show", "--name-only", "--format=", "HEAD").strip() == \
        "People/bob/Notes/[draft] *.md"
    assert " M People/bob/Notes/other.md" in git(master, "status", "--porcelain")


def test_cli_writeback_reports_held_even_on_error(master: Path, tmp_path: Path,
                                                    monkeypatch, capsys):
    from brain.cli import main
    from tests.test_cli import seed_meta

    seed_meta(master)
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    (vault / "People/bob/Memory.md").write_text("bob edit\n")
    (vault / "Company/Home.md").write_text("held\n")  # out of scope
    import brain.writeback as wb
    real = wb._git

    def failing(cwd, *args):
        # Only the person's own applied-change commit fails here; the
        # separate hold-recording commit (a different message) must still
        # succeed so the CLI's own HELD/error reporting runs.
        if "commit" in args and any(a.startswith("writeback:") for a in args):
            raise subprocess.CalledProcessError(1, ["git", *args], stderr="disk full")
        return real(cwd, *args)

    monkeypatch.setattr(wb, "_git", failing)
    code = main(["writeback", "--master", str(master), "--vault", str(vault),
                 "--person", "bob"])
    assert code == 1
    err = capsys.readouterr().err
    assert "HELD" in err and "Company/Home.md" in err
    assert "write-back failed" in err and "disk full" in err


def test_held_record_is_never_compiled_or_written_back(master: Path, tmp_path: Path):
    (master / "People/bob/.held.json").write_text('{"sha": null, "paths": [], "at": "x"}\n')
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    assert not (vault / "People/bob/.held.json").exists()
    (vault / "People/bob/.held.json").write_text("{}\n")  # planted by the agent
    assert all(not c.path.endswith(".held.json") for c in diff_vault(vault))


def test_confirmation_record_is_never_compiled_or_written_back(master: Path, tmp_path: Path):
    (master / "People/bob/.corrections.json").write_text("{}\n")
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    assert not (vault / "People/bob/.corrections.json").exists()
    # Planted by the agent, at the person's root and nested: never a change.
    (vault / "People/bob/.corrections.json").write_text('{"evil": {"sha256": "x"}}\n')
    (vault / "People/bob/Notes").mkdir(parents=True, exist_ok=True)
    (vault / "People/bob/Notes/.corrections.json").write_text("{}\n")
    assert all(not c.path.endswith(".corrections.json") for c in diff_vault(vault))
