# Contributing to brainkit

Thanks for looking. This file covers the setup, the parts of the codebase that
need extra care, and what a reviewable change looks like here.

**Security issues do not belong in an issue or a PR.** See
[SECURITY.md](SECURITY.md) for private reporting.

## Setup

```bash
git clone https://github.com/joedanz/brainkit && cd brainkit
uv sync --extra dev
uv run pytest
```

Python 3.12+ and `git`. Nothing else — the runtime dependencies are `pyyaml`,
`sqlite-vec`, and `aiohttp`.

A few tests skip themselves when `sqlite-vec` can't load, which happens on a
Python built without loadable-extension support (most often the python.org
macOS installer). That's expected: search degrades to keyword-only and so does
the suite. Check yours with:

```bash
python -c "import sqlite3; print(hasattr(sqlite3.Connection, 'enable_load_extension'))"
```

To try a change end-to-end against a throwaway vault:

```bash
uv run brain init /tmp/master --company "Test Co"
uv run brain compile --master /tmp/master --out /tmp/compiled
uv run brain doctor --master /tmp/master
```

## The parts that need care

One guarantee holds this project up: **a person's compiled vault physically
contains only notes they may read.** It isn't enforced by policy or by asking an
agent nicely — it's enforced by the compiler never writing the bytes. Four
modules can break it:

| module | what it decides |
|---|---|
| `resolver.py` | which spaces a person may read, and which paths they may write |
| `compiler.py` | what actually lands in a vault — the security boundary itself |
| `writeback.py` | which edits are allowed back into master |
| `ingest.py` | where an incoming note is allowed to land |

If you're touching these, read `tests/test_leak_property.py` and
`tests/test_search_leak_property.py` first. They assert the invariant directly
rather than testing a particular code path, which means they keep holding as the
implementation changes — and they are the tests most worth extending.

Two rules that are easy to violate by accident:

- **Fail closed.** When something is ambiguous or broken, show *less*, never
  more. An unknown space has no readers. A rule that doesn't parse fails the
  cycle instead of defaulting to permissive.
- **Ask the rules, not the disk** — and know which one you need.
  `enumerate_spaces` answers "what exists on disk"; `can_read`/`can_write_path`
  answer "what do the rules allow". They are not interchangeable, and past bugs
  came from mixing them up.

## Tests

The suite is the contract.

```bash
uv run pytest              # all of it, ~80s
uv run pytest tests/test_compiler.py -q
uv run pytest -k leak      # the invariant tests
```

New behavior needs a test. A bug fix needs a test that fails before it and
passes after — if you can't write one, that's usually a sign the diagnosis isn't
finished yet.

Prefer tests that assert the property rather than the implementation. A test
that breaks when someone refactors, without anything actually being wrong, costs
more than it protects.

## Style

`ruff` runs in CI. Use the same version it does, or you'll chase findings CI
doesn't have — and miss ones it does:

```bash
uvx ruff@0.16.0 check .
uvx ruff@0.16.0 check . --fix
```

Both pins are deliberate. `ruff.toml` pins the *rules* with an explicit
`select`, and `.github/workflows/tests.yml` pins the *linter*, because ruff's
default rule set grows with releases — a floating version means a new ruff can
turn someone's unrelated PR red. Bump the two together.

Every entry under `ignore` records why it's there. A rule turned off without a
reason is indistinguishable from one nobody got around to fixing, so if a rule
fights the codebase deliberately, add it there with the reason rather than
sprinkling `noqa`.

Beyond that: match the file you're editing. Comments here explain *why*, not
what — the code already says what.

## Docs

`docs/` is a [Holocron](https://holocron.so) site, deployed to Vercel:

```bash
cd docs && npm install && npm run dev
```

If your change alters a documented behavior — a flag, an exit code, a file
layout, a permission rule — update the page in the same PR. Docs drifting from
the code is a bug in this repo.

## Pull requests

- Branch from `main`. One concern per PR.
- Explain **why** in the description, not just what. The diff shows what.
- Say how you verified it. "Tests pass" is fine when tests cover it; if you
  checked something by hand, say what you ran and what you saw.
- CI must be green.

If you're planning something large, open an issue first — it's a cheaper place
to find out that an approach won't work than a finished branch.

## Reporting bugs

Include what you ran, what happened, and what you expected. For anything
vault-related, `brain doctor --master <path>` output is usually the fastest way
to a diagnosis.

Please don't paste real vault contents into a public issue.

## Cutting a release

Releases are tag-driven: pushing `vX.Y.Z` runs `.github/workflows/release.yml`,
which builds, verifies, publishes to PyPI, and attaches the same two artifacts
to a GitHub release.

The thing to internalize before touching any of this: **a PyPI version number
is permanent.** You can yank a release, but you can never re-upload that
version, and even a deleted file's name stays claimed. Everything below is
front-loaded for that reason — there is no fixing it afterward, only a `.postN`
release explaining what went wrong.

### One-time setup

Configure a [Trusted Publisher](https://docs.pypi.org/trusted-publishers/) on
PyPI (and separately on TestPyPI, if rehearsing) rather than storing an API
token:

| field | value |
|---|---|
| Owner / repository | `joedanz` / `brainkit` |
| Workflow name | `release.yml` |
| Environment name | `pypi` (or `testpypi`) |

Then add matching [GitHub environments](https://github.com/joedanz/brainkit/settings/environments).
Putting a
required reviewer on `pypi` is the point of the environment: it inserts a human
in front of the one step in the workflow that cannot be undone.

Trusted Publishing means GitHub mints a short-lived OIDC token that PyPI trades
for an upload token scoped to this repo and workflow. There is no long-lived
credential to leak, rotate, or forget to scope — and it's what signs the
[PEP 740](https://peps.python.org/pep-0740/) attestations uploaded beside the
artifacts.

### Each release

1. **Rehearse**, if the packaging changed: Actions → release →
   *Run workflow* → target `testpypi`. This spends a TestPyPI version number,
   not a real one, and exercises the same build and the same guards. Bump the
   patch version if you need a second rehearsal.
2. **Bump the version** in `pyproject.toml`. Nowhere else — `brain mcp` reads
   its version from installed metadata, and `tests/test_packaging.py` fails if
   the two ever disagree.
3. **Move the CHANGELOG section** from `Unreleased` to `[X.Y.Z] - YYYY-MM-DD`,
   and add the compare link at the bottom.
4. **Check the README's claims are still true.** It says in two places that
   there is no PyPI package; the first release makes both false.
5. Merge, then `git tag vX.Y.Z && git push origin vX.Y.Z`.
6. Approve the `pypi` environment when Actions asks.

### What the workflow refuses to publish

Each of these exists because it already went wrong once, or because it can only
go wrong on the run you can't redo:

- **A tag that disagrees with `pyproject.toml`.** hatchling names the artifact
  from pyproject and ignores the tag, so a forgotten bump publishes `0.1.2` from
  tag `v0.1.3` — a name that can then never be corrected.
- **An sdist that swallowed a build tree.** hatchling reads only the *root*
  `.gitignore`, so `docs/.gitignore` never reaches it. This has broken twice,
  once to 133 MB and 24,990 files, and both times a local build looked fine
  because the trees weren't there. The workflow creates them on purpose first.
- **Fonts without their license.** The vendored `.woff2` files are OFL 1.1,
  whose section 2 permits redistribution only if each copy carries the license.
  0.1.0 shipped eight fonts and zero copies of it.
- **A description PyPI won't render.** `twine check --strict` runs the same
  renderer the project page uses. Without it, a broken long description degrades
  silently to plain text.
