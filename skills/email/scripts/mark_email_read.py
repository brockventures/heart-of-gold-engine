#!/usr/bin/env python3
"""
mark_email_read skill — flip the \\Seen flag on one message in the
"Marvin" Gmail label, via gmail_guard's MarvinFolderOnly. This is the
sanctioned write path added 2026-09-01 per Ian's explicit sign-off
in #general: "I am good with you reading/unreading anything in the
Marvin folder as a matter of record."

Deliberately narrow: takes a uid and a read/unread bool, nothing else.
No folder argument (gmail_guard hardcodes it), no flag argument beyond
the read/unread bool (gmail_guard hardcodes \\Seen specifically).

Credentials load the same way as read_marvin_folder.py and send_email.py
— read directly from config/.env, not the process environment.
"""

import json
import os
import re
import sys
from pathlib import Path

from gmail_guard import MarvinFolderOnly

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
ENV_PATH = WORKSPACE_ROOT / "config" / ".env"


def load_env_var(name: str) -> str:
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
    uid = args.get("uid")
    read = bool(args.get("read", True))

    if uid is None:
        print(json.dumps({"error": "uid is required"}))
        sys.exit(1)
    uid = str(uid)

    address = load_env_var("GMAIL_ADDRESS")
    app_password = load_env_var("GMAIL_APP_PASSWORD")
    if not address or not app_password:
        print(json.dumps({
            "error": "GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing from config/.env"
        }))
        sys.exit(1)

    try:
        with MarvinFolderOnly(address, app_password) as gmail:
            ok = gmail.mark_seen(uid) if read else gmail.mark_unseen(uid)
        print(json.dumps({"uid": int(uid), "read": read, "ok": ok}))
    except Exception as e:
        print(json.dumps({"error": f"Mark-read failed: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
