"""Scaffolding content for a new company master vault."""

from __future__ import annotations

import subprocess
from pathlib import Path

ORG_YAML = """\
people:
  # id: {name: Full Name, roles: [admin], teams: [sales], email: name@example.com}
  # email is optional and must be unique; it's the auth key for `brain ingest --from`.
  founder: {name: Founder, roles: [admin], teams: [], email: founder@example.com}
"""

SPACES_YAML = """\
spaces:
  - {path: Company,     read: [everyone],        write: ["role:admin"]}
  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}
  - {path: "Clients/*", read: [everyone],        write: ["role:admin"]}
"""

CHIEF_OF_STAFF_PROTOCOL = """\
# Chief-of-Staff Protocol (server — full master vault)

This is the master vault. You are the company chief-of-staff agent with full
access. Personal agents see only their compiled slice; you maintain the whole.

## Transcript pipeline

When a transcript appears in any `People/<person>/Inbox/`:
1. Summarize it.
2. Extract decisions, action items (owner + deadline), and context updates.
3. Route:
   - Action items -> that person's `People/<person>/Actions/Action Tracker.md`
   - Client facts -> the matching `Clients/<client>/` file
   - Company-wide decisions -> `Company/Decisions/`
   - Session summary -> `People/<person>/Sessions/`
   - General insights -> `Company/Memory.md`
4. Archive the processed transcript into `People/<person>/Sessions/`.

If you cannot place an item confidently, add it to `Company/Needs-Routing.md`
instead of guessing. Doing nothing is always safer than routing wrongly.

## Dashboards

Keep `Company/Home.md` current as the priority dashboard: open actions by
owner, pending promotions, recent decisions.

## Boundaries

- Never move content out of a `People/` space except via an approved
  promotion in `_meta/promotions/`.
- `_meta/` is operational state; never surface its contents in shared notes.
- Drafts only for anything outward-facing: a human sends every message and
  approves every commitment.
"""


def _home_md(company: str) -> str:
    return (
        f"# {company} — Home\n\n"
        "## Priorities\n\n(maintained by the chief-of-staff agent)\n\n"
        "## Links\n\n- [[Memory]]\n- Decisions/\n- Frameworks/\n- Templates/\n"
    )


def _memory_md(company: str) -> str:
    return (
        f"# {company} — Company Memory\n\n"
        "Business overview, positioning, offers, team structure. Maintained by\n"
        "the chief-of-staff agent; substantive changes arrive via promotions or\n"
        "admin edits.\n"
    )


def scaffold_master(dest: Path, company: str) -> list[str]:
    files: dict[str, str] = {
        "AGENTS.md": CHIEF_OF_STAFF_PROTOCOL,
        "Company/Home.md": _home_md(company),
        "Company/Memory.md": _memory_md(company),
        "Company/Decisions/.gitkeep": "",
        "Company/Frameworks/.gitkeep": "",
        "Company/Templates/.gitkeep": "",
        "Teams/.gitkeep": "",
        "People/.gitkeep": "",
        "Clients/.gitkeep": "",
        "_meta/org.yaml": ORG_YAML,
        "_meta/spaces.yaml": SPACES_YAML,
        "_meta/promotions/pending/.gitkeep": "",
        "_meta/promotions/approved/.gitkeep": "",
        "_meta/promotions/rejected/.gitkeep": "",
    }
    created = []
    for rel, content in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        created.append(rel)
    subprocess.run(["git", "-C", str(dest), "init", "-b", "main"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(dest), "-c", "user.name=Brain Init",
                    "-c", "user.email=init@brain.local",
                    "commit", "-m", f"init: {company} master vault"],
                   capture_output=True, check=True)
    return created
