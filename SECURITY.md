# Security Policy

brainkit's core promise is structural privacy: a person's compiled vault must never
contain a note they aren't allowed to read. Reports that break that promise are
treated as the highest severity.

## Reporting a vulnerability

Please report vulnerabilities privately — do not open a public issue.

- **Preferred:** GitHub's private vulnerability reporting — use *Security →
  Report a vulnerability* on this repository.
- **Alternative:** email joe@ticc.net with a description and reproduction steps.

You'll get an acknowledgement within a few days. Please allow time for a fix
before disclosing publicly.

## What counts as a vulnerability here

In rough priority order:

1. **Cross-vault leakage** — any way a compiled vault, search index, dashboard,
   or MCP response can expose content from a space the person cannot read
   (including via symlinks, path traversal, link stubs, or crash states).
2. **Write-scope bypass** — a write-back or ingest path accepting a change
   outside the sender's allowed spaces, or a promotion publishing without human
   approval.
3. **Intake spoofing** — getting a note filed as someone else, or frontmatter/
   path injection through a capture channel.
4. **Dashboard issues** — CSRF, XSS, or any action reachable without the
   guarded primitives.

Out of scope: vulnerabilities in your embedding provider, agent runtime
(Claude Code, Hermes), or deployment infrastructure — though we're happy to
document mitigations.

## Supported versions

brainkit is pre-1.0; only the latest release (and `main`) receives security
fixes.
