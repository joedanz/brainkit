# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While brainkit is pre-1.0, minor versions may contain breaking changes. Anything
that changes a vault layout, a `_meta` schema, or a permission rule will say so
explicitly under **Changed**, with what to do about it.

## [Unreleased]

### Added

- **A health snapshot Fleet can read: `<master>/_meta/cache/health.json`.**
  Every `brain cycle` now writes this file — `brain doctor`'s findings reduced
  to `severity:check` counts, plus the cycle's tamper tallies, the brainkit
  version that produced them, and a UTC timestamp. Counts ONLY: messages and
  paths are dropped at the source (`triage.count_findings`), because finding
  text carries restricted client names and file paths. It lives under
  `_meta/cache/`, which `brain init` gitignores, and a master whose
  `.gitignore` does not cover that path (one predating the 2026-07-21
  template) gets NO file rather than a committable one — `brain cycle` now
  says so in `health_warnings`, as it does for a write that failed or a cycle
  whose triage crashed. A control plane reading the file's absence should read
  it as "cannot say", never as "no findings". **This is the release that
  starts publishing it**: an older brainkit publishes nothing, so a box that
  reads as not-reporting is answered by upgrading it.

### Changed

- `brain cycle --json` and `brain doctor --json` now emit compact,
  single-line JSON instead of indented output. Both are appended to files
  named `.jsonl` by the reference deployment's crons, and indented output
  made those files unparseable line by line. Piping either through `jq .`
  restores the old readability.

## [0.4.2] - 2026-08-18

### Added

- **Documented what a revoke does not undo.** Removing someone from a space
  takes it out of their next compile, but each compiled vault is a git repo:
  notes they could read while granted stay in its history, and a fresh clone
  carries that history. This was true from the start and stated nowhere —
  a gap worth closing precisely because the other limits are spelled out, so
  "revokes apply immediately" was easy to over-read. Now a Limitations bullet
  and a
  [What a revoke does not undo](https://brainkit-docs.vercel.app/concepts/spaces-and-permissions#what-a-revoke-does-not-undo)
  section, both naming the remedy: rotate anything whose value survives being
  read, and purge the server's copy by deleting that person's compiled vault
  and recompiling (the rebuild starts a fresh repo). A clone already on their
  laptop is beyond reach by construction. Behaviour unchanged; a new test pins
  both the limit and the remedy.

### Fixed

- **`brain mcp` now names the argument a caller left out.** A `tools/call`
  missing a required argument used to run the tool anyway with an empty
  string, so the client got whatever that produced downstream —
  `not in index: ` for `brain_links`, `refused: '' is not inside any readable
  space` for `brain_read` — naming neither the argument nor the mistake. Bad
  arguments are now refused before dispatch as JSON-RPC `-32602`, the same
  code an unknown tool already returned, with a message that names the missing
  argument. The check is derived from the `required` list each tool publishes
  through `tools/list`, so it cannot drift from the advertised schema and a new
  tool inherits it. A present-but-blank value is refused the same way; tools
  that require nothing (`brain_recent`, `brain_facts`) are unaffected.

- **`npm ci` now works in `docs/`.** The committed lockfile had drifted from
  `package.json` — `@emnapi/runtime` was pinned where `@emnapi/wasi-threads`
  was required — so `npm ci` exited `EUSAGE` and only `npm install` could
  build the docs. That kept the docs out of any lockfile-respecting build.
  Regenerating preserves every platform-gated dependency (204 Linux-scoped
  entries before and after); a clean `npm ci && npm run build` now succeeds.

### Changed

- **The scaffolded assistant protocol now describes promotions, not just
  shares.** A new vault's protocol documented the delegated-decision path for
  space shares in full while saying nothing about the promotion equivalent
  added in 0.4.0 — including the rule that promotions into the shared space
  are decided at the dashboard and never in-vault. Existing vaults are
  unaffected; the protocol is written once at scaffold time, so this reaches
  new vaults only. The behaviour it documents was already live: a pending
  promotion's decision recipe is generated into the decider's `Shares.md`
  regardless.

## [0.4.1] - 2026-08-18

### Changed

- **`brain status --master --json` / `/api/stats`: two payload fields removed,
  one added.** `promotions_pending` entries gain `eligible_approvers` — the
  person ids `may_approve` admits for *that* item — and `people` entries lose
  `roles` and `teams`, which existed only to let the dashboard re-derive that
  same rule in JavaScript and have no remaining consumer.

  **Upgrading:** nothing to do unless you script against those payloads.
  `roles`/`teams` shipped in 0.4.0 only and were never documented; if you were
  reading them to work out who may approve something, read
  `eligible_approvers` instead — it is the answer rather than the inputs.
  `promotions_pending`'s full field list is now documented in the
  [CLI reference](https://brainkit-docs.vercel.app/reference/cli).

### Fixed

- **The dashboard no longer keeps its own copy of the approval rule.**
  `may_approve` was encoded twice — once in Python, once as a regex in
  `admin.js` — with nothing able to hold the two in sync, and no JavaScript
  suite in which a divergence could be caught. Eligibility is now resolved
  server-side per pending item and served in the payload; the dropdown filters
  on that list. The server always enforced on `approve()`, so a drift would
  have surfaced as a refused approval the UI had already offered — a
  confusing way to learn about a bug.

  This also corrects the empty-state message, which asked whether the org had
  *any* admin. That was the right question when only admins could approve and
  the wrong one afterwards: an org with leads and no admin can still clear its
  `Teams/` items, but was told nothing could be approved.

### Added

- **Working cron jobs for both human gates**, replacing the prompt drafts that
  shipped in 0.4.0. `deploy/agents-box/cron-jobs/brain-decisions-digest.py`
  runs per person under `hermes cron --no-agent`;
  `deploy/brain-box/brain-queue-digest.sh` runs on the brain box from host
  cron. Both print nothing when nothing is pending, so nothing is delivered —
  silence is mechanical rather than an instruction a model has to remember,
  which is what stops a daily digest becoming noise people filter.

  The prompts they replace could not have worked as written: the admin one
  called `brain promotions list --master` from an agent container, and agents
  hold no master access by design. Verified against a live container,
  including a real scheduler-driven run; the README's install steps are now
  the commands that were actually run. Two hermes gotchas are documented
  there — `--script` resolves `$HOME/scripts/` rather than the
  `~/.hermes/scripts/` its `--help` claims, and `hermes` is not on `PATH`
  under `docker exec`.

## [0.4.0] - 2026-08-18

### Changed

- **Who may approve a promotion is now a permission rule, and it moved twice.**
  Approval used to accept any person id in `_meta/org.yaml`; it now accepts
  only someone entitled to approve *that* promotion — an admin for any target,
  or a `lead` on team T for a `Teams/T/…` target. The shared space and entity
  spaces stay admin-only. One function, `may_approve` in `brain/promotions.py`,
  is the whole rule; the CLI, the dashboard API, and the in-vault seam all
  inherit it.

  **Upgrading:** if a non-admin has been approving promotions, either give them
  `roles: [admin]` in `_meta/org.yaml`, or — if their approvals were all into
  one team's space — give them `roles: [lead]` with that team in their `teams`.
  Already-approved notes are untouched. Nothing changes for an org where an
  admin has always approved.

- **New vault-layout convention: `People/<pid>/PromotionApprovals/`.** A
  person's own space gains a second decision folder beside the existing
  `Approvals/` (which remains shares-only). Promotion ids and share ids are
  both free-form slugs and can collide, so they get separate folders rather
  than a shared one with a discriminator key. No migration: the folder is
  created by whoever first writes a decision into it, and a vault without one
  behaves exactly as before.

### Added

- **Team leads approve promotions into their own team's space, from their
  own vault.** `may_approve` routes `Teams/<team>/` targets to anyone with
  `role: lead` on that team, mirroring how share requests already route.
  Leads get a **Promotions awaiting your decision** section in `Shares.md`
  showing what would be published (body, or a diff for `patch`), and decide by
  writing `People/<lead>/PromotionApprovals/<promo-id>.md`; `brain cycle`
  applies it after re-checking eligibility. Company-wide promotions are never
  decidable in-vault — they stay at the dashboard where the diff and audience
  warning are visible. `brain doctor` surfaces delegated promotion decisions
  for 30 days. New `brain cycle --json` fields: `promotion_decisions_applied`,
  `promotion_decisions_refused`, `promotion_tampering`.

- **Two ready-made cron prompts for the human gates**, in
  `deploy/agents-box/cron-prompts/`. Both gates are *pull* — `Shares.md` is
  always current, but nothing tells anyone to open it. A per-person nudge
  reports what is waiting and records the decision its human gives; an admin
  queue nudge reports depth and age and deliberately refuses to decide, so
  company-wide publishing stays at the dashboard where the diff and audience
  warning are visible. Both stay silent when nothing is pending. Prompts only —
  no code, no new authority.

### Fixed

- **Promotion approval now requires `role: admin`.** The docs have always said
  approval is an admin's call — "unlike promotions, that approval isn't always
  an admin's call" is how the shares routing table draws the contrast — but
  `approve()` only ever checked that the approver appeared in
  `_meta/org.yaml`. Any colleague could be recorded as `approved-by` on a
  promotion publishing to the whole company; the intended rule was enforced
  purely by who could reach the master box.

  The check now lives in `may_approve(person, target_path, shared)`, the
  promotions counterpart to `shares.may_decide`, and the core `approve()` call
  is the single place it runs — so the CLI, the dashboard API, and any future
  caller inherit it rather than reimplementing it. See **Changed**, above, for
  the resulting rule and what to do about it.

- **The in-vault decider section leaked content from spaces a lead cannot
  read.** The section listing a lead's decidable promotions filtered on
  `may_approve` — role and team membership — and never on read access. Where an
  exact `spaces.yaml` rule shadowed the `Teams/*` wildcard (exact beats
  wildcard), a lead was shown the current bytes of a file absent from their own
  compiled vault, carried in by the `patch` diff. Now intersected with
  `can_read`, failing closed, with a cross-vault regression test. Found in
  review before release; no shipped version was affected.

- **A promotion body containing a code fence could break out of it.** The body
  is rendered into the lead's `Shares.md` inside a fenced block; a body
  carrying its own ``` sequence closed the fence early and bled into the note's
  structural markdown — including the recipe telling an agent how to record a
  decision. Fence length is now derived from the content.

- **Docs claimed an approval story the code no longer told.** The HTML
  explainers described the dashboard's approver dropdown as listing "the
  company's people," and named the admin as the identity who approves shares —
  which had already been untrue since share decisions began routing to
  recipients and team leads. One also promised that an employee's agent tells
  them when a share goes live; nothing did, and that sentence now describes
  what the vault actually records.

## [0.3.6] - 2026-08-14

### Fixed

- **SOUL.md is three-way merged on an image upgrade, not overwritten.** Four
  agents lost their names to the previous behaviour.

  The re-sync is a dpkg-conffile pattern — overwrite from the image only when
  the live file still matches what we shipped — but the sentinel recorded the
  *live* hash after every re-sync, including on the branch that had just
  decided the file was locally modified. A customised SOUL therefore became
  its own baseline, and on the next image change was declared unmodified and
  replaced. It survived exactly one upgrade and died on the second.

  Recording the image's hash would have stopped the loss while freezing every
  shipped SOUL improvement, because the fleet customises every agent's SOUL.
  So instead `.company-brain-soul.base` now keeps the exact SOUL.md the image
  last installed, and an upgrade merges (last-shipped → now-shipped) into the
  live file with `git merge-file`. Both sides survive.

  A conflict **keeps the local file** and logs where to reconcile, rather than
  writing conflict markers into a file the agent reads as instructions — so a
  conflicting shipped change is not applied until somebody acts on the log.

  Agents upgrading from 0.3.5 or earlier have no recorded base; the first
  upgrade after this falls back to the old comparison and records one.

## [0.3.5] - 2026-08-14

### Added

- **CLI for Microsoft 365 in the agent image** (`@pnp/cli-microsoft365`,
  pinned). This is how an agent reaches Outlook mail and calendar, OneDrive
  and Teams. Installing it here rather than from the job that connects an
  account makes the version a property of the image, so upgrading it is a
  brainkit release rather than a per-agent step.

  Installed to the default global prefix, deliberately **not** `/opt/data` —
  that is the runtime volume, and a build-time write there is masked when the
  volume mounts.

  `CLIMICROSOFT365_NOUPDATE=true` is set alongside it. The CLI's update notice
  prints on stdout, and the device-code prompt is parsed out of that stream
  when an agent signs in; a nag arriving mid-output is a parse failure with no
  obvious cause.

  **Migrating an agent that had this hand-installed:** the runtime `PATH` puts
  `/opt/data/.local/bin` ahead of `/usr/local/bin`, so a copy on the volume
  shadows the image's indefinitely. Remove the hand-installed copy, or the
  pinned version is a fiction.

## [0.3.0] - 2026-07-31

The shared space stops being called `Company`. A household runs `Family/`, a
solo brain runs `Home/`, and a company keeps exactly what it had — the name is
now a per-vault setting rather than a hardcoded path.

**Nothing to do for existing vaults.** The `shared:` key is written only when
it is not the default, so a default vault's `config.yaml`, compiled manifests,
and `brain init` output are byte-for-byte what 0.2.1 produced (the scaffold's
git tree hash is pinned by a test).

The name is fixed at `brain init --shared` and has no rename command — unlike
the entity tree, whose `brain rename-entities` counterpart still exists. Pick
it at the start.

### Added

- `brain init --shared` names the shared top-level space (default `Company`) —
  a household can run `Family/`, a solo brain `Home/`. The name is fixed after
  init. New optional `shared:` key in `_meta/config.yaml`; compiled-vault
  manifests carry a `shared` key only when it is not the default.
- `docs/guides/family-brain` — running brainkit for a household.

### Changed

- `_meta/config.yaml` schema: new optional `shared:` key. **Nothing to do for
  existing vaults** — a missing key means `Company`, and every output
  (scaffold, compiles, doctor) is byte-identical to before.

### Fixed

- Sharing a subfolder of the shared space is now refused at request time under
  any shared name (previously a silent no-op grant under a renamed top).
- Share grants and revokes on a vault with a non-default `shared:` no longer
  fail against the default name. A revoke was the worse half: it was refused
  inside the sweep's per-request guard, so it left the access in place with no
  outcome, no inbox note, and no log entry, every cycle.
- `brain rename-entities` no longer drops a non-default `shared:` key from
  `_meta/config.yaml`.

## [0.2.1] - 2026-07-29

Stops agents paging their users about our maintenance. Nothing here changes a
vault layout, a `_meta` schema, or a permission rule.

### Changed

- **Gateway lifecycle notifications are off by default** for `telegram` and
  `email` in the company-brain profile. The hermes gateway broadcasts
  "Gateway shutting down" / "Gateway online" to each platform's home channel on
  every restart — but on a managed fleet a restart is always an operator action
  (image roll, compose regen, box reboot), so the person on the other end gets
  paged about a maintenance window they can do nothing with.

  Pinned per platform at the **top level** of `config.yaml`, not under
  `gateway.platforms` — that nested path is the headline case of hermes #34067,
  written without complaint and then never read. A block naming only this key
  does not enable the platform; enablement still comes from `TELEGRAM_BOT_TOKEN`
  or the four `EMAIL_*` vars.

  One tradeoff worth knowing: the same flag silences the per-active-session
  "your task was interrupted, message me and I'll resume" note. Hermes has no
  separate control for the two.

### Fixed

- **First boot heals the flag on agents that already exist.** The profile
  template alone only ever reaches a new volume, because `config.yaml` is never
  re-synced — hermes rewrites it at runtime and regenerating would discard live
  settings, including the model an operator chose. Without the heal, every agent
  provisioned before this release would keep pinging forever, as would any
  restore from an older backup.

  The heal uses `hermes config set` (a real YAML merge; a shell append would
  produce a duplicate top-level key and an unparseable file) and fires **only
  when the key is unset**, so an operator who deliberately turns notifications
  back on is not overridden on the next boot — the same "only replace what we
  know we wrote" rule `SOUL.md` already follows.

## [0.2.0] - 2026-07-27

Makes the package publishable to PyPI, and makes a deployment able to say what
it is running. The minor bump is for `brain --version`; nothing here is
breaking, and no vault layout, `_meta` schema, or permission rule changed.

### Added

- **`brain --version`**, which answers "what is this box running?" over ssh or
  `docker exec` — the version was previously reachable only through the MCP
  `initialize` handshake. It reports the installed version, plus the commit when
  the version alone would not identify the code: a git install records its
  resolved revision under [PEP 610](https://peps.python.org/pep-0610/), so a box
  provisioned from `git+…` describes itself with nothing stamped in by hand. A
  released install reports no revision, correctly — the version already
  identifies the code exactly. See
  [Knowing what is deployed](https://brainkit-docs.vercel.app/guides/reference-deployment#knowing-what-is-deployed).
- **A tag-driven release workflow** (`.github/workflows/release.yml`). Pushing
  `vX.Y.Z` builds, verifies, publishes to PyPI via
  [Trusted Publishing](https://docs.pypi.org/trusted-publishers/), and attaches
  the artifacts to a GitHub release. No API token is stored anywhere; PyPI trades
  a short-lived GitHub OIDC token for an upload token scoped to this repo and
  workflow, and signs [PEP 740](https://peps.python.org/pep-0740/) attestations
  with it. `workflow_dispatch` runs the same path against TestPyPI. The runbook,
  including the one-time PyPI setup, is in
  [CONTRIBUTING.md](https://github.com/joedanz/brainkit/blob/main/CONTRIBUTING.md#cutting-a-release).
- Four release gates, each for a failure that is unfixable after upload — a PyPI
  version number can be yanked but never reused: the git tag must match the
  packaged version, the sdist must not have swallowed a build tree, the vendored
  fonts must ship their license, and the long description must render.
- `tests/test_packaging.py` guards the PyPI-facing surface, which nothing else
  exercised.

### Changed

- **The agents-box image installs brainkit from git rather than copying the
  working tree.** Nothing outside `deploy/agents-box/` crosses into the image
  now, so a container is traceable to a commit anyone can fetch — and it is what
  lets the image describe itself, since a VCS install records its revision where
  `brain --version` reads it. Build a specific one with
  `REF=<sha|tag|branch> deploy/agents-box/build-image.sh`. Operator-facing only;
  no change to what runs inside the container.

### Fixed

- **A new agents-box image did not reach the agents running on it.** First boot
  gated on the mere existence of `/opt/data/.company-brain-installed`, so the
  company-brain profile was copied once and never again: a newer image could
  land, the container be recreated onto it, and `SOUL.md` and `skills/` stay at
  whatever the original build shipped, with nothing recording *which* build had
  been applied so nothing could detect the drift. The sentinel now records the
  build (`brain --version` in full, so this keeps working once the image installs
  a release, which has no revision to report) and re-syncs when it differs.
  What re-syncs is deliberately narrow: `skills/` is refreshed, `SOUL.md` only
  when untouched since we wrote it — the hash we shipped is recorded, so an edit
  someone meant is preserved and the refusal logged — and `config.yaml` never,
  because hermes rewrites it at runtime and regenerating it would discard live
  settings including the operator's chosen model. A `SOUL.md` that is *missing*
  rather than edited is restored on the next boot, so a partial backup restore
  heals itself. **If you run the agents box, roll your containers onto a fresh
  image** — this is the release that starts propagating profile changes.

- **The package metadata was almost empty.** The published 0.1.1 wheel carried a
  name, version, summary, license, and dependencies — and nothing else. With no
  `readme` key the PyPI project page would have shown a single summary line above
  a blank body, with no link to the docs, the repo, or this changelog, and no
  classifiers to be found by. Now sets `readme`, `authors`, `keywords`, twelve
  classifiers, and five project URLs.
- **README images would have been broken on PyPI**, which does not resolve
  relative paths the way GitHub does. All nine relative references — the demo
  GIF, both dashboard screenshots, and six file links — are now absolute.
- `brain mcp` no longer restates its version in a second place. `SERVER_INFO`
  reads it from installed metadata, so a release cannot report a version its
  artifact doesn't have; a test asserts the two agree.

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

[Unreleased]: https://github.com/joedanz/brainkit/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/joedanz/brainkit/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/joedanz/brainkit/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/joedanz/brainkit/compare/v0.3.6...v0.4.0
[0.3.6]: https://github.com/joedanz/brainkit/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/joedanz/brainkit/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/joedanz/brainkit/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/joedanz/brainkit/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/joedanz/brainkit/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/joedanz/brainkit/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/joedanz/brainkit/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/joedanz/brainkit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/joedanz/brainkit/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/joedanz/brainkit/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/joedanz/brainkit/releases/tag/v0.1.0
