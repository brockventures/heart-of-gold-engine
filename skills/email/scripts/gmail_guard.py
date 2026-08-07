#!/usr/bin/env python3
"""
gmail_guard.py — the ONLY sanctioned way to touch Ian's personal Gmail
over IMAP. Hard safeguard, Ian's explicit instruction, 2026-08-06:
"prevent you from reading my other email or interacting in any way
besides reading the Marvin folder."

Why this has to be enforced here, not at Google's end: a Gmail App
Password grants full-mailbox IMAP access at the credential level — there
is no way to scope an app password to a single label. The restriction is
therefore a code-level boundary, and the design goal is to make it a real
structural one rather than a convention that happens to be followed
today: this wrapper does not expose imaplib's list(), or select() with a
caller-supplied folder name, or any write/delete/move IMAP command at
all. There is exactly one usable folder (a class constant, not a
constructor argument — cannot be parameterized by a caller), and exactly
one access mode, read-only. The only operations available through this
wrapper are search and fetch, both implicitly scoped to whatever folder
was selected at connect time.

Known limit, stated plainly rather than glossed over: this cannot stop a
future script from importing imaplib directly and bypassing this
entirely — that's outside what a code-level wrapper can enforce against
its own author. What it does do is make every legitimate call site go
through one auditable choke point, so a bypass would have to be a
deliberate, conspicuous departure from the sanctioned path, not an easy
oversight or a "just this once" shortcut in a new script. Any future
Gmail/IMAP work should extend this file rather than opening a fresh
imaplib connection elsewhere.
"""

from __future__ import annotations

import imaplib
from typing import Any


class MarvinFolderOnly:
    """Read-only IMAP access to exactly one Gmail label. Nothing else is
    reachable through this class."""

    ALLOWED_FOLDER = "Marvin"  # not a parameter — cannot be overridden by a caller

    def __init__(self, address: str, app_password: str) -> None:
        self._conn = imaplib.IMAP4_SSL("imap.gmail.com")
        self._conn.login(address, app_password)
        status, _ = self._conn.select(f'"{self.ALLOWED_FOLDER}"', readonly=True)
        if status != "OK":
            self._conn.logout()
            raise RuntimeError(f"Could not open '{self.ALLOWED_FOLDER}' folder")

    def search(self, criterion: str):
        """UID search within the Marvin folder only."""
        return self._conn.uid("search", None, criterion)

    def fetch(self, uid: str, parts: str):
        """UID fetch within the Marvin folder only."""
        return self._conn.uid("fetch", uid, parts)

    def close(self) -> None:
        self._conn.logout()

    def __enter__(self) -> "MarvinFolderOnly":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


# -- selftest -----------------------------------------------------------
def _selftest() -> int:
    """Static checks only — no network call, no real credentials needed.
    Confirms the class doesn't accidentally expose anything dangerous."""
    fails = 0

    def check(label, ok):
        nonlocal fails
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")

    print("── gmail_guard selftest (static) ──")

    import inspect
    public_methods = {
        name for name, val in inspect.getmembers(MarvinFolderOnly)
        if not name.startswith("_") and inspect.isfunction(val)
    }
    check("only search/fetch/close are public methods",
          public_methods == {"search", "fetch", "close"})

    check("ALLOWED_FOLDER is a hardcoded class constant",
          MarvinFolderOnly.ALLOWED_FOLDER == "Marvin")

    init_params = list(inspect.signature(MarvinFolderOnly.__init__).parameters)
    check("__init__ takes no folder argument (only self/address/app_password)",
          init_params == ["self", "address", "app_password"])

    print("PASS  no unsafe methods exposed" if not fails else f"FAIL  {fails} case(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
