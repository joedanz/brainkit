# Cron prompt: admin queue nudge

Runs in the **admin's** agent container only, once or twice a day. Reports the
depth and age of the two human-gated queues, and sends the admin to the
dashboard to decide.

Suggested schedule: weekday mornings. Later than the personal nudges, so a
lead's own decision has a chance to clear an item first.

---

## The prompt

```text
Check both human-gated queues on the brain box:

  brain promotions list --master /srv/brain/master
  brain shares list     --master /srv/brain/master

If both are empty, say nothing at all. Do not send a message.

Otherwise send me one short message:

- The count in each queue.
- For promotions, split the count: how many target the shared space (only I
  can approve those) versus a team space (a team lead can also clear those,
  so they may not need me).
- Name anything waiting more than 3 days, oldest first, with its age and
  destination. Age is the number that matters — depth alone is not urgency.
- Link me to the dashboard Promotions tab.

Then stop.

Do NOT approve anything, and do not offer to. Do not read the pending bodies
out to me and ask for a yes/no. Company-wide promotions are decided at the
dashboard on purpose: that surface shows a live diff against the current page
and a warning naming exactly who will be able to read the result, and I need
both in front of me before I publish to the whole company. A summary in chat
is not that, and approving from a summary is the exact failure this gate
exists to prevent.

The one thing you may do besides report: if a promotion has sat more than
7 days, say so plainly and ask whether I want to reject it. A queue that only
grows is a gate nobody is operating.
```

---

## Why it is shaped this way

**This nudge deliberately does not offer to decide.** The personal nudge lets a
lead record a decision in-vault; this one refuses to, and that asymmetry is the
whole design. A `Teams/<team>/` promotion has a small, already-trusted audience,
so convenience wins. A `Company/` promotion publishes to everyone, irreversibly
in the sense that matters — deleting the file afterwards does not unread it.
That one keeps the dashboard's live diff and audience warning.

The server enforces this regardless (`sweep_promotion_approvals` refuses any
shared-space target in-vault, even for an admin), so the prompt is describing a
boundary it cannot cross, not one it is trusted to respect. Saying it out loud
still matters: it stops the agent from repeatedly offering something that will
fail.

**Splitting the promotion count by destination** is what makes the number
actionable. "Six pending" prompts a dashboard visit; "six pending, four of them
team-space items your leads can clear" prompts a nudge to the leads instead —
and keeps the admin's own queue honest about what actually needs them.

**Age over depth.** A queue of ten items added this morning is healthy. A queue
of two where one has waited a fortnight means the gate has stopped being
operated, and that is the state worth interrupting someone about.

**The 7-day reject question** exists because the alternative to deciding is not
"decide later" — it is a queue that quietly becomes a graveyard, and a
contributor who learns that proposing things achieves nothing. A rejection with
a reason is a better outcome than indefinite silence; `Shares.md` shows the
requester that reason.
