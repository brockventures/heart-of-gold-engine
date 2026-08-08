"""
Tests for bin/discord-read.py — direct Discord API read path, independent
of relay's ingest pipeline into message_queue.

Built 2026-08-08 after a real message (Amos, #agent-chat, 08:23:41 UTC)
was found live on Discord but absent from message_queue entirely. This is
the backup/source-of-truth path for exactly that gap, so its channel
allowlist and token resolution are what's under test here — the actual
Discord API call is mocked, not exercised live.
"""

import json

import pytest

from conftest import import_script


@pytest.fixture
def discord_read():
    return import_script("discord-read")


@pytest.fixture
def workspace(tmp_path, discord_read, monkeypatch):
    """Point the module at an isolated config dir instead of /workspace."""
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(discord_read, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


def write_channels(workspace, channels):
    (workspace / "config" / "channels.json").write_text(json.dumps({"channels": channels}))


def write_agents(workspace, agents):
    (workspace / "config" / "agents.json").write_text(json.dumps({"agents": agents}))


def test_load_channel_id_resolves_known_channel(discord_read, workspace):
    write_channels(workspace, {"agent-chat": {"id": "12345"}})
    assert discord_read.load_channel_id("agent-chat") == "12345"


def test_load_channel_id_rejects_unknown_channel(discord_read, workspace):
    """No literal-ID fallback (unlike discord-notify.sh) — an unresolvable
    name is a hard error. This is the allowlist: the whole point of this
    script is a scoped read path, not an arbitrary-channel one."""
    write_channels(workspace, {"agent-chat": {"id": "12345"}})
    with pytest.raises(SystemExit):
        discord_read.load_channel_id("some-other-channel")


def test_load_channel_id_rejects_channel_missing_id(discord_read, workspace):
    write_channels(workspace, {"agent-chat": {}})
    with pytest.raises(SystemExit):
        discord_read.load_channel_id("agent-chat")


def test_load_bot_token_resolves_named_agent(discord_read, workspace, monkeypatch):
    write_agents(workspace, {
        "Marvin": {"discord_bot_token_env": "TEST_MARVIN_TOKEN"},
        "relay": {},
    })
    monkeypatch.setenv("TEST_MARVIN_TOKEN", "shh-its-a-secret")
    assert discord_read.load_bot_token("Marvin") == "shh-its-a-secret"


def test_load_bot_token_falls_back_to_first_available(discord_read, workspace, monkeypatch):
    write_agents(workspace, {
        "relay": {},
        "Marvin": {"discord_bot_token_env": "TEST_MARVIN_TOKEN"},
    })
    monkeypatch.setenv("TEST_MARVIN_TOKEN", "shh-its-a-secret")
    assert discord_read.load_bot_token(None) == "shh-its-a-secret"


def test_load_bot_token_errors_when_env_var_unset(discord_read, workspace, monkeypatch):
    write_agents(workspace, {"Marvin": {"discord_bot_token_env": "TEST_MARVIN_TOKEN_UNSET"}})
    monkeypatch.delenv("TEST_MARVIN_TOKEN_UNSET", raising=False)
    with pytest.raises(SystemExit):
        discord_read.load_bot_token("Marvin")


def test_load_bot_token_errors_for_unknown_agent(discord_read, workspace, monkeypatch):
    write_agents(workspace, {"Marvin": {"discord_bot_token_env": "TEST_MARVIN_TOKEN"}})
    monkeypatch.setenv("TEST_MARVIN_TOKEN", "shh-its-a-secret")
    with pytest.raises(SystemExit):
        discord_read.load_bot_token("NotARealAgent")


def test_fetch_messages_retries_on_429(discord_read, monkeypatch):
    """Discord's rate-limit response includes retry_after in the JSON body
    — the fetch should sleep that long and retry once, transparently."""
    import urllib.error

    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = json.dumps(payload).encode()

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class RateLimited(urllib.error.HTTPError):
        def __init__(self, req):
            self._body = json.dumps({"retry_after": 0.01}).encode()
            super().__init__(req.full_url, 429, "Too Many Requests", {}, None)

        def read(self):
            return self._body

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimited(req)
        return FakeResponse([{"id": "1", "content": "hi"}])

    monkeypatch.setattr(discord_read.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(discord_read.time, "sleep", lambda s: None)

    result = discord_read.fetch_messages("chan-id", "tok", 20, None, None)
    assert result == [{"id": "1", "content": "hi"}]
    assert calls["n"] == 2
