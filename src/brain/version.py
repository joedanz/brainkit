"""One place that answers "which brainkit is this?".

The distribution version alone cannot identify a deployment. `pyproject.toml`
reads 0.1.2 from the moment it is bumped until the next release, so every commit
in between reports the same string — and neither live box installs a release:
the brain box uses `uv tool install git+…` and the agents image `COPY`s a working
tree. Two boxes built weeks apart can honestly both answer "0.1.2".

So the version comes from installed metadata (never restated, so it cannot drift
from pyproject) and the *build* is identified separately by a revision the
builder stamps in. That is a deliberate split: a version names a release, a
revision names a build, and only the second one can tell you what is running
between releases.
"""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError, distribution, version

DISTRIBUTION = "brainkit"

_SHORT = 12

try:
    __version__ = version(DISTRIBUTION)
except PackageNotFoundError:
    # Importable but never installed — a source checkout on sys.path. The code
    # still runs; it just cannot honestly name its own version, and inventing
    # one here is how a hand-maintained copy silently drifts from pyproject.
    __version__ = "0+unknown"


def _abbreviate(rev: str) -> str:
    """Shorten a full hex sha for display, leave anything else alone.

    The two sources disagree on length — a build arg carries the full 40 chars
    so the OCI label is complete, while a tag name is not a sha at all.
    """
    lowered = rev.lower()
    if len(rev) >= _SHORT and all(c in "0123456789abcdef" for c in lowered):
        return lowered[:_SHORT]
    return rev


def _recorded_revision() -> str | None:
    """The commit an installer wrote down, per PEP 610.

    This is what makes `uv tool install git+…` self-describing: for a VCS
    install the exact commit is already in `direct_url.json` beside the package
    — it had just never been surfaced, which is why deployed commits ended up
    tracked by hand. Returns None for an editable checkout, correctly: its
    commit is whatever HEAD is right now, which install metadata cannot know.
    """
    try:
        raw = distribution(DISTRIBUTION).read_text("direct_url.json")
    except (PackageNotFoundError, OSError):
        return None
    try:
        commit = (json.loads(raw or "{}").get("vcs_info") or {}).get("commit_id")
    except ValueError:
        return None
    return commit if isinstance(commit, str) and commit else None


def revision() -> str | None:
    """The commit this build came from, abbreviated for display.

    Two sources, in order of authority. `BRAINKIT_GIT_SHA` is stamped by the
    agents-box Dockerfile, which installs a working tree an installer cannot
    describe; it wins because a builder who bothered to say is more reliable
    than inference. Otherwise PEP 610 install metadata, which covers the brain
    box. Absent for a release install, where the version already identifies the
    code exactly.
    """
    stamped = os.environ.get("BRAINKIT_GIT_SHA", "").strip()
    found = stamped or _recorded_revision()
    return _abbreviate(found) if found else None


def version_string() -> str:
    """What `brain --version` prints: enough to identify a deployment."""
    rev = revision()
    return f"brain {__version__}" + (f" (rev {rev})" if rev else "")
