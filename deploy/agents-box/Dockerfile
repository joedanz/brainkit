# hermes-brain — the one image every per-person agent container runs.
#
# Base: the official hermes image (s6-overlay as PID 1, supervised gateway,
# all state on the /opt/data volume). This layer adds:
#   - brainkit (installed from git — it is not on PyPI), in its own venv
#   - the company-brain hermes profile, staged for first boot
#   - a first-boot hook (profile install, deploy-key mint, /vault ownership)
#   - an s6-supervised vault-sync loop (git pull → brain index → git push)
#
# Build from the REPO ROOT — the COPY paths below are repo-relative — but every
# file this image bakes in now lives beside this Dockerfile in deploy/agents-box/,
# and brainkit comes from the public repo rather than the working tree. Nothing
# outside this directory crosses into the image:
#   docker build -f deploy/agents-box/Dockerfile -t hermes-brain:latest .
# Which hermes base to build on. Default preserves every existing build path;
# the deploy dashboard's upgrade-hermes job passes a digest-pinned ref
# (nousresearch/hermes-agent@sha256:…) so fleet upgrades are byte-exact.
ARG HERMES_IMAGE=nousresearch/hermes-agent:latest
FROM ${HERMES_IMAGE}

USER root

# The base image pins the agent's file-write guard to /opt/data alone — but
# this image adds /vault, and writing notes there is the whole point. Extend
# the guard (os.pathsep-separated) rather than unsetting it; without this,
# every write_file into the vault is denied and "saved" notes silently bounce.
ENV HERMES_WRITE_SAFE_ROOT=/opt/data:/vault

# Scratch that lands INSIDE the guard above. The default /tmp is outside it, so
# every throwaway probe script an agent writes there is denied — and the denial
# only surfaces in the file-mutation verifier footer, after the agent has
# already said it wrote the file.
#
# Under .cache/ on purpose, not /opt/data/tmp: `hermes backup` walks the whole
# volume into the nightly zip and skips only the names in its _EXCLUDED_DIRS,
# of which .cache is one. Scratch is exactly the traffic that must not be
# archived — a captured HAR carries auth headers and session cookies, and those
# zips ship to R2.
#
# The directory itself is created by 03-brain-first-boot, NOT here: /opt/data is
# a volume mount, so anything mkdir'd into it at build time is masked at runtime
# and TMPDIR would point at nothing.
ENV TMPDIR=/opt/data/.cache/tmp

# brainkit in a dedicated venv: uv brings its own interpreter, so nothing in
# the hermes install tree (/opt/hermes, immutable at runtime) is touched.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
# Which brainkit to install. A VCS install records the resolved commit in
# direct_url.json (PEP 610) and `brain --version` reads it — so the image
# describes itself, with no separate stamp that could name a commit the image
# does not contain. build-image.sh passes the resolved commit; a bare build
# takes whatever main points at.
#   docker build --build-arg BRAINKIT_REF=<sha> …
ARG BRAINKIT_REF=main
LABEL org.opencontainers.image.revision="$BRAINKIT_REF"
LABEL org.opencontainers.image.source="https://github.com/joedanz/brainkit"

# Re-declared: an ARG before FROM is scoped to FROM only (Docker semantics).
ARG HERMES_IMAGE=nousresearch/hermes-agent:latest
LABEL io.deploy.hermes-image="$HERMES_IMAGE"

# Installing from git rather than the working tree is what keeps this image out
# of brainkit's build: no source is copied, so the only thing crossing the
# boundary is a commit on the public repo. uv shells out to git, which the
# hermes base already carries (vault-sync uses it at runtime).
RUN uv venv /opt/brainkit --python 3.13 \
    && uv pip install --python /opt/brainkit/bin/python \
       "brainkit @ git+https://github.com/joedanz/brainkit@${BRAINKIT_REF}" \
    && ln -s /opt/brainkit/bin/brain /usr/local/bin/brain \
    && /usr/local/bin/brain --help >/dev/null

# CLI for Microsoft 365 — how an agent reaches Outlook mail and calendar,
# OneDrive and Teams. Installed here rather than by the job that connects an
# account, so the version is a property of the image and upgrading it is a
# brainkit release, not a per-agent step.
#
# PINNED. A floating tag would let the tool surface move under agents that
# were never rebuilt, and the failure would look like the agent forgetting how
# to do something it did yesterday.
#
# NOT into /opt/data. That is the runtime volume, so anything written there at
# build time is masked the moment the volume mounts — the same trap the TMPDIR
# comment above describes. The default global prefix (/usr/local) is baked into
# the image and survives.
#
# Note for whoever migrates an agent that had this hand-installed: the runtime
# PATH puts /opt/data/.local/bin BEFORE /usr/local/bin, so a hand-installed
# copy on the volume SHADOWS this one indefinitely. Remove it, or the pin here
# is a fiction.
ARG M365_CLI_VERSION=11.10.0
LABEL io.deploy.m365-cli-version="$M365_CLI_VERSION"
RUN npm install -g --no-fund --no-audit "@pnp/cli-microsoft365@${M365_CLI_VERSION}" \
    && m365 --version >/dev/null

# Suppresses the CLI's update check. Not cosmetic: the nag prints on stdout,
# and the device-code prompt is parsed out of that stream by the job that
# signs an agent in — an update notice landing mid-output is a parse failure
# with no obvious cause.
ENV CLIMICROSOFT365_NOUPDATE=true

# The company-brain profile (SOUL.md, config defaults, brain-protocol skill).
# First boot copies it into /opt/data with terminal.cwd pointed at /vault.
COPY deploy/agents-box/company-brain-profile /opt/brain-profile

# OpenSSH resolves `~/.ssh` from the passwd entry (pw_dir = /opt/data), NOT
# from $HOME (/opt/data/home for hermes tool subprocesses) — so the deploy key
# minted at /opt/data/home/.ssh would never be found without absolute paths.
# A system-wide drop-in pins them for every user and every caller (vault-sync
# and agent-initiated git alike).
RUN mkdir -p /etc/ssh/ssh_config.d && printf '%s\n' \
    'Host *' \
    '  IdentityFile /opt/data/home/.ssh/id_ed25519' \
    '  IdentitiesOnly yes' \
    '  StrictHostKeyChecking accept-new' \
    '  UserKnownHostsFile /opt/data/home/.ssh/known_hosts' \
    > /etc/ssh/ssh_config.d/brain.conf

# First-boot hook runs from s6's cont-init stage (as root, after the stock
# 01-hermes-setup seeds defaults); vault-sync is a supervised longrun.
COPY deploy/agents-box/scripts/03-brain-first-boot /etc/cont-init.d/03-brain-first-boot
COPY deploy/agents-box/scripts/vault-sync /usr/local/bin/vault-sync
COPY deploy/agents-box/scripts/vault-sync-run /etc/services.d/vault-sync/run
COPY deploy/agents-box/scripts/serve-starter /usr/local/bin/serve-starter
COPY deploy/agents-box/scripts/serve-starter-run /etc/services.d/serve-starter/run
# TMPDIR lives on the volume now, so nothing wipes it at restart the way /tmp
# was wiped — this longrun is what keeps it from growing forever.
COPY deploy/agents-box/scripts/tmp-reaper-run /etc/services.d/tmp-reaper/run
RUN chmod 755 /etc/cont-init.d/03-brain-first-boot \
              /usr/local/bin/vault-sync \
              /etc/services.d/vault-sync/run \
              /usr/local/bin/serve-starter \
              /etc/services.d/serve-starter/run \
              /etc/services.d/tmp-reaper/run

# No trailing USER directive: s6-overlay's /init must start as root — it fixes
# volume ownership on boot, then drops to the `hermes` user (uid 10000) for
# every supervised service and for the container's main program.
