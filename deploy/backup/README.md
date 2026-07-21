# deploy/backup — offsite backups to Cloudflare R2

Generic, per-company provisioning for encrypted offsite backups with restic.
One bucket per company (`<slug>-backups`), object versioning + 30-day
noncurrent-version lifecycle, restic repos under prefixes. The bucket is the
isolation boundary: R2 S3 tokens scope to a bucket, so one company's boxes can
never touch another company's backups. Within a company, versioning provides
the undelete window; per-repo restic passwords provide confidentiality.

- `provision-r2.sh <slug>` — run once per company (see script header for the
  required Cloudflare token scopes). Produces an `r2.env` to install on each
  box as `/etc/brain-backup/r2.env` (0600).
- `r2.env.example` — inert, fully commented template of that file.

Backup jobs themselves live with their box: `../brain-box/backup-master.sh`
and `../agents-box/backup-agents-offsite.sh`. Install to `/usr/local/sbin/`
and run from cron; each needs `/etc/brain-backup/r2.env` plus an on-box
generated restic password file (see script headers). Store every restic
password in a password manager — client-side encryption means a lost
password is a lost backup.
