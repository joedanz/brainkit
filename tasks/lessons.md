# Lessons

## Verify sensitivity before recommending remediation (2026-07-09)

- **Pattern:** I found realistic-looking client material (named client, ROI dollar figures) in `archive/` and inferred it was confidential — then recommended and ran a git history rewrite on that basis.
- **Correction:** It was synthetic sample data from e2e testing. The remediation was harmless here, but the inference was wrong.
- **Rule:** When content merely *looks* sensitive (names, money, credentials-shaped strings), ask Joe whether it's real before recommending destructive remediation (history rewrites, force-pushes, support tickets). Realistic test fixtures are common in this codebase — the e2e suite generates plausible company data by design.

## Writing for humans (2026-07-09)

- **Pattern:** When drafting outward-facing docs (README, website copy), I defaulted to a register aimed at deeply technical readers (threat models, `(commit, person) → vault` notation).
- **Correction:** Joe wants public-facing writing understandable by the *average* developer, who may not be deeply technical. Credibility should come from clarity, not jargon.
- **Rule:** For anything a newcomer reads first (README, landing copy, getting-started), explain concepts in plain English before (or instead of) formal notation. Jargon and formal guarantees belong in linked concept docs, not the front door. Test: would a developer who's never run a server follow the first screenful?
