# hermes-brain — the one image every per-person agent container runs.
#
# Base: the official hermes image (s6-overlay as PID 1, supervised gateway,
# all state on the /opt/data volume). This layer adds:
#   - brainkit (from this repo's source — it is not on PyPI), in its own venv
#   - the company-brain hermes profile, staged for first boot
#   - a first-boot hook (profile install, deploy-key mint, /vault ownership)
#   - an s6-supervised vault-sync loop (git pull → brain index → git push)
#
# Build from the REPO ROOT (the context must include pyproject.toml and src/):
#   docker build -f deploy/agents-box/Dockerfile -t hermes-brain:latest .
FROM nousresearch/hermes-agent:latest

USER root

# brainkit in a dedicated venv: uv brings its own interpreter, so nothing in
# the hermes install tree (/opt/hermes, immutable at runtime) is touched.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
COPY pyproject.toml /tmp/brainkit/pyproject.toml
COPY src /tmp/brainkit/src
RUN uv venv /opt/brainkit --python 3.13 \
    && uv pip install --python /opt/brainkit/bin/python /tmp/brainkit \
    && ln -s /opt/brainkit/bin/brain /usr/local/bin/brain \
    && rm -rf /tmp/brainkit \
    && /usr/local/bin/brain --help >/dev/null

# The company-brain profile (SOUL.md, config defaults, brain-protocol skill).
# First boot copies it into /opt/data with terminal.cwd pointed at /vault.
COPY templates/company-brain-profile /opt/brain-profile

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
