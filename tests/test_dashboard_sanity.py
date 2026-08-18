"""
Dashboard sanity checks — route export validity and session-secret
consistency.

Split out of tests/test_smoke_docker.py on 2026-08-18 when the Docker
build/compose tests in that file were deleted (this repo has run native
systemd since 2026-08-11, see native-migration-complete-2026-08-11 in
memory — the Docker build was no longer testing anything real). These two
checks have nothing to do with Docker; they catch real regressions
(issues #32 and the split-SESSION_SECRET auth bug) regardless of how the
dashboard is deployed, so they moved here instead of being deleted with
the rest of the file.
"""

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent.parent


class TestNextjsRouteExports:
    """Verify Next.js route files only export valid handlers.

    This test directly prevents the verifySessionToken export bug
    that broke Ian's build (issue #32).
    """

    VALID_EXPORTS = {
        "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
        # Next.js config exports
        "dynamic", "dynamicParams", "revalidate", "fetchCache",
        "runtime", "preferredRegion", "maxDuration",
        "generateStaticParams", "generateMetadata", "metadata",
    }

    def test_route_files_have_valid_exports(self):
        """Route files should only export valid Next.js handlers."""
        app_dir = PACKAGE_ROOT / "dashboard" / "app"
        issues = []

        for route_file in app_dir.rglob("route.ts"):
            content = route_file.read_text()
            export_pattern = re.compile(
                r'export\s+(?:async\s+)?function\s+(\w+)'
                r'|export\s*\{\s*([^}]+)\s*\}'
            )
            for match in export_pattern.finditer(content):
                if match.group(1):
                    name = match.group(1)
                    if name not in self.VALID_EXPORTS:
                        rel = route_file.relative_to(PACKAGE_ROOT)
                        issues.append(f"{rel}: invalid export '{name}'")
                elif match.group(2):
                    for name in match.group(2).split(","):
                        name = name.strip().split(" as ")[0].strip()
                        if name and name not in self.VALID_EXPORTS:
                            rel = route_file.relative_to(PACKAGE_ROOT)
                            issues.append(f"{rel}: invalid export '{name}'")

        assert not issues, (
            "Route files have invalid exports:\n" + "\n".join(issues)
        )


class TestSessionSecretConsistency:
    """Verify SESSION_SECRET is handled correctly across the codebase.

    Catches the split-secret bug where route.ts and lib/api.ts each
    generated their own random SESSION_SECRET, making auth permanently
    broken without the env var set.
    """

    def test_no_duplicate_session_secret_definitions(self):
        """Only lib/api.ts should define SESSION_SECRET."""
        auth_route = PACKAGE_ROOT / "dashboard" / "app" / "api" / "auth" / "route.ts"
        content = auth_route.read_text()

        assert "SESSION_SECRET" not in content, (
            "auth/route.ts should not define SESSION_SECRET. "
            "Import generateSessionToken from @/lib/api instead."
        )

    def test_auth_route_imports_from_shared_lib(self):
        """Auth route should import token generation from shared lib."""
        auth_route = PACKAGE_ROOT / "dashboard" / "app" / "api" / "auth" / "route.ts"
        content = auth_route.read_text()

        assert "from \"@/lib/api\"" in content or "from '@/lib/api'" in content, (
            "auth/route.ts should import from @/lib/api for shared session handling"
        )

    def test_no_random_fallback_in_auth_route(self):
        """Auth route must not have crypto.randomBytes fallback for secrets."""
        auth_route = PACKAGE_ROOT / "dashboard" / "app" / "api" / "auth" / "route.ts"
        content = auth_route.read_text()

        assert "randomBytes" not in content, (
            "auth/route.ts should not generate random secrets. "
            "SESSION_SECRET should come from the environment via lib/api.ts."
        )
