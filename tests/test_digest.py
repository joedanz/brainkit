import pytest

from brain.compiler import compile_vault, is_weekly_digest
from brain.doctor import run_doctor
from brain.indexer import build_index
from brain.search import search_index
from tests.conftest import ALICE, RULES
from tests.test_cli import seed_meta


@pytest.mark.parametrize("rel, expected", [
    ("People/alice/Inbox/weekly-digest.md", True),
    ("People/bob/Inbox/weekly-digest.md", True),
    ("People/alice/Inbox/doctor-digest.md", False),
    ("People/alice/Notes/weekly-digest.md", False),
    ("Company/Inbox/weekly-digest.md", False),
    ("People/alice/Inbox/sub/weekly-digest.md", False),
])
def test_only_a_persons_inbox_digest_is_a_weekly_digest(rel, expected):
    assert is_weekly_digest(rel) is expected


DIGEST = ("---\ntitle: Weekly digest\nsource: digest\n---\n"
          "- `Company/Home.md`: zebrafruit statement (from 2026-01)\n"
          "- broken line with only an end date [until:: 2026-01]\n"
          "unlinked [[Nowhere Note]] mention\n")


def test_the_doctor_does_not_read_the_weekly_digest(master):
    seed_meta(master)
    p = master / "People/bob/Inbox/weekly-digest.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(DIGEST)
    findings = run_doctor(master)
    assert not [f for f in findings
                if any("weekly-digest.md" in x for x in f.paths)
                or "weekly-digest.md" in f.message]


def test_the_weekly_digest_is_not_searchable(master, tmp_path):
    p = master / "People/alice/Inbox/weekly-digest.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(DIGEST)
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    assert (vault / "People/alice/Inbox/weekly-digest.md").exists()
    build_index(vault, provider=None, cache=None)
    assert search_index(vault, "zebrafruit", keyword_only=True).hits == []
    # control: an ordinary note is still found
    assert search_index(vault, "pipeline", keyword_only=True).hits
