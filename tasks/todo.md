# Batch: agent backups — backup-agents.sh + README section

Follow-up to Phase 3: per-person agent containers hold irreplaceable state on
their `<person>-state:/opt/data` volume (sessions, memories, pairing, deploy
key). The `/vault` volume is derived (re-clone + reindex) and is deliberately
NOT backed up. Process agreed in conversation:

- `hermes backup` inside each container (SQLite `backup()` API — WAL-safe
  while the gateway runs), copied out via `docker cp`, pruned by age.
- Restore via `hermes import <zip> --force` with the gateway stopped.
- Backup zips are secrets (deploy key, .env, bot token, chat history).

## Tasks

- [x] **A. deploy/agents-box/backup-agents.sh** — loop running `agent-*`
  containers, `hermes backup` (full or `BACKUP_QUICK=1`), `docker cp` out,
  age-based pruning, per-container failure isolation, nonzero exit on any
  failure (cron-alert friendly).
- [x] **B. README "Backups" section** — what to back up and why, cron line,
  restore procedure, secrets caveat.
- [x] **C. Verify** — shellcheck; live run against a container from the
  built `hermes-brain:latest` image; inspect zip contents; import roundtrip.

## Review

Static: shellcheck clean. Live (image `hermes-brain:latest`, container
`agent-backuptest` with `gateway run`): full backup while the gateway ran
(475 files, 10 MB zip); `unzip -l` confirmed config.yaml, state.db, SOUL.md,
skills, and — once seeded — `.env` and `home/.ssh/id_ed25519` (deploy key
travels in the backup). Restore roundtrip: deleted the key, `hermes import
--force`, key back intact; import preserves this machine's gateway.lock /
gateway_state.json. Failure isolation: a second `agent-broken` container
without hermes failed, the good container still backed up, script exited 1
naming the straggler. Prune verified with a 20-day back-dated zip.

Findings worth remembering:
- Bare `docker run` of the image drops into the interactive TUI and exits
  cleanly (no tty) — test containers need `gateway run` like the compose file.
- The docker-exec shim drops only `hermes ...` to the runtime user; a
  `docker exec ... sh -c` runs as root, and hermes-user `import` silently
  skips files under root-owned dirs. Not an issue in the real flow (first
  boot chowns `.ssh` to hermes before any restore).

Test containers, alpine image, and scratch backups removed.
