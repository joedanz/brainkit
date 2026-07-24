# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While brainkit is pre-1.0, minor versions may contain breaking changes. Anything
that changes a vault layout, a `_meta` schema, or a permission rule will say so
explicitly under **Changed**, with what to do about it.

## [Unreleased]

Nothing yet.

## [0.1.1] - 2026-07-24

A licensing fix and the documentation 0.1.0 should have shipped with. No
behavior changes.

### Fixed

- **The vendored fonts now carry their license.** `src/brain/assets/vendor/fonts/`
  redistributes eight `.woff2` binaries under the SIL Open Font License 1.1,
  whose section 2 permits redistribution only "provided that each copy contains
  the above copyright notice and this license". The 0.1.0 wheel and sdist
  shipped the fonts without it. `OFL.txt` now sits beside the binaries, carrying
  the license text and the copyright notices for all three families, and is
  present in both build artifacts. **If you are redistributing 0.1.0, upgrade** —
  this is the only reason to.
- The docs homepage was titled `brainkit — brainkit`, because the site composes
  `{page title} — {site name}` and both were the same word.
- Corrected a claim in the vendored-assets README: none of the three font
  families reserve a font name, so the OFL's Reserved Font Name clause
  constrains nothing about their use here.

### Added

- **A Limitations section in the README.** It previously said its limits were
  "spelled out below rather than hidden"; they were not spelled out anywhere.
  Every entry is measured or read out of the code — disk amplification,
  last-write-wins on concurrent edits, the dashboard's lack of authentication,
  what the embedding provider sees, and where scale has actually been tested.
- Three operator questions that were undocumented, each placed where someone
  already looks for it:
  - **Disk sizing** ([reference deployment](https://brainkit-docs.vercel.app/guides/reference-deployment#sizing)) —
    disk scales with readers × shared content, not with master size. Measured:
    1 MB of master content and ten people compiles to 9.2 MB, or 13.8 MB
    indexed. `.git` is 57% of a vault. Includes a warning that a compiled vault
    is *not* a disposable artifact — it is the git remote its owner pushes to.
  - **Concurrent edits** ([spaces & permissions](https://brainkit-docs.vercel.app/concepts/spaces-and-permissions#two-people-one-file)) —
    two people writing one file in a shared writable space is last-write-wins,
    with no merge and no warning. Personal spaces cannot collide.
  - **Index schema upgrades** ([retrieval](https://brainkit-docs.vercel.app/concepts/retrieval#upgrading-across-a-schema-change)) —
    an older index rebuilds itself fully; a newer one refuses to open and names
    the fix.
- The README command table lists all 18 subcommands. `graph`, `triage`, and
  `rename-entities` were missing.

### Changed

- `aiohttp` floor raised to 3.14.3 (the 3.14.1 floor remains a security bound —
  see the note in `pyproject.toml`).
- Docs toolchain: `react`, `react-dom`, and `vite` patch/minor bumps. Cleared
  every fixable npm advisory; the three that remain are documented with why
  taking them would mean downgrading Holocron past a working deploy.
- CI runs a lint gate (pinned `ruff`), a 3.12/3.13/3.14 matrix, and a packaging
  check that builds with build trees present and asserts the sdist did not
  absorb them.

## [0.1.0] - 2026-07-24

First tagged release. Everything below already worked; this marks the point it
became something you can pin.

### Added

**The compiler and its guarantee.** A deterministic `(master commit, person) →
filtered vault` build. Each person's copy holds only the spaces they may read,
links to notes they can't see are stubbed rather than dangling, and the build
fails closed — a bug can only ever show *less*. Vaults swap into place in two
phases, so a crash mid-run leaves every vault either wholly refreshed or wholly
previous, never half-written, and the next run repairs the tombstone.

**Spaces and permissions.** `_meta/spaces.yaml` decides who reads and writes
what, by person, team, or role, with wildcards bound to the reader's own
identity. Third-party spaces are deny-by-default.

**Write-back.** Edits made in a person's vault are validated server-side against
their write scope before touching master. One out-of-scope path rejects the
whole change set. A manifest of post-processing hashes keeps per-person link
stubbing from registering as phantom user edits.

**Promotions.** The only route from a private space to a shared one, with a
human approving every publish. `create`, `append`, and `patch` modes; `patch`
fails closed if the target moved since the draft was queued.

**Shares.** People request read or write access to spaces they own via their own
vault, and the share's decider approves — the recipient for a person-share, a
team lead for a team-share, an admin for company-wide. Revokes apply
immediately.

**Intake.** `brain ingest` is the safe server-side primitive that can only write
into one person's Inbox; `brain webhook` serves it over signed HTTP with
Standard Webhooks HMAC verification, replay dedup, and per-source rate limits.

**Retrieval.** Per-vault hybrid search — keyword, optional semantic, and
Personalized PageRank over the note graph — built only from that vault's own
compiled slice, so search inherits the compiler's boundary by construction.
Reached from the CLI or over MCP.

**MCP server.** A dependency-free stdio server exposing six read-only tools
(`brain_search`, `brain_read`, `brain_links`, `brain_graph`, `brain_recent`,
`brain_facts`) to any MCP client.

**Typed relations and facts.** Five frontmatter relations (`up`/`down`, `same`,
`prev`/`next`) with derived inverses, plus structure mined from folders, date
sequences, and shared entity types. Fact lines carry `[from::]`/`[until::]` and
a source, so the vault can answer what was true on a date — and what it believed
on a date.

**Operations.** `brain cycle` runs the whole loop on a schedule; `brain doctor`
checks integrity across 20+ checks; `brain triage` routes findings into people's
inboxes; `brain status`, `brain dashboard`, and a generated `Map.md` per vault
answer "what's in here?".

**18 subcommands** in all, documented with their flags and exit codes in the
[CLI reference](https://brainkit-docs.vercel.app/reference/cli).

[Unreleased]: https://github.com/joedanz/brainkit/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/joedanz/brainkit/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/joedanz/brainkit/releases/tag/v0.1.0
