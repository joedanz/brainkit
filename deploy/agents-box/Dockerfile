# hermes-brain — the one image every per-person agent container runs.
#
# Base: the official hermes image (s6-overlay as PID 1, supervised gateway,
# all state on the /opt/data volume). This layer adds:
#   - brainkit (installed from git — it is not on PyPI), in its own venv
#   - the company-brain hermes profile, staged for first boot
#   - a first-boot hook (profile install, deploy-key mint, /vault ownership)
#   - an s6-supervised vault-sync loop (git pull → brain index → git push)
#
# Build from the REPO ROOT — the context still supplies the profile template and
# the s6 scripts below, though brainkit itself now comes from the public repo:
#   docker build -f deploy/agents-box/Dockerfile -t hermes-brain:latest .
FROM nousresearch/hermes-agent:latest

USER root

# The base image pins the agent's file-write guard to /opt/data alone — but
# this image adds /vault, and writing notes there is the whole point. Extend
# the guard (os.pathsep-separated) rather than unsetting it; without this,
# every write_file into the vault is denied and "saved" notes silently bounce.
ENV HERMES_WRITE_SAFE_ROOT=/opt/data:/vault

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

# Installing from git rather than the working tree is what keeps this image out
# of brainkit's build: no source is copied, so the only thing crossing the
# boundary is a commit on the public repo. uv shells out to git, which the
# hermes base already carries (vault-sync uses it at runtime).
RUN uv venv /opt/brainkit --python 3.13 \
    && uv pip install --python /opt/brainkit/bin/python \
       "brainkit @ git+https://github.com/joedanz/brainkit@${BRAINKIT_REF}" \
    && ln -s /opt/brainkit/bin/brain /usr/local/bin/brain \
    && /usr/local/bin/brain --help >/dev/null

# The company-brain profile (SOUL.md, config defaults, brain-protocol skill).
# First boot copies it into /opt/data with terminal.cwd pointed at /vault.
COPY templates/company-brain-profile /opt/brain-profile

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
RUN chmod 755 /etc/cont-init.d/03-brain-first-boot \
              /usr/local/bin/vault-sync \
              /etc/services.d/vault-sync/run

# No trailing USER directive: s6-overlay's /init must start as root — it fixes
# volume ownership on boot, then drops to the `hermes` user (uid 10000) for
# every supervised service and for the container's main program.
