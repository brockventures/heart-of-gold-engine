FROM node:20-slim AS dashboard-build
WORKDIR /app
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.11-slim

# Install tini for proper PID 1 + signal handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl tini jq \
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

WORKDIR /workspace
COPY --from=dashboard-build /app/.next dashboard/.next
COPY --from=dashboard-build /app/node_modules dashboard/node_modules
COPY --from=dashboard-build /app/public dashboard/public
COPY --from=dashboard-build /app/package.json dashboard/package.json
COPY . .

# Create data directories
RUN mkdir -p data/messages data/memory data/health logs/agent-streams logs/session-summaries inbox

ENTRYPOINT ["/usr/bin/tini", "--", "/workspace/bin/entrypoint.sh"]
