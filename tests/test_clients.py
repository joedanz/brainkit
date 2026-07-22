import pytest

from brain.clients import ClientError, normalize_client_name


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
