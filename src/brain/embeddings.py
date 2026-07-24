"""Embedding providers and a content-addressed embedding cache.

Providers are pluggable behind a tiny protocol. The default is any
OpenAI-compatible ``/embeddings`` endpoint (OpenAI, Together, a local
llama-server, a company proxy) — one base URL and one key, not sixteen recipes.
A deterministic `FakeEmbeddingProvider` backs the tests and offline use so no
suite ever touches the network.

The `EmbeddingCache` is keyed by ``(chunk_sha, model)`` only — it stores no
paths and no person ids, so a cache shared across people (identical chunks
embed once) cannot reveal which documents exist, only that some bytes were
embedded before. This is what makes a full-rebuild compile cheap: unchanged
content never re-embeds.
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml

from brain.errors import BrainError

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIM = 512
_RETRYABLE = (429, 500, 502, 503, 504)


class EmbeddingError(BrainError, RuntimeError):
    """An embedding provider request failed and could not be retried."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    model: str
    dim: int | None

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


def pack_vector(vec: list[float]) -> bytes:
    """Little-endian float32 blob — exactly what sqlite-vec's vec0 accepts."""
    return struct.pack(f"<{len(vec)}f", *vec)


class FakeEmbeddingProvider:
    """Deterministic hash-based embeddings for tests and offline use.

    Not semantic — but texts sharing words land on overlapping dimensions, so
    KNN ranking is meaningful enough to test retrieval plumbing end to end.
    """

    model = "fake-32"
    dim = 32

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        import hashlib
        import math

        v = [0.0] * self.dim
        for word in text.lower().split():
            h = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "big")
            v[h % self.dim] += 1.0
            v[(h // self.dim) % self.dim] += 0.5
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


class OpenAICompatProvider:
    """Embeddings via any OpenAI-compatible ``POST {base_url}/embeddings``."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dim: int | None = None,
        batch_size: int = 128,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.batch_size = batch_size
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            out.extend(self._request(texts[i:i + self.batch_size]))
        return out

    def _request(self, batch: list[str]) -> list[list[float]]:
        payload: dict = {"model": self.model, "input": batch}
        if self.dim:
            payload["dimensions"] = self.dim
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        last: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read())
                return [d["embedding"] for d in body["data"]]
            except urllib.error.HTTPError as e:
                last = e
                if e.code in _RETRYABLE and attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise EmbeddingError(f"embedding request failed: HTTP {e.code}") from e
            except urllib.error.URLError as e:
                last = e
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise EmbeddingError(f"embedding request failed: {e}") from e
        raise EmbeddingError(f"embedding request failed: {last}")


class EmbeddingCache:
    """Content-addressed store of packed vectors keyed by (chunk_sha, model)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            "chunk_sha TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL, "
            "vector BLOB NOT NULL, PRIMARY KEY (chunk_sha, model))"
        )
        self._conn.commit()

    def get_many(self, shas: list[str], model: str) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        # chunk the IN clause to stay under SQLite's variable limit
        for i in range(0, len(shas), 500):
            batch = shas[i:i + 500]
            placeholders = ",".join("?" * len(batch))
            rows = self._conn.execute(
                f"SELECT chunk_sha, vector FROM embeddings "
                f"WHERE model = ? AND chunk_sha IN ({placeholders})",
                (model, *batch),
            ).fetchall()
            out.update({sha: bytes(vec) for sha, vec in rows})
        return out

    def put_many(self, items: list[tuple[str, bytes]], model: str) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO embeddings (chunk_sha, model, dim, vector) "
            "VALUES (?, ?, ?, ?)",
            [(sha, model, len(blob) // 4, blob) for sha, blob in items],
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _config_path() -> Path:
    override = os.environ.get("BRAIN_CONFIG")
    return Path(override) if override else Path.home() / ".config/brain/config.yaml"


def provider_from_config() -> EmbeddingProvider | None:
    """Resolve a provider from env then config file, else None (keyword-only).

    Env wins: BRAIN_EMBED_BASE_URL / _API_KEY / _MODEL / _DIM. Otherwise a
    ``~/.config/brain/config.yaml`` with an ``embeddings:`` block (api key read
    from the env var named by ``api_key_env``, never stored in the file).
    """
    base_url = os.environ.get("BRAIN_EMBED_BASE_URL")
    if base_url:
        # empty-string vars count as unset: compose files pass `${VAR:-}`
        # through, so an operator who sets only the base URL must still get
        # the defaults instead of `int("")` or a blank model name.
        return OpenAICompatProvider(
            base_url=base_url,
            api_key=os.environ.get("BRAIN_EMBED_API_KEY", ""),
            model=os.environ.get("BRAIN_EMBED_MODEL") or DEFAULT_MODEL,
            dim=int(os.environ.get("BRAIN_EMBED_DIM") or DEFAULT_DIM),
        )

    path = _config_path()
    try:
        text = path.read_text() if path.is_file() else None
    except OSError:
        text = None  # e.g. HOME pointing somewhere unreadable — no config, not a crash
    if text is not None:
        data = yaml.safe_load(text) or {}
        emb = data.get("embeddings")
        if isinstance(emb, dict) and emb.get("base_url"):
            api_key = os.environ.get(emb.get("api_key_env", ""), "")
            return OpenAICompatProvider(
                base_url=emb["base_url"],
                api_key=api_key,
                model=emb.get("model", DEFAULT_MODEL),
                dim=int(emb.get("dim", DEFAULT_DIM)),
            )
    return None


def default_cache_path() -> Path:
    override = os.environ.get("BRAIN_EMBED_CACHE")
    return Path(override) if override else Path.home() / ".cache/brain/embeddings.db"
