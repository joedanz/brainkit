import json
import shutil
from pathlib import Path

import pytest

from brain.cli import main
from brain.compiler import compile_vault
from brain.embeddings import EmbeddingCache, FakeEmbeddingProvider
from brain.indexer import build_index
from brain.store import IndexStore
from tests.conftest import ALICE, BOB, RULES, requires_vectors


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


@requires_vectors
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


@requires_vectors
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


@requires_vectors
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


@requires_vectors
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


def _links(vault: Path) -> set[tuple[str, str, int]]:
    s = IndexStore.open(vault / ".brain/index.db")
    rows = {
        (src, tgt, res)
        for src, tgt, res in s.conn.execute(
            "SELECT src_rel_path, target_rel_path, resolved FROM links")
    }
    s.close()
    return rows


def test_links_populated_and_resolved(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    links = _links(vault)
    # Home.md links to two notes alice can read; both resolve to real paths.
    assert ("Company/Home.md", "Company/Decisions/Big Deal Decision.md", 1) in links
    assert ("Company/Home.md", "Teams/sales/Q3 Pipeline.md", 1) in links


def test_stubbed_links_never_reach_the_index(master, tmp_path):
    # Bob can't read Teams/sales, so the compiler stubs [[Q3 Pipeline]] out of
    # his Home.md — the link must not exist in his index in any form.
    vault = tmp_path / "bob"
    compile_vault(master, BOB, RULES, vault)
    build_index(vault, provider=None, cache=None)
    assert not any("Q3 Pipeline" in tgt for _, tgt, _ in _links(vault))


def test_path_form_links_resolve_exactly(master, tmp_path):
    (master / "Company/Paths.md").write_text(
        "See [[Company/Decisions/Big Deal Decision]] and [[Company/Nope/Missing]].\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    links = _links(vault)
    assert ("Company/Paths.md", "Company/Decisions/Big Deal Decision.md", 1) in links
    assert ("Company/Paths.md", "Company/Nope/Missing", 0) in links


def test_removed_target_demotes_link_from_unchanged_source(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)

    (master / "Company/Decisions/Big Deal Decision.md").unlink()
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)  # Home.md itself unchanged
    links = _links(vault)
    assert ("Company/Home.md", "Company/Decisions/Big Deal Decision.md", 0) in links


def test_editing_source_replaces_its_links(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)

    (master / "Company/Home.md").write_text("# Home\nNow only [[Q3 Pipeline]].\n")
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    home_links = {tgt for src, tgt, _ in _links(vault) if src == "Company/Home.md"}
    assert home_links == {"Teams/sales/Q3 Pipeline.md"}


def test_schema_migration_forces_full_rebuild(master, tmp_path):
    import sqlite3

    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)

    # Simulate a pre-links index: wipe the table, pin user_version back to 1.
    db = vault / ".brain/index.db"
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM links")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    report = build_index(vault, provider=None, cache=None)
    assert report.files_indexed > 0  # rebuilt, not skipped as unchanged
    assert _links(vault)  # links repopulated


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


def test_entities_facts_and_aliases_indexed(master, tmp_path):
    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme.md").write_text(
        "---\n"
        "entity: client\n"
        "aliases: [Acme Corp, ACME]\n"
        "---\n"
        "# Acme\n\n"
        "- Sarah Kim ([[Big Deal Decision]]) is our main contact\n"
        "  [from:: 2026-01]\n"
        "- Dana Ortiz was our main contact\n"
        "  [from:: 2024-06] [until:: 2026-01]\n"
    )
    # an alias-form wikilink that only resolves through the aliases table
    (master / "Company/Notes.md").write_text("See [[ACME]] for the account.\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)

    s = IndexStore.open_readonly(vault / ".brain/index.db", want_vectors=False)
    assert s.entities() == [("Company/Intel/Acme.md", "client", ["Acme Corp", "ACME"])]

    rows = s.fact_rows("Company/Intel/Acme.md")
    assert [(r[3], r[4], r[5]) for r in rows] == [
        ("Sarah Kim ([[Big Deal Decision]]) is our main contact", "2026-01-01", None),
        ("Dana Ortiz was our main contact", "2024-06-01", "2026-01-31"),
    ]
    # fact 1 resolves its [[Big Deal Decision]] target AND the implicit page subject
    targets = {t for (t,) in s.conn.execute(
        "SELECT target_rel_path FROM fact_entities WHERE fact_id = ?", (rows[0][0],))}
    assert targets == {"Company/Decisions/Big Deal Decision.md", "Company/Intel/Acme.md"}

    # [[ACME]] resolved via alias
    assert ("Company/Notes.md", "Company/Intel/Acme.md") in s.link_pairs()
    s.close()


def test_alias_added_later_resolves_previously_unresolved_link(master, tmp_path):
    (master / "Company/Notes.md").write_text("See [[ACME]] for the account.\n")
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)
    s = IndexStore.open_readonly(vault / ".brain/index.db", want_vectors=False)
    assert s.links_from("Company/Notes.md") == [("ACME", 0)]  # unresolved
    s.close()

    # the entity page (with the alias) arrives in a later compile+index cycle
    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme.md").write_text(
        "---\nentity: client\naliases: [ACME]\n---\n# Acme\n")
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=FakeEmbeddingProvider(), cache=None)
    s = IndexStore.open_readonly(vault / ".brain/index.db", want_vectors=False)
    # Notes.md was unchanged, but the post-pass re-resolved its link via the alias
    assert s.links_from("Company/Notes.md") == [("Company/Intel/Acme.md", 1)]
    s.close()


def test_alias_collision_resolves_deterministically(master, tmp_path):
    # Two entity pages in different spaces both claim the alias "Acme". Their
    # filenames deliberately differ from the alias itself so by_stem can't
    # resolve [[Acme]] first — this exercises the by_alias fallback path.
    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme Corp.md").write_text(
        "---\nentity: client\naliases: [Acme]\n---\n# Acme Corp\n")
    (master / "Teams/sales").mkdir(parents=True, exist_ok=True)
    (master / "Teams/sales/Acme Ltd.md").write_text(
        "---\nentity: client\naliases: [Acme]\n---\n# Acme Ltd\n")
    (master / "Company/Notes.md").write_text("See [[Acme]] for details.\n")

    vault = tmp_path / "alice"

    def _resolved():
        compile_vault(master, ALICE, RULES, vault)
        build_index(vault, provider=FakeEmbeddingProvider(), cache=None)
        s = IndexStore.open_readonly(vault / ".brain/index.db", want_vectors=False)
        links = s.links_from("Company/Notes.md")
        s.close()
        return links

    first = _resolved()
    # lexicographically first path wins: "Company/..." < "Teams/..."
    assert first == [("Company/Intel/Acme Corp.md", 1)]

    shutil.rmtree(vault / ".brain")
    second = _resolved()
    assert second == first


def test_build_index_populates_typed_edges(master, tmp_path):
    (master / "Company/Projects").mkdir(parents=True, exist_ok=True)
    (master / "Company/Projects/Projects.md").write_text("# Projects\n")
    (master / "Company/Projects/Alpha.md").write_text(
        "---\nup: [[Beta]]\n---\n# Alpha\n")
    (master / "Company/Projects/Beta.md").write_text(
        "---\nentity: project\n---\n# Beta\n")
    (master / "Company/Projects/Gamma.md").write_text(
        "---\nentity: project\n---\n# Gamma\n")

    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)

    s = IndexStore.open_readonly(vault / ".brain" / "index.db", want_vectors=False)
    rows = set(s.conn.execute(
        "SELECT src_rel_path, dst_rel_path, rel, provenance FROM edges").fetchall())
    assert ("Company/Projects/Alpha.md", "Company/Projects/Beta.md",
            "up", "explicit") in rows
    assert ("Company/Projects/Beta.md", "Company/Projects/Alpha.md",
            "down", "inverse") in rows
    assert ("Company/Projects/Alpha.md", "Company/Projects/Projects.md",
            "up", "folder") in rows
    assert ("Company/Projects/Beta.md", "Company/Projects/Gamma.md",
            "same", "entity") in rows
    s.close()
