import pytest

from brain.notes import NoteAccessError, read_note


def test_reads_a_file_inside_a_space(master):
    assert "Home" in read_note(master, "Company/Home.md")


@pytest.mark.parametrize("bad_path", [
    "AGENTS.md",                # vault root, inside no space
    "_meta/org.yaml",           # _meta is reserved
    "Company/DoesNotExist.md",  # missing file
    "Company/Decisions",        # a directory, not a note
])
def test_refuses_unreachable_paths(master, bad_path):
    with pytest.raises(NoteAccessError):
        read_note(master, bad_path)


def test_refuses_parent_traversal(master, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("top secret\n")
    with pytest.raises(NoteAccessError):
        read_note(master, "Company/../../secret.md")


def test_refuses_absolute_path(master, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("top secret\n")
    with pytest.raises(NoteAccessError):
        read_note(master, str(secret))


def test_refuses_symlink_escaping_vault(master, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("outside the vault\n")
    link = master / "Company" / "Link.md"
    link.symlink_to(outside)
    with pytest.raises(NoteAccessError):
        read_note(master, "Company/Link.md")
