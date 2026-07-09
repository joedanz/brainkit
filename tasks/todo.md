# Batch: Phase 3 — hermes-brain image + compose (agents box)

Implements Phase 3 of the two-box runbook (docs/deployments/two-box-chat-only.html,
website/guides/reference-deployment.mdx): one Docker image for every per-person
agent container, plus the compose file and onboarding helper.

Design decisions (from research):
- Base on the official `nousresearch/hermes-agent` image (s6-overlay PID 1,
  `/opt/data` state volume, supervised gateway) rather than hand-rolling the
  hermes install. Derived-image pattern is documented upstream.
- brainkit is NOT on PyPI — bake it from this repo's source (build context =
  repo root), in its own venv so the hermes venv is untouched.
- Vault sync = s6 `services.d` longrun (pull → `brain index` → push), NOT
  hermes cron (that schedules LLM prompts, not shell jobs).
- Deploy key is generated INSIDE the container on first boot; pubkey printed
  to `docker logs` + saved at `/opt/data/deploy-key.pub`. Private key never
  leaves the container. Clone retries until the operator authorizes the key.
- Gap found: `compiler.py:200` git-inits compiled vaults but never sets
  `receive.denyCurrentBranch` — agent pushes would bounce. Onboarding
  checklist must run `git config receive.denyCurrentBranch updateInstead`.
- Doc fix needed: merged docs say `alice-state:/root/.hermes`; the official
  image's state volume is `/opt/data`.

## Tasks

- [x] **A. deploy/agents-box/Dockerfile** — FROM nousresearch/hermes-agent,
  brainkit venv via uv, company-brain profile staged at /opt/brain-profile,
  cont-init + services.d scripts installed.
- [x] **B. First boot script** (`scripts/03-brain-first-boot`) — install
  profile (SOUL.md, config.yaml with terminal.cwd=/vault + brain MCP server +
  tool-loop hard stops, skills), mint deploy key, print pubkey banner, chown.
- [x] **C. Sync scripts** (`scripts/vault-sync`, `scripts/vault-sync-run`) —
  single pass (clone-or: commit strays, pull -X theirs, index, push) + s6 loop.
- [x] **D. docker-compose.yml + .env.example** — x-agent-base anchor, one
  example person, named volumes `<p>-state:/opt/data`, `<p>-vault:/vault`.
- [x] **E. add-agent.sh** — prints per-person compose stanza + full operator
  checklist (org.yaml, BotFather, deploy-key authorization line, updateInstead).
- [x] **F. deploy/brain-box/brain-serve-repo** — forced-command wrapper: one
  key → one repo (upload-pack/receive-pack only).
- [x] **G. README.md** — build, first-boot flow, verification, failure modes.
- [x] **H. Doc corrections** — reference-deployment.mdx + two-box HTML:
  `/opt/data` volume path, point Phase 3/4 at deploy/agents-box/.
- [x] **I. Verify** — see review.

## Review

Static: shellcheck clean (5 scripts); compose + generated config.yaml parse.
Live (Docker Desktop, image built at 3.96 GB): booted a container against a
REAL compiled vault (`brain init` + `brain compile` fixture) and verified the
full life-of-a-note loop — first boot installs profile + mints deploy key
(banner in `docker logs`), supervised vault-sync clones + indexes, an
agent-written Inbox note auto-commits and pushes (updateInstead), `brain
cycle` applies it (`applied: 1`), the compile commit pulls back, the index
refreshes, and `brain search` finds the note. Container restart preserved
everything (s6 reconciler). Website build + 265 pytest green.

Bugs caught by testing:
- first-boot chowned /vault before ensuring it exists → `mkdir -p` added.
- (fixture-only) bind-mounting a compiled vault breaks on compile's two-phase
  directory swap — irrelevant over SSH, which re-resolves paths per connection.

Known trade-offs: config.yaml written by first boot is schema-migrated by the
stock hook on the NEXT boot (harmless — gateway migrates at startup too);
sync conflict policy is `-X theirs` (brain box is arbiter).
