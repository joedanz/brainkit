# Batch: full doc-sync review — artifact, docs/, website/

Post-PR-#18/19/20 audit: four parallel readers (two-box HTML runbook, website
guides, website core pages, docs/ misc) checked every operational claim
against the shipped code; the published artifact ("Company Brain — How It
Works") reviewed directly.

## Verdicts

- **Artifact** — accurate at its abstraction level; no deployment claims to
  go stale. No change.
- **docs/onboarding.md** — accurate; complements add-agent.sh, no divergence.
- **docs/superpowers/** — frozen specs/plans; point-in-time by design
  (embedded pre-cycle onboarding copy in the company-brain plan is historical,
  left as is).
- **website concepts/, reference/, guides (3 of 4), docs.json** — accurate;
  all 12 CLI subcommands documented, none phantom; MCP tool set correct.

## Fixed (10 issues)

- [x] two-box HTML: `uv tool install brainkit` → `git+https://github.com/joedanz/brainkit`
  (brainkit is not on PyPI; install would fail).
- [x] two-box HTML: 5× "cron: pull → index → push" / "her container's cron" /
  "Bob's cron" → "sync loop" (the agent-side sync is an s6-supervised
  longrun, not cron — doc's own Phase 3 prose already said so).
- [x] two-box HTML: restore drill now says `hermes import <zip> --force` +
  `docker restart` (restart re-fixes key modes; without --force a fresh-boot
  container refuses the overwrite).
- [x] reference-deployment.mdx: same `uv tool install brainkit` fix (missed
  by the auditor, caught by grep sweep).
- [x] reference-deployment.mdx: `brain promotions approve` was missing the
  REQUIRED `--master` flag — command failed as written.
- [x] reference-deployment.mdx: same restore-drill --force/restart fix.
- [x] reference/cli.mdx: synopsis omitted `status` and `dashboard` (both
  documented in the same file).
- [x] getting-started.mdx: same stale subcommand enumeration.
- [x] concepts/promotions.mdx: broken anchor `/reference/cli#promotions` →
  `#brain-promotions`.
- [x] website/README.md: guides list missing `reference-deployment`.

## Review

Grep sweep clean (no remaining `uv tool install brainkit`, `cron: pull`,
`cli#promotions`); website build green. Changes uncommitted, awaiting PR.
