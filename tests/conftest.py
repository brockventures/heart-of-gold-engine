"""
Shared pytest fixtures for Karakos test suite.
"""

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent


def import_script(name: str, file_path: Path = None):
    """Import a Python script by name, handling hyphens in filenames.

    Searches bin/ and system/ directories for the script.
    """
    module_name = name.replace("-", "_")

    if file_path is None:
        for search_dir in ["bin", "system", "mcp"]:
            candidate = PACKAGE_ROOT / search_dir / f"{name}.py"
            if candidate.exists():
                file_path = candidate
                break

    if file_path is None or not file_path.exists():
        raise FileNotFoundError(f"Script not found: {name}")

    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    module = importlib.util.module_from_spec(spec)
    # Don't cache in sys.modules — allows reload with different env
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with expected directory structure."""
    dirs = [
        "data/messages",
        "data/memory",
        "data/health",
        "logs/agent-streams",
        "logs/session-summaries",
        "logs/git-events",
        "config",
        "mcp",
        "bin",
        "agents/templates",
        "inbox",
    ]
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    # Create minimal agents config
    agents_config = {
        "agents": {
            "test-agent": {
                "system_prompt": "agents/test-agent/SYSTEM_PROMPT.md",
                "discord_bot_token_env": "DISCORD_BOT_TOKEN_TEST",
            }
        }
    }
    (tmp_path / "config" / "agents.json").write_text(json.dumps(agents_config))

    # Create minimal channels config
    channels_config = {
        "channels": {
            "general": "123456789",
            "signals": "987654321",
        }
    }
    (tmp_path / "config" / "channels.json").write_text(json.dumps(channels_config))

    return tmp_path


@pytest.fixture
def protected_paths_config(tmp_workspace):
    """Create protected paths config for testing."""
    config = {
        "tier1_protected": [
            "system/",
            "config/",
            "bin/agent-server.py",
            "bin/relay.py",
            "Dockerfile",
        ],
        "tier2_review_required": [
            "bin/",
            "agents/templates/",
        ],
        "unprotected_overrides": [
            "agents/*/persona/",
            "agents/*/journal/",
        ],
    }
    config_path = tmp_workspace / "config" / "protected-paths.json"
    config_path.write_text(json.dumps(config))
    return config


@pytest.fixture
def memory_db(tmp_workspace):
    """Create an initialized memory database."""
    db_path = tmp_workspace / "data" / "memory" / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT NOT NULL,
            importance REAL DEFAULT 5.0,
            channel TEXT,
            tags TEXT,
            agents TEXT,
            created_at TIMESTAMP,
            consolidated_at TIMESTAMP DEFAULT NULL,
            embedding BLOB
        );
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            domain TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            pattern_type TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.7,
            reinforcement_count INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        );
    """)
    conn.commit()
    return conn, db_path
