# TODO — brain cycle + brain doctor

Plan: `docs/superpowers/plans/2026-07-07-cycle-and-doctor.md` (full TDD task breakdown)

- [ ] Task 1: `src/brain/cycle.py` — `run_cycle` (writeback all → sweep → compile-all; rejection isolation; skip vaults without manifest)
- [ ] Task 2: CLI `brain cycle --master --out [--json]` + update `docs/onboarding.md` daily flow
- [ ] Task 3: `src/brain/doctor.py` — `Finding` model + meta/subjects/rule-paths/space-coverage checks
- [ ] Task 4: doctor — symlink check + compiled-vault checks (tombstones, `_meta` leak = security error, drift = info)
- [ ] Task 5: doctor — promotion-queue checks (malformed pending, stuck drafts sweep will never move)
- [ ] Task 6: CLI `brain doctor --master [--out] [--json]`, exit 1 on any error finding

## Review

(fill in after implementation)
