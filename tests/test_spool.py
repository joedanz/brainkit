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
