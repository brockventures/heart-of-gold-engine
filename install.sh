#!/usr/bin/env bash
# Karakos Installer for Linux/macOS
# Run: curl -fsSL https://raw.githubusercontent.com/mcarmody/karakos-package/main/install.sh | bash
# Or:  bash install.sh
#
# Override the upstream source via env vars (useful for forks):
#   KARAKOS_REPO=user/repo  bash install.sh

set -euo pipefail

INSTALL_DIR="${KARAKOS_DIR:-$HOME/karakos}"
KARAKOS_REPO="${KARAKOS_REPO:-mcarmody/karakos-package}"
KARAKOS_REPO_URL="${KARAKOS_REPO_URL:-https://github.com/${KARAKOS_REPO}.git}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}==>${NC} $*"; }
warn() { echo -e "${YELLOW}Warning:${NC} $*"; }
err()  { echo -e "${RED}Error:${NC} $*" >&2; }

echo ""
echo -e "${CYAN}================================${NC}"
echo -e "${CYAN}  Karakos Installer${NC}"
echo -e "${CYAN}================================${NC}"
echo ""

# --- Detect OS ---
OS="$(uname -s)"
case "$OS" in
    Linux*)  PLATFORM="linux" ;;
    Darwin*) PLATFORM="mac" ;;
    *)       err "Unsupported OS: $OS"; exit 1 ;;
esac

# --- Package manager ---
install_pkg() {
    local pkg="$1"
    if [ "$PLATFORM" = "mac" ]; then
        if command -v brew &>/dev/null; then
            brew install "$pkg"
        else
            err "Homebrew not found. Install it first: https://brew.sh"
            exit 1
        fi
    else
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq && sudo apt-get install -y -qq "$pkg"
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y "$pkg"
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm "$pkg"
        else
            err "No supported package manager found. Please install $pkg manually."
            exit 1
        fi
    fi
}

# --- Git ---
if ! command -v git &>/dev/null; then
    log "Installing git..."
    install_pkg git
else
    log "Git found: $(git --version)"
fi

# --- Docker ---
if ! command -v docker &>/dev/null; then
    log "Installing Docker..."
    if [ "$PLATFORM" = "mac" ]; then
        if command -v brew &>/dev/null; then
            brew install --cask docker
            log "Docker Desktop installed. Please launch it from Applications, then re-run this script."
            exit 0
        fi
    else
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER"
        warn "You may need to log out and back in for Docker permissions to take effect."
    fi
else
    log "Docker found: $(docker --version)"
fi

# --- Check Docker is running ---
if ! docker info &>/dev/null 2>&1; then
    err "Docker is installed but not running."
    if [ "$PLATFORM" = "mac" ]; then
        err "Please launch Docker Desktop from Applications and wait for it to start."
    else
        err "Try: sudo systemctl start docker"
    fi
    exit 1
fi

# --- Docker Compose ---
if ! docker compose version &>/dev/null 2>&1; then
    err "Docker Compose v2 not found."
    err "Update Docker or install the compose plugin:"
    err "  https://docs.docker.com/compose/install/"
    exit 1
fi
log "Docker Compose available."

# --- jq ---
if ! command -v jq &>/dev/null; then
    log "Installing jq..."
    install_pkg jq
else
    log "jq found."
fi

# --- curl ---
if ! command -v curl &>/dev/null; then
    log "Installing curl..."
    install_pkg curl
fi

# --- Clone ---
# Verify that $INSTALL_DIR actually looks like a karakos checkout before
# we offer to nuke it. Without this guard, a user who points
# KARAKOS_DIR at $HOME (or any pre-existing dir) and answers "y" loses
# everything in that directory.
is_karakos_dir() {
    local d="$1"
    [ -f "$d/setup.sh" ] && [ -d "$d/bin" ] && [ -d "$d/agents" ] && [ -f "$d/README.md" ]
}

if [ -d "$INSTALL_DIR" ]; then
    log "Directory $INSTALL_DIR already exists."
    if ! is_karakos_dir "$INSTALL_DIR"; then
        echo "  Refusing to overwrite: $INSTALL_DIR does not look like a karakos checkout"
        echo "  (missing setup.sh / bin/ / agents/ / README.md)."
        echo "  Move or remove it manually, or set KARAKOS_DIR to a different path."
        exit 1
    fi
    read -p "  Overwrite existing karakos install? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$INSTALL_DIR"
        log "Cloning karakos into $INSTALL_DIR..."
        git clone "$KARAKOS_REPO_URL" "$INSTALL_DIR"
    else
        log "Keeping existing installation."
    fi
else
    log "Cloning karakos into $INSTALL_DIR..."
    git clone "$KARAKOS_REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
chmod +x setup.sh

echo ""
echo -e "${CYAN}================================${NC}"
echo -e "${CYAN}  Prerequisites installed.${NC}"
echo -e "${CYAN}  Launching setup wizard...${NC}"
echo -e "${CYAN}================================${NC}"
echo ""

# Run setup with error handling
setup_exit=0
./setup.sh || setup_exit=$?

if [ "$setup_exit" -eq 0 ]; then
    echo ""
    log "Installation complete."
    echo -e "  Logs:  docker compose -f $INSTALL_DIR/config/docker-compose.yml logs -f"
    echo ""
else
    err "Setup wizard failed or was cancelled (exit code $setup_exit)."
    err "Re-run with: cd $INSTALL_DIR && ./setup.sh"
    exit "$setup_exit"
fi
