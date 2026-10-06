import json
from datetime import UTC, datetime

import pytest

from brain import spool as sp
from brain.spool import SpoolError, pending_count, write_envelope

NOW = datetime(2026, 10, 6, 14, 30, 5, tzinfo=UTC)


def test_write_envelope_is_one_complete_json_file(tmp_path):
    name = write_envelope(tmp_path, "bob", text="Met Ana re: Lisbon", title="Lisbon", now=NOW)
    assert name.startswith("20261006T143005Z-") and name.endswith(".json")
    env = json.loads((tmp_path / name).read_text())
    assert env == {"version": 1, "person": "bob", "title": "Lisbon",
                   "body": "Met Ana re: Lisbon", "source": "mcp", "created": "2026-10-06"}
    assert list((tmp_path / ".tmp").iterdir()) == []   # temp file renamed away
    assert pending_count(tmp_path) == 1


@pytest.mark.parametrize("text,title,match", [
    ("   ", "", "empty"),
    ("x" * (sp.MAX_TEXT + 1), "", "limit"),
    ("ok", "two\nlines", "single line"),
    ("ok", "t" * (sp.MAX_TITLE + 1), "longer"),
])
def test_bad_input_writes_nothing(tmp_path, text, title, match):
    with pytest.raises(SpoolError, match=match):
        write_envelope(tmp_path, "bob", text=text, title=title, now=NOW)
    assert pending_count(tmp_path) == 0


def test_full_queue_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "MAX_PENDING", 2)
    write_envelope(tmp_path, "bob", text="a", title="", now=NOW)
    write_envelope(tmp_path, "bob", text="b", title="", now=NOW)
    with pytest.raises(SpoolError, match="queue full"):
        write_envelope(tmp_path, "bob", text="c", title="", now=NOW)


def test_rejected_envelopes_do_not_count_as_pending(tmp_path):
    (tmp_path / ".rejected").mkdir()
    (tmp_path / ".rejected" / "old.json").write_text("{}")
    assert pending_count(tmp_path) == 0
import subprocess

from brain.schemas import load_org, load_spaces
from brain.spool import drain_spools
from tests.test_cli import seed_meta


def _drain(master, root):
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    return drain_spools(root, master, org, rules, shared="Company")


def _queue(root, pid, **env):
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    base = {"version": 1, "person": pid, "title": "Ana", "body": "aisle seats",
            "source": "mcp", "created": "2026-10-06"}
    base.update(env)
    p = d / "20261006T143005Z-abcd1234.json"
    p.write_text(json.dumps(base))
    return p


def test_drain_files_a_capture_into_master_inbox_and_commits(master, tmp_path):
    seed_meta(master)
    root = tmp_path / "spool"
    env = _queue(root, "bob")
    report = _drain(master, root)
    assert (report.ingested, report.rejected) == (1, 0)
    assert not env.exists()
    [note] = (master / "People/bob/Inbox").glob("2026-10-06-ana*.md")
    text = note.read_text()
    assert "source: mcp" in text and "from: bob@acme.com" in text and text.endswith("aisle seats")
    log = subprocess.run(["git", "-C", str(master), "log", "-1", "--format=%an"],
                         capture_output=True, text=True).stdout.strip()
    assert log == "Brain Ingest"


@pytest.mark.parametrize("pid,env,why", [
    ("mallory", {}, "not a person"),
    ("bob", {"person": "alice"}, "different person"),
    ("bob", {"version": 9}, "version"),
    ("bob", {"body": 5}, "malformed"),
    ("bob", {"created": "../../x"}, "created"),
])
def test_drain_rejects_and_keeps_bad_envelopes(master, tmp_path, pid, env, why):
    seed_meta(master)
    root = tmp_path / "spool"
    path = _queue(root, pid, **env)
    report = _drain(master, root)
    assert (report.ingested, report.rejected) == (0, 1)
    assert (path.parent / ".rejected" / path.name).exists()
    assert why in report.warnings[0]
    assert not list(master.rglob("x.md"))


def test_drain_ignores_tmp_and_rejected_folders(master, tmp_path):
    seed_meta(master)
    root = tmp_path / "spool"
    (root / "bob" / ".tmp").mkdir(parents=True)
    (root / "bob" / ".tmp" / "half.json").write_text("{")
    assert _drain(master, root).ingested == 0


def test_drain_of_missing_root_warns(master, tmp_path):
    seed_meta(master)
    report = _drain(master, tmp_path / "nope")
    assert report.ingested == 0 and "not a directory" in report.warnings[0]


def test_write_envelope_refuses_text_that_cannot_be_stored_as_utf8(tmp_path):
    """JSON allows a lone surrogate ("\\ud800"); it cannot be written to a note."""
    with pytest.raises(SpoolError, match="UTF-8"):
        write_envelope(tmp_path, "bob", text="hi \ud800", title="", now=NOW)
    with pytest.raises(SpoolError, match="UTF-8"):
        write_envelope(tmp_path, "bob", text="ok", title="t\ud800", now=NOW)
    assert pending_count(tmp_path) == 0


def test_drain_rejects_an_unencodable_envelope_and_leaves_no_stray_note(master, tmp_path):
    seed_meta(master)
    root = tmp_path / "spool"
    path = _queue(root, "bob")
    # hand-written escape: json.loads yields a lone surrogate
    path.write_text('{"version":1,"person":"bob","title":"Ana","body":"hi \\ud800",'
                    '"source":"mcp","created":"2026-10-06"}')
    report = _drain(master, root)
    assert (report.ingested, report.rejected) == (0, 1)
    assert (path.parent / ".rejected" / path.name).exists()
    assert not list((master / "People/bob/Inbox").glob("*"))   # no empty leftover file


def test_drain_survives_an_os_error_from_one_envelope(master, tmp_path, monkeypatch):
    seed_meta(master)
    root = tmp_path / "spool"
    bad = _queue(root, "bob")
    good = root / "bob" / "99999999T000000Z-zzzzzzzz.json"
    good.write_text(bad.read_text())
    import brain.ingest as ingest
    real = ingest.ingest_note
    calls = []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return real(*a, **kw)

    monkeypatch.setattr(ingest, "ingest_note", flaky)
    report = _drain(master, root)
    assert (report.ingested, report.rejected) == (1, 1)
    assert "OSError" in report.warnings[0]


def test_drain_warns_when_a_person_folder_is_unreadable(master, tmp_path):
    import os
    seed_meta(master)
    root = tmp_path / "spool"
    _queue(root, "bob")
    os.chmod(root / "bob", 0)
    try:
        report = _drain(master, root)
    finally:
        os.chmod(root / "bob", 0o700)
    assert report.ingested == 0
    assert "bob" in report.warnings[0] and "cannot read" in report.warnings[0]
