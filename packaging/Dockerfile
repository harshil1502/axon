# Axon daemon in a container — the Stage 1 headless smoke test from docs/BOOT.md.
#
# This is NOT "Axon as an OS" (a container shares the host kernel); it validates
# that the exact process systemd will supervise comes up clean with only Python
# and the stdlib. Build/run:
#   docker build -f packaging/Dockerfile -t axon:dev .
#   docker run --rm -it axon:dev          # daemon in the foreground on its socket
#
# Axon core is pure stdlib; `anthropic` is optional (agent_loop only) and left
# out so the image stays dependency-free and works fully offline.

FROM python:3.11-slim

# Durable, isolated state dir. The kernel uses Path.home()/.axon, so HOME steers
# it; here that resolves to /var/lib/axon/.axon.
ENV HOME=/var/lib/axon \
    PYTHONUNBUFFERED=1

WORKDIR /opt/axon

# Install the package. Copy metadata first so the dep layer caches; then sources.
COPY pyproject.toml README.md ./
COPY axon ./axon
COPY docs ./docs
RUN pip install --no-cache-dir -e . \
    && install -d "$HOME"

# The daemon binds a Unix domain socket under HOME/.axon (axon.sock) — no TCP
# port to expose. Other processes in the container reach it via axon.client.Client.

# ExecStart equivalent. Uses an explicit one-liner for the same reason as
# packaging/axon.service: boot() wires the built-in actions and there is no
# `axon-daemon` entrypoint yet. Runs in the foreground so the container's life
# tracks the daemon's.
CMD ["python", "-c", "from axon import boot; from axon.daemon import Daemon; Daemon(boot()).serve_forever()"]
