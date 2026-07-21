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


# ---- facts & entities in the static snapshot --------------------------------

ACME = (
    "---\nentity: client\naliases: [Acme Corp, ACME]\n---\n# Acme\n\n"
    "- Sarah Kim is our main contact [from:: 2026-01] [source:: [[Q3 Pipeline]]]\n"
    "- Dana Ortiz was our main contact [from:: 2024-06] [until:: 2026-01]\n"
)


def _facts_vault(master, tmp_path) -> Path:
    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme.md").write_text(ACME)
    return _vault(master, tmp_path)


def _facts_stats(master, tmp_path):
    return collect_vault_stats(_facts_vault(master, tmp_path),
                               include_graph=True, include_facts=True)


def test_vault_dashboard_embeds_facts_and_entities(master, tmp_path):
    html_text = render_dashboard(_facts_stats(master, tmp_path))
    blob = _extract_blob(html_text)
    assert blob["facts_total"] == 2
    assert blob["entities_total"] == 1
    assert len(blob["facts"]) == 2  # include_ended baked in
    acme = next(n for n in blob["graph"]["nodes"]
                if n["rel_path"] == "Company/Intel/Acme.md")
    assert acme["entity"] == "client"
    assert acme["aliases"] == ["Acme Corp", "ACME"]


def test_static_renderer_uses_entity_data(master, tmp_path):
    html_text = render_dashboard(_facts_stats(master, tmp_path))
    # entity ring + legend + panel tag in the graph, and the Facts section
    assert 'colorFor("entity:" + ' in html_text
    assert "renderFacts" in html_text
    assert "(entity)" in html_text


def test_dashboard_with_facts_stays_offline(master, tmp_path):
    html_text = render_dashboard(_facts_stats(master, tmp_path))
    assert "http://" not in html_text
    assert "https://" not in html_text
    assert not re.search(r'(src|href)\s*=\s*"(?!#)', html_text)


def test_cli_static_dashboard_bakes_facts(master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    out = tmp_path / "dash.html"
    assert main(["dashboard", "--vault", str(vault), "--html", str(out)]) == 0
    blob = _extract_blob(out.read_text(encoding="utf-8"))
    assert blob["facts_total"] == 2 and len(blob["facts"]) == 2


def test_baked_facts_are_vault_scoped(master, tmp_path):
    # The boundary is structural — the index only holds this vault's notes —
    # but assert it: a fact in Bob's private space must never reach Alice's
    # baked snapshot (extends the leak-property discipline to fact rows).
    (master / "People/bob/Secrets.md").write_text(
        "- Bob's secret deal [from:: 2026-01]\n")
    blob = _extract_blob(render_dashboard(_facts_stats(master, tmp_path)))
    assert blob["facts"], "fixture should bake Alice-visible facts"
    assert all("People/bob" not in f["rel_path"] for f in blob["facts"])
