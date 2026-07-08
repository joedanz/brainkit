# company-brain — Hermes profile distribution

Provisions an employee's personal Chief-of-Staff agent against their compiled
brain vault.

## Install (per employee)

1. Fork/copy this directory into your company's provisioning repo.
2. Edit `config.yaml`: set `terminal.cwd` to the employee's synced vault path.
3. On the employee's machine:

   hermes profile install github.com/<your-org>/company-brain --alias

Credentials, memories, and sessions stay on the employee's machine.

## Deployment rule (do not skip)

Hermes profiles do NOT sandbox the filesystem. Run this agent only:
- on the employee's own device against their synced vault, or
- server-side in a container that mounts ONLY that person's compiled vault.

Never run multiple employees' profiles side-by-side on one uncontained host.
