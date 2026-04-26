FROM node:20-slim AS dashboard-build
WORKDIR /app
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.11-slim

# Install runtime tools (tini for PID 1, git/curl/jq for runtime use)
# build-essential is needed because some Python deps (PyStemmer via fastembed)
# ship no aarch64 wheel and have to compile from source. We purge it after
# pip install to keep the final image small.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl tini jq build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install supervisord
RUN pip install --no-cache-dir supervisor

# Install Node.js for Claude CLI
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Purge build toolchain after pip is done with it — keeps image lean
RUN apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user. The Claude Code CLI refuses
# `--dangerously-skip-permissions` when the running uid is 0, so the agent
# subprocess MUST run as a non-root user.  uid 1000 is a common host uid which
# makes bind-mounted credentials (~/.claude) line up cleanly for most users.
ARG KARAKOS_UID=1000
ARG KARAKOS_GID=1000
RUN groupadd --system --gid ${KARAKOS_GID} karakos \
    && useradd --system --uid ${KARAKOS_UID} --gid ${KARAKOS_GID} \
        --home-dir /home/karakos --create-home --shell /bin/bash karakos

WORKDIR /workspace
# WORKDIR creates the directory as root. Hand it to karakos so the user can
# write into it (entrypoint.sh runs `git init` there, agents log to it, etc.).
RUN chown karakos:karakos /workspace

# Use --chown on COPY rather than a post-hoc `chown -R` so the workspace's
# millions of node_modules files don't have to be rewritten in a new layer.
COPY --chown=karakos:karakos --from=dashboard-build /app/.next dashboard/.next
COPY --chown=karakos:karakos --from=dashboard-build /app/node_modules dashboard/node_modules
COPY --chown=karakos:karakos --from=dashboard-build /app/public dashboard/public
COPY --chown=karakos:karakos --from=dashboard-build /app/package.json dashboard/package.json
COPY --chown=karakos:karakos . .

# Create data directories owned by karakos so volume mounts get the right
# ownership when first created.
RUN install -d -o karakos -g karakos \
        data data/messages data/memory data/health \
        logs logs/agent-streams logs/session-summaries \
        inbox

USER karakos
ENTRYPOINT ["/usr/bin/tini", "--", "/workspace/bin/entrypoint.sh"]
