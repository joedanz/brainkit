import subprocess
from pathlib import Path

import pytest

from brain.compiler import compile_vault
from brain.indexer import build_index
from brain.watch import Lens, fingerprint
from tests.conftest import ALICE, RULES


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def _commit_all(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


def _compiled_indexed(master: Path, tmp_path: Path) -> Path:
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    return vault


def test_fingerprint_stable_when_nothing_changes(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    lens = Lens(kind="vault", vault=vault)
    assert fingerprint(lens) == fingerprint(lens)


def test_fingerprint_changes_after_reindex(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    lens = Lens(kind="vault", vault=vault)
    before = fingerprint(lens)
    (master / "People/alice/Memory.md").write_text("Different memory.\n")
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    assert fingerprint(lens) != before


def test_fingerprint_changes_after_git_commit(master, tmp_path):
    vault = _compiled_indexed(master, tmp_path)
    _git(vault, "init", "-b", "main")
    _commit_all(vault, "one")
    lens = Lens(kind="vault", vault=vault)
    before = fingerprint(lens)
    (vault / "Company" / "New.md").write_text("new\n")
    _commit_all(vault, "two")
    assert fingerprint(lens).git_heads != before.git_heads


def test_master_fingerprint_tracks_promotions_and_vaults(master, tmp_path):
    out_root = tmp_path / "compiled"
    compile_vault(master, ALICE, RULES, out_root / "alice")
    lens = Lens(kind="master", master=master, out_root=out_root)
    fp = fingerprint(lens)
    assert fp.promo_mtime is not None  # pending dir exists in the fixture
    assert any("alice" in db for db, _ in fp.index_mtimes)


def test_missing_repo_and_db_are_none_not_crash(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    lens = Lens(kind="vault", vault=empty)
    fp = fingerprint(lens)
    assert fp.git_heads == ((str(empty), None),)
    assert fp.index_mtimes[0][1] is None
