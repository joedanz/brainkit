import pytest

from brain.shares import ShareError, validate_space, validate_subject


@pytest.mark.parametrize("subject,expected", [
    ("person:mary", ("person", "mary")),
    ("team:concierge", ("team", "concierge")),
    ("person:j.o-e_2", ("person", "j.o-e_2")),
])
def test_validate_subject_accepts_person_and_team(subject, expected):
    assert validate_subject(subject) == expected


@pytest.mark.parametrize("bad", [
    "mary", "role:admin", "everyone", "person:", "person:has space",
    'person:x"], "read": ["everyone', "team:with\nnewline", "person:a/b", "",
])
def test_validate_subject_rejects_everything_else(bad):
    with pytest.raises(ShareError):
        validate_subject(bad)


@pytest.mark.parametrize("space", ["Clients/Danziger Family", "Teams/sales"])
def test_validate_space_accepts_two_segment_shared_spaces(space):
    validate_space(space)  # no raise


@pytest.mark.parametrize("bad", [
    "People/joe", "Company", "Clients", "Clients/A/B", "Clients/*",
    "../etc", "_meta/shares", "",
])
def test_validate_space_rejects_non_shareable_paths(bad):
    with pytest.raises(ShareError):
        validate_space(bad)
