import json
from pathlib import Path

import pytest

from brain.health import HEALTH_REL, SCHEMA, write_health


def _master(tmp_path: Path, gitignore: str = "_meta/cache/\n") -> Path:
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / ".gitignore").write_text(gitignore)
    return master


def test_writes_the_file_when_the_path_is_ignored(tmp_path):
    master = _master(tmp_path)
    written = write_health(master, {"warn:link-rot": 3}, {"clients": 0},
                           now="2026-08-18T20:30:00Z")
    assert written is True
    data = json.loads((master / HEALTH_REL).read_text())
    assert data["schema"] == SCHEMA
    assert data["generated_at"] == "2026-08-18T20:30:00Z"
    assert data["counts"] == {"warn:link-rot": 3}
    assert data["tamper"] == {"clients": 0}
    assert data["ok"] is True
    assert isinstance(data["brainkit_version"], str) and data["brainkit_version"]


def test_ok_is_false_when_an_error_finding_is_present(tmp_path):
    master = _master(tmp_path)
    write_health(master, {"error:symlinks": 1}, {}, now="2026-08-18T20:30:00Z")
    data = json.loads((master / HEALTH_REL).read_text())
    assert data["ok"] is False


def test_skips_the_write_when_gitignore_does_not_cover_the_path(tmp_path):
    master = _master(tmp_path, gitignore="node_modules/\n")
    written = write_health(master, {"warn:link-rot": 3}, {},
                           now="2026-08-18T20:30:00Z")
    assert written is False
    assert not (master / HEALTH_REL).exists()


def test_skips_the_write_when_there_is_no_gitignore_at_all(tmp_path):
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    written = write_health(master, {}, {}, now="2026-08-18T20:30:00Z")
    assert written is False
    assert not (master / HEALTH_REL).exists()


def test_never_leaves_a_temp_file_behind(tmp_path):
    master = _master(tmp_path)
    write_health(master, {"warn:link-rot": 1}, {}, now="2026-08-18T20:30:00Z")
    leftovers = list((master / "_meta/cache").glob("*.tmp"))
    assert leftovers == []


def test_overwrites_rather_than_appends(tmp_path):
    master = _master(tmp_path)
    write_health(master, {"warn:link-rot": 9}, {}, now="2026-08-18T20:00:00Z")
    write_health(master, {"warn:link-rot": 1}, {}, now="2026-08-18T21:00:00Z")
    data = json.loads((master / HEALTH_REL).read_text())
    assert data["counts"] == {"warn:link-rot": 1}
    assert data["generated_at"] == "2026-08-18T21:00:00Z"


def test_carries_no_message_or_path_content(tmp_path):
    """The Global Constraint, asserted structurally rather than by eyeball.

    Fails if a future change widens the payload to carry finding text — the
    top-level key set is closed, and every counts key is severity:check.
    """
    master = _master(tmp_path)
    write_health(master, {"warn:plain-ref": 2}, {"shares": 0},
                 now="2026-08-18T20:30:00Z")
    data = json.loads((master / HEALTH_REL).read_text())
    assert set(data) == {"schema", "generated_at", "brainkit_version",
                         "ok", "counts", "tamper"}
    for key, value in data["counts"].items():
        severity, _, check = key.partition(":")
        assert severity in ("error", "warn", "info")
        assert check and "/" not in check and " " not in check
        assert isinstance(value, int)
