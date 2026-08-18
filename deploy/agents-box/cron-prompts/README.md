# Cron prompts

Scheduled **LLM prompts** for the agent containers — as distinct from the host
cron jobs in [`../README.md`](../README.md), which run shell scripts (backups,
liveness). These are prompts hermes fires on a schedule; the agent reads the
vault and messages its person over the chat gateway it already has.

| Prompt | Runs in | Cadence | Purpose |
|---|---|---|---|
| [`decisions-nudge.md`](decisions-nudge.md) | every employee container | 1–2×/weekday | tell the person what is waiting on their decision; record the decision they give |
| [`admin-queue-nudge.md`](admin-queue-nudge.md) | admin container only | 1×/weekday | report queue depth and age; send the admin to the dashboard |

## Why these exist

Both human gates — [promotions](../../../docs/concepts/promotions.mdx) and
[space shares](../../../docs/concepts/spaces-and-permissions.mdx) — are **pull**.
`brain cycle` moves items through the queues, and each person's generated
`People/<them>/Shares.md` shows what is waiting. But nothing tells anyone to
look. A person learns they have a decision only if they happen to open that
file, and an admin only if they happen to open the dashboard.

These prompts are the doorbell. They add no authority: the decision seam and
its server-side eligibility re-check are unchanged, and a nudge that never
fires costs nothing but a slower queue.

## The one rule both prompts share

**Say nothing when there is nothing.** An agent that reports "nothing waiting"
every morning teaches its person to ignore it, and the message that finally
matters gets ignored with the rest. Both prompts open by describing the empty
case and instructing silence.

## Wiring

> **Check the current hermes cron syntax before installing.** Nothing in this
> repo configures a hermes cron yet — these are the first — so the invocation
> below is illustrative, not verified. Confirm against `hermes cron --help`
> inside a container.

```bash
docker exec agent-<person> hermes cron --help    # confirm the syntax first
```

Points worth getting right whatever the syntax turns out to be:

- **Stagger the schedules** across containers. Every agent reading the vault at
  09:00 is a thundering herd against one brain box.
- **Run the admin nudge after the personal ones**, so a lead clearing their own
  team's item removes it from the admin's report.
- **Weekdays only.** A weekend nudge about a queue nobody will touch until
  Monday is pure noise.
- Each container already has its own chat gateway and its own vault mount, so
  the prompt needs no credentials or paths beyond what the agent already has.

## Verifying one before trusting it

Run the prompt by hand against a container whose person genuinely has nothing
pending, and confirm it stays silent. That is the failure mode worth testing
first — a nudge that fires on an empty queue is the one that gets the whole
channel muted.

Then queue one real item and confirm the message names it, its age, and its
destination, and that recording a decision produces the right file under
`People/<them>/Approvals/` or `People/<them>/PromotionApprovals/`.
