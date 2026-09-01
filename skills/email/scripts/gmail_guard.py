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
caller-supplied folder name, or any raw write/delete/move IMAP command.
There is exactly one usable folder (a class constant, not a constructor
argument — cannot be parameterized by a caller).

Scope widened 2026-09-01, Ian's explicit sign-off in #general: "I am
good with you reading/unreading anything in the Marvin folder as a
matter of record." That's flag-state on messages already confined to
this one folder, not a loosening of the folder boundary itself — the
mailbox is now selected read-write (Gmail's IMAP has no "read-write but
flags-only" mode to select into), but the only write operation this
class exposes is toggling the \\Seen flag via mark_seen()/mark_unseen(),
and the flag name is hardcoded exactly like ALLOWED_FOLDER is — not a
caller-supplied argument. Still no delete, no move, no expunge, no
arbitrary STORE. search/fetch/close are otherwise unchanged.

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
    """IMAP access to exactly one Gmail label. Nothing else is reachable
    through this class: search/fetch, plus toggling the \\Seen flag
    (2026-09-01, Ian's sign-off) — no delete, no move, no arbitrary
    flag/STORE command."""

    ALLOWED_FOLDER = "Marvin"  # not a parameter — cannot be overridden by a caller

    def __init__(self, address: str, app_password: str) -> None:
        self._conn = imaplib.IMAP4_SSL("imap.gmail.com")
        self._conn.login(address, app_password)
        # Read-write: Gmail's IMAP has no "read-only mailbox, but let me
        # flip flags" mode — EXAMINE (readonly=True) rejects STORE outright.
        # The mailbox-open mode is therefore no longer the enforcement
        # point; which methods this class exposes is.
        status, _ = self._conn.select(f'"{self.ALLOWED_FOLDER}"')
        if status != "OK":
            self._conn.logout()
            raise RuntimeError(f"Could not open '{self.ALLOWED_FOLDER}' folder")

    def search(self, criterion: str):
        """UID search within the Marvin folder only."""
        return self._conn.uid("search", None, criterion)

    def fetch(self, uid: str, parts: str):
        """UID fetch within the Marvin folder only."""
        return self._conn.uid("fetch", uid, parts)

    def mark_seen(self, uid: str) -> bool:
        """Flag one message \\Seen. The flag is hardcoded, same pattern as
        ALLOWED_FOLDER — not something a caller can substitute."""
        status, _ = self._conn.uid("store", uid, "+FLAGS", r"(\Seen)")
        return status == "OK"

    def mark_unseen(self, uid: str) -> bool:
        """Clear \\Seen on one message. Same hardcoded-flag constraint as
        mark_seen()."""
        status, _ = self._conn.uid("store", uid, "-FLAGS", r"(\Seen)")
        return status == "OK"

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
    check("only search/fetch/mark_seen/mark_unseen/close are public methods",
          public_methods == {"search", "fetch", "mark_seen", "mark_unseen", "close"})

    mark_seen_src = inspect.getsource(MarvinFolderOnly.mark_seen)
    mark_unseen_src = inspect.getsource(MarvinFolderOnly.mark_unseen)
    check("mark_seen/mark_unseen take no flag argument (uid only, flag is hardcoded)",
          list(inspect.signature(MarvinFolderOnly.mark_seen).parameters) == ["self", "uid"] and
          list(inspect.signature(MarvinFolderOnly.mark_unseen).parameters) == ["self", "uid"])
    check(r"mark_seen/mark_unseen only ever touch \Seen",
          r"\Seen" in mark_seen_src and r"\Seen" in mark_unseen_src)

    class_src = inspect.getsource(MarvinFolderOnly)
    check("no delete/expunge/move/copy exposed anywhere in the class",
          not any(bad in class_src for bad in ["\\Deleted", "expunge", "COPY", "MOVE"]))

    check("ALLOWED_FOLDER is a hardcoded class constant",
          MarvinFolderOnly.ALLOWED_FOLDER == "Marvin")

    init_params = list(inspect.signature(MarvinFolderOnly.__init__).parameters)
    check("__init__ takes no folder argument (only self/address/app_password)",
          init_params == ["self", "address", "app_password"])

    print("PASS  no unsafe methods exposed" if not fails else f"FAIL  {fails} case(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
