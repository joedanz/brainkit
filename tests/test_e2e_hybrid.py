"""End-to-end hybrid retrieval over a real HTTP embedding endpoint.

The unit tests mock urllib; this one stands up an actual OpenAI-compatible
embeddings server in a thread and drives the whole CLI through it, so the real
OpenAICompatProvider (URL building, headers, batching over a socket),
sqlite-vec KNN, RRF fusion, and the embedding cache are all exercised together.
"""

import hashlib
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from brain.cli import main
from brain.compiler import compile_vault
from tests.conftest import ALICE, RULES

_DIM = 64


def _vec(text: str):
    v = [0.0] * _DIM
    for w in text.lower().split():
        h = int.from_bytes(hashlib.sha256(w.encode()).digest()[:8], "big")
        v[h % _DIM] += 1.0
        v[(h // _DIM) % _DIM] += 0.5
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class _EmbedServer:
    def __init__(self):
        self.texts = 0
        parent = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                # the provider must send auth + model + our dimension
                assert self.headers["Authorization"] == "Bearer test-key"
                assert body["model"] == "mock-embed"
                assert body["dimensions"] == _DIM
                inputs = body["input"]
                parent.texts += len(inputs)
                data = [{"embedding": _vec(t), "index": i} for i, t in enumerate(inputs)]
                payload = json.dumps({"data": data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

        self._srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *a):
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture
def hybrid_env(monkeypatch, tmp_path):
    with _EmbedServer() as srv:
        monkeypatch.setenv("BRAIN_EMBED_BASE_URL", f"http://127.0.0.1:{srv.port}/v1")
        monkeypatch.setenv("BRAIN_EMBED_API_KEY", "test-key")
        monkeypatch.setenv("BRAIN_EMBED_MODEL", "mock-embed")
        monkeypatch.setenv("BRAIN_EMBED_DIM", str(_DIM))
        monkeypatch.setenv("BRAIN_EMBED_CACHE", str(tmp_path / "cache.db"))
        monkeypatch.delenv("BRAIN_CONFIG", raising=False)
        yield srv


def test_cli_index_and_search_hybrid_over_http(master, tmp_path, hybrid_env, capsys):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)

    # index against the live HTTP provider
    assert main(["index", "--vault", str(vault), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "hybrid"
    assert report["chunks_embedded"] > 0
    assert hybrid_env.texts == report["chunks_embedded"]

    # search fuses both legs; a hit is corroborated by the vector leg
    assert main(["search", "pipeline", "--vault", str(vault), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "hybrid"
    assert any("vector" in h["sources"] for h in result["hits"])


def test_incremental_reindex_hits_provider_zero_times(master, tmp_path, hybrid_env, capsys):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    main(["index", "--vault", str(vault)])
    capsys.readouterr()
    embedded_first = hybrid_env.texts
    assert embedded_first > 0

    # recompile with no master change, reindex → the provider is not called again
    compile_vault(master, ALICE, RULES, vault)
    assert main(["index", "--vault", str(vault), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["chunks_embedded"] == 0
    assert hybrid_env.texts == embedded_first  # no new socket calls
