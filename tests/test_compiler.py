import json
from pathlib import Path

import pytest

from brain.compiler import MANIFEST_NAME, compile_vault
from tests.conftest import ALICE, BOB, RULES


def test_compiles_only_readable_spaces(master: Path, tmp_path: Path):
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
    # Master-root files (server chief-of-staff protocol) are never copied
    assert "AGENTS.md" not in {p.name for p in out.iterdir()}
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
