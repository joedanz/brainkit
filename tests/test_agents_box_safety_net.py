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


def test_the_scan_is_in_the_image_and_executable():
    df = (DEPLOY / "Dockerfile").read_text()
    assert "scripts/brain-context-scan /usr/local/bin/brain-context-scan" in df
    assert "/usr/local/bin/brain-context-scan" in df.split("RUN chmod 755")[1]
    assert os.access(SCAN, os.X_OK)
