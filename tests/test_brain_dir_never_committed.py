"""`.brain/` (the search index) must never enter a vault's git history.

Live-found 2026-10-30: a hard reboot of embark-brain left every vault's
`.gitignore` empty, the next `git add -A` tracked the 118 MB `index.db`, and
from then on the cycle and every agent's vault-sync committed a fresh copy each
run: the brain disk filled, three manifests were cut off mid-write, and every
agent sync failed (the live index changes between the commit and the pull).
Nothing may depend on `.gitignore` for this.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from brain.compiler import write_manifest
from brain.holds import commit_vault_strays

SYNC = Path(__file__).resolve().parents[1] / "deploy/agents-box/scripts/vault-sync"


def _g(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


def _repo(path: Path) -> Path:
    path.mkdir()
    _g(path, "init", "-q", "-b", "main")
    _g(path, "config", "user.name", "t")
    _g(path, "config", "user.email", "t@t")
    (path / "a.md").write_text("a\n")
    _g(path, "add", "-A")
    _g(path, "commit", "-qm", "seed")
    return path


def test_the_cycle_never_commits_brain_dir_even_without_a_gitignore(tmp_path):
    vault = _repo(tmp_path / "v")
    (vault / ".brain").mkdir()
    (vault / ".brain/index.db").write_bytes(b"index")
    (vault / "note.md").write_text("edit\n")
    assert commit_vault_strays(vault)
    assert _g(vault, "ls-files").split() == ["a.md", "note.md"]


def test_the_cycle_untracks_a_brain_dir_a_crash_already_committed(tmp_path):
    vault = _repo(tmp_path / "v")
    (vault / ".brain").mkdir()
    (vault / ".brain/index.db").write_bytes(b"v1")
    _g(vault, "add", "-A")
    _g(vault, "commit", "-qm", "the bad commit")
    (vault / ".brain/index.db").write_bytes(b"v2")
    commit_vault_strays(vault)
    assert ".brain/index.db" not in _g(vault, "ls-files")
    assert (vault / ".brain/index.db").read_bytes() == b"v2"  # the live index stays


def test_a_manifest_rewrite_cut_short_leaves_the_old_manifest_whole(tmp_path, monkeypatch):
    path = tmp_path / ".brain-manifest.json"
    write_manifest(path, {"compiled": "x", "files": ["a"] * 50})
    before = path.read_bytes()
    real = Path.write_text

    def disk_full(self, data, *a, **k):
        real(self, data[: len(data) // 2], *a, **k)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", disk_full)
    with pytest.raises(OSError):
        write_manifest(path, {"compiled": "y", "files": ["b"] * 50})
    assert path.read_bytes() == before
    json.loads(before)


def test_vault_sync_never_commits_brain_dir_and_untracks_a_committed_one(tmp_path):
    server = _repo(tmp_path / "server")
    (server / ".brain-manifest.json").write_text("{}")
    _g(server, "add", "-A")
    _g(server, "commit", "-qm", "manifest")
    _g(server, "config", "receive.denyCurrentBranch", "updateInstead")
    vault = tmp_path / "vault"
    subprocess.run(["git", "clone", "-q", str(server), str(vault)], check=True)
    _g(vault, "config", "user.name", "bob (agent)")
    _g(vault, "config", "user.email", "bob@agents.brain.local")
    # The crash: an index already committed in the agent's clone.
    (vault / ".brain").mkdir()
    (vault / ".brain/index.db").write_bytes(b"v1")
    _g(vault, "add", "-A")
    _g(vault, "commit", "-qm", "agent: sync (bad)")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # The agent's own `brain index` rewrites the live index on every run.
    (bindir / "brain").write_text(f'#!/bin/sh\necho v2 > "{vault}/.brain/index.db"\n')
    (bindir / "brain").chmod(0o755)
    env = {**os.environ, "BRAIN_VAULT_DIR": str(vault), "PATH": f"{bindir}:{os.environ['PATH']}"}

    (vault / "note.md").write_text("agent work\n")
    for _ in range(2):
        r = subprocess.run(["sh", str(SYNC)], capture_output=True, text=True, env=env)
        assert "pull failed" not in r.stderr and "push refused" not in r.stderr, r.stderr
    assert ".brain/" not in _g(vault, "ls-files")
    assert ".brain/" not in _g(server, "ls-files")
    assert (server / "note.md").read_text() == "agent work\n"
    assert (vault / ".brain/index.db").exists()
