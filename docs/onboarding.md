# Pilot Onboarding — Company Brain

## 0. One-time company setup (operator)

    brain init /srv/brain/master --company "Acme Co"
    # edit /srv/brain/master/_meta/org.yaml  — add people, roles, teams
    # edit /srv/brain/master/_meta/spaces.yaml — adjust visibility if needed
    brain compile --master /srv/brain/master --out /srv/brain/compiled
    brain doctor --master /srv/brain/master --out /srv/brain/compiled
    # run after setup and from cron; nonzero exit = integrity error

Re-run `brain compile` on every master change (cron or a git post-commit hook).

## 1. Per-employee setup

1. Give the employee sync access to `/srv/brain/compiled/<person-id>` ONLY
   (e.g., per-person deploy key or per-person remote). Never to the master.
2. On their machine, clone it and open the folder in Obsidian:

       git clone <their-vault-remote> ~/brain

3. Install the agent profile (Hermes users):

       hermes profile install github.com/<your-org>/company-brain --alias
       # set terminal.cwd to ~/brain in the profile config before first run

   Claude Code users need no install — the vault's CLAUDE.md carries the
   same protocol.

## 2. Daily flow

- Transcripts and notes drop into `People/<you>/Inbox/`; the agent ingests
  and routes them.
- Edits sync back; the server runs one command per interval (cron or
  post-receive hook):

      brain cycle --master /srv/brain/master --out /srv/brain/compiled --json

  It applies every person's writeback (rejections are reported and revert
  on the next compile), sweeps agent drafts into the pending queue, and
  recompiles all vaults. Nonzero exit = at least one rejected writeback.
- Sharing: the agent drafts promotions; approve with
  `brain promotions approve <id> --master ... --approver <you>`.

## Deployment rule

Personal agents run on the employee's device, or in a container mounting
only that person's vault. Hermes profiles do not sandbox the filesystem —
never co-host multiple employees' agents uncontained.
