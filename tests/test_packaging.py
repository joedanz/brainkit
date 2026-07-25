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

import re
import tomllib
from pathlib import Path

import pytest

from brain.mcp import SERVER_INFO

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
