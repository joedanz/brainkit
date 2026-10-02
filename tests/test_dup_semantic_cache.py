"""Doctor's semantic near-duplicate tier, remembered between runs.

The pair results live in the dedup cache (<master>/_meta/cache/dedup.db) so a
warm run compares only new and changed notes. The cache may never change a
finding: every test here holds an incremental run to what a full all-pairs
pass over the same notes reports.
"""

import random
import shutil

from brain.doctor import run_doctor

from .test_cli import seed_meta
from .test_doctor import _ignore_cache, _warm_embeddings, _writable_run

DUP_CHECKS = ("dup-exact", "dup-near", "stem-collision")


def _dups(findings):
    return [(f.severity, f.check, f.paths, f.message)
            for f in findings if f.check in DUP_CHECKS]


def _bag(word, n=40):
    return [f"{word}{i}" for i in range(n)]


def _note(master, rel, words, seed, *, front=""):
    """A note whose body is `words` shuffled: MinHash sees little overlap
    between two shuffles, the bag-of-words fake embedding sees a lot."""
    ws = list(words)
    random.Random(seed).shuffle(ws)
    title = rel.rsplit("/", 1)[-1][:-3]
    path = master / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{front}# {title}\n\n" + " ".join(ws) + "\n")
    return rel


def _variant(words, k, tag):
    """`words` with its first k replaced: cosine drifts down as k grows."""
    return [f"{tag}{i}" for i in range(k)] + list(words[k:])


def _semantic_brain(master):
    """Every shape the semantic tier meets: a group inside one space, pairs
    across spaces with and without a common reader, a chain, notes either
    side of the cosine threshold, a declared parent/child, a lexical
    duplicate the semantic tier must not repeat, two notes with the same
    chunks (one vector key), and far notes. Returns every rel written."""
    rels = []
    alpha = _bag("alpha")
    for i, k in enumerate((0, 2, 4, 6, 9, 14, 20, 26, 32)):
        rels.append(_note(master, f"Company/Alpha {i}.md",
                          _variant(alpha, k, f"a{i}x"), seed=i))
    beta = _bag("beta")
    rels.append(_note(master, "People/alice/Notes/Beta.md", beta, seed=10))
    rels.append(_note(master, "People/bob/Notes/Beta Copy.md", beta, seed=11))
    gamma = _bag("gamma")
    rels.append(_note(master, "Teams/sales/Gamma.md", gamma, seed=20))
    rels.append(_note(master, "Company/Gamma Shared.md", _variant(gamma, 3, "g"), seed=21))
    rels.append(_note(master, "Teams/ops/Gamma Ops.md", _variant(gamma, 5, "h"), seed=22))
    delta = _bag("delta", 60)
    rels.append(_note(master, "Company/Chain/One.md", delta[:40], seed=30))
    rels.append(_note(master, "Company/Chain/Two.md", delta[8:48], seed=31))
    rels.append(_note(master, "Company/Chain/Three.md", delta[16:56], seed=32))
    eps = _bag("eps")
    rels.append(_note(master, "Company/Parent.md", eps, seed=40))
    rels.append(_note(master, "Company/Child.md", eps, seed=41,
                      front="---\nup: \"[[Parent]]\"\n---\n"))
    rels.append(_note(master, "Company/Sibling.md", _variant(eps, 1, "s"), seed=42))
    zeta = _bag("zeta")
    for who in ("alice", "bob"):  # same body, same stem: one vector key
        rels.append(_note(master, f"People/{who}/Twin.md", zeta, seed=50,
                          front=f"---\nowner: {who}\n---\n"))
    for i in range(6):
        rels.append(_note(master, f"Clients/acme/Far {i}.md", _bag(f"far{i}x"), seed=60 + i))
    return rels


def _fresh(master, tmp_path):
    """What a full all-pairs pass reports now: doctor with no cache file."""
    db = master / "_meta/cache/dedup.db"
    aside = tmp_path / "dedup-aside.db"
    moved = db.exists()
    if moved:
        shutil.move(db, aside)
    try:
        return run_doctor(master)
    finally:
        if moved:
            shutil.move(aside, db)


def _setup(master, tmp_path, monkeypatch):
    seed_meta(master)
    _ignore_cache(master)
    rels = _semantic_brain(master)
    _warm_embeddings(master, tmp_path, monkeypatch, rels)
    return rels


# What the all-pairs pass reported for _semantic_brain before any of this
# was remembered (taken from 447e865). The cache must reproduce it exactly.
BASELINE = [
    ('warn', 'dup-near',
     ('Company/Gamma Shared.md', 'Teams/ops/Gamma Ops.md'),
     ('Company/Gamma Shared.md and Teams/ops/Gamma Ops.md are near-duplicates'
      ' (semantic similarity) — fold one into the other via a mode: patch '
      'promotion')),
    ('warn', 'dup-near',
     ('Company/Gamma Shared.md', 'Teams/sales/Gamma.md'),
     ('Company/Gamma Shared.md and Teams/sales/Gamma.md are near-duplicates '
      '(semantic similarity) — fold one into the other via a mode: patch '
      'promotion')),
    ('info', 'dup-near',
     ('People/alice/Notes/Beta.md', 'People/bob/Notes/Beta Copy.md'),
     ('People/alice/Notes/Beta.md and People/bob/Notes/Beta Copy.md cover '
      'similar content in unshared spaces — promotion candidate')),
    ('info', 'dup-near',
     ('Teams/ops/Gamma Ops.md', 'Teams/sales/Gamma.md'),
     ('Teams/ops/Gamma Ops.md and Teams/sales/Gamma.md cover similar content '
      'in unshared spaces — promotion candidate')),
    ('warn', 'dup-near',
     ('Company/Alpha 0.md',
      'Company/Alpha 1.md',
      'Company/Alpha 2.md',
      'Company/Alpha 3.md',
      'Company/Alpha 4.md',
      'Company/Alpha 5.md'),
     ('6 notes are near-duplicates of each other in Company: Alpha 0.md, '
      'Alpha 1.md, Alpha 2.md, and 3 more — merge them, or if they share a '
      'template on purpose, make them distinct')),
    ('warn', 'dup-near',
     ('Company/Chain/Three.md', 'Company/Chain/Two.md'),
     ('Company/Chain/Three.md and Company/Chain/Two.md are near-duplicates '
      '(semantic similarity) — fold one into the other via a mode: patch '
      'promotion')),
    ('warn', 'dup-near',
     ('Company/Child.md', 'Company/Parent.md', 'Company/Sibling.md'),
     ('3 notes are near-duplicates of each other in Company: Child.md, '
      'Parent.md, and Sibling.md — merge them, or if they share a template on'
      ' purpose, make them distinct')),
]


def _count_comparisons(monkeypatch):
    """Every hamming prefilter and cosine the semantic tier computes, and
    every vector it pools or turns into sign bits."""
    import brain.dedup

    calls = {"hamming": 0, "cosine_with_norms": 0, "sign_bits": 0, "mean_pool": 0}
    for name in calls:
        real = getattr(brain.dedup, name)

        def spy(*a, _real=real, _name=name):
            calls[_name] += 1
            return _real(*a)

        monkeypatch.setattr(brain.dedup, name, spy)
    return calls


def test_a_cold_run_reports_what_the_all_pairs_pass_did(master, tmp_path, monkeypatch):
    _setup(master, tmp_path, monkeypatch)
    assert _dups(run_doctor(master)) == BASELINE
    assert _dups(_writable_run(master)) == BASELINE
    assert _dups(run_doctor(master)) == BASELINE  # read-only, from the cache
    assert _dups(_writable_run(master)) == BASELINE  # warm


def _rewarm(master, tmp_path, monkeypatch, *rels):
    _warm_embeddings(master, tmp_path, monkeypatch, list(rels))


def test_every_incremental_run_matches_a_full_pass(master, tmp_path, monkeypatch):
    _setup(master, tmp_path, monkeypatch)
    alpha = _bag("alpha")

    def add_near():
        _note(master, "Company/Alpha New.md", _variant(alpha, 3, "n"), seed=90)
        _rewarm(master, tmp_path, monkeypatch, "Company/Alpha New.md")

    def edit_away():  # a member leaves its group, with fresh vectors
        _note(master, "Company/Gamma Shared.md", _bag("other"), seed=91)
        _rewarm(master, tmp_path, monkeypatch, "Company/Gamma Shared.md")

    def edit_closer():  # a far note joins a group
        _note(master, "Clients/acme/Far 0.md", _variant(alpha, 1, "c"), seed=92)
        _rewarm(master, tmp_path, monkeypatch, "Clients/acme/Far 0.md")

    def edit_unembedded():  # changed text whose chunks have no vector yet
        _note(master, "Company/Alpha 2.md", _variant(alpha, 2, "u"), seed=93)

    def delete():
        (master / "Teams/sales/Gamma.md").unlink()

    def restore():
        _note(master, "Teams/sales/Gamma.md", _bag("gamma"), seed=20)

    def rename_same_stem():  # moved, same title: the same chunks, one key
        (master / "Company/Old").mkdir()
        (master / "Company/Alpha 3.md").rename(master / "Company/Old/Alpha 3.md")

    def rename_new_stem():  # the title is embedded, so a new key
        (master / "Company/Alpha 4.md").rename(master / "Company/Alpha Four.md")
        _rewarm(master, tmp_path, monkeypatch, "Company/Alpha Four.md")

    def copy_twin():  # identical text to a note already present
        text = (master / "Company/Alpha 0.md").read_text()
        (master / "Teams/ops/Alpha 0.md").write_text(text)

    def revectored():
        # Same text, different vectors (as after a model change): the note's
        # vector key moves with its vectors, so nothing stale survives.
        _warm_embeddings(master, tmp_path, monkeypatch, ["People/bob/Notes/Beta Copy.md"])
        from brain.embeddings import EmbeddingCache

        cache = EmbeddingCache(tmp_path / "emb-cache.db")
        try:
            rows = cache._conn.execute(
                "SELECT chunk_sha, vector FROM embeddings").fetchall()
            beta = _chunk_shas(master, "People/bob/Notes/Beta Copy.md")
            far = _chunk_shas(master, "Clients/acme/Far 3.md")
            blobs = dict(rows)
            cache.put_many([(s, blobs[far[0]]) for s in beta], "fake-32")
        finally:
            cache.close()

    steps = [add_near, edit_away, edit_closer, edit_unembedded, delete, restore,
             rename_same_stem, rename_new_stem, copy_twin, revectored]
    assert _dups(_writable_run(master)) == BASELINE
    seen = {repr(BASELINE)}
    for step in steps:
        step()
        expected = _fresh(master, tmp_path)
        assert _writable_run(master) == expected, step.__name__
        assert run_doctor(master) == expected, step.__name__  # read-only
        assert _writable_run(master) == expected, step.__name__  # warm again
        seen.add(repr(_dups(expected)))
    assert len(seen) > 6  # the steps really moved the findings


def _chunk_shas(master, rel):
    import hashlib

    from brain.chunker import chunk_markdown, embedding_input

    text = (master / rel).read_text()
    return [hashlib.sha256(embedding_input(c).encode("utf-8")).hexdigest()
            for c in chunk_markdown(rel, text)]


def test_a_warm_run_compares_nothing_that_is_unchanged(master, tmp_path, monkeypatch):
    _setup(master, tmp_path, monkeypatch)
    first = _writable_run(master)
    calls = _count_comparisons(monkeypatch)
    assert _writable_run(master) == first
    assert run_doctor(master) == first
    assert calls == {"hamming": 0, "cosine_with_norms": 0, "sign_bits": 0, "mean_pool": 0}


def test_one_new_note_is_compared_once_against_each_note(master, tmp_path, monkeypatch):
    rels = _setup(master, tmp_path, monkeypatch)
    _writable_run(master)
    _note(master, "Company/Alpha New.md", _variant(_bag("alpha"), 3, "n"), seed=90)
    _rewarm(master, tmp_path, monkeypatch, "Company/Alpha New.md")
    calls = _count_comparisons(monkeypatch)
    findings = _writable_run(master)
    assert calls["sign_bits"] == 1  # only the new note's bits are computed
    # The others are pooled only where a cosine needs them.
    assert calls["mean_pool"] <= 1 + calls["cosine_with_norms"]
    assert 0 < calls["hamming"] <= len(rels) + 1
    assert calls["cosine_with_norms"] <= calls["hamming"]
    assert findings == _fresh(master, tmp_path)


def test_a_version_change_recomputes_every_pair(master, tmp_path, monkeypatch):
    import brain.dedup

    rels = _setup(master, tmp_path, monkeypatch)
    _writable_run(master)
    monkeypatch.setattr(brain.dedup, "NEAR_SCHEME", brain.dedup.NEAR_SCHEME + 1)
    calls = _count_comparisons(monkeypatch)
    assert _dups(_writable_run(master)) == BASELINE
    keys = len(rels) - 1  # the two Twin notes share their chunks
    assert calls["hamming"] == keys * (keys + 1) // 2
    assert calls["sign_bits"] == keys


def test_a_threshold_change_recomputes_rather_than_reuses(master, tmp_path, monkeypatch):
    import brain.dedup

    _setup(master, tmp_path, monkeypatch)
    _writable_run(master)
    monkeypatch.setattr(brain.dedup, "DUP_COSINE", 0.97)
    stricter = _fresh(master, tmp_path)
    assert _dups(stricter) != BASELINE
    assert _writable_run(master) == stricter
    monkeypatch.setattr(brain.dedup, "DUP_HAMMING_FRAC", 0.05)
    narrower = _fresh(master, tmp_path)
    assert _writable_run(master) == narrower


def test_standalone_doctor_never_writes_the_pair_cache(master, tmp_path, monkeypatch):
    _setup(master, tmp_path, monkeypatch)
    _writable_run(master)
    _note(master, "Company/Alpha New.md", _variant(_bag("alpha"), 3, "n"), seed=90)
    _rewarm(master, tmp_path, monkeypatch, "Company/Alpha New.md")
    db = master / "_meta/cache/dedup.db"
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    assert run_doctor(master) == _fresh(master, tmp_path)
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
