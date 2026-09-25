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


# ---- the cache under a master: gitignore gate and damage ------------------ #

def _damage(db, where):
    from .test_triage import _damage as damage
    damage(db, where)


def _filled(path):
    cache = EmbeddingCache(path)
    cache.put_many([(f"sha-{i}", pack_vector([float(i)] * 64)) for i in range(400)], "m")
    cache.close()


def test_for_master_refuses_a_master_that_does_not_ignore_the_cache(tmp_path):
    assert EmbeddingCache.for_master(tmp_path) is None
    assert not (tmp_path / "_meta").exists()


def test_for_master_opens_the_cache_when_ignored(tmp_path):
    (tmp_path / ".gitignore").write_text("_meta/cache/\n")
    cache = EmbeddingCache.for_master(tmp_path)
    assert cache is not None
    cache.put_many([("a", pack_vector([1.0]))], "m")
    cache.close()
    assert (tmp_path / "_meta/cache/embeddings.db").is_file()


@pytest.mark.parametrize("where", ["header", "interior"])
def test_a_damaged_cache_is_rebuilt_once_with_a_warning(tmp_path, where):
    (tmp_path / ".gitignore").write_text("_meta/cache/\n")
    db = tmp_path / "_meta/cache/embeddings.db"
    db.parent.mkdir(parents=True)
    _filled(db)
    (db.parent / "dedup.db").write_bytes(b"keep me")
    _damage(db, where)

    cache = EmbeddingCache.for_master(tmp_path)
    assert cache.get_many([f"sha-{i}" for i in range(400)], "m") == {}
    cache.put_many([("new", pack_vector([2.0]))], "m")
    assert cache.get_many(["new"], "m") == {"new": pack_vector([2.0])}
    assert len(cache.warnings) == 1
    assert "embeddings.db" in cache.warnings[0] and "rebuilt" in cache.warnings[0]
    cache.close()
    assert sorted(p.name for p in db.parent.iterdir()) == ["dedup.db", "embeddings.db"]
    assert (db.parent / "dedup.db").read_bytes() == b"keep me"

    again = EmbeddingCache.for_master(tmp_path)
    assert again.get_many(["new"], "m") == {"new": pack_vector([2.0])}
    assert again.warnings == []
    again.close()


def test_a_busy_cache_is_not_deleted(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "emb.db"
    _filled(db)
    cache = EmbeddingCache(db)

    class Busy:
        def execute(self, *a, **k):
            e = sqlite3.OperationalError("database is locked")
            e.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise e

        def close(self):
            pass

    real = cache._conn
    cache._conn = Busy()
    with pytest.raises(sqlite3.OperationalError):
        cache.get_many(["sha-1"], "m")
    cache._conn = real
    assert cache.get_many(["sha-1"], "m") != {}
    cache.close()
