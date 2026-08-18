# Cron prompt: personal decisions nudge

Runs in **each employee's** agent container, once or twice a day. Tells the
person what is waiting on them and nothing else.

Suggested schedule: weekday mornings, and optionally mid-afternoon. Stagger
across containers so the brain box isn't read by everyone at once.

---

## The prompt

```text
Read People/<you>/Shares.md in your vault. It is generated and read-only —
never edit it.

Check these three sections. Only two of them are things waiting on YOU:

1. "## Promotions awaiting your decision" — promotions into a team you lead.
   Someone is waiting on your call.
2. "## Awaiting your decision" — space shares naming you as recipient, or
   naming a team you lead.
3. "## Awaiting approval" — your OWN proposals still pending someone else.
   Not action for you; mention only if something has sat more than 5 days.

If sections 1 and 2 are both absent or empty, and nothing in section 3 is
older than 5 days, say nothing at all. Do not send a message. A daily "nothing
to do" is how a person learns to ignore you.

Otherwise send me one short message:

- One line per item waiting on my decision: what it is, who it's from, how
  long it has been waiting, and where it would go.
- For a promotion, add one line on what it would publish — the section already
  contains the body or a diff, so summarise it, do not paste it.
- Include the audience warning verbatim if the section shows one. "readable by
  everyone in the org" is the part I most need to see.
- End with the single most useful next step, not a menu.

Then stop. Do not decide anything.

When I tell you a decision, record it exactly as I said it:

- Share  -> People/<you>/Approvals/<share-id>.md
- Promotion -> People/<you>/PromotionApprovals/<promo-id>.md

Frontmatter for both: `decision: approve` or `decision: reject`, `owner: <you>`,
and `reason:` (required when rejecting — use my words, not a paraphrase).

Rules that do not bend:

- Record only a decision I have explicitly made. Never infer one from my mood,
  my silence, or the fact that something looks routine.
- If I am ambiguous ("sure, whatever", "that's fine I guess"), ask once for a
  plain approve or reject before writing anything.
- Company-wide promotions and shares to everyone are never yours or mine to
  record here — they need an admin at the dashboard. If I ask you to record
  one, say so and don't write the file.
- The next `brain cycle` applies the decision and re-checks eligibility
  server-side. If it refuses, you'll get a note in your Inbox explaining why —
  read it and tell me plainly rather than retrying.
```

---

## Why it is shaped this way

**Silence is the default.** The failure mode for a daily agent nudge is
becoming wallpaper. A message that arrives only when something is genuinely
waiting stays worth reading; one that arrives every morning gets filtered
within a week — and then the one that mattered gets filtered too.

**Summarise, don't paste.** The `Shares.md` section already carries the full
body or diff, capped at 4 KB. Pasting that into chat buries the decision in
content. The person can open the note when they want the detail.

**The audience line is the part worth repeating verbatim.** It is the one fact
that changes whether a promotion should be approved at all, and it is the thing
a busy person skims past.

**Ask once on ambiguity.** The gate's whole value is that a human decided. An
agent that reads "sure, whatever" as approval has quietly removed the human
from a human gate — which is worse than no gate, because the audit trail now
says a person approved it.
