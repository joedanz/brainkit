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

Running agents server-side instead of on employee devices? Follow the
two-box reference deployment ([deployments/two-box-chat-only.html](deployments/two-box-chat-only.html)):
steps 3–4 below happen inside each person's container at first boot, and the
employee's entire setup is a chat pairing code.

1. Give the employee sync access to `/srv/brain/compiled/<person-id>` ONLY
   (e.g., per-person deploy key or per-person remote). Never to the master.
2. On their machine, clone it and open the folder in Obsidian:

       git clone <their-vault-remote> ~/brain

3. Install the agent profile (Hermes users):

       hermes profile install github.com/<your-org>/company-brain --alias
       # set terminal.cwd to ~/brain in the profile config before first run

   Claude Code users need no install — the vault's CLAUDE.md carries the
   same protocol.

4. Build the local search index and register it as an agent tool:

       brain index --vault ~/brain
       claude mcp add brain -- brain mcp --vault ~/brain

   `brain index` is keyword-only until an embedding provider is configured
   (see reference/configuration — `BRAIN_EMBED_BASE_URL` etc.); set one to add
   semantic search. The `.brain/` index is machine-local and gitignored, so it
   never syncs — each device builds its own.

## 2. Daily flow

- Transcripts and notes reach `People/<you>/Inbox/`; the agent ingests and
  routes them. Drop a file directly, or feed a channel (email/chat/voice/upload)
  that wraps the intake primitive:

      echo "decided X" | brain ingest --master /srv/brain/master --person <you> --source voice

  Services that push (Fathom, Zapier, Composio triggers) can deliver directly:
  declare sources in `_meta/webhook.yaml` and run the signed receiver on the
  server, behind a TLS reverse proxy —

      brain webhook --master /srv/brain/master
- Edits sync back; the server runs one command per interval (cron or
  post-receive hook):

      brain cycle --master /srv/brain/master --out /srv/brain/compiled --json

  It applies every person's writeback (rejections are reported and revert
  on the next compile), sweeps agent drafts into the pending queue, and
  recompiles all vaults. Nonzero exit = at least one rejected writeback.
- Sharing: the agent drafts promotions; approve with
  `brain promotions approve <id> --master ... --approver <you>`.
- Search: after pulling, the agent refreshes its index (`brain index --vault
  ~/brain` — cheap, only changed files re-embed) and queries via the `brain`
  MCP tools. For server-hosted agents, add `--index` to the cycle so indexes
  refresh centrally:

      brain cycle --master /srv/brain/master --out /srv/brain/compiled --index --json

## Deployment rule

Personal agents run on the employee's device, or in a container mounting
only that person's vault. Hermes profiles do not sandbox the filesystem —
never co-host multiple employees' agents uncontained.

The worked example of the container branch of this rule — two boxes,
chat-only users, one hermes container per person, plus the sync plane,
visual surfaces, and a full runbook — is
[deployments/two-box-chat-only.html](deployments/two-box-chat-only.html).
