"""backup-agents.sh against a stub `docker`: which hermes backup failures keep
the archive, and which fail the run."""

import os
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy/agents-box"
SCRIPT = DEPLOY / "backup-agents.sh"

# What `hermes backup` printed on agent-ayal, 2026-09-26..28: hermes cron
# rotated its own output files between the scan and the archive. The archive
# was kept; hermes exited non-zero, so the script never copied it out.
VANISHED = """\
  Archive kept, but 2 file(s) could not be added:
  cron/output/e619/2026-09-27_22-40-41.md: [Errno 2] No such file or directory: '/opt/data/cron/output/e619/2026-09-27_22-40-41.md'
  cron/output/dbec/2026-09-27_22-40-33.md: [Errno 2] No such file or directory: '/opt/data/cron/output/dbec/2026-09-27_22-40-33.md'
"""
UNREADABLE = """\
  Archive kept, but 2 file(s) could not be added:
  cron/output/e619/a.md: [Errno 2] No such file or directory: '/opt/data/cron/output/e619/a.md'
  state.db: [Errno 13] Permission denied: '/opt/data/state.db'
"""

# Each container's behaviour comes from a file named after it:
#   <name>.out  what `hermes backup` prints     <name>.rc  its exit code
#   <name>.zip  present = the archive exists in the container
STUB = """#!/bin/sh
S="$STUB_DIR"
case "$1" in
  ps) cat "$S/containers" ;;
  exec)
    c="$2"; shift 2
    case "$1" in
      hermes) cat "$S/$c.out" 2>/dev/null; exit "$(cat "$S/$c.rc" 2>/dev/null || echo 0)" ;;
      test) [ -f "$S/$c.zip" ] ;;
      rm) exit 0 ;;
    esac ;;
  cp) c="${2%%:*}"; [ -f "$S/$c.zip" ] && cp "$S/$c.zip" "$3" ;;
esac
"""


def _run(tmp_path, containers):
    stub = tmp_path / "stub"
    (stub / "bin").mkdir(parents=True)
    docker = stub / "bin/docker"
    docker.write_text(STUB)
    docker.chmod(0o755)
    (stub / "containers").write_text("".join(f"{c}\n" for c in containers))
    for c, (out, rc, has_zip) in containers.items():
        (stub / f"{c}.out").write_text(out)
        (stub / f"{c}.rc").write_text(str(rc))
        if has_zip:
            (stub / f"{c}.zip").write_text("zip")
    dest = tmp_path / "dest"
    env = {**os.environ, "STUB_DIR": str(stub),
           "PATH": f"{stub / 'bin'}:{os.environ['PATH']}"}
    r = subprocess.run(["sh", str(SCRIPT), str(dest)],
                       capture_output=True, text=True, env=env)
    return r, sorted(p.name.split("-2")[0] for p in dest.glob("*.zip"))


def test_a_clean_backup_is_copied(tmp_path):
    r, zips = _run(tmp_path, {"agent-a": ("done\n", 0, True)})
    assert r.returncode == 0, r.stderr
    assert zips == ["agent-a"]


def test_files_that_vanished_mid_backup_keep_the_archive(tmp_path):
    r, zips = _run(tmp_path, {"agent-ayal": (VANISHED, 1, True),
                              "agent-hymie": ("done\n", 0, True)})
    assert r.returncode == 0, r.stderr
    assert zips == ["agent-ayal", "agent-hymie"]
    # Still said, so a pattern of vanishing files is visible in the log.
    assert "vanished" in r.stdout + r.stderr
    assert "2026-09-27_22-40-41.md" in r.stdout + r.stderr


def test_any_other_unreadable_file_still_fails_the_run(tmp_path):
    r, zips = _run(tmp_path, {"agent-ayal": (UNREADABLE, 1, True),
                              "agent-hymie": ("done\n", 0, True)})
    assert r.returncode == 1
    assert "FAILED: agent-ayal" in r.stderr
    assert zips == ["agent-hymie"]


def test_a_failure_with_no_archive_fails_even_if_the_text_matches(tmp_path):
    r, zips = _run(tmp_path, {"agent-ayal": (VANISHED, 1, False)})
    assert r.returncode == 1
    assert zips == []


def test_a_plain_failure_still_fails(tmp_path):
    r, zips = _run(tmp_path, {"agent-ayal": ("Traceback: boom\n", 1, True)})
    assert r.returncode == 1
    assert zips == []
