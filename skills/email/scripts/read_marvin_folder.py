#!/usr/bin/env python3
"""
read_marvin_folder skill — read new mail from the "Marvin" label in Ian's
personal Gmail, via IMAP.

Deliberately does NOT mark messages read/unread in Gmail — that's Ian's
own mailbox and his own read state to control. Instead, "new since last
call" is tracked via IMAP UID (stable across sessions, unlike sequence
numbers) in a local state file. Set include_seen=true to bypass that and
return everything in the folder regardless of history.

Credentials (GMAIL_ADDRESS, GMAIL_APP_PASSWORD) are read directly from
config/.env rather than the process environment, same reasoning as
send_email.py: they were added after the container was already running,
and docker-compose's env_file only loads at container creation.

Hard safeguard, Ian's explicit instruction 2026-08-06: all IMAP access
goes through gmail_guard.MarvinFolderOnly rather than calling imaplib
directly. That wrapper hardcodes the folder (not a parameter, cannot be
overridden), connects read-only, and doesn't expose anything beyond
search/fetch — see gmail_guard.py for the full reasoning.
"""

import email
import email.message
import imaplib
import json
import os
import re
import sys
from email.header import decode_header
from pathlib import Path

from gmail_guard import MarvinFolderOnly

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
ENV_PATH = WORKSPACE_ROOT / "config" / ".env"
STATE_PATH = WORKSPACE_ROOT / "data" / "gmail-marvin-state.json"
FOLDER = MarvinFolderOnly.ALLOWED_FOLDER
MAX_BODY_CHARS = 4000  # keep responses reasonable; this is a summary tool, not a full mail client


def load_env_var(name: str) -> str:
    if not ENV_PATH.exists():
        return ""
    pattern = re.compile(rf"^{re.escape(name)}=(.*)$")
    for line in ENV_PATH.read_text().splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def decode_mime_str(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


def extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if content_type == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return "(no plain-text body found — message may be HTML-only or all attachments)"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return ""


def main():
    args = json.loads(os.environ.get("TOOL_ARGS", "{}"))
    include_seen = bool(args.get("include_seen", False))

    address = load_env_var("GMAIL_ADDRESS")
    app_password = load_env_var("GMAIL_APP_PASSWORD")

    if not address or not app_password:
        print(json.dumps({
            "error": "GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing from config/.env"
        }))
        sys.exit(1)

    state = load_state()
    last_uid = 0 if include_seen else int(state.get("last_uid", 0))

    try:
        with MarvinFolderOnly(address, app_password) as gmail:
            search_criterion = "ALL" if last_uid == 0 else f"UID {last_uid + 1}:*"
            status, uid_data = gmail.search(search_criterion)
            if status != "OK":
                print(json.dumps({"error": "IMAP search failed"}))
                sys.exit(1)

            uids = [u for u in uid_data[0].decode().split() if u]
            # A UID range search like "N:*" on Gmail returns the last message
            # again even when nothing new exists past it, per IMAP semantics —
            # filter anything not strictly greater than what we've already seen.
            uids = [u for u in uids if include_seen or int(u) > last_uid]

            messages = []
            highest_uid = last_uid
            for uid in uids:
                status, msg_data = gmail.fetch(uid, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                body = extract_body(msg)
                if len(body) > MAX_BODY_CHARS:
                    body = body[:MAX_BODY_CHARS] + "... [truncated]"
                messages.append({
                    "uid": int(uid),
                    "from": decode_mime_str(msg.get("From", "")),
                    "subject": decode_mime_str(msg.get("Subject", "")),
                    "date": msg.get("Date", ""),
                    "body": body,
                })
                highest_uid = max(highest_uid, int(uid))

        if not include_seen:
            state["last_uid"] = highest_uid
            save_state(state)

        print(json.dumps({
            "folder": FOLDER,
            "new_message_count": len(messages),
            "messages": messages,
        }))

    except imaplib.IMAP4.error as e:
        print(json.dumps({"error": f"IMAP error: {e}"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Read failed: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
