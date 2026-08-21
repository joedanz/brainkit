---
name: brain-protocol
description: Operate the company brain vault — ingest, route, and promote knowledge. Use whenever processing new information (transcripts, meeting notes, emails, decisions) or when asked to share knowledge with the team.
---

# Brain Protocol

Your working directory is a compiled brain vault. The root AGENTS.md defines
your spaces and permissions — read it before acting.

## Admit (before anything is written)

Routing decides where a fact goes; admission decides whether it goes anywhere.
The vault's AGENTS.md carries the authoritative tests — a candidate is
recorded only if all four hold:

1. **Durable** — it outlasts the conversation, not passing status. Something
   with an end date still counts.
2. **Relevant** — changes how we work: with a third party, with a colleague,
   or as a company. A fact about a person that changes nothing about working
   with them is not recorded, however true it is.
3. **New** — search first; an existing page is updated, not restated.
4. **Attributable** — you can say where it came from. Recording it as a claim
   ("X believes Y") is for a position someone took on the work, not a way to
   keep a rumour about a person.

Not recording is the ordinary outcome. When something fails these tests, use
it to answer well and write nothing.

Attribution runs the other way too: a reply to a human that drew on vault
notes ends with a `Sources:` line linking each note you read. The link
format and the dashboard URL are in your SOUL.md (`## Sources`); when that
section is absent there is no published dashboard, so name the note paths
instead. Vault *writes* keep the `[source:: [[note]]]` grammar from the
vault's AGENTS.md — the URL form is only for replies.

## Ingest (new transcript or note in People/<you>/Inbox/)

1. Summarize what was decided and what changed, not what was said.
2. Extract: decisions; action items (owner + deadline); changes to what the
   vault already says.
3. Admit each extracted item (above), then route what survives per the vault's
   routing rules (actions -> Actions/Tracker.md, summary -> Sessions/, durable
   facts -> Memory.md).
4. Delete the processed item. It is not archived: the summary from step 3 is
   what the episode leaves behind, and it is what a fact line cites. Delete it
   last, so an interrupted routing loses nothing. An item that belongs in the
   vault but has no clear home goes to Needs-Routing.md — never guess, and
   never park what failed admission there.

## Promote (share knowledge with the team)

1. Draft a sanitized note containing only what is being shared.
2. Save under People/<you>/Promotions/ with frontmatter:
   target-path: <file in a shared space>
   source: <originating note path>
   mode: create | append | patch
   `mode: create` (default) needs a target that does not exist yet —
   decisions -> the shared space's Decisions/ folder, standing processes or
   standards -> its Playbook/ folder (your vault's AGENTS.md names the shared
   space — follow it). To update an existing shared page instead, use
   `mode: append` (your note is added under a divider) or `mode: patch`
   (your note body is the complete revised page — approval fails closed if
   the page changed since it was queued).
3. Tell the owner it awaits their approval (`brain promotions list`). Never
   write directly into a read-only space; the write-back service rejects it.
4. To answer status questions ("did it go live?"), read
   People/<you>/Shares.md — generated and read-only: pending items,
   approvals, and rejections with reasons. Never edit it.

## Third parties (not you)

Your vault's root AGENTS.md names the restricted tree for third parties
(clients, customers, families — whatever the company calls them) and the
request folder under `People/<you>/`. Take the exact folder and
frontmatter-key names from there; the shapes below use placeholders.

1. A **named third party** — a person, family, or company you work with or
   track — belongs in that tree, never in a `People/` note, even one sharing
   your own surname. You are a specific person with your own id; a third party
   who happens to share your last name is still a third party. Check the
   vault's routing rules for your id before filing anything under `People/`.
2. No space for it yet -> write a request into the requests folder your
   AGENTS.md names (`People/<you>/<Entity>Requests/<name>.md`) with the
   frontmatter keys it shows (`<entity>-name: <full name>`, `owner: <you>`,
   `entity: <type>`). The server provisions the space on the next cycle; from
   then on write there directly.
3. Name it with the fullest reasonable identifier (a full name, not a bare
   surname). Ask the user for one distinguishing detail before creating only
   when the name is thin or ambiguous — a bare common surname, a name that
   matches an entry you already have, or one that collides with your own
   household. Don't interrogate a distinctive name that's already unambiguous.
4. One utterance can split into two homes: e.g. a family attending an event
   also gets an `Intel/Events/<Name>.md` promotion under the shared space,
   cross-linked so each references the other.
5. To share or revoke access to a space you own, write
   `People/<you>/ShareRequests/<name>.md` with frontmatter: `space: <the space>`,
   `share-with: person:<id>` or `team:<name>`, `access: read|write`,
   `action: share` or `action: revoke`. The body is an optional note for the
   approver. Shares await their decider's approval — the recipient for a
   person-share, a team lead for a team-share, an admin for company-wide;
   revokes apply automatically. Your own
   access is never blocked by pending shares — keep writing. Status appears in
   `People/<you>/Shares.md`. You cannot revoke your own access.
6. If your `Shares.md` shows an **Awaiting your decision** section, those
   shares name you (or a team you lead) as recipient. Decide by writing
   `People/<you>/Approvals/<share-id>.md` with `decision: approve` or
   `decision: reject` (a rejection needs a `reason:` line) and `owner: <you>`.
   Record only a decision your human has explicitly made — never decide on
   your own. Company-wide shares (to `everyone`) always need an admin.

## Intel (articles, posts, links, PDFs, screenshots)

1. Distill, never archive: read the source (fetch a URL, extract PDF text,
   read an image) and pull the durable facts — the full text or file never
   enters the vault. Cite every claim `[source](URL), as of YYYY-MM`: the
   source is the URL, or the publication/title (or uploaded filename) when
   there's no link; use the source's own date, or `captured YYYY-MM` (today)
   when it shows none.
2. Route destination, provider, event, or trend intel to the shared wiki via
   a promotion targeting the shared Intel wiki (`<shared space>/Intel/` —
   your AGENTS.md names it) — Destinations/<Place>.md,
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
  the heading. Give notes plain, specific titles: retrieval is hybrid —
  keyword, meaning, and graph proximity fused — so a title that names the
  thing beats one stuffed with keywords. It falls back to keyword-only when
  no embedding provider is configured, and says so when it does; treat a
  search that found nothing as possibly incomplete, not as proof the vault
  is empty on that subject.
- Surface stale actions and unprocessed Inbox items when asked for status.
- A doctor digest (`People/<you>/Inbox/doctor-digest.md`, `source: doctor`)
  lists integrity findings routed to you. Fix what you can in writable
  spaces: link unlinked notes, fold addenda, close superseded facts with
  `[until::]`, merge duplicates. Fixes to shared pages go as `mode: patch`
  promotions. Items only a human can decide get a one-line reason in
  `Needs-Routing.md`. Never edit or archive the digest itself — it rewrites
  itself as findings change and disappears when everything is fixed.
