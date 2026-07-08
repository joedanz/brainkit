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
        return FakeResp(len(body["input"]))

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    p = OpenAICompatProvider("https://api.example.com/v1/", "KEY",
                             "text-embedding-3-small", dim=512, batch_size=2)
    out = p.embed(["a", "b", "c"])
    assert len(out) == 3
    # 3 inputs, batch size 2 → two requests
    assert [len(c["input"]) for c in calls] == [2, 1]


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


def test_default_cache_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_EMBED_CACHE", str(tmp_path / "c.db"))
    assert default_cache_path() == tmp_path / "c.db"
