import os
import shlex
import stat
import subprocess
from pathlib import Path

from brain.cli import build_parser

BOX = Path(__file__).resolve().parents[1] / "deploy" / "brain-box"


def _execstart(unit: str) -> list[str]:
    line = next(ln for ln in (BOX / unit).read_text().splitlines() if ln.startswith("ExecStart="))
    argv = shlex.split(line.removeprefix("ExecStart="))
    return [a.replace("%i", "alice").replace("${BRAIN_MCP_EMAIL}", "alice@acme.com")
             .replace("${BRAIN_MCP_PORT}", "8901") for a in argv]


def test_person_unit_parses_against_the_real_cli():
    argv = _execstart("brain-mcp@.service")
    assert argv[0] == "/usr/local/bin/brain"
    args = build_parser().parse_args(argv[1:])
    assert args.http and args.person == "alice" and args.port == 8901
    assert args.vault == "/srv/brain/compiled/alice" and args.spool == "/srv/brain/spool/alice"


def test_router_unit_parses_and_cannot_see_brains():
    args = build_parser().parse_args(_execstart("brain-mcp-router.service")[1:])
    assert args.routes == "/etc/brain/mcp-routes.yaml" and args.port == 8900
    text = (BOX / "brain-mcp-router.service").read_text()
    assert "InaccessiblePaths=/srv/brain" in text and "ReadWritePaths" not in text


def test_person_unit_reads_shared_and_per_person_env():
    text = (BOX / "brain-mcp@.service").read_text()
    assert "EnvironmentFile=/etc/brain/mcp.env" in text
    assert "EnvironmentFile=/etc/brain/mcp.d/%i.env" in text
    assert "InaccessiblePaths=-/srv/brain/master" in text


def _fake(bin_dir: Path, name: str, script: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def test_liveness_checks_instances_not_templates(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "checked"
    _fake(bin_dir, "systemctl", f"""
case "$1" in
  list-unit-files) printf 'brain-admin.service enabled\\nbrain-mcp@.service enabled\\n' ;;
  list-units) printf 'brain-mcp@alice.service loaded active running x\\n' ;;
  is-active) echo "$3" >> {log}; exit 0 ;;
esac
""")
    _fake(bin_dir, "curl", "exit 0\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "HEALTHCHECK_URL": "https://hc.example/x"}
    subprocess.run(["sh", str(BOX / "brain-liveness.sh")], env=env, check=True)
    checked = log.read_text().split()
    assert "brain-mcp@alice.service" in checked and "brain-admin.service" in checked
    assert "brain-mcp@.service" not in checked
