# Batch: promotion + privacy integrity fixes

Investigated from the luxury-travel-agency e2e demo. Scope refined after reading
the code: the original "rule-path typo" check is already covered by
`doctor._check_rule_paths`, so it's dropped.

## Tasks

- [x] **A. Sweep resurrection fix** (`src/brain/promotions.py`)
  - `sweep()` dedups only against `pending/`, so an already **approved** or
    **rejected** promotion gets re-queued on the next `cycle` (its draft is
    written back from the person's vault, then re-swept).
  - Fix: skip a draft whose id already exists in pending/ OR approved/ OR
    rejected/. Unlink the resolved leftover draft so it stops lingering.
  - Tests: approve->re-draft->sweep (not re-queued, draft cleaned); reject variant.

- [x] **B. Orphan loose-file doctor check** (`src/brain/doctor.py`)
  - A `.md` directly under `Teams/`/`People/`/`Clients/` (not in a subfolder)
    belongs to no space and compiles into NO vault — vanishes silently.
  - Fix: new `_check_orphan_files` -> warn, check "orphan-files".

- [x] **C. Cross-space reference privacy check** (`src/brain/doctor.py`)
  - A wikilink in space S resolving to a file in space T where some reader of S
    cannot read T = the name leaks even though the file never crosses.
  - Fix: new `_check_cross_space_refs(master, org, rules)` -> warn, "cross-refs".
    Master-wide stem map, resolve links, compare reader sets, dedupe per
    (source, target-space). Plain-text mentions out of scope.

- [x] **D. Deny-by-default scaffold + docs** (`templates.py`, docs, e2e)
  - Scaffold `Clients/*` default -> `read/write: ["role:admin"]` + grant pattern.
  - Decouple `test_e2e_lifecycle._populate` with its own explicit spaces.yaml.
  - Docs: `spaces-and-permissions.mdx` per-client rules + keep-names-out rule.

## Review

All four shipped; full suite **218 passed**. Verified live on the travel demo:
approve → double `cycle` leaves the queue empty and clears the stale draft
(resurrection fixed); `doctor` now emits 22 real `cross-refs` warnings and the
`orphan-files` check; fresh `brain init` is deny-by-default for Clients.

Refinement made during implementation: `cross-refs` exempts `People/*` sources —
a personal space has a single reader (its owner), so a name there can't leak to
anyone else. Without this, every private note referencing a restricted doc
produced an "owner cannot see it" false positive (24 → 22 findings on the demo).

Files: `promotions.py` (sweep + `_resolved_ids`), `doctor.py` (`_check_orphan_files`,
`_check_cross_space_refs`, `_content_files`, `_resolve_target`), `templates.py`
(scaffold default), `spaces-and-permissions.mdx` + `cli.mdx` (docs),
`test_promotions.py` +2, `test_doctor.py` +2, `test_e2e_lifecycle.py` (explicit spaces).

Follow-up (shipped separately): `plain-ref` doctor check — scans shared prose for
restricted **space names** (proper-noun/capitalized only, to bound false
positives), same reader-set comparison as cross-refs. Space-names-only + warn
were Joe's calls. Caught 4 real leaks on the travel demo unprompted. Residual
gap: names that aren't folder names (nicknames, note titles) still can't be
matched structurally.
