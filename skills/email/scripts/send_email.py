#!/usr/bin/env python3
"""
send_email skill — outbound mail via Mailgun, from marvin@iancoley.org.

Reads MAILGUN_API_KEY / MAILGUN_DOMAIN directly from config/.env rather
than the process environment. They were added to .env 2026-08-06 after
the container was already running, and docker-compose's env_file loading
only happens at container creation — so they won't reach os.environ until
the container is recreated, which nobody was going to force just for
this. Reading the file directly sidesteps that entirely and keeps this
script correct regardless of when the container is next restarted.

The MCP tool server calls this script with TOOL_ARGS as a JSON environment
variable (to/subject/body/from_name). Output JSON to stdout; non-zero exit
+ stderr text is treated as an error by the caller.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
ENV_PATH = WORKSPACE_ROOT / "config" / ".env"


def load_env_var(name: str) -> str:
    """Read a single KEY=VALUE line directly from config/.env, bypassing
    the process environment (see module docstring for why)."""
    if not ENV_PATH.exists():
        return ""
    pattern = re.compile(rf"^{re.escape(name)}=(.*)$")
    for line in ENV_PATH.read_text().splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def main():
    args = json.loads(os.environ.get("TOOL_ARGS", "{}"))
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")
    from_name = args.get("from_name", "Marvin")

    if not to or not subject or not body:
        print(json.dumps({"error": "to, subject, and body are all required"}))
        sys.exit(1)

    api_key = load_env_var("MAILGUN_API_KEY")
    domain = load_env_var("MAILGUN_DOMAIN")

    if not api_key or not domain:
        print(json.dumps({
            "error": "MAILGUN_API_KEY or MAILGUN_DOMAIN missing from config/.env"
        }))
        sys.exit(1)

    # Mailgun's sending domain (mg.iancoley.org) is separate from the
    # visible From address (marvin@iancoley.org) — this is the standard,
    # documented Mailgun pattern for a subdomain-verified sender, and
    # passes DMARC's default relaxed alignment since both share the same
    # organizational domain.
    mailgun_api_domain = domain if domain.startswith("mg.") else f"mg.{domain}"
    from_domain = domain[3:] if domain.startswith("mg.") else domain
    from_address = f"{from_name} <marvin@{from_domain}>"

    payload = urllib.parse.urlencode({
        "from": from_address,
        "to": to,
        "subject": subject,
        "text": body,
    }).encode()

    url = f"https://api.mailgun.net/v3/{mailgun_api_domain}/messages"
    request = urllib.request.Request(url, data=payload, method="POST")
    credentials = f"api:{api_key}".encode()
    import base64
    request.add_header(
        "Authorization", f"Basic {base64.b64encode(credentials).decode()}"
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
            print(json.dumps({
                "status": "sent",
                "message_id": result.get("id", ""),
                "mailgun_response": result.get("message", ""),
                "from": from_address,
                "to": to,
            }))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode(errors="ignore")
        print(json.dumps({
            "error": f"Mailgun API error {e.code}: {error_body}"
        }))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Send failed: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
