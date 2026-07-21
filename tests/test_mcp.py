import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from brain.compiler import compile_vault
from brain.embeddings import FakeEmbeddingProvider
from brain.indexer import build_index
from brain.mcp import serve
from tests.conftest import ALICE, RULES


@pytest.fixture
def vault(master, tmp_path):
    v = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, v)
    build_index(v, provider=FakeEmbeddingProvider(), cache=None)
    return v


def _exchange(vault: Path, requests: list[dict]) -> list[dict]:
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    serve(vault, stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def test_initialize_handshake(vault):
    (resp,) = _exchange(vault, [{"jsonrpc": "2.0", "id": 1, "method": "initialize"}])
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"]
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "brainkit"


def test_tools_list_schema(vault):
    (resp,) = _exchange(vault, [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"brain_search", "brain_read", "brain_links", "brain_recent"}
    search = next(t for t in resp["result"]["tools"] if t["name"] == "brain_search")
    assert search["inputSchema"]["required"] == ["query"]
    assert "center" in search["inputSchema"]["properties"]


def test_tools_call_search_returns_content(vault):
    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "brain_search", "arguments": {"query": "pipeline"}},
    }])
    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    assert "Q3 Pipeline.md" in text


def test_tools_call_read_happy_path(vault):
    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "brain_read", "arguments": {"rel_path": "People/alice/Memory.md"}},
    }])
    assert resp["result"]["isError"] is False
    assert "Alice private memory" in resp["result"]["content"][0]["text"]


@pytest.mark.parametrize("bad", ["../secret", "/etc/passwd", ".brain/index.db", "_meta/org.yaml"])
def test_tools_call_read_rejects_escapes(vault, bad):
    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "brain_read", "arguments": {"rel_path": bad}},
    }])
    assert resp["result"]["isError"] is True
    assert "refused" in resp["result"]["content"][0]["text"]


def test_read_rejects_symlink_planted_in_vault(master, tmp_path):
    v = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, v)
    build_index(v, provider=FakeEmbeddingProvider(), cache=None)
    # plant a symlink at an in-space path pointing at master server-only data
    link = v / "People/alice/leak.md"
    link.symlink_to(master / "_meta/org.yaml")
    (resp,) = _exchange(v, [{
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "brain_read", "arguments": {"rel_path": "People/alice/leak.md"}},
    }])
    assert resp["result"]["isError"] is True


def test_notification_is_silent_and_loop_survives_bad_json(vault):
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + "{ this is not json\n"
        + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n"
    )
    stdout = io.StringIO()
    serve(vault, stdin=stdin, stdout=stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    # notification → no response; bad json → parse error (id null); ping → result
    assert {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}} in responses
    assert any(r.get("id") == 9 and r.get("result") == {} for r in responses)
    # exactly two responses (parse error + ping), the notification produced none
    assert len(responses) == 2


def test_subprocess_smoke(vault):
    proc = subprocess.run(
        [sys.executable, "-m", "brain.cli", "mcp", "--vault", str(vault)],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n",
        capture_output=True, text=True, timeout=30,
    )
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    assert lines[0]["result"]["serverInfo"]["name"] == "brainkit"
    assert {t["name"] for t in lines[1]["result"]["tools"]} == {
        "brain_search", "brain_read", "brain_links", "brain_recent"}


def test_tools_call_search_with_center(vault):
    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {"name": "brain_search",
                   "arguments": {"query": "pipeline", "center": "Company/Home.md"}},
    }])
    assert resp["result"]["isError"] is False
    # no embed provider in tests → keyword leg + the graph rerank
    assert "[keyword-only+graph]" in resp["result"]["content"][0]["text"]


def test_tools_call_links(vault):
    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "brain_links",
                   "arguments": {"rel_path": "Teams/sales/Q3 Pipeline.md"}},
    }])
    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    assert "Backlinks (1):" in text and "Company/Home.md" in text

    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 12, "method": "tools/call",
        "params": {"name": "brain_links", "arguments": {"rel_path": "Company/Home.md"}},
    }])
    text = resp["result"]["content"][0]["text"]
    assert "Company/Decisions/Big Deal Decision.md" in text
    assert "Teams/sales/Q3 Pipeline.md" in text


def test_tools_call_links_unknown_note(vault):
    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 13, "method": "tools/call",
        "params": {"name": "brain_links", "arguments": {"rel_path": "Nope.md"}},
    }])
    assert resp["result"]["isError"] is True
    assert "not in index" in resp["result"]["content"][0]["text"]


def test_tools_call_recent_without_git(vault):
    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 14, "method": "tools/call",
        "params": {"name": "brain_recent", "arguments": {}},
    }])
    assert resp["result"]["isError"] is False
    assert "no git history" in resp["result"]["content"][0]["text"]


def test_tools_call_recent_with_git(vault):
    def git(*argv):
        subprocess.run(["git", "-C", str(vault), "-c", "user.name=t",
                        "-c", "user.email=t@t", *argv],
                       check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")
    (vault / "People/alice/Memory.md").write_text("Alice private memory. Updated.\n")
    git("add", "-A")
    git("commit", "-q", "-m", "update memory")

    (resp,) = _exchange(vault, [{
        "jsonrpc": "2.0", "id": 15, "method": "tools/call",
        "params": {"name": "brain_recent", "arguments": {"k": 3}},
    }])
    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    # the note touched by the newest commit leads the list
    assert text.splitlines()[1].startswith("- People/alice/Memory.md")
