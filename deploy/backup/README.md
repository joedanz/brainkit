# deploy/backup — offsite backups to Cloudflare R2

Generic, per-company provisioning for encrypted offsite backups with restic.
Two buckets per company — `<slug>-brain-backups` and `<slug>-agents-backups` —
each with its own bucket-scoped S3 token installed only on its box. The bucket
is the ONLY isolation boundary R2 offers: tokens scope to whole buckets (no
prefix scoping) and R2 has no object versioning (its S3 `GetBucketVersioning`
is a compat stub — verified against release notes 2026-07), so per-box buckets
are what keeps a compromised box from deleting the other box's backups.
Per-repo restic passwords, generated on each box, provide confidentiality.

- `provision-r2.sh <slug>` — run once per company (see script header for the
  required Cloudflare token scopes). Produces one `r2.env` per box to install
  as `/etc/brain-backup/r2.env` (0600).
- `r2.env.example` — inert, fully commented template of that file.

Backup jobs themselves live with their box: `../brain-box/backup-master.sh`
and `../agents-box/backup-agents-offsite.sh`. Install to `/usr/local/sbin/`
and run from cron; each needs `/etc/brain-backup/r2.env` plus an on-box
generated restic password file (see script headers). Store every restic
password in a password manager — client-side encryption means a lost
password is a lost backup.
