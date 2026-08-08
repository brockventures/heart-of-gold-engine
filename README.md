# Heart of Gold

[![Internal Use Only](https://img.shields.io/badge/access-private%2Finternal-red.svg)]()
[![Anthropic Claude](https://img.shields.io/badge/Powered_by-Claude-orange)](https://www.anthropic.com/claude)

**This is not the Karakos package.** This is `iacoley`'s private install
repo for a running Karakos instance — config, agent memory, and deployment
state for the "Heart of Gold" household system. It is not installable by
anyone else and is not meant to be; there is no public setup flow here.

- **The actual software** lives upstream at
  [mcarmody/karakos-package](https://github.com/mcarmody/karakos-package)
  (public, MIT). This repo's deployment pulls the prebuilt image
  `ghcr.io/mcarmody/karakos:latest` from there — it does not build from
  the `Dockerfile` in this tree.
- **What's actually unique to this repo**: `config/` (this install's
  Discord server/channel wiring, `.env`), `agents/*/memory/` (this
  instance's episodic memory and facts), and deployment-specific files
  like `config/docker-compose.yml`'s image pin.
- **What's a stale/drifted copy of the package**: `bin/`, `dashboard/`,
  and most everything else — carried over from when this repo was a fork
  of `karakos-package` rather than a clean install-only repo. Some of it
  has local bug fixes not yet upstreamed; some of it is just behind. Not
  authoritative — treat `mcarmody/karakos-package` as the source of truth
  for anything that isn't config or memory.

If you're trying to install Karakos for your own use, go to
[mcarmody/karakos-package](https://github.com/mcarmody/karakos-package)
instead — see its README for the install command.

## What is Karakos?

Karakos is a multi-agent system that provides:
- **Discord integration** — Agents respond in your Discord server
- **Local dashboard** — Web interface for chat and monitoring
- **Memory system** — Episodic memory with consolidation and recall
- **Coding stack** — Builder and reviewer agents that can modify the system
- **Session persistence** — Context preserved across restarts
- **Cost tracking** — Monitor API spend with configurable limits

## System Requirements

- **Hardware**: 4GB RAM minimum (8GB recommended), 2+ CPU cores, 10GB disk space
- **OS**: Windows 10/11, Ubuntu 22.04+, Debian 12+, macOS 12+
- **Software**: Docker Engine 24+ with Compose v2
- **Network**: Stable internet for Anthropic API calls
- **Runtime**: 24/7 recommended

**Expected cost**: $5-15/week typical usage

## Installation

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for detailed installation instructions.

## CLI access

`bin/kara` is a Python CLI that talks to the agent-server's `/message`
endpoint and tails the response from `message_queue` — same transport as
the dashboard chat.

```bash
# one-shot
./bin/kara "what's on my calendar?"
echo "summarize this" | ./bin/kara

# REPL (interactive, slash commands)
./bin/kara
```

Slash commands inside the REPL: `/health`, `/agents`, `/agent <name>`,
`/cost`, `/reset`, `/reload`, `/restart` (macOS), `/help`, `/quit`.

Env vars: `AGENT_SERVER_TOKEN` (required), `AGENT_SERVER_URL`
(default `http://127.0.0.1:18791`), `KARA_AGENT`, `KARA_CHANNEL`
(default `cli`), `KARA_TIMEOUT` (default `300`s).

## Documentation

- [QUICKSTART.md](docs/QUICKSTART.md) — Installation and first steps
- [DISCORD_SETUP.md](docs/DISCORD_SETUP.md) — Discord bot creation guide
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — System architecture overview
- [EXTENDING.md](docs/EXTENDING.md) — Adding skills and customizing agents
- [UPGRADING.md](docs/UPGRADING.md) — Manual upgrade instructions

## Architecture

Karakos consists of:
- **Agent Server** — Manages Claude subprocess lifecycle, message queue, cost tracking
- **Relay** — Routes Discord messages, dispatches work to builder/reviewer agents
- **Scheduler** — Runs periodic tasks (heartbeats, memory consolidation)
- **Dashboard** — Next.js web interface for monitoring and chat
- **Agents** — Configurable Claude instances with specialized roles

## Core Agents

- **Primary** — Main agent, handles general requests and coordination
- **Relay** — Lightweight monitor, processes heartbeats and system notifications

## Optional Agents

- **Builder** — Code generation agent (invoke-builder.sh)
- **Reviewer** — Adversarial code review agent (invoke-reviewer.sh)

## License

Inherited MIT license from upstream `karakos-package` for the software
itself. This repo (config, memory, deployment state) is private and not
licensed for reuse.

## Contributing

This is a private install repo, not accepting outside contributions.
Bugs found here that trace back to the actual package should be reported
or PR'd against
[mcarmody/karakos-package](https://github.com/mcarmody/karakos-package)
instead — see `agents/Marvin/memory/facts/` for ones already flagged
upstream.

---

Maintained by Marvin, an instance of [Claude Code](https://claude.ai/claude-code) (Anthropic), on behalf of `iacoley`.
