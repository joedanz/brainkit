"""The marker every deliberate, operator-facing failure carries.

`brain` promises one thing about failure (docs/reference/cli.mdx): every
subcommand "exits 0 on success and 1 on a handled error ... with a
human-readable message on stderr." Keeping that promise needs a way to tell
two kinds of exception apart:

- We anticipated this, and the message is written for whoever runs the
  command. Print it and exit 1.
- We did not anticipate this. It is a bug, and a traceback is the honest
  output — swallowing it into a tidy one-liner would hide the defect and
  lie about what happened.

Catching `ValueError` in ``main`` cannot make that distinction: nine of the
domain errors below subclass it, but so does ``int("abc")``. Hence a marker.
Every deliberate error inherits it *alongside* its existing base, so
``except SchemaError`` and ``except ValueError`` still catch what they always
did — this adds to the MRO, it does not reroute it.

Subclasses keep living beside the code that raises them; only the marker is
central. That matters for startup: ``cli`` imports the heavier modules lazily
inside each command, and a catch-all built from concrete types would drag all
of them in eagerly on every ``brain`` invocation.
"""

from __future__ import annotations

import subprocess


class BrainError(Exception):
    """A failure with a message meant for the person who ran the command."""


# The two failure families we expect but do not author messages for. Both are
# environmental — a wrong path, a full disk, a read-only mount, a git that
# refused — so both are the operator's to fix, and neither is evidence of a
# bug. Anything outside this set still reaches the terminal as a traceback,
# which is the correct output for something we did not anticipate.
HANDLED: tuple[type[BaseException], ...] = (
    BrainError,
    OSError,
    subprocess.CalledProcessError,
)


def describe(exc: BaseException) -> str:
    """One actionable line for a handled failure.

    ``BrainError`` subclasses already say what went wrong, so they pass
    through. The other two need help: ``CalledProcessError`` stringifies to
    "returned non-zero exit status 128" and drops git's actual complaint,
    while ``OSError`` renders an errno the reader has to decode. Both are
    recoverable only if the message names the command or the path.
    """
    if isinstance(exc, subprocess.CalledProcessError):
        cmd = exc.cmd
        cmd_text = " ".join(str(a) for a in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        detail = (exc.stderr or "").strip() or f"exit status {exc.returncode}"
        return f"{cmd_text}: {detail}"
    if isinstance(exc, OSError):
        where = f" ({exc.filename})" if exc.filename else ""
        return f"{exc.strerror or exc}{where}"
    return str(exc)
