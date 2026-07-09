# Batch: full e2e smoke test — two-box deployment over real SSH

Goal: prove the merged deployment (PR #18 + #19) is 100% complete and working,
end to end, using the real transport this time: a `brain-box` container
running sshd + the `brain-serve-repo` forced command, and per-person agent
containers reaching it over a docker network. No repo changes expected —
deliverable is a pass/fail report (and fixes if anything is broken).

## Test matrix

- [x] **1. Brain box up** — hermes-brain image + openssh-server; `brain init`
  master with alice + bob in org.yaml; compile; `updateInstead` on both
  compiled repos; `brain-serve-repo` installed; sshd running.
- [x] **2. Agent first boot** — agent-alice container: profile installed,
  deploy key minted + banner in logs, gateway supervised.
- [x] **3. Key authorization** — pubkey from `docker logs` into brain-sync's
  authorized_keys with the forced command; clone lands within 30s;
  `brain index` builds.
- [x] **4. Security boundary** — with alice's key: shell command denied;
  cloning bob's repo denied; alice's own repo works. (Symmetric check with
  bob's key at the end: alice's repo denied.)
- [x] **5. Life of a note** — Inbox note → auto-commit → SSH push
  (updateInstead) → `brain cycle --index` applied: 1 → pull-back →
  `brain search` hit.
- [x] **6. Cross-person flow** — promotion draft in People/alice/Promotions →
  sync → cycle swept: 1 → approve --approver joe → cycle → decision lands in
  bob's compiled vault with promoted-by/approved-by/source frontmatter.
- [x] **7. MCP server** — JSON-RPC initialize + tools/call brain_search over
  stdio inside the agent; found the promoted decision alice pulled back.
- [x] **8. Backup + restore** — backup-agents.sh on the live rig; deploy key
  in the zip; delete-key → `hermes import --force` → key restored.
- [x] **9. Restart persistence** — restart: vault + profile marker intact,
  no re-banner; sync resumes (after bug #4 fix below).
- [x] **10. Doctor** — 0 errors, 0 warnings, exit 0.
- [x] **Capstone** — agent-bob booted from the final image (zero hot patches):
  first boot → clone → index; bob's search finds alice's promoted decision.
- [x] **Cleanup** — containers, volumes, network, scratch backups removed.

## Review — 4 real bugs found and fixed (all live-verified)

1. **SSH transport was completely broken in production** (would have blocked
   the first real deployment): OpenSSH resolves `~/.ssh` from the passwd
   entry (`pw_dir=/opt/data`), NOT `$HOME` (`/opt/data/home`) — the deploy
   key and per-user config written by first boot were never read. The Phase 3
   test used a file-path remote, so this never surfaced. Fix: system-wide
   `/etc/ssh/ssh_config.d/brain.conf` baked into the image with absolute
   paths (IdentityFile, IdentitiesOnly, accept-new, UserKnownHostsFile);
   first boot no longer writes the dead per-user config.
2. **`docker exec <c> vault-sync` (the README's verification command) failed
   entirely**: docker exec runs as root; git refuses the hermes-owned repo
   ("dubious ownership") and `brain index` as root would leave root-owned
   files. Fix: vault-sync self-drops to hermes via `/command/s6-setuidgid`
   when invoked as root.
3. **`brain mcp`/`brain search` crashed on an unreadable HOME**
   (`~/.config/brain/config.yaml` under `/root` → PermissionError traceback):
   embeddings config probe now treats OSError as "no config". 265 pytest
   green after the change.
4. **The documented restore flow silently killed sync**: `hermes import`
   does not preserve file modes — the restored private key came back 0644
   and ssh refused it. Fix: first boot enforces `chmod 700 .ssh` /
   `600 id_ed25519` on EVERY boot (the restore flow ends in a restart),
   so restores just work.

Everything else passed as designed: fail-closed clone-retry until key
authorization, forced-command tenant isolation in both directions,
updateInstead push flow, promotion gate with provenance, index freshness,
backup/restore, restart persistence, doctor clean. Final image rebuilt with
all fixes; capstone agent (bob) ran the whole pipeline from that image
unpatched.
