import json
from pathlib import Path

import pytest

from brain.cli import main
from brain.compiler import compile_vault
from brain.embeddings import EmbeddingCache, FakeEmbeddingProvider
from brain.indexer import build_index
from brain.store import IndexStore
from tests.conftest import ALICE, BOB, RULES


class SpyProvider(FakeEmbeddingProvider):
    """FakeEmbeddingProvider that counts how many texts it actually embeds."""

    def __init__(self):
        self.embed_calls = 0
        self.embed_texts = 0

    def embed(self, texts):
        self.embed_calls += 1
        self.embed_texts += len(texts)
        return super().embed(texts)


def _index_files(vault: Path) -> dict:
    s = IndexStore.open(vault / ".brain/index.db")
    files = s.files()
    s.close()
    return files


def test_fresh_build_indexes_only_manifest_markdown(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    report = build_index(vault, provider=FakeEmbeddingProvider(), cache=None)
    files = _index_files(vault)
    # generated protocol files are never indexed
    assert not any(f.endswith(("CLAUDE.md", "AGENTS.md", ".gitignore")) for f in files)
    assert ".brain-manifest.json" not in files
    # real content is
    assert "People/alice/Memory.md" in files
    assert report.mode == "hybrid"
    assert report.files_indexed == len(files)


def test_incremental_reindex_embeds_nothing_when_unchanged(master, tmp_path):
    cache = EmbeddingCache(tmp_path / "cache.db")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=cache)

    # recompile with no master change, then reindex with a counting provider
    compile_vault(master, ALICE, RULES, vault)
    spy = SpyProvider()
    report = build_index(vault, provider=spy, cache=cache)
    assert report.files_indexed == 0
    assert report.files_unchanged > 0
    assert spy.embed_texts == 0  # cost control: nothing re-embedded


def test_editing_one_file_reembeds_only_that_file(master, tmp_path):
    cache = EmbeddingCache(tmp_path / "cache.db")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=cache)

    (master / "People/alice/Memory.md").write_text("Alice brand new memory content here.\n")
    compile_vault(master, ALICE, RULES, vault)
    spy = SpyProvider()
    report = build_index(vault, provider=spy, cache=cache)
    assert report.files_indexed == 1
    assert spy.embed_texts >= 1  # only the edited file's new chunks


def test_second_person_reuses_shared_cache(master, tmp_path):
    cache = EmbeddingCache(tmp_path / "cache.db")
    va = tmp_path / "alice"
    vb = tmp_path / "bob"
    compile_vault(master, ALICE, RULES, va)
    compile_vault(master, BOB, RULES, vb)
    build_index(va, provider=FakeEmbeddingProvider(), cache=cache)
    report = build_index(vb, provider=FakeEmbeddingProvider(), cache=cache)
    # Company/Clients content is readable by both; those chunks come from cache.
    assert report.chunks_from_cache > 0


def test_model_swap_forces_full_rebuild(master, tmp_path):
    cache = EmbeddingCache(tmp_path / "cache.db")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=cache)

    class OtherModel(FakeEmbeddingProvider):
        model = "other-model"

    spy_texts = []

    class CountingOther(OtherModel):
        def embed(self, texts):
            spy_texts.extend(texts)
            return super().embed(texts)

    report = build_index(vault, provider=CountingOther(), cache=cache)
    assert report.files_indexed > 0  # everything rebuilt, not skipped
    assert len(spy_texts) > 0


def test_no_provider_is_keyword_only(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    report = build_index(vault, provider=None, cache=None)
    assert report.mode == "keyword-only"
    assert report.chunks_embedded == 0
    assert report.files_indexed > 0  # chunks still stored for FTS


def test_cli_index_json_and_missing_manifest(master, tmp_path, capsys):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    # no embedding provider configured in the test env → keyword-only, no network
    assert main(["index", "--vault", str(vault), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["mode"] == "keyword-only"

    # uncompiled dir → handled error, exit 1
    assert main(["index", "--vault", str(tmp_path / "nope")]) == 1


@pytest.fixture(autouse=True)
def _no_ambient_provider(monkeypatch, tmp_path):
    # Ensure tests never pick up a real provider from the developer's env.
    for var in ("BRAIN_EMBED_BASE_URL", "BRAIN_EMBED_API_KEY", "BRAIN_EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BRAIN_CONFIG", str(tmp_path / "no-config.yaml"))
