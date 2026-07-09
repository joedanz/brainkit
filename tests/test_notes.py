import pytest

from brain.notes import NoteAccessError, read_note


def test_reads_a_file_inside_a_space(master):
    assert "Home" in read_note(master, "Company/Home.md")


def test_refuses_path_outside_any_space(master):
    # AGENTS.md sits at the vault root, inside no space.
    with pytest.raises(NoteAccessError):
        read_note(master, "AGENTS.md")
    # _meta is reserved.
    with pytest.raises(NoteAccessError):
        read_note(master, "_meta/org.yaml")


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


def test_refuses_missing_file(master):
    with pytest.raises(NoteAccessError):
        read_note(master, "Company/DoesNotExist.md")


def test_refuses_directory(master):
    with pytest.raises(NoteAccessError):
        read_note(master, "Company/Decisions")
