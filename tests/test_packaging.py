"""The PyPI-facing surface, which nothing else exercises.

A published version number is permanent — PyPI lets you yank a release but
never re-upload it, and even a deleted file's name stays claimed. So the
mistakes guarded here are specifically the ones that are unfixable after the
fact rather than merely embarrassing: a wrong version baked into the artifact
name, a description that renders as nothing, and a classifier PyPI refuses.

These read pyproject.toml directly. That is the point: the assertions are about
agreement between the manifest and the code, so deriving both from one source
would assert nothing.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

import brain.version
from brain.cli import build_parser
from brain.mcp import SERVER_INFO
from brain.version import revision, version_string

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
README = Path(__file__).resolve().parents[1] / "README.md"


@pytest.fixture(scope="module")
def project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def test_mcp_reports_the_packaged_version(project):
    """`initialize` must not name a version the artifact doesn't have.

    mcp.py reads this from installed metadata rather than restating it, so the
    only way to fail is for the test environment to have a stale install — which
    is worth knowing about too.
    """
    assert SERVER_INFO["version"] == project["version"]
    assert SERVER_INFO["name"] == project["name"]


def test_the_readme_is_the_long_description(project):
    """Without this key the project page has a summary line and nothing else."""
    assert project["readme"] == "README.md"


def test_readme_links_are_absolute():
    """PyPI does not resolve relative paths; GitHub does.

    So a relative link is invisible here and broken there — images especially,
    which render as a broken-image icon on the page most people land on first.
    """
    relative = [
        m.group(3)
        for m in re.finditer(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)", README.read_text(encoding="utf-8"))
        if not m.group(3).startswith(("http://", "https://", "#", "mailto:"))
    ]
    assert not relative, f"relative README references break on PyPI: {relative}"


def test_no_license_classifier_beside_the_spdx_expression(project):
    """PyPI rejects an upload carrying both, and the rejection is at upload time.

    `license = "MIT"` is a PEP 639 expression. Adding the legacy
    "License :: OSI Approved :: MIT License" classifier next to it is the kind
    of well-meant addition that only fails on the one run that can't be redone.
    """
    assert isinstance(project["license"], str), "expected a PEP 639 SPDX expression"
    offenders = [c for c in project["classifiers"] if c.startswith("License ::")]
    assert not offenders, f"remove these, or drop the SPDX expression: {offenders}"


def test_project_urls_cover_the_sidebar(project):
    """The PyPI sidebar is the only navigation a `pip install` arrival gets."""
    assert {"Homepage", "Documentation", "Changelog", "Issues"} <= set(project["urls"])


def test_brain_version_reports_the_packaged_version(project, capsys):
    """`brain --version` is how you ask a deployed box what it is running.

    Exercised through the real parser rather than by calling version_string(),
    because the thing that could break is argparse: the subcommand is
    `required=True`, and a version action that ran too late would fail with
    "the following arguments are required" instead of printing.
    """
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"brain {project['version']}"


def test_a_stamped_build_names_its_commit(monkeypatch):
    """Between releases the version repeats, so the commit is the only identifier.

    The agents-box image installs a working tree and stamps BRAINKIT_GIT_SHA at
    build time; this is what makes `docker exec … brain --version` useful. The
    build arg carries a full sha so the OCI label is complete, so display has to
    abbreviate it.
    """
    monkeypatch.setenv("BRAINKIT_GIT_SHA", "9627593abc1234567890deadbeefcafe00112233")
    assert revision() == "9627593abc12"
    assert version_string().endswith(" (rev 9627593abc12)")

    # Not a sha — a tag or branch is passed through rather than mangled.
    monkeypatch.setenv("BRAINKIT_GIT_SHA", "v0.1.1")
    assert revision() == "v0.1.1"

    # Whitespace-only is the shape a shell passes when a build arg is unset;
    # this must fall through to install metadata, not report " " as a revision.
    monkeypatch.setenv("BRAINKIT_GIT_SHA", "  ")
    assert revision() == _recorded_revision_abbreviated()


def test_a_git_install_reports_its_commit_without_any_stamp(monkeypatch, tmp_path):
    """PEP 610 already records the commit for `uv tool install git+…`.

    This is the brain box's case, and the reason no build-system change is
    needed to identify it: the commit was on disk all along.
    """
    monkeypatch.delenv("BRAINKIT_GIT_SHA", raising=False)
    payload = json.dumps(
        {
            "url": "https://github.com/joedanz/brainkit",
            "vcs_info": {"vcs": "git", "commit_id": "4fdc3e6b83584497b980c17441163c288ce4a36f"},
        }
    )
    monkeypatch.setattr(brain.version, "distribution", lambda _: _FakeDist(payload))
    assert revision() == "4fdc3e6b8358"

    # An editable checkout records dir_info, not vcs_info. Reporting nothing is
    # right: its commit is whatever HEAD is now, which metadata cannot know.
    editable = json.dumps({"url": "file:///src", "dir_info": {"editable": True}})
    monkeypatch.setattr(brain.version, "distribution", lambda _: _FakeDist(editable))
    assert revision() is None


class _FakeDist:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read_text(self, name: str) -> str | None:
        return self._payload if name == "direct_url.json" else None


def _recorded_revision_abbreviated() -> str | None:
    from brain.version import _abbreviate, _recorded_revision

    found = _recorded_revision()
    return _abbreviate(found) if found else None


def test_mcp_does_not_leak_the_revision_into_serverinfo():
    """MCP clients may parse serverInfo.version; PEP 440 is the only contract.

    A "(rev …)" suffix there would be a wire-format change, not a nicety.
    """
    assert "rev" not in SERVER_INFO["version"]
