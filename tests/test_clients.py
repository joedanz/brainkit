import pytest
from pathlib import Path

from brain.clients import ClientError, normalize_client_name, request_client
from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path


@pytest.mark.parametrize("raw,expected", [
    ("Danziger Family", "Danziger Family"),
    ("  John   Danziger  ", "John Danziger"),
    ("Smith (Acme)", "Smith (Acme)"),
    ("O'Brien & Sons", "O'Brien & Sons"),
])
def test_normalize_keeps_human_readable_names(raw, expected):
    assert normalize_client_name(raw) == expected


@pytest.mark.parametrize("bad", [
    "", "   ", ".", "..", "a/b", "a\\b", ".hidden", 'has"quote', "line\nbreak",
])
def test_normalize_rejects_unsafe(bad):
    with pytest.raises(ClientError):
        normalize_client_name(bad)


def test_request_client_writes_artifact_in_owner_space(tmp_path: Path):
    rel = request_client(tmp_path, "joe", "Danziger Family",
                         "Members: Mikey (football), Roslyn (basketball).\n",
                         "2026-07-22", source="People/joe/Inbox/chat.md")
    assert space_of_path(rel) == "People/joe"
    meta, body = split_frontmatter((tmp_path / rel).read_text())
    assert meta["client-name"] == "Danziger Family"
    assert meta["owner"] == "joe"
    assert meta["entity"] == "client"
    assert "Mikey" in body


def test_request_client_rejects_empty_body(tmp_path: Path):
    with pytest.raises(ClientError):
        request_client(tmp_path, "joe", "Danziger", "   \n", "2026-07-22")


def test_request_client_refuses_symlinked_ancestor(tmp_path: Path):
    (tmp_path / "People").mkdir()
    (tmp_path / "People/joe").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ClientError):
        request_client(tmp_path, "joe", "Danziger", "body\n", "2026-07-22")
