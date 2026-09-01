"""
Tests for the email skill's 2026-09-01 additions:

1. gmail_guard.MarvinFolderOnly widened to allow toggling \\Seen (Ian's
   explicit sign-off: "I am good with you reading/unreading anything in
   the Marvin folder as a matter of record") — still folder-hardcoded,
   still flag-hardcoded, no delete/move/expunge.
2. read_marvin_folder.py's create_intake_tasks(): every genuinely-new
   message spawns a taskboard entry (source="email-intake") so it has to
   be explicitly closed out, per Ian: "make sure emails get addressed
   when they come in, not just noticed."
3. send_email.py's standing bcc to Ian (IAN_BCC_ADDRESS / with_standing_bcc):
   Mailgun sends never touch Ian's own Gmail, so without this there's no
   trail on his side of what went out under his name. Not caller-optional.

The tools-server.py side of the loop (completing an email-intake task
flips \\Seen via mark_email_read.py) is covered separately in
test_tools_server.py::TestTaskboardEmailIntakeMarkRead.
"""

import json
import sys

import pytest

from conftest import import_script, PACKAGE_ROOT

EMAIL_SCRIPTS_DIR = PACKAGE_ROOT / "skills" / "email" / "scripts"


@pytest.fixture(autouse=True)
def _scripts_on_path():
    # gmail_guard.py / read_marvin_folder.py / mark_email_read.py all do
    # bare `from gmail_guard import ...` (or don't, but sit in the same
    # dir) — only resolves when their own directory is on sys.path.
    path = str(EMAIL_SCRIPTS_DIR)
    added = path not in sys.path
    if added:
        sys.path.insert(0, path)
    yield
    if added:
        sys.path.remove(path)


class FakeImapConn:
    """Minimal stand-in for imaplib.IMAP4_SSL, just enough surface for
    MarvinFolderOnly to drive."""

    def __init__(self):
        self.select_calls = []
        self.store_calls = []
        self.logged_out = False

    def login(self, address, app_password):
        self.address = address

    def select(self, folder, readonly=False):
        self.select_calls.append({"folder": folder, "readonly": readonly})
        return ("OK", [b"1"])

    def uid(self, command, *args):
        if command == "store":
            self.store_calls.append(args)
            return ("OK", [b""])
        if command == "search":
            return ("OK", [b"1 2 3"])
        if command == "fetch":
            return ("OK", [(b"1 (UID 1)", b"raw")])
        raise AssertionError(f"unexpected uid command: {command}")

    def logout(self):
        self.logged_out = True


@pytest.fixture
def gmail_guard(monkeypatch):
    mod = import_script("gmail_guard", file_path=EMAIL_SCRIPTS_DIR / "gmail_guard.py")
    fake = FakeImapConn()
    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", lambda host: fake)
    mod._fake = fake  # stash for assertions
    return mod


class TestGmailGuardWidenedScope:
    def test_folder_is_selected_read_write_not_examine(self, gmail_guard):
        """EXAMINE (readonly=True) would reject STORE outright — the
        mailbox has to open read-write now for mark_seen/mark_unseen to
        work at all."""
        gmail_guard.MarvinFolderOnly("addr", "pw")
        assert gmail_guard._fake.select_calls[0]["readonly"] is False

    def test_mark_seen_sends_hardcoded_seen_flag(self, gmail_guard):
        guard = gmail_guard.MarvinFolderOnly("addr", "pw")
        ok = guard.mark_seen("42")
        assert ok is True
        assert gmail_guard._fake.store_calls[-1] == ("42", "+FLAGS", r"(\Seen)")

    def test_mark_unseen_sends_hardcoded_seen_flag(self, gmail_guard):
        guard = gmail_guard.MarvinFolderOnly("addr", "pw")
        ok = guard.mark_unseen("42")
        assert ok is True
        assert gmail_guard._fake.store_calls[-1] == ("42", "-FLAGS", r"(\Seen)")

    def test_search_and_fetch_still_work(self, gmail_guard):
        guard = gmail_guard.MarvinFolderOnly("addr", "pw")
        status, _ = guard.search("ALL")
        assert status == "OK"
        status, _ = guard.fetch("1", "(RFC822)")
        assert status == "OK"

    def test_no_public_method_accepts_a_folder_or_flag_argument(self, gmail_guard):
        """mark_seen/mark_unseen take (self, uid) only — a caller cannot
        pass a different flag or folder through this interface."""
        import inspect
        assert list(inspect.signature(gmail_guard.MarvinFolderOnly.mark_seen).parameters) == ["self", "uid"]
        assert list(inspect.signature(gmail_guard.MarvinFolderOnly.mark_unseen).parameters) == ["self", "uid"]


class TestCreateIntakeTasks:
    def _load(self, mod):
        return json.loads((mod.WORKSPACE_ROOT / "data" / "taskboard.json").read_text())

    def _mod(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        return import_script(
            "read_marvin_folder", file_path=EMAIL_SCRIPTS_DIR / "read_marvin_folder.py"
        )

    def test_creates_one_task_per_message(self, monkeypatch, tmp_path):
        mod = self._mod(monkeypatch, tmp_path)
        mod.create_intake_tasks([
            {"uid": 5, "subject": "Hi", "from": "a@b.com"},
            {"uid": 6, "subject": "Bye", "from": "c@d.com"},
        ])
        tasks = self._load(mod)["tasks"]
        assert len(tasks) == 2
        assert {t["email_uid"] for t in tasks} == {5, 6}
        assert all(t["source"] == "email-intake" for t in tasks)
        assert all(t["status"] == "pending" for t in tasks)

    def test_title_includes_subject_and_sender(self, monkeypatch, tmp_path):
        mod = self._mod(monkeypatch, tmp_path)
        mod.create_intake_tasks([{"uid": 1, "subject": "Re: budget", "from": "ian@x.com"}])
        task = self._load(mod)["tasks"][0]
        assert "Re: budget" in task["title"]
        assert "ian@x.com" in task["title"]

    def test_empty_list_is_a_noop(self, monkeypatch, tmp_path):
        mod = self._mod(monkeypatch, tmp_path)
        mod.create_intake_tasks([])
        assert not (tmp_path / "data" / "taskboard.json").exists()

    def test_preserves_existing_tasks(self, monkeypatch, tmp_path):
        mod = self._mod(monkeypatch, tmp_path)
        existing = {"tasks": [{"id": "task-preexisting", "title": "old", "status": "done"}]}
        (tmp_path / "data" / "taskboard.json").write_text(json.dumps(existing))

        mod.create_intake_tasks([{"uid": 9, "subject": "New", "from": "x@y.com"}])

        tasks = self._load(mod)["tasks"]
        ids = {t["id"] for t in tasks}
        assert "task-preexisting" in ids
        assert len(tasks) == 2

    def test_ids_unique_across_messages_in_one_call(self, monkeypatch, tmp_path):
        mod = self._mod(monkeypatch, tmp_path)
        mod.create_intake_tasks([
            {"uid": 1, "subject": "a", "from": "a@a.com"},
            {"uid": 2, "subject": "b", "from": "b@b.com"},
        ])
        ids = [t["id"] for t in self._load(mod)["tasks"]]
        assert len(ids) == len(set(ids))


class TestStandingBccToIan:
    @pytest.fixture
    def send_email(self):
        return import_script("send_email", file_path=EMAIL_SCRIPTS_DIR / "send_email.py")

    def test_no_caller_bcc_gets_just_ian(self, send_email):
        assert send_email.with_standing_bcc("") == send_email.IAN_BCC_ADDRESS

    def test_caller_bcc_gets_ian_appended(self, send_email):
        result = send_email.with_standing_bcc("someone@example.com")
        addrs = [a.strip() for a in result.split(",")]
        assert "someone@example.com" in addrs
        assert send_email.IAN_BCC_ADDRESS in addrs
        assert len(addrs) == 2

    def test_caller_bcc_already_including_ian_is_not_duplicated(self, send_email):
        result = send_email.with_standing_bcc(send_email.IAN_BCC_ADDRESS)
        addrs = [a.strip() for a in result.split(",")]
        assert addrs == [send_email.IAN_BCC_ADDRESS]

    def test_case_insensitive_dedup(self, send_email):
        result = send_email.with_standing_bcc(send_email.IAN_BCC_ADDRESS.upper())
        addrs = [a.strip() for a in result.split(",")]
        assert len(addrs) == 1

    def test_multiple_caller_bcc_addresses_all_preserved(self, send_email):
        result = send_email.with_standing_bcc("a@example.com, b@example.com")
        addrs = {a.strip() for a in result.split(",")}
        assert addrs == {"a@example.com", "b@example.com", send_email.IAN_BCC_ADDRESS}
