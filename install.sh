#!/usr/bin/env bash

# hf-serve one-liner userspace installer
# Installs hf-serve securely using 'uv tool' under ~/.local/bin/

set -euo pipefail

# Visual helpers
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== hf-serve Userspace Installer ===${NC}"

# 1. Ensure git and curl are available
if ! command -v git &> /dev/null; then
    echo -e "${RED}Error: git is required but not installed.${NC}" >&2
    exit 1
fi
if ! command -v curl &> /dev/null; then
    echo -e "${RED}Error: curl is required but not installed.${NC}" >&2
    exit 1
fi

# 2. Check for Python 3.12+
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is required but not installed.${NC}" >&2
    exit 1
fi

# 3. Handle uv installation
if ! command -v uv &> /dev/null; then
    echo -e "${YELLOW}uv package manager not found. Installing uv...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    
    # Source uv environment variables for current execution
    # shellcheck source=/dev/null
    source "$HOME/.local/bin/env" || true
    export PATH="$HOME/.local/bin:$PATH"
fi

# Verify uv is now active
if ! command -v uv &> /dev/null; then
    echo -e "${RED}Error: Failed to install or resolve uv.${NC}" >&2
    exit 1
fi

# 4. Install hf-serve using uv tool
echo -e "${BLUE}Installing hf-serve CLI...${NC}"
if [ -f "pyproject.toml" ]; then
    # Running from within cloned directory
    uv tool install --force .
else
    # Running directly via curl one-liner
    uv tool install --force git+https://git.elfenlabs.com/elfenlabs/hf-serve.git
fi

# 5. Ensure ~/.local/bin is in PATH
LOCAL_BIN="$HOME/.local/bin"
case :$PATH: in
    *:$LOCAL_BIN:*) ;; # Already in PATH
    *)
        echo -e "${YELLOW}Adding ~/.local/bin to PATH...${NC}"
        SHELL_RC=""
        if [ -n "${ZSH_VERSION:-}" ]; then
            SHELL_RC="$HOME/.zshrc"
        elif [ -n "${BASH_VERSION:-}" ]; then
            SHELL_RC="$HOME/.bashrc"
        fi
        
        if [ -n "$SHELL_RC" ] && [ -f "$SHELL_RC" ]; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
            echo -e "${GREEN}Added to $SHELL_RC. Restart your terminal or run: source $SHELL_RC${NC}"
        else
            echo -e "${YELLOW}Please add export PATH=\"\$HOME/.local/bin:\$PATH\" to your shell RC profile.${NC}"
        fi
        ;;
esac

echo -e "${GREEN}✓ hf-serve successfully installed!${NC}"
echo -e "Try running: ${BLUE}hf-serve --help${NC}"
