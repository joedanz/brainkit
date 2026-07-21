"""Property test: retrieval never crosses the tenant boundary.

Because each index is built from one compiled vault, cross-space leakage is
structurally impossible — but we prove it the same way the compiler does, with
honeypots planted in master and randomized worlds. Three layers: the strongest
is a scan of what is actually stored (no query can return what isn't there);
then live query probes; then the MCP read tool's path scoping.
"""

import json
import random
import subprocess
from pathlib import Path

import pytest

from brain.compiler import MANIFEST_NAME, compile_all
from brain.embeddings import EmbeddingCache, FakeEmbeddingProvider
from brain.indexer import build_index
from brain.mcp import _tool_read
from brain.resolver import readable_spaces
from brain.search import search_index
from brain.store import IndexStore

from tests.test_leak_property import RULES, random_world


def _build_world(tmp_path: Path, seed: int):
    rng = random.Random(seed)
    master = tmp_path / f"master{seed}"
    master.mkdir()
    org = random_world(rng, master)
    out_root = tmp_path / f"out{seed}"
    compile_all(master, org, RULES, out_root)
    cache = EmbeddingCache(tmp_path / f"cache{seed}.db")
    for person in org.people.values():
        build_index(out_root / person.id, provider=FakeEmbeddingProvider(), cache=cache)
    return master, org, out_root


def test_stored_content_never_leaves_readable_spaces(tmp_path):
    for seed in range(5):
        master, org, out_root = _build_world(tmp_path, seed)
        for person in org.people.values():
            allowed = set(readable_spaces(master, person, RULES))
            store = IndexStore.open(out_root / person.id / ".brain/index.db")
            for (space,) in store.conn.execute("SELECT DISTINCT space FROM files"):
                assert space in allowed, f"LEAK(files) {person.id}: {space}"
            for (space,) in store.conn.execute("SELECT DISTINCT space FROM chunks"):
                assert space in allowed, f"LEAK(chunks) {person.id}: {space}"
            store.close()


def test_query_probes_stay_in_readable_spaces(tmp_path):
    for seed in range(5):
        master, org, out_root = _build_world(tmp_path, seed)
        for person in org.people.values():
            allowed = set(readable_spaces(master, person, RULES))
            # the honeypot phrase + a generic term that matches many notes
            for query in ("leaked server note", "content note", "secret"):
                report = search_index(out_root / person.id, query, k=50,
                                      provider=FakeEmbeddingProvider())
                for h in report.hits:
                    assert h.space in allowed, (
                        f"LEAK(query {query!r}) {person.id}: {h.rel_path} ({h.space})")


def test_cross_person_private_content_is_unreachable(tmp_path):
    master, org, out_root = _build_world(tmp_path, 3)
    people = list(org.people.values())
    if len(people) >= 2:
        a, b = people[0], people[1]
        # text that exists only in b's private People space
        report = search_index(out_root / a.id, f"content of People/{b.id}/note0",
                              k=50, provider=FakeEmbeddingProvider())
        assert not any(h.rel_path.startswith(f"People/{b.id}/") for h in report.hits)


def test_index_never_enters_vault_git(tmp_path):
    master, org, out_root = _build_world(tmp_path, 1)
    # recompile after indexing — the index must survive and stay untracked
    compile_all(master, org, RULES, out_root)
    for person in org.people.values():
        vault = out_root / person.id
        assert (vault / ".brain/index.db").is_file()
        tracked = subprocess.run(
            ["git", "-C", str(vault), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        assert not any(p.startswith(".brain/") for p in tracked)


def test_facts_never_leave_readable_spaces(tmp_path):
    from brain.facts import query_facts
    from brain.resolver import space_of_path

    saw_nonempty_facts = False
    for seed in range(5):
        master, org, out_root = _build_world(tmp_path, seed)
        for person in org.people.values():
            allowed = set(readable_spaces(master, person, RULES))
            store = IndexStore.open(out_root / person.id / ".brain/index.db")
            rels = store.conn.execute("SELECT DISTINCT rel_path FROM facts").fetchall()
            if rels:
                saw_nonempty_facts = True
            for (rel,) in rels:
                space = space_of_path(rel)
                assert space in allowed, f"LEAK(facts) {person.id}: {rel}"
            store.close()
            hits, _ = query_facts(out_root / person.id, include_ended=True)
            for h in hits:
                assert space_of_path(h.rel_path) in allowed, (
                    f"LEAK(query_facts) {person.id}: {h.rel_path}")
    assert saw_nonempty_facts, (
        "no facts were indexed for any person across any seed — the test "
        "would be vacuous (random_world's honeypot fact line never made it "
        "into the facts table)"
    )


def test_mcp_read_refuses_symlink_into_master(tmp_path):
    master, org, out_root = _build_world(tmp_path, 2)
    person = next(iter(org.people.values()))
    vault = out_root / person.id
    own_space = f"People/{person.id}"
    link = vault / own_space / "leak.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(master / "_meta/secret.yaml")
    text, is_err = _tool_read(vault, {"rel_path": f"{own_space}/leak.md"})
    assert is_err
    assert "secret" not in text
