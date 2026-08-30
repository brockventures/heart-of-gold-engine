"""
Test for load_config()'s last-known-good fallback (bin/agent-server.py,
2026-08-30, task-1788075644).

Before this fix, a malformed edit to config/agents.json or
config/channels.json raised json.JSONDecodeError straight out of
load_config() -- crashing startup() on a cold boot, or the
/agents/{name}/register hot-reload path on a running server. There was
also no structural validation at all: a syntactically-valid JSON file
missing the expected "agents"/"channels" key, or with a channel entry
missing "id", would silently produce a config object the rest of the
server couldn't actually use.

Modeled on Aerial's git_sync last-known-good-config fallback + Discord
alert on bad config -- see facts/context around the Aerial repo review
2026-08-30. This is deliberately just the config-validation slice of
that pattern, not the full two-repo engine/instance split.
"""

import json

import pytest

from conftest import import_script


@pytest.fixture
def agent_server():
    return import_script("agent-server")


@pytest.fixture
def alerts(agent_server, monkeypatch):
    """Capture calls to add_pending instead of actually touching the
    outbox / hitting Discord."""
    sent = []
    monkeypatch.setattr(
        agent_server, "add_pending",
        lambda channel, content, **kw: sent.append((channel, content)),
    )
    return sent


class TestValidators:
    def test_agents_config_rejects_non_dict(self, agent_server):
        assert agent_server._validate_agents_config([1, 2, 3]) is not None

    def test_agents_config_rejects_missing_agents_key(self, agent_server):
        assert agent_server._validate_agents_config({"nope": {}}) is not None

    def test_agents_config_rejects_non_dict_agent_entry(self, agent_server):
        reason = agent_server._validate_agents_config({"agents": {"Marvin": "oops"}})
        assert reason is not None
        assert "Marvin" in reason

    def test_agents_config_accepts_well_formed(self, agent_server):
        assert agent_server._validate_agents_config({"agents": {"Marvin": {"model": "sonnet"}}}) is None

    def test_channels_config_rejects_missing_channels_key(self, agent_server):
        assert agent_server._validate_channels_config({}) is not None

    def test_channels_config_rejects_entry_missing_id(self, agent_server):
        reason = agent_server._validate_channels_config({"channels": {"general": {"guild_id": "1"}}})
        assert reason is not None
        assert "general" in reason

    def test_channels_config_accepts_well_formed(self, agent_server):
        data = {"channels": {"general": {"id": "123", "guild_id": "456"}}}
        assert agent_server._validate_channels_config(data) is None


class TestLoadValidatedConfig:
    @pytest.mark.asyncio
    async def test_missing_file_returns_none_quietly(self, agent_server, alerts, tmp_path):
        result = await agent_server._load_validated_config(
            tmp_path / "does-not-exist.json", "test.json", agent_server._validate_agents_config
        )
        assert result is None
        assert alerts == []  # missing file is a warning, not an alert-worthy edit

    @pytest.mark.asyncio
    async def test_malformed_json_returns_none_and_alerts(self, agent_server, alerts, tmp_path):
        bad = tmp_path / "agents.json"
        bad.write_text("{not valid json")
        result = await agent_server._load_validated_config(
            bad, "config/agents.json", agent_server._validate_agents_config
        )
        assert result is None
        assert len(alerts) == 1
        channel, content = alerts[0]
        assert channel == "signals"
        assert "config/agents.json" in content
        assert "failed to parse" in content

    @pytest.mark.asyncio
    async def test_schema_invalid_json_returns_none_and_alerts(self, agent_server, alerts, tmp_path):
        bad = tmp_path / "channels.json"
        bad.write_text(json.dumps({"channels": {"general": {"guild_id": "no id field"}}}))
        result = await agent_server._load_validated_config(
            bad, "config/channels.json", agent_server._validate_channels_config
        )
        assert result is None
        assert len(alerts) == 1
        assert "failed validation" in alerts[0][1]

    @pytest.mark.asyncio
    async def test_valid_json_returns_parsed_data_no_alert(self, agent_server, alerts, tmp_path):
        good = tmp_path / "agents.json"
        good.write_text(json.dumps({"agents": {"Marvin": {"model": "sonnet"}}}))
        result = await agent_server._load_validated_config(
            good, "config/agents.json", agent_server._validate_agents_config
        )
        assert result == {"agents": {"Marvin": {"model": "sonnet"}}}
        assert alerts == []


class TestLoadConfigFallback:
    @pytest.mark.asyncio
    async def test_bad_edit_keeps_last_known_good_in_memory(self, agent_server, alerts, tmp_path, monkeypatch):
        agents_path = tmp_path / "agents.json"
        channels_path = tmp_path / "channels.json"
        agents_path.write_text(json.dumps({"agents": {"Marvin": {"model": "sonnet"}}}))
        channels_path.write_text(json.dumps({"channels": {"general": {"id": "1"}}}))
        monkeypatch.setattr(agent_server, "AGENTS_CONFIG_PATH", agents_path)
        monkeypatch.setattr(agent_server, "CHANNELS_CONFIG_PATH", channels_path)

        # First load: good config takes effect.
        await agent_server.load_config()
        assert agent_server.agent_config == {"Marvin": {"model": "sonnet"}}
        assert agent_server.channels_config["channels"]["general"]["id"] == "1"
        assert alerts == []

        # Now corrupt agents.json in place (the live-edit scenario) and
        # reload, the way /agents/{name}/register does.
        agents_path.write_text("{totally broken")
        await agent_server.load_config()

        # Last-known-good config from the first load is still in memory...
        assert agent_server.agent_config == {"Marvin": {"model": "sonnet"}}
        # ...and channels_config (which wasn't touched) is untouched too.
        assert agent_server.channels_config["channels"]["general"]["id"] == "1"
        # ...and someone got told about it.
        assert len(alerts) == 1
        assert "config/agents.json" in alerts[0][1]

    @pytest.mark.asyncio
    async def test_cold_start_bad_config_degrades_to_empty_not_a_crash(self, agent_server, alerts, tmp_path, monkeypatch):
        agents_path = tmp_path / "agents.json"
        channels_path = tmp_path / "channels.json"
        agents_path.write_text("{not json at all")
        channels_path.write_text(json.dumps({"channels": {}}))
        monkeypatch.setattr(agent_server, "AGENTS_CONFIG_PATH", agents_path)
        monkeypatch.setattr(agent_server, "CHANNELS_CONFIG_PATH", channels_path)
        monkeypatch.setattr(agent_server, "agent_config", {})
        monkeypatch.setattr(agent_server, "channels_config", {})

        # Must not raise -- this is the exact scenario that used to take
        # down startup() entirely.
        await agent_server.load_config()

        assert agent_server.agent_config == {}
        assert len(alerts) == 1
