#!/bin/bash
# Entry point for Linux/Mac — installs Python & Docker if needed, then runs _setup_wizard.py
set -e
cd "$(dirname "$0")"

RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m' NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }

# --- Find or install Python 3.10+ ---

find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            if "$cmd" -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
                echo "$cmd"
                return
            fi
        fi
    done
}

PYTHON=$(find_python)

if [ -z "$PYTHON" ]; then
    warn "Python 3.10+ not found. Installing..."

    if [ "$(uname)" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install python@3
        else
            err "Install Homebrew (https://brew.sh) or Python from https://www.python.org/downloads/"
            exit 1
        fi
    elif [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|debian|pop|linuxmint|raspbian)
                sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip ;;
            fedora)
                sudo dnf install -y python3 python3-pip ;;
            arch|manjaro)
                sudo pacman -Sy --noconfirm python python-pip ;;
            opensuse*|sles)
                sudo zypper install -y python3 python3-pip ;;
            *)
                err "Auto-install not supported for $ID. Install Python 3.10+ manually."
                exit 1 ;;
        esac
    else
        err "Install Python 3.10+ from https://www.python.org/downloads/"
        exit 1
    fi

    PYTHON=$(find_python)
    if [ -z "$PYTHON" ]; then
        err "Python installation failed. Install manually and re-run."
        exit 1
    fi
fi

info "Python: $($PYTHON --version)"

# --- Hand off to _setup_wizard.py ---

exec "$PYTHON" _setup_wizard.py
