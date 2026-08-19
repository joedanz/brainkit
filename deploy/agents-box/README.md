# hermes-brain — the agents-box image

Phase 3 of the [two-box reference deployment](https://raw.githack.com/joedanz/brainkit/main/docs/explainers/two-box-chat-only.html):
one Docker image, one container per person, each mounting **only** that
person's compiled vault. The mount is the tenant boundary.

Built on the official `nousresearch/hermes-agent` image (s6-overlay
supervision, all state on `/opt/data`), with three additions:

| Addition | Where | What it does |
| --- | --- | --- |
| **brainkit** | `/opt/brainkit` venv, `brain` on PATH | `brain index / search / mcp` inside the container (installed from `git+https://github.com/joedanz/brainkit` — it is not on PyPI) |
| **company-brain profile** | staged at `/opt/brain-profile` | installed into `/opt/data` on first boot: SOUL.md, `terminal.cwd: /vault`, the brain MCP server, tool-loop hard stops, gateway lifecycle pings off, the brain-protocol skill |
| **vault-sync** | s6-supervised longrun | `git pull → brain index → git push` every 5 minutes (`BRAIN_SYNC_INTERVAL` to change); crash-restarted by s6 like the gateway itself |

## What is in this directory

Everything hermes-specific lives here, and nothing outside it is baked into the
image — brainkit arrives from the public repo at build time.

```text
deploy/agents-box/
├── Dockerfile                  the image
├── build-image.sh              resolve a ref → build → tag :latest and :<sha>
├── docker-compose.yml          one stanza per person
├── add-agent.sh                prints a stanza + the onboarding checklist
├── company-brain-profile/      the hermes profile, staged to /opt/brain-profile
│   ├── SOUL.md                 how the agent should behave
│   ├── config.yaml             terminal.cwd, brain MCP server, disabled skills,
│   │                           gateway lifecycle pings off
│   └── skills/brain-protocol/  how an agent should use a brain
├── scripts/                    s6 hooks: first boot, vault-sync
├── agents-liveness.sh          fleet check → healthchecks.io
└── backup-agents*.sh           nightly state zips, encrypted offsite
```

One caveat worth knowing: `company-brain-profile/skills/brain-protocol/SKILL.md`
is not hermes-specific in *substance*. It describes how any agent should work
with a brain — look it up first, enrich what you read, cite the source — and
that guidance would apply equally to a different runtime. It lives here because
it ships as a hermes skill file, and that file is currently the only written
statement of it. If brainkit ever grows a second deployment, this is the piece
to lift out into runtime-neutral docs first.

## Build

```bash
# preferred — resolves the tip of main to a commit and tags :<short-sha>
deploy/agents-box/build-image.sh

# a specific commit, tag, or branch
REF=v0.2.0 deploy/agents-box/build-image.sh

# or let compose do it (defaults to the tip of main)
cd deploy/agents-box && docker compose up -d --build
```

**Where brainkit comes from.** The image installs it from the public repo
(`git+https://github.com/joedanz/brainkit@<ref>`), not from the working tree.
That makes the container traceable to a commit anyone can fetch, and it means
the image describes itself: a VCS install records the resolved commit in
`direct_url.json` (PEP 610), which is where `brain --version` reads it from.

```bash
docker exec agent-alice brain --version    # brain 0.2.0 (rev 10ea7eb…)
docker inspect --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}' hermes-brain:latest
```

The label is a convenience for inspecting an image without running it;
`brain --version` is the authoritative answer, since it reports what pip
actually installed rather than what the build was asked for.

**Local work is not in the image.** Only pushed commits can be, by
construction. `build-image.sh` prints a note when your local `HEAD` differs
from the ref it is building, so the surprise happens at build time rather than
on the box.

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

## Rolling a new image

The profile is installed once and then **re-synced whenever the image's brainkit
build changes**. The sentinel at `/opt/data/.company-brain-installed` records
which build was applied (`brain --version`, i.e. the whole string — a release
install has no revision to report, so keying on the revision alone would stop
detecting anything the moment this image installs from PyPI), so the container
can tell "already current" from "never updated".

What re-syncs is deliberately narrow:

| | on a new build | why |
|---|---|---|
| `skills/` | refreshed, except what the company's own skills repo names | additive, and the image is the source of truth — unless a company skills repo is mounted and names the same skill, in which case the image defers (see below) |
| `SOUL.md` | only if untouched since we wrote it | the hash we shipped is recorded; equal means safe to advance, different means someone meant it, so it is left alone and the refusal logged |
| `config.yaml` | never regenerated; one key healed | hermes rewrites it at runtime — model, `_config_version`, plugins, approvals. Regenerating it would discard live settings, including the model an operator chose |

### Handing a skill to a company skills repo

Mount a read-only git repo of company skills at `/opt/company-skills` (override
with `COMPANY_SKILLS`), laid out one `<skill>/SKILL.md` or
`<category>/<skill>/SKILL.md` per directory, and pull it on a timer. Any skill
it names, the image stops seeding — including `brain-protocol`.

That inversion is the point. Skills otherwise refresh from the image, so a
skill the image ships wins forever: every image roll re-copies its version into
`/opt/data/skills`, where it shadows the company's, and a push to the skills
repo would silently never take effect. Deferring turns a protocol change from
"pull, rebuild, recreate every container" into one push that lands on the next
timer — no restart, no dropped conversation.

The migration is automatic and conservative. A copy already in `/opt/data/skills`
is removed only when it is byte-identical to the image's, which is what
identifies a copy this hook wrote and nobody has edited. Anything else is
somebody's own work: it stays, and it goes on shadowing the company's copy,
which is the documented behaviour for a locally-authored skill.

Provisioning it is one command, and it is the intended path — `brain-protocol`
is brainkit's skill, so it is deployed from brainkit rather than hand-maintained
in the company's repo:

```sh
./install-brain-skill.sh [/opt/company-skills]   # then recreate the containers once
```

It copies the skill in and adds `brain-protocol/` to the checkout's
`.git/info/exclude`. Provisioned, not committed: the pull job runs `fetch` and
`reset --hard` and never `git clean`, so an untracked directory survives every
pull, and excluding it keeps `git status` clean so nobody reads it as a stray
edit. Ownership stays honest — the file belongs to brainkit and is
re-provisioned from brainkit, while the repo stays the company's own. Re-run it
after every brainkit upgrade on the box.

Keeping the repo's copy of `brain-protocol` current is then a sync step, not a
guess — the file in `company-brain-profile/skills/brain-protocol/SKILL.md` stays
the source of truth, and a deployment copies it across when it changes.

The single exception is `<platform>.gateway_restart_notification`, set to `false`
**only when unset**, on every boot, via `hermes config set` — a real YAML merge,
never an append. It is an exception because the alternative is worse than the
rule: the template default only ever reaches a new volume, so every agent that
already exists would keep mailing its user "Gateway shutting down" on every image
roll, forever. Setting it only when unset keeps the rule's spirit — an operator
who turns pings back on is never overridden.

So a person's edits to their own SOUL survive an upgrade, and so does their
model. To hand the image's SOUL back, delete `/opt/data/SOUL.md` and restart:
an absent file is not a local edit, so it is restored on the next boot — which
also means a backup restored without one heals itself.

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
| `BRAIN_EMBED_API_KEY` | no | bearer key for the embedding endpoint |
| `BRAIN_EMBED_MODEL` | no | embedding model (default `text-embedding-3-small`) |
| `BRAIN_EMBED_DIM` | no | vector dimension (default 512) — match what the endpoint really returns |
| `BRAIN_SYNC_INTERVAL` | no | sync period in seconds (default 300) |

\* or another chat platform's token — see the hermes gateway docs.

## Fleet liveness monitoring

`agents-liveness.sh` pings a [healthchecks.io](https://healthchecks.io) check
every cron tick: success while every compose service has a running container,
`/fail` with the missing names as the ping body otherwise. One check (5-minute
period) covers the whole box — missed pings mean the box or cron died, an
active `/fail` names the dead container — and the expected list is read from
`docker-compose.yml`, so adding an agent needs no monitoring change. Install
to `/usr/local/sbin/` and add the cron line from the script header.

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

### Offsite (R2)

`backup-agents.sh` keeps 14 days of zips on the box; [`backup-agents-offsite.sh`](backup-agents-offsite.sh)
ships them to Cloudflare R2 encrypted (restic). Provision the bucket once with
[`../backup/provision-r2.sh`](../backup/provision-r2.sh), install this script
to `/usr/local/sbin/`, and cron it after the local job (03:45). See
[`../backup/README.md`](../backup/README.md).

## Failure modes

| Symptom | Cause / fix |
| --- | --- |
| `clone failed` repeating in logs | deploy key not yet in `authorized_keys`, or the person isn't compiled yet (`org.yaml` + next cycle). Key is in `docker logs` and `/opt/data/deploy-key.pub`. |
| `push refused` occasionally | the push raced a `brain cycle` (`updateInstead` refuses a dirty worktree) — self-heals next interval. If it repeats forever: `receive.denyCurrentBranch updateInstead` was never set on that repo. |
| edits vanish after sync | working as designed — the cycle rejected an illegal write-back and the compile reverted it (fail closed). |
| agent chats but knows nothing | vault not cloned yet (see clone failure above) or index missing — run `docker exec agent-alice vault-sync`. |
| agent says "saved" but the vault never changes | `write_file` denied by `HERMES_WRITE_SAFE_ROOT` (the hermes base image pins it to `/opt/data`; this image extends it with `/vault` — don't override it without keeping both paths). The gateway reply looks like success; only the file-mutation verifier footer reveals the denial. |
| `Write denied: '/tmp/…' is outside HERMES_WRITE_SAFE_ROOT` | working as designed — `/tmp` is not in the guard. Scratch belongs in `/opt/data/.cache/tmp`, which is `TMPDIR` (Dockerfile) and is named in the managed block `03-brain-first-boot` keeps at the end of SOUL.md. An agent hitting this on a *fresh* container has neither yet — check the boot log for `SOUL.md scratch block applied`. |

## Scratch files

Agents write throwaway files — probe scripts, HAR captures, downloads — to
`/opt/data/.cache/tmp`. Two mechanisms put them there, and both are needed:

- `TMPDIR` (Dockerfile) covers everything that asks the OS for a temp path:
  `tempfile`, subprocesses, shell redirection.
- A marked block at the end of `SOUL.md`, re-applied on every boot by
  `03-brain-first-boot`, covers the model choosing `/tmp` on its own —
  `write_file` never consults `TMPDIR`.

`.cache/` and not `/opt/data/tmp` because `hermes backup` walks the volume into
the nightly zip and skips `.cache` by name. A captured HAR carries auth headers
and session cookies; those zips go to R2.

The block is invisible to the profile re-sync: the sentinel's `soul_md5`
records SOUL.md with the block stripped, so "has a human edited this" keeps
answering correctly and image SOUL updates keep flowing. Delete the block by
hand and the next boot puts it back.

`/tmp` was wiped by every restart; this directory is on the volume and is not,
so the `tmp-reaper` s6 service removes anything nothing has touched in 7 days
(`SCRATCH_REAP_DAYS`, `SCRATCH_REAP_INTERVAL`). It judges each **top-level
entry** by its newest descendant, so a cache still in use survives whole rather
than being hollowed out one old file at a time. It refuses to run against a
TMPDIR outside `/opt/data`, and drops to `hermes` before deleting anything.

## What this image deliberately does not do

- **No published ports.** Telegram/WhatsApp long-poll outward; the vault syncs
  outward over ssh. The visual layer (hermes dashboard on 9119, brain dashboard)
  is Phase 6 — private-network-only first (tailnet or equivalent), then an SSO proxy.
- **No master access, no brain credentials.** The only secret that touches the
  brain system is one ssh key that can reach one repo.
- **One person per container.** Never mount a second vault; never point two
  containers at one `/opt/data` volume.
