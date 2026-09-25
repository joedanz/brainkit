"""The agents-box safety net: brain-context-scan's marker logic, its wiring,
and agents-liveness.sh's /fail body."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy/agents-box"
SCAN = DEPLOY / "scripts/brain-context-scan"

# A stand-in for the container's own Hermes: tools.threat_patterns with the
# same signature, flagging one word.
STUB = '''
def scan_for_threats(content, scope="context"):
    assert scope == "context"
    hits = []
    if "\\ufeff" in content:
        hits.append("invisible_unicode_U+FEFF")
    if "Mythic" in content:
        hits.append("known_c2_framework")
    return hits
'''


@pytest.fixture
def hermes(tmp_path):
    root = tmp_path / "hermes"
    (root / "tools").mkdir(parents=True)
    (root / "tools/__init__.py").write_text("")
    (root / "tools/threat_patterns.py").write_text(STUB)
    return root


def _scan(pythonpath: Path, *args: str):
    env = {**os.environ, "PYTHONPATH": str(pythonpath)}
    return subprocess.run([sys.executable, str(SCAN), *args],
                          capture_output=True, text=True, env=env)


def test_a_hit_writes_the_marker_and_a_clean_pass_removes_it(hermes, tmp_path):
    agents, soul = tmp_path / "AGENTS.md", tmp_path / "SOUL.md"
    agents.write_text("# Protocol\nClient: Mythic Games\n")
    soul.write_text("You are a helpful assistant.\n")
    marker = tmp_path / "data/.brain-context-blocked"
    marker.parent.mkdir()
    r = _scan(hermes, "--marker", str(marker), str(agents), str(soul),
              str(tmp_path / "missing.md"))
    assert r.returncode == 3
    assert r.stdout.strip() == "AGENTS.md:known_c2_framework"
    assert marker.read_text() == "AGENTS.md:known_c2_framework\n"
    agents.write_text("# Protocol\nClient: Acme\n")
    r = _scan(hermes, "--marker", str(marker), str(agents), str(soul))
    assert r.returncode == 0 and r.stdout == ""
    assert not marker.exists()


def test_a_leading_bom_is_stripped_as_hermes_does(hermes, tmp_path):
    f = tmp_path / "SOUL.md"
    f.write_text("\ufeffYou are a helpful assistant.\n")
    assert _scan(hermes, str(f)).returncode == 0


def test_hermes_internals_moving_is_silent_and_leaves_the_marker(tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    probe = subprocess.run([sys.executable, "-c", "import tools.threat_patterns"],
                           capture_output=True, env={**os.environ, "PYTHONPATH": str(empty)})
    if probe.returncode == 0:
        pytest.skip("a tools.threat_patterns is importable in this environment")
    marker = tmp_path / ".brain-context-blocked"
    marker.write_text("AGENTS.md:c2_heartbeat\n")
    f = tmp_path / "AGENTS.md"
    f.write_text("Mythic\n")
    r = _scan(empty, "--marker", str(marker), str(f))
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert marker.read_text() == "AGENTS.md:c2_heartbeat\n"


def test_hermes_changing_the_filter_signature_is_silent_and_leaves_the_marker(tmp_path):
    root = tmp_path / "hermes"
    (root / "tools").mkdir(parents=True)
    (root / "tools/__init__.py").write_text("")
    (root / "tools/threat_patterns.py").write_text("def scan_for_threats(content):\n    return []\n")
    marker = tmp_path / ".brain-context-blocked"
    marker.write_text("AGENTS.md:c2_heartbeat\n")
    f = tmp_path / "AGENTS.md"
    f.write_text("Mythic\n")
    r = _scan(root, "--marker", str(marker), str(f))
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert marker.read_text() == "AGENTS.md:c2_heartbeat\n"


def test_the_build_probe_imports_from_outside_the_hermes_checkout():
    df = (DEPLOY / "Dockerfile").read_text()
    probe = df[df.index("from tools.threat_patterns import") - 120:]
    assert "RUN cd / && /opt/hermes/.venv/bin/python -c" in probe


def test_the_scan_is_in_the_image_and_executable():
    df = (DEPLOY / "Dockerfile").read_text()
    assert "scripts/brain-context-scan /usr/local/bin/brain-context-scan" in df
    assert "/usr/local/bin/brain-context-scan" in df.split("RUN chmod 755")[1]
    assert os.access(SCAN, os.X_OK)


HERMES_PY = "/opt/hermes/.venv/bin/python"
MARKER = "/opt/data/.brain-context-blocked"


def test_vault_sync_scans_after_the_pull_and_never_fails_on_it():
    src = (DEPLOY / "scripts/vault-sync").read_text()
    scan = src.index("brain-context-scan")
    assert src.index("pull -q") < scan < src.index("push -q")
    block = src[scan - 400: scan + 400]
    assert HERMES_PY in block and MARKER in block
    for f in ('"$V/AGENTS.md"', '"$V/CLAUDE.md"', "/opt/data/SOUL.md"):
        assert f in block
    assert '[ -x "$HERMES_PY" ]' in src or f"[ -x {HERMES_PY} ]" in src


def test_first_boot_scans_soul_after_the_managed_blocks_and_only_warns():
    src = (DEPLOY / "scripts/03-brain-first-boot").read_text()
    scan = src.index("brain-context-scan")
    assert src.index("# --- SOUL.md: managed blocks") < scan
    line = src[scan: src.index("\n", src.index("||", scan))]
    assert '"$DATA/SOUL.md"' in line and "--marker" not in line
    assert "|| echo" in line  # set -eu: a hit must never fail the boot


LIVENESS = DEPLOY / "agents-liveness.sh"

FAKE_DOCKER = """#!/bin/sh
case "$*" in
  "compose config --services") cat "$FAKE/services" ;;
  "compose ps --services --status running") cat "$FAKE/running" ;;
  "compose exec -T "*) cat "$FAKE/markers/$4" 2>/dev/null || exit 1 ;;
  *) echo "unexpected: $*" >&2; exit 9 ;;
esac
"""
FAKE_CURL = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE/curl.args"
"""


def _liveness(tmp_path, services, running, markers):
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    (fake / "markers").mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        p = fake / "bin" / name
        p.write_text(body)
        p.chmod(0o755)
    (fake / "services").write_text("\n".join(services) + "\n")
    (fake / "running").write_text("\n".join(running) + "\n")
    for svc, text in markers.items():
        (fake / "markers" / svc).write_text(text)
    env = {**os.environ, "FAKE": str(fake), "COMPOSE_DIR": str(tmp_path),
           "HEALTHCHECK_URL": "https://hc.example/uuid",
           "PATH": f"{fake / 'bin'}:{os.environ['PATH']}"}
    r = subprocess.run(["sh", str(LIVENESS)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return (fake / "curl.args").read_text().splitlines()


def test_all_up_and_clean_pings_success(tmp_path):
    args = _liveness(tmp_path, ["agent-a", "agent-b"], ["agent-a", "agent-b"], {})
    assert args[-1] == "https://hc.example/uuid" and "--data-raw" not in args


def test_down_body_is_unchanged(tmp_path):
    args = _liveness(tmp_path, ["agent-a", "agent-b"], ["agent-a"], {})
    assert args[-1] == "https://hc.example/uuid/fail"
    assert args[args.index("--data-raw") + 1] == "down: agent-b"


def test_a_blocked_protocol_fails_the_check_and_names_it(tmp_path):
    args = _liveness(tmp_path, ["agent-a", "agent-b"], ["agent-a", "agent-b"],
                     {"agent-b": "AGENTS.md:c2_heartbeat CLAUDE.md:c2_heartbeat\n"})
    assert args[-1] == "https://hc.example/uuid/fail"
    assert args[args.index("--data-raw") + 1] == \
        "blocked: agent-b(AGENTS.md:c2_heartbeat CLAUDE.md:c2_heartbeat)"


def test_down_and_blocked_share_one_body(tmp_path):
    args = _liveness(tmp_path, ["agent-a", "agent-b", "agent-c"], ["agent-a", "agent-b"],
                     {"agent-a": "SOUL.md:known_c2_framework\n"})
    assert args[args.index("--data-raw") + 1] == \
        "down: agent-c; blocked: agent-a(SOUL.md:known_c2_framework)"
