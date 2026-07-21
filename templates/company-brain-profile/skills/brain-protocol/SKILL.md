---
name: brain-protocol
description: Operate the company brain vault — ingest, route, and promote knowledge. Use whenever processing new information (transcripts, meeting notes, emails, decisions) or when asked to share knowledge with the team.
---

# Brain Protocol

Your working directory is a compiled brain vault. The root AGENTS.md defines
your spaces and permissions — read it before acting.

## Ingest (new transcript or note in People/<you>/Inbox/)

1. Summarize the content.
2. Extract: decisions; action items (owner + deadline); context updates.
3. Route per the vault's routing rules (actions -> Actions/Tracker.md,
   summary -> Sessions/, durable facts -> Memory.md).
4. Archive the processed item into Sessions/. Unplaceable items go to
   Needs-Routing.md — never guess.

## Promote (share knowledge with the team)

1. Draft a sanitized note containing only what is being shared.
2. Save under People/<you>/Promotions/ with frontmatter:
   target-path: <new file in a shared space>
   source: <originating note path>
   Pick a target that does not exist yet: decisions -> Company/Decisions/,
   standing processes or standards -> Company/Playbook/. Never target an
   existing note (Memory.md included) — approval fails on it.
3. Tell the owner it awaits their approval (`brain promotions list`). Never
   write directly into a read-only space; the write-back service rejects it.
4. To answer status questions ("did it go live?"), read
   People/<you>/Shares.md — generated and read-only: pending items,
   approvals, and rejections with reasons. Never edit it.

## Maintain

- Keep People/<you>/Memory.md a lean overview, not a running log: file small
  facts under its headings; when a topic outgrows a few lines, move the
  detail to People/<you>/Notes/<Topic>.md and leave a one-line link under
  the heading. Give notes searchable titles — retrieval is keyword-based.
- Surface stale actions and unprocessed Inbox items when asked for status.
