import json
import struct

import pytest

from brain import embeddings
from brain.embeddings import (
    EmbeddingCache,
    EmbeddingError,
    FakeEmbeddingProvider,
    OpenAICompatProvider,
    default_cache_path,
    pack_vector,
    provider_from_config,
)


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot  # fake vectors are unit-norm, so dot == cosine


def test_fake_is_deterministic_and_right_dim():
    p = FakeEmbeddingProvider()
    v1 = p.embed(["hello world"])
    v2 = p.embed(["hello world"])
    assert v1 == v2
    assert len(v1[0]) == p.dim == 32


def test_fake_shared_words_are_closer():
    p = FakeEmbeddingProvider()
    a, b, c = p.embed([
        "quarterly revenue pipeline growth",
        "quarterly revenue pipeline forecast",
        "unrelated cooking recipe ingredients",
    ])
    assert _cos(a, b) > _cos(a, c)


def test_pack_vector_is_float32_le():
    blob = pack_vector([1.0, 2.0, 0.5])
    assert blob == struct.pack("<3f", 1.0, 2.0, 0.5)
    assert len(blob) == 12


def test_cache_round_trip_and_model_isolation(tmp_path):
    cache = EmbeddingCache(tmp_path / "emb.db")
    va, vb = pack_vector([0.1, 0.2]), pack_vector([0.3, 0.4])
    cache.put_many([("sha-a", va), ("sha-b", vb)], model="m1")
    got = cache.get_many(["sha-a", "sha-b", "missing"], model="m1")
    assert got == {"sha-a": va, "sha-b": vb}
    # different model → no hits for the same shas
    assert cache.get_many(["sha-a"], model="m2") == {}


def test_openai_provider_request_shape_and_batching(monkeypatch):
    calls = []

    class FakeResp:
        def __init__(self, n):
            self._n = n
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"data": [{"embedding": [0.0, 1.0]} for _ in range(self._n)]}).encode()

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data)
        calls.append(body)
        assert req.full_url == "https://api.example.com/v1/embeddings"
        assert req.headers["Authorization"] == "Bearer KEY"
        assert body["model"] == "text-embedding-3-small"
        assert body["dimensions"] == 512
        assert "providerOptions" not in body  # gateway-only field
        return FakeResp(len(body["input"]))

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    p = OpenAICompatProvider("https://api.example.com/v1/", "KEY",
                             "text-embedding-3-small", dim=512, batch_size=2)
    out = p.embed(["a", "b", "c"])
    assert len(out) == 3
    # 3 inputs, batch size 2 → two requests
    assert [len(c["input"]) for c in calls] == [2, 1]


def test_openai_provider_asks_vercel_gateway_for_no_training_upstreams(monkeypatch):
    bodies = []

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"data": [{"embedding": [0.0]}]}).encode()

    def fake_urlopen(req, timeout=None):
        bodies.append(json.loads(req.data))
        return FakeResp()

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    OpenAICompatProvider("https://ai-gateway.vercel.sh/v1", "K", "voyage/voyage-4").embed(["a"])
    OpenAICompatProvider("https://ai-gateway.vercel.sh.example.com/v1", "K", "m").embed(["a"])
    assert bodies[0]["providerOptions"] == {"gateway": {"disallowPromptTraining": True}}
    assert "providerOptions" not in bodies[1]  # a lookalike host is not the gateway


def test_openai_provider_retries_then_raises(monkeypatch):
    import urllib.error

    monkeypatch.setattr(embeddings.time, "sleep", lambda *_: None)
    attempts = {"n": 0}

    def always_429(req, timeout=None):
        attempts["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, None)

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", always_429)
    p = OpenAICompatProvider("https://x/v1", "K", "m")
    with pytest.raises(EmbeddingError):
        p.embed(["a"])
    assert attempts["n"] == 3  # initial + 2 retries


def test_openai_provider_recovers_after_transient_429(monkeypatch):
    import urllib.error

    monkeypatch.setattr(embeddings.time, "sleep", lambda *_: None)
    state = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"data": [{"embedding": [1.0]}]}).encode()

    def flaky(req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "unavailable", {}, None)
        return FakeResp()

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", flaky)
    p = OpenAICompatProvider("https://x/v1", "K", "m")
    assert p.embed(["a"]) == [[1.0]]


def test_provider_from_config_env_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "nope.yaml"))
    monkeypatch.delenv("BRAIN_EMBED_BASE_URL", raising=False)
    # no base url configured anywhere → None (keyword-only)
    assert provider_from_config() is None

    monkeypatch.setenv("BRAIN_EMBED_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("BRAIN_EMBED_API_KEY", "sk-test")
    monkeypatch.setenv("BRAIN_EMBED_MODEL", "custom-model")
    monkeypatch.setenv("BRAIN_EMBED_DIM", "256")
    p = provider_from_config()
    assert isinstance(p, OpenAICompatProvider)
    assert p.model == "custom-model"
    assert p.dim == 256


def test_provider_from_config_empty_vars_mean_unset(monkeypatch, tmp_path):
    # compose files pass `${VAR:-}` through, so model/dim arrive as empty
    # strings when the operator sets only the base URL — defaults must apply
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "nope.yaml"))
    monkeypatch.setenv("BRAIN_EMBED_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("BRAIN_EMBED_API_KEY", "")
    monkeypatch.setenv("BRAIN_EMBED_MODEL", "")
    monkeypatch.setenv("BRAIN_EMBED_DIM", "")
    p = provider_from_config()
    assert isinstance(p, OpenAICompatProvider)
    assert p.model == embeddings.DEFAULT_MODEL
    assert p.dim == embeddings.DEFAULT_DIM


def test_provider_from_config_unresolved_placeholder_base_url_means_unset(monkeypatch, tmp_path):
    # hermes interpolates `${VAR}` in an MCP server's env block and leaves the
    # literal text when VAR is missing from its own environment. A provider
    # aimed at that "URL" would make every search fail; keyword-only is right.
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "nope.yaml"))
    monkeypatch.setenv("BRAIN_EMBED_BASE_URL", "${BRAIN_EMBED_BASE_URL}")
    monkeypatch.setenv("BRAIN_EMBED_API_KEY", "${BRAIN_EMBED_API_KEY}")
    monkeypatch.setenv("BRAIN_EMBED_MODEL", "${BRAIN_EMBED_MODEL}")
    monkeypatch.setenv("BRAIN_EMBED_DIM", "${BRAIN_EMBED_DIM}")
    assert provider_from_config() is None


def test_provider_from_config_unresolved_placeholders_fall_back_to_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "nope.yaml"))
    monkeypatch.setenv("BRAIN_EMBED_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("BRAIN_EMBED_API_KEY", "${BRAIN_EMBED_API_KEY}")
    monkeypatch.setenv("BRAIN_EMBED_MODEL", "${BRAIN_EMBED_MODEL}")
    monkeypatch.setenv("BRAIN_EMBED_DIM", "${BRAIN_EMBED_DIM}")  # int() would raise
    p = provider_from_config()
    assert isinstance(p, OpenAICompatProvider)
    assert p.api_key == ""
    assert p.model == embeddings.DEFAULT_MODEL
    assert p.dim == embeddings.DEFAULT_DIM


def test_provider_from_config_placeholder_env_falls_through_to_config_file(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "embeddings:\n  base_url: https://file.example.com/v1\n  api_key_env: FILE_KEY\n"
    )
    monkeypatch.setenv("BRAIN_CONFIG", str(cfg))
    monkeypatch.setenv("BRAIN_EMBED_BASE_URL", "${BRAIN_EMBED_BASE_URL}")
    monkeypatch.setenv("FILE_KEY", "${FILE_KEY}")
    p = provider_from_config()
    assert isinstance(p, OpenAICompatProvider)
    assert p.base_url == "https://file.example.com/v1"
    assert p.api_key == ""


def test_provider_from_config_resolved_values_with_dollar_signs_survive(monkeypatch, tmp_path):
    # only the `${...}` shape is a placeholder; a key with a bare `$` is real
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "nope.yaml"))
    monkeypatch.setenv("BRAIN_EMBED_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("BRAIN_EMBED_API_KEY", "sk-$abc")
    p = provider_from_config()
    assert isinstance(p, OpenAICompatProvider)
    assert p.api_key == "sk-$abc"


def test_default_cache_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_EMBED_CACHE", str(tmp_path / "c.db"))
    assert default_cache_path() == tmp_path / "c.db"
