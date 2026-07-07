# Multi-Tenant Company Brain — Design Spec

**Date:** 2026-07-07
**Status:** Approved design, pre-implementation
**Product:** Reusable multi-tenant AI Chief of Staff memory system on Obsidian-compatible vaults

## 1. Purpose & Goals

A reusable product: every employee gets a private, Obsidian-compatible knowledge vault maintained by an AI Chief of Staff, while the company gets a shared "company brain" that grows from employee knowledge — without private data ever leaking across employees.

Goals:

- **Structural privacy** — an employee's vault physically contains only what they may see. Privacy is never enforced by agent instructions alone.
- **Company brain that feeds itself** — knowledge flows upward from private spaces through a human-approved promotion queue, so the shared brain doesn't starve (the wiki failure mode) and doesn't leak (the auto-sync failure mode).
- **Obsidian-compatible, plain markdown** — the product promise is "your brain is portable files you own."
- **Start small, scale structurally** — pilot with a small pool (~5–25 people); growing to 150+ changes capacity, not architecture.
- **Agent-runtime friendly** — first-class support for Hermes Agent (primary runtime for many users) and Claude Code, without either becoming a dependency.

Non-goals (v1): chat interfaces (round 2), real-time sync, per-note ACLs, enterprise IAM/SOC 2.

## 2. Architecture Overview

Hub-and-spoke, one hub per company:

```
Master vault (git repo, one per company — hard tenant wall)
        │  every master commit
        ▼
Vault compiler  ←— THE security boundary
        │  (master commit, person) → filtered vault
        ▼
Per-person repos → synced to devices (Obsidian Git plugin / sync daemon)
        ▲
        │  write-back service (server-side path validation)
        └— promotion queue (agent-drafted, human-approved)
```

Agents:

- **Server chief-of-staff** (per company) — full master access; ingests transcripts, routes knowledge, maintains Company dashboards. May itself be a Hermes instance with cron.
- **Personal agent** (per person) — runs against that person's *filtered* vault only; maintains their private space; drafts promotion candidates.

## 3. Vault & Spaces Model

Every note lives in exactly **one space**; the space determines visibility.

```
master/
  Company/                # visible to everyone
    Home.md               # entry point / priority dashboard
    Memory.md             # business overview, positioning, team structure
    Decisions/            # decision log with reasoning
    Frameworks/           # SOPs, playbooks, workflows
    Templates/            # reusable formats
  Teams/<team>/           # visible to team members
  People/<person>/        # visible to that person only (+ optional delegate)
    Memory.md  Actions/  Sessions/  Inbox/   # Inbox: transcripts land here
  Clients/<client>/       # company-wide by default; restrictable per client
  _meta/                  # server-only, never compiled into any personal vault
    org.yaml              # people, roles, teams
    spaces.yaml           # space path → read/write rules (roles/people)
    promotions/           # pending/ approved/ rejected/
```

Rules:

- `spaces.yaml` maps each space to `read:` and `write:` lists of roles/people. Adding an employee or team = editing `org.yaml` + compiler rerun; nothing structural.
- Cross-space wikilinks are allowed. In a compiled vault, a link to an invisible note is stubbed (rendered as plain text or a marker note), never resolved.
- Restricted client spaces support confidentiality walls (a client visible to only some people).

## 4. Vault Compiler — the Security Boundary

A single deterministic component: `(master commit, person) → filtered vault`.

- Includes only spaces the person may **read**, plus generated agent context files (§6).
- Runs on every master commit; output committed to that person's per-person repo.
- Stubs dangling cross-space links.
- **Fails closed:** on any error, the previous compiled output stands; a failure can only mean someone temporarily sees *less*, never more.
- Testing: property tests assert no file outside the person's allowed spaces ever appears in output; golden-vault tests per role; link-stub tests.

Nothing downstream (sync, agents, devices) can leak content that was never materialized.

## 5. Sync & Write-Back

- **Down:** per-person repo → device via Obsidian Git plugin (pilot) or a small sync daemon that hides git (later).
- **Up:** a **write-back service** receives each person's commits and validates every changed path against their **write** permissions server-side (client trust never assumed), then applies to master with author attribution. Out-of-scope paths are rejected whole (the commit bounces with a clear message).
- Conflicts: rare by design — each space effectively has one writer (your space; your team's space). Shared-space collisions resolve last-write-wins with full git history as recovery + audit log.

## 6. Agent Context Files (compiled into every vault)

The compiled vault is **self-describing to any agent that opens it**:

- **Root `AGENTS.md`** (≤20K chars — Hermes's load limit): the "brain protocol" — what spaces are, this person's read/write scope, routing rules (actions → `Actions/Action Tracker`, client info → client file, decisions → `Decisions/`, session summaries → `Sessions/`), and how to draft promotions.
- **Per-space `AGENTS.md`** (≤8K chars each — Hermes progressively discovers these as the agent navigates): e.g. `People/<person>/AGENTS.md` ("private space — nothing here is shared without an approved promotion"), `Clients/<x>/AGENTS.md` (client-handling rules).
- **Mirrored `CLAUDE.md`** files with the same content so Claude Code users are equally first-class. (Hermes context priority is `.hermes.md` → `AGENTS.md` → `CLAUDE.md`, first match wins, so emitting both is safe for both runtimes.)
- All generated context files are plain declarative instructions — they must pass Hermes's prompt-injection scan (no imperative override phrasing, no hidden HTML).

## 7. Hermes Integration

Hermes Agent is the expected runtime for many users. Findings from the Hermes docs and their consequences:

1. **Profiles are not a tenant boundary.** Hermes profiles isolate state (config, memory, sessions) but *not* the filesystem — a profile's agent has the OS user's full file access. Therefore the **hard deployment rule**: a personal agent runs only where the tenant wall is physical —
   - on the **employee's own device** against their synced vault (default), or
   - **server-side in a container** (Hermes Docker terminal backend) mounting *only* that person's compiled vault.
   - Never multiple employees' Hermes profiles side-by-side on one uncontained host.
2. **Provisioning = Hermes profile distribution.** A `company-brain` git repo installable via `hermes profile install <repo> --alias`, shipping: the company `SOUL.md` (Hermes reads SOUL.md only from the profile home, never the working directory), `config.yaml` with `terminal.cwd` pinned to the synced vault path, brain skills (ingest / route / draft-promotion), and cron jobs. Credentials, memories, and sessions stay per-machine. Onboarding is one command.
3. **Built-in memory stays tiny and pointed at the vault.** Seed one MEMORY.md entry: "Your knowledge base is the vault at `<path>`; follow its AGENTS.md protocol." The vault is the deep brain; Hermes memory (~2,200 chars) is an anchor, not a store.
4. **External memory providers off by policy.** Honcho/Mem0/etc. would fork the source of truth away from the auditable vault. The provisioning config disables them; the policy is documented in the brain protocol.
5. **Chat (round 2): per-person gateways.** One shared team bot is a single trust domain (shared filesystem, memory, SOUL). Instead, each employee's own Hermes runs its own gateway with its own bot token (per-profile gateways and token-lock collision detection are first-class in Hermes), locked to that person via allowlist/DM pairing. An optional company-wide bot runs as a separate instance whose vault contains only the Company space.
6. **Aligned posture.** Hermes's human-gated writes (`memory.write_approval`, `skills.write_approval`), context-file injection scanning, and dangerous-command approval match this design's draft-and-approve philosophy; provisioning enables the gates.

## 8. Promotion Queue

- The personal agent flags promotable knowledge (a decision, reusable client fact, SOP, lesson) and drafts a **sanitized** note into `_meta/promotions/pending/<id>.md` with: source reference, target space, rendered preview.
- The owner approves via a checkbox in Obsidian or CLI (chat button in round 2). On approval, the write-back service commits the note to the target space with provenance frontmatter (`promoted-by`, `source`, `date`).
- Rejections move to `_meta/promotions/rejected/` with an optional reason — training signal for what this company considers shareable.
- Promotions are the *only* mechanism that moves content from a more-private space to a less-private one.

## 9. Server Chief-of-Staff

- Runs with full master access, on cron and on Inbox drops.
- Transcript pipeline: summarize → extract decisions, action items (owner + deadline), context updates → route to `Actions/Action Tracker`, client files, `Decisions/`, `Sessions/`; general insights → `Company/Memory.md`.
- Maintains `Company/Home.md` as a priority dashboard.
- Failure posture: an agent that fails does nothing; it never routes on low confidence — items it can't place go to a `Needs-Routing` list for a human.

## 10. Scaling Path

- **Company ↔ company:** absolute isolation — separate master repos (and separate server instances if desired).
- **5 → 25 people:** the v1 design as-is; agents read files directly (no search index, Karpathy-wiki style).
- **25 → 150+:** swap per-person repos for object-storage materialization; add a search index when vaults outgrow direct reading; add curator roles per team. The compiler contract `(commit, person) → vault` never changes.

## 11. Error Handling Summary

| Failure | Behavior |
|---|---|
| Compiler error | Fail closed; previous output stands; alert operator |
| Write-back out-of-scope path | Reject whole commit with clear message |
| Agent uncertain where to route | `Needs-Routing` list, human decides |
| Promotion rejected | Recorded with reason; nothing published |
| Sync conflict | One-writer-per-space convention; git history recovery |

## 12. Round Scope

**This round:** master vault structure + `spaces.yaml`/`org.yaml` schemas; compiler with property/golden tests; write-back service; context-file generation (AGENTS.md/CLAUDE.md); server chief-of-staff; personal-agent brain skills; promotion queue; `company-brain` Hermes profile distribution; pilot onboarding docs.

**Round 2:** per-person chat gateways (Slack/Telegram/web); sync daemon replacing the git plugin; company-wide Company-space-only bot; curator roles.

## 13. Open Questions (deferred, non-blocking)

- Hosting shape for the reusable product (per-company VPS vs. multi-company control plane — tenant walls stay per-repo either way).
- Delegate access to a `People/` space (e.g., an EA) — supported by `spaces.yaml` reads, policy to be decided per company.
- Restricted-client-space UX for people who lose access mid-engagement (compiler already handles removal; the notification/communication flow is round-2 design work).
