# Cron jobs for the human-gated queues

Both gates — [promotions](../../../docs/concepts/promotions.mdx) and space
shares — are **pull**. `brain cycle` moves items through the queues and each
person's generated `Shares.md` shows what is waiting, but nothing tells anyone
to look. These close that loop.

| Job | Runs on | Delivers |
|---|---|---|
| [`brain-decisions-digest.py`](brain-decisions-digest.py) | each **agent container** (hermes cron) | that person's chat gateway |
| [`../../brain-box/brain-queue-digest.sh`](../../brain-box/brain-queue-digest.sh) | the **brain box** (host cron) | cron's `MAILTO` |

## Silence is mechanical, not instructed

Both are watchdogs: **they print nothing when there is nothing.** Empty output
means no message — hermes suppresses delivery for a `--no-agent` job with empty
stdout, and cron mails only what a job writes.

That is deliberately not an instruction to a model. An early draft of this
directory was a pair of *prompts* telling an agent to stay quiet on an empty
queue; a prompt can be talked out of it, and one cheerful "nothing waiting
today!" a week is enough for someone to mute the channel — after which the
message that mattered is muted too.

Neither job decides anything, and the brain-box one has no mechanism to. It
reports; you decide at the dashboard, where the diff and the audience warning
are. Recording a decision stays a conversation with your own agent, driven by
the protocol already in your vault's `AGENTS.md`.

## Installing the per-person digest

Verified against a live container, 2026-08-18:

```bash
# 1. Ship the script. Note the path — see the gotcha below.
docker exec -i -u hermes agent-<person> \
  bash -lc 'cat > $HOME/scripts/brain-decisions-digest.py && chmod +x $HOME/scripts/brain-decisions-digest.py' \
  < brain-decisions-digest.py

# 2. Schedule it. `hermes` is not on PATH under docker exec.
docker exec -u hermes agent-<person> /opt/hermes/bin/hermes cron create \
  "0 8 * * 1-5" --name brain-decisions --script brain-decisions-digest.py \
  --no-agent --deliver origin

# 3. Prove it is silent before trusting it.
docker exec -u hermes agent-<person> /opt/hermes/bin/hermes cron run <job-id>
docker exec -u hermes agent-<person> /opt/hermes/bin/hermes cron runs | head -3
```

A `completed` run with no output line is the silent path — contrast a failure,
which prints its reason inline. Run step 3 against someone whose queue is
**empty**: that is the behaviour worth confirming first, because a digest that
fires on an empty queue is the one that gets the channel muted.

### Two gotchas, both learned the hard way

- **`--script` resolves against `$HOME/scripts/`, not `~/.hermes/scripts/`** —
  despite what `hermes cron create --help` says. Installing to the documented
  path fails at run time with `Script not found: /opt/data/scripts/<name>`.
- **`hermes` is not on `PATH` for `docker exec`.** Use
  `/opt/hermes/bin/hermes`; the bare name works in an interactive shell and
  fails in a scripted one.

### Knobs

`BRAIN_VAULT` (default `/vault`), `BRAIN_PERSON` (default: inferred — a
compiled slice holds exactly one person's own space), `BRAIN_STALE_DAYS`
(default `5`) — how long one of *your own* proposals must sit before it is
worth mentioning, since it is not action for you.

## Installing the admin digest

On the brain box, because only it has the master:

```bash
install -m 755 brain-queue-digest.sh /usr/local/sbin/
# crontab (root) — after the per-person nudges, so a lead's own decision
# has already cleared its item
0 9 * * 1-5 MASTER=/srv/brain/master /usr/local/sbin/brain-queue-digest.sh
```

Set `MAILTO` in the crontab to choose where it lands.

## Who sees what

An **admin** gets no "Promotions awaiting your decision" section in their own
vault — by design, the display side computes eligibility on an admin-stripped
view, so admins use the dashboard and leads use the seam. An admin's personal
digest therefore only ever reports share decisions and their own stale
proposals, and the brain-box digest is their real queue view.
