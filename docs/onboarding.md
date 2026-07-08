# Pilot Onboarding — Company Brain

## 0. One-time company setup (operator)

    brain init /srv/brain/master --company "Acme Co"
    # edit /srv/brain/master/_meta/org.yaml  — add people, roles, teams
    # edit /srv/brain/master/_meta/spaces.yaml — adjust visibility if needed
    brain compile --master /srv/brain/master --out /srv/brain/compiled

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
- Edits sync back; the server runs `brain writeback` per person, then
  `brain promotions sweep` (agent drafts -> pending queue), then
  `brain compile` to refresh everyone.
- Sharing: the agent drafts promotions; approve with
  `brain promotions approve <id> --master ... --approver <you>`.

## Deployment rule

Personal agents run on the employee's device, or in a container mounting
only that person's vault. Hermes profiles do not sandbox the filesystem —
never co-host multiple employees' agents uncontained.
