# hermes-brain — the agents-box image

Phase 3 of the [two-box reference deployment](https://raw.githack.com/joedanz/brainkit/main/docs/explainers/two-box-chat-only.html):
one Docker image, one container per person, each mounting **only** that
person's compiled vault. The mount is the tenant boundary.

Built on the official `nousresearch/hermes-agent` image (s6-overlay
supervision, all state on `/opt/data`), with three additions:

| Addition | Where | What it does |
| --- | --- | --- |
| **brainkit** | `/opt/brainkit` venv, `brain` on PATH | `brain index / search / mcp` inside the container (built from this repo's source — brainkit is not on PyPI, so the build context is the repo root) |
| **company-brain profile** | staged at `/opt/brain-profile` | installed into `/opt/data` on first boot: SOUL.md, `terminal.cwd: /vault`, the brain MCP server, tool-loop hard stops, the brain-protocol skill |
| **vault-sync** | s6-supervised longrun | `git pull → brain index → git push` every 5 minutes (`BRAIN_SYNC_INTERVAL` to change); crash-restarted by s6 like the gateway itself |

## Build

```bash
# from the repo root — the build bakes brainkit from src/
docker build -f deploy/agents-box/Dockerfile -t hermes-brain:latest .

# or let compose do it
cd deploy/agents-box && docker compose up -d --build
```

## First boot, step by step

1. s6's stock init seeds hermes defaults on the `/opt/data` volume.
2. `03-brain-first-boot` (this image) installs the company-brain profile,
   generates an ed25519 deploy key at `/opt/data/home/.ssh/`, and prints the
   public key to `docker logs`. **The private key never leaves the container.**
3. The supervised gateway starts — the person can already chat (pairing code),
   though the agent has no vault yet.
4. `vault-sync` retries `git clone $BRAIN_GIT_REMOTE /vault` every 30s. The
   moment you authorize the key on the brain box, the clone lands, `brain
   index` builds the search index, and the agent is fully live.

Every step is idempotent; `docker compose up -d --force-recreate` is always safe.

## Adding a person

```bash
./add-agent.sh carol            # prints her compose stanza + the full checklist
```

The checklist covers the brain-box half: `org.yaml`, `receive.denyCurrentBranch
updateInstead` on her compiled repo (pushes bounce without it), and the
one-line `authorized_keys` entry using [`deploy/brain-box/brain-serve-repo`](../brain-box/brain-serve-repo)
so her key can sync exactly one repo and run nothing else.

## Per-container environment

| Variable | Required | Meaning |
| --- | --- | --- |
| `BRAIN_PERSON` | yes | person id from `org.yaml`; unset = plain hermes container |
| `BRAIN_GIT_REMOTE` | yes | `brain-sync@brain-box:/srv/brain/compiled/<person>` |
| `TELEGRAM_BOT_TOKEN` | yes* | per-person bot; presence alone enables the platform |
| `ANTHROPIC_API_KEY` | yes | shared LLM key (any hermes-supported provider var works) |
| `BRAIN_EMBED_BASE_URL` | no | embedding endpoint → semantic search in `brain index` |
| `BRAIN_SYNC_INTERVAL` | no | sync period in seconds (default 300) |

\* or another chat platform's token — see the hermes gateway docs.

## Verifying a running container

```bash
docker logs agent-alice                       # gateway + first-boot banner
docker exec agent-alice vault-sync            # force one sync pass, watch it
docker exec agent-alice brain status --vault /vault
docker exec agent-alice hermes gateway status
```

## Backups

Only the **state volume** (`<person>-state:/opt/data`) is irreplaceable — it
holds sessions, memories, Telegram pairing approvals, `.env`, and the deploy
key (`home/.ssh/id_ed25519`). The `/vault` volume is derived data: lose it and
vault-sync re-clones from the brain box and `brain index` rebuilds. The image
rebuilds from this repo. (The knowledge itself lives on the brain box —
back up `/srv/brain` as its own job.)

Nightly, from **host cron** (hermes cron schedules LLM prompts, not shell jobs):

```bash
15 3 * * * /opt/brain/deploy/agents-box/backup-agents.sh /srv/backups/agents
```

[`backup-agents.sh`](backup-agents.sh) runs `hermes backup` inside every
running `agent-*` container — SQLite's `backup()` API makes the snapshot
consistent while the gateway is running (a raw tar of the live volume is
not) — copies the zip out, and prunes by age (`BACKUP_RETENTION_DAYS`,
default 14; `BACKUP_QUICK=1` for fast state-only snapshots).

**Restore** (dead container / new box):

```bash
docker compose up -d agent-alice                 # fresh volumes, first boot runs
docker cp alice.zip agent-alice:/tmp/restore.zip
docker exec agent-alice hermes import /tmp/restore.zip --force
docker restart agent-alice
```

Nothing else: the deploy key is inside the zip, so the container reconnects
to the brain box without re-authorizing a new key, pairing survives, and
vault-sync re-clones `/vault` on its own.

Two rules: **backup zips are secrets** (deploy key, `.env`, bot token, full
chat history — encrypt off-box, e.g. restic/borg), and **never restore one
person's zip into another person's container**.

## Failure modes

| Symptom | Cause / fix |
| --- | --- |
| `clone failed` repeating in logs | deploy key not yet in `authorized_keys`, or the person isn't compiled yet (`org.yaml` + next cycle). Key is in `docker logs` and `/opt/data/deploy-key.pub`. |
| `push refused` occasionally | the push raced a `brain cycle` (`updateInstead` refuses a dirty worktree) — self-heals next interval. If it repeats forever: `receive.denyCurrentBranch updateInstead` was never set on that repo. |
| edits vanish after sync | working as designed — the cycle rejected an illegal write-back and the compile reverted it (fail closed). |
| agent chats but knows nothing | vault not cloned yet (see clone failure above) or index missing — run `docker exec agent-alice vault-sync`. |
| agent says "saved" but the vault never changes | `write_file` denied by `HERMES_WRITE_SAFE_ROOT` (the hermes base image pins it to `/opt/data`; this image extends it with `/vault` — don't override it without keeping both paths). The gateway reply looks like success; only the file-mutation verifier footer reveals the denial. |

## What this image deliberately does not do

- **No published ports.** Telegram/WhatsApp long-poll outward; the vault syncs
  outward over ssh. The visual layer (hermes dashboard on 9119, brain dashboard)
  is Phase 6 — private-network-only first (tailnet or equivalent), then an SSO proxy.
- **No master access, no brain credentials.** The only secret that touches the
  brain system is one ssh key that can reach one repo.
- **One person per container.** Never mount a second vault; never point two
  containers at one `/opt/data` volume.
