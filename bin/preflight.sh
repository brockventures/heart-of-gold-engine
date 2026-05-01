#!/usr/bin/env bash
# bin/preflight.sh — Pre-install host state checks for Karakos
#
# Usage:
#   ./bin/preflight.sh [--json] [--quiet]
#
# Exits 0 if all checks pass (warnings do not affect the exit code).
# Exits 1 if any check fails.
#
# See docs/QUICKSTART.md and Makefile for integration into the install path.

set -uo pipefail

# =============================================================================
# Working-directory anchoring
# Allows the script to be called from any cwd, e.g.:
#   bash /abs/path/to/karakos-package/bin/preflight.sh
# PREFLIGHT_REPO_ROOT may be overridden for testing.
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${PREFLIGHT_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$REPO_ROOT"

# =============================================================================
# Argument parsing
# =============================================================================
JSON_MODE=false
QUIET_MODE=false

for _arg in "$@"; do
    case "$_arg" in
        --json)  JSON_MODE=true  ;;
        --quiet) QUIET_MODE=true ;;
    esac
done

# =============================================================================
# Color setup — only emit ANSI codes when stdout is a real TTY
# =============================================================================
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    C_GREEN="$(tput setaf 2)"
    C_YELLOW="$(tput setaf 3)"
    C_RED="$(tput setaf 1)"
    C_RESET="$(tput sgr0)"
else
    C_GREEN=""
    C_YELLOW=""
    C_RED=""
    C_RESET=""
fi

# =============================================================================
# Result accumulation
# =============================================================================
declare -a _NAMES=()
declare -a _STATUSES=()
declare -a _REASONS=()
_FAILURES=0
_WARNINGS=0

# =============================================================================
# Output helpers
# =============================================================================
_pass() {
    local name="$1"
    _NAMES+=("$name")
    _STATUSES+=("pass")
    _REASONS+=("null")
    if [ "$JSON_MODE" = false ] && [ "$QUIET_MODE" = false ]; then
        printf '%s✓ %s%s\n' "$C_GREEN" "$name" "$C_RESET"
    fi
}

_warn() {
    local name="$1" reason="$2" remediation="$3"
    _NAMES+=("$name")
    _STATUSES+=("warn")
    _REASONS+=("$reason")
    _WARNINGS=$((_WARNINGS + 1))
    if [ "$JSON_MODE" = false ]; then
        printf '%s⚠ %s: %s%s\n' "$C_YELLOW" "$name" "$reason" "$C_RESET"
        printf '  %s\n' "$remediation"
    fi
}

_fail() {
    local name="$1" reason="$2" remediation="$3"
    _NAMES+=("$name")
    _STATUSES+=("fail")
    _REASONS+=("$reason")
    _FAILURES=$((_FAILURES + 1))
    if [ "$JSON_MODE" = false ]; then
        printf '%s✗ %s: %s%s\n' "$C_RED" "$name" "$reason" "$C_RESET"
        printf '  %s\n' "$remediation"
    fi
}

# =============================================================================
# Check 1: docker_engine_reachable
# Use timeout 10 so a hung Docker socket doesn't make preflight itself hang.
# =============================================================================
_check_docker_engine_reachable() {
    timeout 10 docker info >/dev/null 2>&1
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        _pass "docker_engine_reachable"
    elif [ "$rc" -eq 124 ]; then
        _fail "docker_engine_reachable" \
            "Docker engine reachable but unresponsive (timed out after 10s). Docker Desktop may be mid-start, the daemon may be hung, or the socket may be wedged." \
            "Restart Docker Desktop / \`sudo systemctl restart docker\`."
    else
        _fail "docker_engine_reachable" \
            "Docker engine not reachable." \
            "Install Docker Desktop (Windows/macOS) or docker-engine (Linux): https://docs.docker.com/get-docker/"
    fi
}

# =============================================================================
# Check 2: docker_compose_v2
# =============================================================================
_check_docker_compose_v2() {
    local version_output
    version_output="$(docker compose version 2>/dev/null)" || {
        _fail "docker_compose_v2" \
            "Docker Compose v2 not found." \
            "Update Docker Desktop, or install the compose-plugin package on Linux."
        return
    }

    # Parse e.g. "Docker Compose version v2.27.0"
    local version_str major
    version_str="$(printf '%s' "$version_output" | grep -oE 'v?[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
    major="$(printf '%s' "$version_str" | grep -oE '^v?[0-9]+' | tr -d 'v')"

    if [ -z "$major" ] || ! [ "$major" -ge 2 ] 2>/dev/null; then
        _fail "docker_compose_v2" \
            "Docker Compose v2 not found (found: ${version_str:-unknown})." \
            "Update Docker Desktop, or install the compose-plugin package on Linux."
    else
        _pass "docker_compose_v2"
    fi
}

# =============================================================================
# Check 3: wsl_integration_healthy  (only runs inside WSL)
# Use timeout 10 here too — same rationale as check 1.
# =============================================================================
_check_wsl_integration_healthy() {
    [ -n "${WSL_DISTRO_NAME:-}" ] || return 0

    local docker_out rc
    docker_out="$(timeout 10 docker info 2>&1)"
    rc=$?

    if [ "$rc" -eq 124 ]; then
        _fail "wsl_integration_healthy" \
            "Docker reachable but unresponsive from WSL (timed out)." \
            "Restart Docker Desktop and re-toggle WSL integration."
    elif [ "$rc" -ne 0 ] || printf '%s' "$docker_out" | grep -qE "Permission denied|WSL distro is not running"; then
        _fail "wsl_integration_healthy" \
            "Docker Desktop's WSL integration is not running for this distro." \
            "Open Docker Desktop → Settings → Resources → WSL Integration, toggle ${WSL_DISTRO_NAME} off, apply, toggle on, apply."
    else
        _pass "wsl_integration_healthy"
    fi
}

# =============================================================================
# Check 4: architecture_supported
# =============================================================================
_check_architecture_supported() {
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64|aarch64|arm64)
            _pass "architecture_supported"
            ;;
        *)
            _fail "architecture_supported" \
                "Unsupported architecture: ${arch}. Karakos images are published for amd64 and arm64 only." \
                "Use an x86_64 or arm64 machine."
            ;;
    esac
}

# =============================================================================
# Check 5: shell_script_line_endings
# Belt-and-suspenders alongside .gitattributes — catches clones made before
# the LF-enforcement fix landed or with core.autocrlf=true on Windows.
# =============================================================================
_check_shell_script_line_endings() {
    local crlf_files=()
    local f

    for f in "$REPO_ROOT"/bin/*.sh "$REPO_ROOT/bin/kara"; do
        [ -f "$f" ] || continue
        if file "$f" 2>/dev/null | grep -q "CRLF line terminators"; then
            crlf_files+=("$(basename "$f")")
        fi
    done

    if [ "${#crlf_files[@]}" -gt 0 ]; then
        local files_list
        files_list="${crlf_files[*]}"
        _fail "shell_script_line_endings" \
            "${files_list} has Windows line endings." \
            "Run: git config core.autocrlf input && git rm --cached -r . && git reset --hard. Or: dos2unix bin/*.sh bin/kara."
    else
        _pass "shell_script_line_endings"
    fi
}

# =============================================================================
# .env parser helpers
#
# Reads config/.env line-by-line WITHOUT source/eval — no arbitrary code exec.
# Handles:
#   DASHBOARD_PORT=3000
#   export DASHBOARD_PORT=3000
#   declare -x DASHBOARD_PORT=3000
#   DASHBOARD_PORT="3000"   (double-quoted)
#   DASHBOARD_PORT='3000'   (single-quoted)
# Skips blank lines and comments.
#
# Cross-reference: required_vars list mirrors bin/entrypoint.sh.
# If entrypoint.sh adds new required vars, update _REQUIRED_VARS below too.
# =============================================================================
_parse_env_value() {
    local target="$1" env_file="$2"
    local line name value

    while IFS= read -r line || [ -n "$line" ]; do
        # Strip leading spaces and tabs
        line="${line#"${line%%[!	 ]*}"}"

        # Skip blank lines and comments
        [[ -z "$line" || "$line" == \#* ]] && continue

        # Strip optional prefixes
        line="${line#export }"
        line="${line#declare -x }"

        # Split on first '='
        name="${line%%=*}"
        value="${line#*=}"

        # Strip surrounding double or single quotes from value
        if [[ "$value" == \"*\" ]]; then
            value="${value#\"}"
            value="${value%\"}"
        elif [[ "$value" == \'*\' ]]; then
            value="${value#\'}"
            value="${value%\'}"
        fi

        if [[ "$name" == "$target" && -n "$value" ]]; then
            printf '%s' "$value"
            return 0
        fi
    done < "$env_file"

    return 1
}

# Returns 0 if any variable matching the glob pattern has a non-empty value.
# shellcheck disable=SC2254
_env_has_pattern() {
    local pattern="$1" env_file="$2"
    local line name value

    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[!	 ]*}"}"
        [[ -z "$line" || "$line" == \#* ]] && continue

        line="${line#export }"
        line="${line#declare -x }"

        name="${line%%=*}"
        value="${line#*=}"

        if [[ "$value" == \"*\" ]]; then
            value="${value#\"}"
            value="${value%\"}"
        elif [[ "$value" == \'*\' ]]; then
            value="${value#\'}"
            value="${value%\'}"
        fi

        if [[ "$name" == $pattern && -n "$value" ]]; then
            return 0
        fi
    done < "$env_file"

    return 1
}

# =============================================================================
# Check 6: required_env_vars
# Required vars mirror bin/entrypoint.sh — update both if requirements change.
# =============================================================================
_REQUIRED_VARS=("DASHBOARD_PORT" "AGENT_SERVER_TOKEN")
_ENV_FILE="$REPO_ROOT/config/.env"
_PARSED_DASHBOARD_PORT=""

_check_required_env_vars() {
    if [ ! -f "$_ENV_FILE" ]; then
        _fail "required_env_vars" \
            "config/.env missing or incomplete. Required: DASHBOARD_PORT, AGENT_SERVER_TOKEN, at least one DISCORD_TOKEN_*." \
            "See config/.env.template."
        return
    fi

    local all_ok=true var val

    for var in "${_REQUIRED_VARS[@]}"; do
        val="$(_parse_env_value "$var" "$_ENV_FILE")" || true
        if [ -z "$val" ]; then
            all_ok=false
        elif [ "$var" = "DASHBOARD_PORT" ]; then
            _PARSED_DASHBOARD_PORT="$val"
        fi
    done

    # At least one Discord token must be present.
    # Matches DISCORD_BOT_TOKEN_PRIMARY, DISCORD_TOKEN_RELAY, etc.
    if ! _env_has_pattern "*DISCORD*TOKEN*" "$_ENV_FILE"; then
        all_ok=false
    fi

    if [ "$all_ok" = false ]; then
        _fail "required_env_vars" \
            "config/.env missing or incomplete. Required: DASHBOARD_PORT, AGENT_SERVER_TOKEN, at least one DISCORD_TOKEN_*." \
            "See config/.env.template."
    else
        _pass "required_env_vars"
    fi
}

# =============================================================================
# Check 7: port_available
# Skipped if required_env_vars failed (no DASHBOARD_PORT to check).
# =============================================================================
_check_port_available() {
    [ -n "$_PARSED_DASHBOARD_PORT" ] || return 0

    local port="$_PARSED_DASHBOARD_PORT" in_use=false

    if command -v ss >/dev/null 2>&1; then
        ss -lnt 2>/dev/null | grep -q ":${port} " && in_use=true
    elif command -v lsof >/dev/null 2>&1; then
        lsof -i ":${port}" >/dev/null 2>&1 && in_use=true
    else
        _warn "port_available" \
            "Cannot check port ${port}: neither ss nor lsof is installed." \
            "Install iproute2 (ss) or lsof, or verify manually: lsof -i :${port}"
        return
    fi

    if [ "$in_use" = true ]; then
        _fail "port_available" \
            "Port ${port} is already in use on this host." \
            "Change DASHBOARD_PORT in config/.env or stop the conflicting service."
    else
        _pass "port_available"
    fi
}

# =============================================================================
# Check 8: disk_space
# Warn below 10GB, fail below 5GB.
# Uses timeout 10 on docker info per the spec requirement.
# Skipped gracefully if Docker is not running (already caught by check 1).
# =============================================================================
_check_disk_space() {
    local docker_root
    docker_root="$(timeout 10 docker info 2>/dev/null | grep "Docker Root Dir" | awk '{print $NF}')"

    if [ -z "$docker_root" ]; then
        # Docker unreachable — already reported by docker_engine_reachable.
        # Don't add noise by repeating the failure here.
        return
    fi

    local free_gb
    free_gb="$(df -BG "$docker_root" 2>/dev/null | awk 'NR==2 {gsub("G",""); print $4}')"

    if [ -z "$free_gb" ]; then
        _warn "disk_space" \
            "Could not determine free disk space for Docker root (${docker_root})." \
            "Run: df -h ${docker_root}"
        return
    fi

    if [ "$free_gb" -lt 5 ]; then
        _fail "disk_space" \
            "Less than 5GB free in Docker root. Image + dashboard layer is ~3-4GB." \
            "Free space or move Docker root."
    elif [ "$free_gb" -lt 10 ]; then
        _warn "disk_space" \
            "Low disk space (${free_gb}GB free). Install will succeed but you'll have little headroom for logs and data." \
            "Consider freeing up space before proceeding."
    else
        _pass "disk_space"
    fi
}

# =============================================================================
# Run all checks unconditionally
# =============================================================================
_check_docker_engine_reachable
_check_docker_compose_v2
_check_wsl_integration_healthy
_check_architecture_supported
_check_shell_script_line_endings
_check_required_env_vars
_check_port_available
_check_disk_space

# =============================================================================
# Final output
# =============================================================================
if [ "$JSON_MODE" = true ]; then
    n="${#_NAMES[@]}"
    printf '{\n'
    printf '  "checks": [\n'
    for (( i = 0; i < n; i++ )); do
        name="${_NAMES[$i]}"
        status="${_STATUSES[$i]}"
        reason="${_REASONS[$i]}"

        if [ "$reason" = "null" ]; then
            reason_json="null"
        else
            reason_esc="${reason//\"/\\\"}"
            reason_json="\"${reason_esc}\""
        fi

        if [ $(( i + 1 )) -lt "$n" ]; then
            comma=","
        else
            comma=""
        fi
        printf '    {"name": "%s", "status": "%s", "reason": %s}%s\n' \
            "$name" "$status" "$reason_json" "$comma"
    done
    printf '  ],\n'

    if [ "$_FAILURES" -gt 0 ]; then pass_val="false"; else pass_val="true"; fi

    printf '  "pass": %s,\n'     "$pass_val"
    printf '  "warnings": %d,\n' "$_WARNINGS"
    printf '  "failures": %d\n'  "$_FAILURES"
    printf '}\n'
else
    printf '\n'
    if [ "$_FAILURES" -gt 0 ]; then
        printf '%sPreflight: FAIL — %d issue(s) above%s\n' "$C_RED" "$_FAILURES" "$C_RESET"
    elif [ "$_WARNINGS" -gt 0 ]; then
        printf '%sPreflight: PASS (%d warning(s))%s\n' "$C_GREEN" "$_WARNINGS" "$C_RESET"
    else
        printf '%sPreflight: PASS%s\n' "$C_GREEN" "$C_RESET"
    fi
fi

if [ "$_FAILURES" -gt 0 ]; then
    exit 1
fi
exit 0
