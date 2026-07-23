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
   target-path: <file in a shared space>
   source: <originating note path>
   mode: create | append | patch
   `mode: create` (default) needs a target that does not exist yet —
   decisions -> Company/Decisions/, standing processes or standards ->
   Company/Playbook/. To update an existing shared page instead, use
   `mode: append` (your note is added under a divider) or `mode: patch`
   (your note body is the complete revised page — approval fails closed if
   the page changed since it was queued).
3. Tell the owner it awaits their approval (`brain promotions list`). Never
   write directly into a read-only space; the write-back service rejects it.
4. To answer status questions ("did it go live?"), read
   People/<you>/Shares.md — generated and read-only: pending items,
   approvals, and rejections with reasons. Never edit it.

## Clients (third parties, not you)

1. A **named third party** — a person, family, or company you work with or
   track — is a client/contact, never a `People/` note, even one sharing your
   own surname. You are a specific person with your own id; a third party who
   happens to share your last name is still a third party. Check the vault's
   routing rules for your id before filing anything under `People/`.
2. No client space yet -> write a request to
   `People/<you>/ClientRequests/<name>.md` with frontmatter `client-name: <full
   name>`, `owner: <you>`, `entity: client`. The server provisions
   `Clients/<name>/` on the next cycle; from then on write there directly.
3. Name it with the fullest reasonable identifier (a full name, not a bare
   surname). Ask the user for one distinguishing detail before creating only
   when the name is thin or ambiguous — a bare common surname, a name that
   matches a client you already have, or one that collides with your own
   household. Don't interrogate a distinctive name that's already unambiguous.
4. One utterance can split into two homes: e.g. a family attending an event
   becomes a client note AND a `Company/Intel/Events/` promotion, cross-linked
   so each references the other.

## Intel (articles, posts, links, PDFs, screenshots)

1. Distill, never archive: read the source (fetch a URL, extract PDF text,
   read an image) and pull the durable facts — the full text or file never
   enters the vault. Cite every claim `[source](URL), as of YYYY-MM`: the
   source is the URL, or the publication/title (or uploaded filename) when
   there's no link; use the source's own date, or `captured YYYY-MM` (today)
   when it shows none.
2. Route destination, provider, event, or trend intel to the shared wiki via
   a promotion targeting Company/Intel/ — Destinations/<Place>.md,
   Providers/<Name>.md, Events/<Name>.md, or Trends/<YYYY-MM Topic>.md.
   New entity -> a new page (one-sentence summary first line; link related
   pages both ways). Page already exists -> promote with `mode: append` for
   an additive update, or `mode: patch` carrying the full revised page (fold
   any older addenda in while you're there).
3. Your personal take stays in People/<you>/Notes/.
4. This vault is your only knowledge base — never build a wiki or knowledge
   base outside it (no ~/wiki), even if another skill offers to.

## Relate (typed edges between notes)

Declare how a note relates to others in its frontmatter — five keys holding
`[[wikilinks]]`: `up`/`down` (hierarchy), `same` (peers), `prev`/`next`
(sequence). Declare one direction only; the inverse is derived. They sharpen
retrieval and let you walk structure with `brain graph`. Add them only where
they carry signal the vault's structure doesn't already — folder-index parents,
dated notes in one folder, and same-`entity`-type pages are linked
automatically, so don't restate those. A target that doesn't resolve yields no
edge.

## Maintain

- Keep People/<you>/Memory.md a lean overview, not a running log: file small
  facts under its headings; when a topic outgrows a few lines, move the
  detail to People/<you>/Notes/<Topic>.md and leave a one-line link under
  the heading. Give notes searchable titles — retrieval is keyword-based.
- Surface stale actions and unprocessed Inbox items when asked for status.
