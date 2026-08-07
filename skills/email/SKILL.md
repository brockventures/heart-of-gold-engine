# Email Skill

Send outbound email as `marvin@iancoley.org` via Mailgun, and read the
"Marvin" label on Ian's personal Gmail via IMAP.

## Background

Ian owns `iancoley.org`. Outbound goes through a Mailgun account with
`mg.iancoley.org` set up as a verified sending subdomain (SPF, DKIM,
tracking CNAME) — Mailgun's API domain is separate from the visible From
address (`marvin@iancoley.org`), standard supported Mailgun pattern,
passes DMARC's default relaxed alignment since both share the
organizational domain.

Inbound arrives via a *separate* Mailgun account that Ian doesn't have API
access to — his Squarespace "email forwarding" is Mailgun white-labeled
under the hood, receiving on the root domain's MX and forwarding to his
personal Gmail, where a filter routes anything from `marvin@iancoley.org`
into a "Marvin" label. Two independent Mailgun accounts on one domain,
no conflict, because MX (Squarespace's) and the sending subdomain's
records live in separate DNS namespaces.

Both directions verified working live 2026-08-06 with full cryptographic
proof (DKIM/SPF/DMARC all passing on a real externally-sent test message,
not just a message existing in a folder — see
`agents/Marvin/memory/facts/email-working.md` for the full story,
including a real mistake made and corrected along the way).

## What It Does

- `send_email` — sends plain-text mail through Mailgun's HTTP API.
- `read_marvin_folder` — reads new messages from the Marvin label via
  IMAP, tracked by UID so repeated calls don't return the same message
  twice. Doesn't mark anything read/unread in Gmail — that stays under
  Ian's control.

## Files

```
email/
├── SKILL.md
├── tools.json
└── scripts/
    ├── send_email.py
    ├── read_marvin_folder.py
    └── gmail_guard.py
```

## Usage

```
Tool: send_email
Input: {"to": "someone@example.com", "subject": "...", "body": "..."}
Output: {"status": "sent", "message_id": "...", "from": "...", "to": "..."}
```

`from_name` is optional (default `"Marvin"`) — controls the display name
only, the address is always `marvin@<root domain>`.

```
Tool: read_marvin_folder
Input: {"include_seen": false}
Output: {"folder": "Marvin", "new_message_count": N, "messages": [...]}
```

`include_seen: true` bypasses the UID-tracked dedup and returns everything
in the folder regardless of history — doesn't advance the tracked state.

Wired into `bin/heartbeat.sh` — every heartbeat does a low-cost poll and
adds a compact summary to the heartbeat message only when there's
something new.

## Hard safeguard on Gmail access

`gmail_guard.py`'s `MarvinFolderOnly` class is the **only** sanctioned way
to touch Ian's personal Gmail over IMAP — Ian's explicit instruction,
2026-08-06: nothing besides the Marvin folder, enforced structurally, not
just by convention. The folder name is a hardcoded class constant (not a
constructor argument), the connection is always read-only, and the class
exposes only `search`/`fetch`/`close` — never `list()` or a
caller-supplied folder name. `read_marvin_folder.py` goes through it
exclusively rather than calling `imaplib` directly. Any future Gmail/IMAP
work should extend `gmail_guard.py` rather than opening a fresh
connection elsewhere — see that file's docstring for the stated limits of
what a code-level wrapper can and can't enforce.

## Credentials

`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`
all live in `config/.env`. Scripts read that file directly rather than
the process environment — those vars were added after the container was
already running, and docker-compose's `env_file` only loads at container
creation, so they won't reach `os.environ` until the container is next
recreated. Reading the file directly means this skill works regardless of
that.
