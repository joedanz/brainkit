import json
import re
from pathlib import Path

import pytest

from brain.cli import main
from brain.compiler import compile_vault
from brain.dashboard import render_dashboard
from brain.indexer import build_index
from brain.stats import collect_master_stats, collect_vault_stats
from tests.conftest import ALICE, RULES


def _vault(master, tmp_path) -> Path:
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    return vault


def _extract_blob(html_text: str) -> dict:
    m = re.search(
        r'<script id="brain-data" type="application/json">(.*?)</script>',
        html_text, re.S)
    assert m, "embedded data blob missing"
    return json.loads(m.group(1))


def test_vault_dashboard_embeds_round_trippable_json(master, tmp_path):
    stats = collect_vault_stats(_vault(master, tmp_path), include_graph=True)
    html_text = render_dashboard(stats)
    blob = _extract_blob(html_text)
    assert blob["kind"] == "vault"
    assert blob["person"] == "alice"
    assert blob["notes_total"] == stats.notes_total
    assert blob["graph"]["nodes"]  # graph shipped for the canvas


def test_dashboard_is_fully_self_contained(master, tmp_path):
    stats = collect_vault_stats(_vault(master, tmp_path), include_graph=True)
    html_text = render_dashboard(stats)
    assert "http://" not in html_text
    assert "https://" not in html_text
    # no external fetches of any kind
    assert not re.search(r'(src|href)\s*=\s*"(?!#)', html_text)


def test_script_breakout_is_neutralized(master, tmp_path):
    # A note title with markup exercises the same door (the JSON blob) as any
    # other untrusted string; a warning carries the classic breakout payload.
    (master / "Company/Pwned <img onerror=x>.md").write_text("evil note\n")
    vault = _vault(master, tmp_path)
    stats = collect_vault_stats(vault, include_graph=True)
    stats.warnings.append('</script><script>alert(1)</script>')
    html_text = render_dashboard(stats)

    # exactly the template's own script closers remain: blob + app script
    assert html_text.count("</script>") == 2
    blob = _extract_blob(html_text)  # closing tag is unambiguous, still parses
    assert blob["warnings"][-1] == '</script><script>alert(1)</script>'
    assert any("Pwned" in n["rel_path"] for n in blob["graph"]["nodes"])


def test_master_dashboard_renders(master, tmp_path):
    from tests.test_cli import seed_meta

    seed_meta(master)
    out_root = tmp_path / "compiled"
    assert main(["compile", "--master", str(master), "--out", str(out_root)]) == 0
    stats = collect_master_stats(master, out_root)
    blob = _extract_blob(render_dashboard(stats))
    assert blob["kind"] == "master"
    assert blob["people_count"] == 2
    assert blob["permissions"]
    html_text = render_dashboard(stats)
    assert "http://" not in html_text and "https://" not in html_text


def test_cli_dashboard_writes_file_and_opens(master, tmp_path, monkeypatch, capsys):
    vault = _vault(master, tmp_path)
    out = tmp_path / "dash.html"

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert main(["dashboard", "--vault", str(vault),
                 "--html", str(out), "--open"]) == 0
    assert out.is_file()
    assert str(out) in capsys.readouterr().out
    assert opened and opened[0].startswith("file:")

    # uncompiled vault → handled error
    assert main(["dashboard", "--vault", str(tmp_path / "nope"),
                 "--html", str(tmp_path / "x.html")]) == 1
    # --vault with --out is a usage error
    assert main(["dashboard", "--vault", str(vault), "--out", "x",
                 "--html", str(out)]) == 2


def test_cli_dashboard_serves_live_by_default(master, tmp_path, monkeypatch):
    vault = _vault(master, tmp_path)
    calls = []

    def fake_run_server(lens, *, host, port, open_browser):
        calls.append({"lens": lens, "host": host, "port": port,
                      "open_browser": open_browser})
        return 0

    monkeypatch.setattr("brain.server.run_server", fake_run_server)

    # bare invocation → live server, loopback defaults, opens browser
    assert main(["dashboard", "--vault", str(vault)]) == 0
    assert len(calls) == 1
    c = calls[0]
    assert c["lens"].kind == "vault" and c["host"] == "127.0.0.1"
    assert c["port"] == 8765 and c["open_browser"] is True

    # flags flow through; --no-open suppresses the browser
    assert main(["dashboard", "--vault", str(vault),
                 "--no-open", "--port", "0"]) == 0
    assert calls[1]["port"] == 0 and calls[1]["open_browser"] is False

    # a nonexistent target fails fast without starting a server
    assert main(["dashboard", "--vault", str(tmp_path / "gone")]) == 1
    assert len(calls) == 2
