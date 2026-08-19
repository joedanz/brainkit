import json
from pathlib import Path

import brain.retrieval as retrieval_module
from brain.retrieval import LOCK_NAME, SCHEMA, STATS_NAME, record

NOW = "2026-08-19T14:02:11Z"


def _vault(tmp_path: Path, person: str = "joe") -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / ".brain-manifest.json").write_text(json.dumps({"person": person}))
    return vault


def _stats(vault: Path) -> dict:
    return json.loads((vault / ".brain" / STATS_NAME).read_text())


def test_first_search_creates_the_file_and_counts_one(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=3, warnings=[], now=NOW)
    d = _stats(vault)
    assert d["schema"] == SCHEMA
    assert d["person"] == "joe"
    assert d["searches"] == 1
    assert d["zero_hit"] == 0
    assert d["by_mode"] == {"hybrid": 1}
    assert d["warn"] == {}
    assert d["updated_at"] == NOW
    assert isinstance(d["brainkit_version"], str) and d["brainkit_version"]


def test_counters_accumulate_across_searches(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=3, warnings=[], now=NOW)
    record(vault, mode="hybrid", hits=0, warnings=[], now=NOW)
    record(vault, mode="keyword-only", hits=1, warnings=[], now=NOW)
    d = _stats(vault)
    assert d["searches"] == 3
    assert d["zero_hit"] == 1
    assert d["by_mode"] == {"hybrid": 2, "keyword-only": 1}


def test_empty_mode_is_recorded_as_no_index(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="", hits=0, warnings=["no index at /srv/x/.brain/index.db — run: brain index"], now=NOW)
    d = _stats(vault)
    assert d["by_mode"] == {"no-index": 1}


def test_the_no_index_warning_is_not_double_counted_in_warn(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="", hits=0, warnings=["no index at /srv/x/.brain/index.db"], now=NOW)
    assert _stats(vault)["warn"] == {}


def test_a_warning_on_a_real_mode_counts_as_vector_degraded(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="keyword-only", hits=2,
           warnings=["sqlite-vec unavailable — keyword-only search"], now=NOW)
    assert _stats(vault)["warn"] == {"vector-degraded": 1}


def test_no_path_from_a_warning_ever_reaches_the_file(tmp_path):
    """Structural: a future change that starts storing warning text fails here."""
    vault = _vault(tmp_path)
    secret = "/srv/brain/compiled/RESTRICTEDCLIENT/.brain/index.db"
    record(vault, mode="", hits=0, warnings=[f"no index at {secret} — run: brain index"], now=NOW)
    assert "RESTRICTEDCLIENT" not in (vault / ".brain" / STATS_NAME).read_text()


def test_the_directory_is_created_when_it_does_not_exist(tmp_path):
    """The no-index search is the one most worth counting, and on that path
    .brain/ may never have been made."""
    vault = _vault(tmp_path)
    assert not (vault / ".brain").exists()
    record(vault, mode="", hits=0, warnings=[], now=NOW)
    assert (vault / ".brain" / STATS_NAME).is_file()


def test_the_file_is_compact_single_line_json(tmp_path):
    """Fleet's sweep parser is line-oriented; an indented object reaches it
    as a lone '{'."""
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW)
    text = (vault / ".brain" / STATS_NAME).read_text()
    assert len(text.strip().splitlines()) == 1


def test_person_is_null_when_the_manifest_is_unreadable(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW)
    assert _stats(vault)["person"] is None


def test_raw_fields_are_present_and_off_by_default(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW)
    d = _stats(vault)
    assert d["raw_log"] is False
    assert d["raw_log_since"] is None
    assert d["raw_truncated"] is False


def test_a_corrupt_stats_file_does_not_raise(tmp_path):
    vault = _vault(tmp_path)
    (vault / ".brain").mkdir()
    (vault / ".brain" / STATS_NAME).write_text("{ not json")
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW)
    assert _stats(vault)["searches"] == 1


def _hammer(vault_str: str) -> None:
    """MODULE level, deliberately: macOS starts multiprocessing with `spawn`,
    which pickles the target. A function defined inside the test body cannot be
    pickled, and the test would ERROR rather than exercise the lock."""
    for _ in range(25):
        record(Path(vault_str), mode="hybrid", hits=1, warnings=[], now=NOW)


def test_concurrent_writers_do_not_lose_increments(tmp_path):
    """Counters are read-modify-write, so this is the reason the lock exists.

    Separate PROCESSES, not threads: two `brain search` invocations are the real
    scenario, and fcntl locks bind to the open file description."""
    import multiprocessing as mp
    vault = _vault(tmp_path)

    procs = [mp.Process(target=_hammer, args=(str(vault),)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert _stats(vault)["searches"] == 100


def test_never_leaves_a_temp_file_behind(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW)
    assert list((vault / ".brain").glob("*.tmp")) == []


import os
import time

from brain.retrieval import RAW_NAME, SENTINEL_NAME


def _switch_on(vault: Path) -> Path:
    brain_dir = vault / ".brain"
    brain_dir.mkdir(parents=True, exist_ok=True)
    sentinel = brain_dir / SENTINEL_NAME
    sentinel.touch()
    return sentinel


def _raw_lines(vault: Path) -> list[dict]:
    raw = vault / ".brain" / RAW_NAME
    if not raw.is_file():
        return []
    # startswith("{"), not just a truthy strip(): the rotation-window test
    # appends raw padding bytes onto the live log to force a rotation on the
    # NEXT call, and the last iteration leaves that padding trailing with no
    # further call to consume it. Real records are the only "{"-prefixed
    # lines, matching the filter already used when reading segments back.
    return [json.loads(line) for line in raw.read_text().splitlines() if line.startswith("{")]


def test_no_raw_log_is_written_without_the_sentinel(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW,
           query="what is our refund policy", hit_locations=[("Company/Policy.md", "Company")])
    assert _raw_lines(vault) == []
    assert _stats(vault)["raw_log"] is False


def test_the_sentinel_switches_the_raw_log_on(tmp_path):
    vault = _vault(tmp_path)
    _switch_on(vault)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW,
           query="what is our refund policy", hit_locations=[("Company/Policy.md", "Company")])
    lines = _raw_lines(vault)
    assert len(lines) == 1
    assert lines[0]["query"] == "what is our refund policy"
    assert lines[0]["mode"] == "hybrid"
    assert lines[0]["hits"] == [{"rel_path": "Company/Policy.md", "space": "Company"}]
    assert lines[0]["at"] == NOW
    assert _stats(vault)["raw_log"] is True


def test_snippets_are_never_written_to_the_raw_log(tmp_path):
    """Structural: hit_locations carries only (rel_path, space) by design."""
    vault = _vault(tmp_path)
    _switch_on(vault)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW,
           query="q", hit_locations=[("Company/Policy.md", "Company")])
    assert "snippet" not in (vault / ".brain" / RAW_NAME).read_text()


def test_raw_log_since_reports_the_sentinel_mtime(tmp_path):
    vault = _vault(tmp_path)
    sentinel = _switch_on(vault)
    backdated = time.time() - (10 * 86400)
    os.utime(sentinel, (backdated, backdated))
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")
    since = _stats(vault)["raw_log_since"]
    assert since is not None
    assert since.endswith("Z")
    # 10 days ago, not the injected NOW
    assert since < NOW


def test_raw_log_since_is_null_when_switched_off(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")
    assert _stats(vault)["raw_log_since"] is None


def test_raw_truncated_is_false_while_raw_logging_is_off(tmp_path):
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")
    assert _stats(vault)["raw_truncated"] is False


def test_person_is_carried_forward_without_re_reading_the_manifest(tmp_path):
    """The manifest carries a sha256 per compiled file, so it grows with the
    vault. Parsing it on every search — inside the lock — to re-derive a string
    that cannot change is waste. Deleting it after the first search proves the
    stored value is reused rather than re-read."""
    vault = _vault(tmp_path)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW)
    assert _stats(vault)["person"] == "joe"

    (vault / ".brain-manifest.json").unlink()
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW)
    assert _stats(vault)["person"] == "joe"


import gzip

from brain.retrieval import (
    RAW_SEGMENTS,
    ROTATE_AT_BYTES,
    ROTATING_NAME,
    segment_path,
)


def _fill_raw(vault: Path, nbytes: int) -> None:
    """Put a live raw log of a known size in place."""
    brain = vault / ".brain"
    brain.mkdir(parents=True, exist_ok=True)
    (brain / RAW_NAME).write_text("x" * nbytes)


def test_rotation_moves_the_live_log_into_segment_one(tmp_path):
    vault = _vault(tmp_path)
    _switch_on(vault)
    _fill_raw(vault, ROTATE_AT_BYTES)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")

    assert segment_path(vault / ".brain", 1).is_file()
    # the live log restarted, holding only the new line
    assert len(_raw_lines(vault)) == 1


def test_the_rotated_segment_is_gzipped_and_readable(tmp_path):
    vault = _vault(tmp_path)
    _switch_on(vault)
    (vault / ".brain").mkdir(parents=True, exist_ok=True)
    (vault / ".brain" / RAW_NAME).write_text("first-line-marker\n" + "x" * ROTATE_AT_BYTES)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")

    with gzip.open(segment_path(vault / ".brain", 1), "rt") as fh:
        assert fh.readline().strip() == "first-line-marker"


def test_segments_shift_and_the_oldest_is_discarded(tmp_path):
    """The set is BOUNDED — that is the whole point of rotating rather than
    archiving."""
    vault = _vault(tmp_path)
    _switch_on(vault)
    for i in range(RAW_SEGMENTS + 3):
        _fill_raw(vault, ROTATE_AT_BYTES)
        record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query=f"q{i}")

    brain = vault / ".brain"
    assert segment_path(brain, RAW_SEGMENTS).is_file()
    assert not segment_path(brain, RAW_SEGMENTS + 1).exists()
    assert len(list(brain.glob(f"{RAW_NAME}.*.gz"))) == RAW_SEGMENTS


def test_the_window_reads_back_complete_and_in_order(tmp_path):
    """The point of rotating is that the whole window is still recoverable.
    Concatenating the segments oldest-first with the live file must reproduce
    every line, in order, with nothing lost at a rotation boundary."""
    vault = _vault(tmp_path)
    _switch_on(vault)
    brain = vault / ".brain"

    for i in range(4):
        # Force a rotation between each marker line.
        record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query=f"marker-{i}")
        (brain / RAW_NAME).write_text(
            (brain / RAW_NAME).read_text() + "x" * ROTATE_AT_BYTES
        )

    seen = []
    for n in range(RAW_SEGMENTS, 0, -1):
        seg = segment_path(brain, n)
        if seg.is_file():
            with gzip.open(seg, "rt") as fh:
                seen += [json.loads(x)["query"] for x in fh if x.startswith("{")]
    seen += [x["query"] for x in _raw_lines(vault)]

    assert seen == [f"marker-{i}" for i in range(4)]


def test_raw_truncated_is_false_until_the_set_is_full(tmp_path):
    vault = _vault(tmp_path)
    _switch_on(vault)
    _fill_raw(vault, ROTATE_AT_BYTES)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")
    assert _stats(vault)["raw_truncated"] is False


def test_raw_truncated_is_true_once_the_set_is_full(tmp_path):
    vault = _vault(tmp_path)
    _switch_on(vault)
    for i in range(RAW_SEGMENTS + 1):
        _fill_raw(vault, ROTATE_AT_BYTES)
        record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query=f"q{i}")
    assert _stats(vault)["raw_truncated"] is True


def test_an_orphan_rotating_file_is_recovered_not_overwritten(tmp_path):
    """A crash between the rename and the compression leaves an orphan. The
    next rotation must compress it rather than clobber it — it holds real
    searches."""
    vault = _vault(tmp_path)
    _switch_on(vault)
    brain = vault / ".brain"
    brain.mkdir(parents=True, exist_ok=True)
    (brain / ROTATING_NAME).write_text("orphan-marker\n")

    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")

    with gzip.open(segment_path(brain, 1), "rt") as fh:
        assert fh.readline().strip() == "orphan-marker"
    assert not (brain / ROTATING_NAME).exists()


def test_the_lock_is_released_before_compression(tmp_path):
    """Structural: compression must not happen inside the lock, because this
    runs on every search in the product. Asserted by checking that the lock
    file is not held when the compressor runs."""
    import fcntl

    vault = _vault(tmp_path)
    _switch_on(vault)
    _fill_raw(vault, ROTATE_AT_BYTES)

    held_during_compress = []
    real = retrieval_module._compress_rotated

    def spy(brain_dir, rotated):
        with open(brain_dir / LOCK_NAME, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held_during_compress.append(False)
                fcntl.flock(fh, fcntl.LOCK_UN)
            except BlockingIOError:
                held_during_compress.append(True)
        return real(brain_dir, rotated)

    retrieval_module._compress_rotated = spy
    try:
        record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")
    finally:
        retrieval_module._compress_rotated = real

    assert held_during_compress == [False], "compression ran while the lock was held"


def test_raw_truncated_clears_when_the_segments_are_deleted(tmp_path):
    vault = _vault(tmp_path)
    _switch_on(vault)
    for i in range(RAW_SEGMENTS + 1):
        _fill_raw(vault, ROTATE_AT_BYTES)
        record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query=f"q{i}")
    assert _stats(vault)["raw_truncated"] is True

    for p in (vault / ".brain").glob(f"{RAW_NAME}.*.gz"):
        p.unlink()
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")
    assert _stats(vault)["raw_truncated"] is False


def test_counters_still_advance_across_a_rotation(tmp_path):
    vault = _vault(tmp_path)
    _switch_on(vault)
    _fill_raw(vault, ROTATE_AT_BYTES)
    record(vault, mode="hybrid", hits=1, warnings=[], now=NOW, query="q")
    assert _stats(vault)["searches"] == 1
