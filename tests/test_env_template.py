"""
Tests for config/.env.template — the documented set of environment
variables every install needs, regardless of how it's deployed. Split out
of tests/test_setup.py on 2026-08-18 when that file's setup.sh-wizard
tests were deleted (setup.sh is the Docker-era interactive installer;
this repo has run native systemd since 2026-08-11 and isn't
reinstalled through it — see native-migration-complete-2026-08-11 in
memory). .env.template itself is deploy-method-agnostic — every
native systemd unit still sources config/.env via EnvironmentFile= — so
its own content checks stayed, unlike the wizard-specific ones.
"""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent.parent


class TestEnvTemplate:
    """Validate .env.template has required variables."""

    def setup_method(self):
        self.content = (PACKAGE_ROOT / "config" / ".env.template").read_text()

    def test_has_required_vars(self):
        required = [
            "AGENT_SERVER_TOKEN",
            "OWNER_DISCORD_ID",
        ]
        for var in required:
            assert var in self.content, f"Missing required env var: {var}"

    def test_has_cost_limits(self):
        assert "COST_DAILY_LIMIT" in self.content
        assert "COST_MONTHLY_LIMIT" in self.content

    def test_has_session_secret(self):
        assert "SESSION_SECRET" in self.content

    def test_no_filled_secrets(self):
        """Template should have placeholder values, not real secrets."""
        lines = self.content.strip().split("\n")
        for line in lines:
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                if "#" in value:
                    value = value[:value.index("#")]
                value = value.strip().strip('"').strip("'")
                if key.strip() in ("AGENT_SERVER_TOKEN", "SESSION_SECRET"):
                    assert not value or "..." in value or value.startswith("$") or value.startswith("<"), (
                        f"Template has non-placeholder value for {key.strip()}"
                    )
