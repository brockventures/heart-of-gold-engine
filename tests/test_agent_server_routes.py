"""
Smoke tests for bin/agent-server.py routing surface.

The full server is heavy (event loop + sqlite + subprocesses) so we avoid
booting it. These tests parse the source and check that the routes
required by the rest of the system are wired up.
"""

import ast
from pathlib import Path

from conftest import PACKAGE_ROOT

AGENT_SERVER = PACKAGE_ROOT / "bin" / "agent-server.py"


def test_agent_server_parses():
    ast.parse(AGENT_SERVER.read_text())


def test_register_endpoint_wired():
    """bin/create-agent.sh POSTs to /agents/{name}/register — the server
    must register a handler for that route or the hot-load is dead."""
    src = AGENT_SERVER.read_text()
    assert 'app.router.add_post("/agents/{name}/register", handle_agent_register)' in src
    assert "async def handle_agent_register" in src


def test_register_endpoint_validates_name():
    """Hot-register accepts a path-segment name, so it must reject anything
    outside [a-zA-Z0-9_-] before touching disk."""
    src = AGENT_SERVER.read_text()
    assert "_AGENT_NAME_RE" in src
    assert "Invalid agent name" in src


def test_register_endpoint_reloads_config():
    """The handler must call load_config() so the new agent's entry in
    agents.json actually shows up before we try to spawn it."""
    src = AGENT_SERVER.read_text()
    register_idx = src.index("async def handle_agent_register")
    next_def = src.index("\nasync def ", register_idx + 1)
    body = src[register_idx:next_def]
    assert "await load_config()" in body
    assert "start_agent_subprocess" in body


def test_existing_routes_still_present():
    """Don't accidentally clobber the routes we already had."""
    src = AGENT_SERVER.read_text()
    for route in [
        '"/message"',
        '"/health"',
        '"/agents"',
        '"/agents/{name}/reset"',
        '"/agents/{name}/reload"',
        '"/cost"',
    ]:
        assert route in src, f"missing route: {route}"


def test_create_agent_script_targets_register():
    """The script and the server must agree on the endpoint path."""
    create_agent = PACKAGE_ROOT / "bin" / "create-agent.sh"
    assert create_agent.exists()
    body = create_agent.read_text()
    assert "/agents/$AGENT_NAME/register" in body
