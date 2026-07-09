# README design — open-source launch

**Date:** 2026-07-09
**Deliverable:** `README.md` at the repo root (currently missing).

## Goals & audience

- Primary audience: engineering teams and CTOs evaluating adoption.
- Register: plain English an average developer can follow — credibility through clarity, not jargon. Formal guarantees live in linked concept docs, not the front door.
- Emphasis (in priority order): security / how knowledge is protected, enterprise-readiness (secure self-hosted deployment), breadth of what's included (Hermes, MCP, dashboard, capture channels).
- Canonical public name: **brainkit** (matches `pyproject.toml`; repo to be renamed `joedanz/brainkit`).

## Structure (approved)

1. **Header** — title, bold tagline ("The self-hosted company brain that keeps private notes private"), one-sentence expansion, static badges (Python 3.12+, license, MCP).
2. **The problem** — stale wiki vs. oversharing auto-sync hook; brainkit as the middle path with a structural guarantee.
3. **How it works** — master vault → filtered per-person copy → personal AI assistant, with a plain-language mermaid diagram (adapted from `website/index.mdx`) including the human-approved sharing queue.
4. **Your knowledge is protected by design** — five bullets: fails closed; client folders deny-by-default; one human-gated sharing door re-checked at publish; git audit trail; randomized trap-note (no-leak property) testing.
5. **Works with the AI tools you already use** — Claude Code (zero-install via compiled `CLAUDE.md`), Hermes Agent (profile + Docker image), any MCP client via `brain mcp`, no vendor lock-in (markdown + git).
6. **Everything in the box** — full aspect list: hardened capture (email/chat/voice/upload), hybrid permission-safe search, live dashboard (user + admin lenses), sharing queue, validated write-back, `brain cycle` automation, `brain doctor` health checks, Obsidian-compatible plain files.
7. **Deploy it securely** — two-box reference deployment (brain box over restricted SSH; agents box with one container per person, mount-as-boundary), per-person backups, runs entirely on own infrastructure, optionally self-hosted embeddings.
8. **Try it** — operator quickstart (install, init, compile), cron loop, per-employee two-liner (`brain index`, `claude mcp add brain`).
9. **The `brain` command** — table of all 12 subcommands (init, ingest, compile, writeback, promotions, cycle, index, search, mcp, status, dashboard, doctor).
10. **Requirements** — Python ≥ 3.12, git; sqlite-vec extension-loading caveat; embeddings optional via any OpenAI-compatible endpoint (self-hostable).
11. **Learn more** — relative links into `website/` docs (getting started, the compiler, spaces & permissions, promotions, retrieval, reference deployment, CLI reference).
12. **Contributing & License** — dev setup with uv + pytest; license section.

## Content sources

All claims trace to repo facts: `website/index.mdx` (diagram, design goals), `website/getting-started.mdx` (commands, prerequisites), `website/reference/cli.mdx` (subcommands), `deploy/` (reference deployment, backups), `tests/` (no-leak property test). No invented capabilities.

## Open items (pre-publication, outside this README)

All resolved 2026-07-09, same branch:

- **License**: MIT `LICENSE` file added (Joe approved).
- **`archive/` removed**: client-specific consulting material deleted from the public tree (history retains it; repo history predates publication).
- **URLs**: the GitHub repo was already named `joedanz/brainkit`; stale `joedanz/brain` URLs in `website/`, `docs/deployments/`, and `tasks/todo.md` were rewritten.
