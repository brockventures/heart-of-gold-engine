# Quick Start

Get Karakos running in under 30 minutes.

## Prerequisites

- A machine running Ubuntu 22.04+ or Debian 12+ (Raspberry Pi 4/5, mini PC, VM, etc.)
- Docker Engine 24+ with Compose v2 (`docker compose version`)
- `jq` installed (`sudo apt install jq`)
- An Anthropic account — login is handled by the Claude Code CLI (`claude login`), no API key required
- A Discord bot token (see [DISCORD_SETUP.md](DISCORD_SETUP.md))

## Install

```bash
git clone <repository-url>
cd karakos
chmod +x setup.sh
./setup.sh
```

The setup wizard walks you through:

1. **System name** — What you want to call your installation
2. **Owner name** — How the system addresses you
3. **Primary agent name** — Your main agent's name (defaults to system name)
4. **Anthropic login** — Opens your browser for `claude login` (OAuth, no API key)
5. **Discord bot** — Token, bot user ID, server ID (see [DISCORD_SETUP.md](DISCORD_SETUP.md))
6. **Discord channels** — Channel IDs for general, signals, and optionally staff-comms
7. **Your Discord user ID** — So the system knows who the owner is
8. **Cost limits** — Daily and monthly spend caps

The credentials produced by `claude login` live in `~/.claude/` on the host
and are bind-mounted into the container at runtime, so the in-container
`claude` CLI inherits the same auth.

The wizard saves progress, so you can quit and resume with `./setup.sh`.

## Start

Run the preflight check first to catch Docker/WSL issues before they surface
deep inside the install:

```bash
./bin/preflight.sh && docker compose pull && docker compose up -d
```

Or use the convenience target:

```bash
make install
```

`make install` runs preflight, pulls the prebuilt image, and starts the
containers. If preflight fails it halts with a clear error — fix the issue
flagged and re-run.

`docker compose pull` downloads the prebuilt multi-arch image from GHCR (~1.2 GB).
No local build step — startup is fast once the image is on disk.

**Version pinning:** To stay on a specific release and control when you upgrade, set
`KARAKOS_VERSION` in `config/.env`:

```bash
KARAKOS_VERSION=v1.3
```

Then `docker compose up -d` will use that version instead of `latest`.

> **First-time package publish (maintainers only):** GHCR packages default to
> private when first created by a `docker push`. After the first `release.yml`
> run, the package owner must visit
> [github.com/mcarmody/karakos-package/pkgs/container/karakos](https://github.com/mcarmody/karakos-package/pkgs/container/karakos)
> → **Package settings** → **Change package visibility** → **Public**, so
> end-users can pull without authentication. This is a one-time step per
> package.

## Verify

1. **Dashboard**: Open `http://localhost:3000` — login with `admin` and the password shown during setup
2. **Discord**: Your primary agent should respond in #general within a minute
3. **Logs**: `docker compose logs -f` to watch startup

## What's Running

Inside the container:

| Process | Description |
|---------|-------------|
| `agent-server.py` | Core — manages Claude subprocesses, message queue, API |
| `relay.py` | Routes Discord messages to agents |
| `scheduler.py` | Periodic tasks (heartbeats, memory, health checks) |
| `dashboard` | Next.js web interface on port 3000 |

## First Steps

1. Say hello to your agent in Discord — it should respond
2. Check the dashboard agents page to see agent status
3. Try the chat page to talk to your agent directly through the browser
4. Check #signals for system health updates

## Adding the Coding Stack

To add builder and reviewer agents:

```bash
docker exec -it karakos-karakos-1 bash
cd /workspace
bin/create-agent.sh --template builder --model sonnet builder
bin/create-agent.sh --template reviewer --model sonnet reviewer
```

The builder can then receive specs in its inbox and create pull requests. The reviewer provides adversarial code review.

## Stopping

```bash
docker compose down
```

The system shuts down gracefully — agents finalize their sessions before exiting (up to 45 seconds).

## Troubleshooting

**Agent not responding in Discord:**
- Check `docker compose logs agent-server` for errors
- Verify your bot token and channel IDs in `config/.env`
- Make sure your bot has been invited to the server with message permissions

**Dashboard won't load:**
- Check port 3000 isn't in use: `lsof -i :3000`
- Check `docker compose logs dashboard` for build errors

**High API costs:**
- Adjust `COST_DAILY_LIMIT` in `config/.env`
- Consider using `haiku` model for the relay agent (already default)

## Next Steps

- [DISCORD_SETUP.md](DISCORD_SETUP.md) — Detailed Discord bot creation
- [ARCHITECTURE.md](ARCHITECTURE.md) — How the system works
- [EXTENDING.md](EXTENDING.md) — Adding skills and custom agents
