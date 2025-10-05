#!/bin/bash
#
# LLM Tools Installation Script for Linux (Debian/Ubuntu/Kali)
# Installs Simon Willison's llm CLI tool and related AI/LLM command-line utilities
#
# Usage: ./install-llm-tools.sh
# Re-run to update all tools

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    exit 1
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    warn "Running as root. Some installations will be done in /root/.local/"
fi

# Check if we're on a Debian-based system (hard requirement)
if ! grep -qE "(debian|ID_LIKE.*debian)" /etc/os-release 2>/dev/null; then
    error "This script requires a Debian-based Linux distribution. Detected system is not compatible."
fi

#############################################################################
# PHASE 0: Self-Update
#############################################################################

log "Checking for script updates..."
cd "$SCRIPT_DIR"

if git rev-parse --git-dir > /dev/null 2>&1; then
    log "Git repository detected, checking for updates..."

    # Fetch latest changes
    git fetch origin 2>/dev/null || true

    # Check if we're behind
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "$LOCAL")

    if [ "$LOCAL" != "$REMOTE" ]; then
        log "Updates found! Pulling latest changes..."
        git pull
        log "Re-executing updated script..."
        exec "$0" "$@"
        exit 0
    else
        log "Script is up to date"
    fi
else
    warn "Not running from a git repository. Self-update disabled."
fi

#############################################################################
# PHASE 1: Install Prerequisites
#############################################################################

log "Installing prerequisites..."

sudo apt-get update

# Install git
if ! command -v git &> /dev/null; then
    log "Installing git..."
    sudo apt-get install -y git
else
    log "git is already installed"
fi

# Install jq
if ! command -v jq &> /dev/null; then
    log "Installing jq..."
    sudo apt-get install -y jq
else
    log "jq is already installed"
fi

# Install Python3
if ! command -v python3 &> /dev/null; then
    log "Installing Python3..."
    sudo apt-get install -y python3
else
    log "Python3 is already installed"
fi

# Install pipx
if ! command -v pipx &> /dev/null; then
    log "Installing pipx..."
    sudo apt-get install -y pipx
else
    log "pipx is already installed"
fi

# Install/update uv
export PATH=$HOME/.local/bin:$PATH
if ! command -v uv &> /dev/null; then
    log "Installing uv..."
    pipx install uv
else
    log "uv is already installed, upgrading..."
    pipx upgrade uv
fi

# Install Rust and Cargo from repositories
if ! command -v cargo &> /dev/null; then
    log "Installing Rust and Cargo from repositories..."
    sudo apt-get install -y cargo rustc
else
    log "Rust/Cargo is already installed"
fi

# Install curl (needed for nvm installer if required)
if ! command -v curl &> /dev/null; then
    log "Installing curl..."
    sudo apt-get install -y curl
else
    log "curl is already installed"
fi

# Install asciinema
if ! command -v asciinema &> /dev/null; then
    log "Installing asciinema..."
    cargo install --locked --git https://github.com/asciinema/asciinema
else
    log "asciinema is already installed, updating..."
    cargo install --locked --force --git https://github.com/asciinema/asciinema
fi

# Check what Node.js version is available in repositories
log "Checking Node.js version in repositories..."
REPO_NODE_VERSION=$(apt-cache policy nodejs 2>/dev/null | grep -oP 'Candidate:\s*\K[0-9]+' | head -1)

if [ -z "$REPO_NODE_VERSION" ]; then
    REPO_NODE_VERSION=0
    warn "Could not determine repository Node.js version"
fi

log "Repository has Node.js version: $REPO_NODE_VERSION"

# Install Node.js - either from repo (if >= 20) or via nvm (if < 20)
if ! command -v node &> /dev/null; then
    if [ "$REPO_NODE_VERSION" -ge 20 ]; then
        log "Installing Node.js from repositories (version $REPO_NODE_VERSION)..."
        sudo apt-get install -y nodejs

        # Install npm separately for repository installations
        if ! command -v npm &> /dev/null; then
            log "Installing npm..."
            sudo apt-get install -y npm
        fi
    else
        log "Repository version $REPO_NODE_VERSION is < 20, installing Node 22 via nvm..."

        # Install nvm
        if [ ! -d "$HOME/.nvm" ]; then
            log "Installing nvm..."
            curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash

            # Source nvm immediately for this script
            export NVM_DIR="$HOME/.nvm"
            [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
        else
            log "nvm is already installed"
            export NVM_DIR="$HOME/.nvm"
            [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
        fi

        # Install Node 22 via nvm
        log "Installing Node.js 22 via nvm..."
        nvm install 22
        nvm use 22
        nvm alias default 22
    fi
else
    CURRENT_NODE_VERSION=$(node --version | grep -oP 'v\K[0-9]+')
    log "Node.js is already installed (version $CURRENT_NODE_VERSION)"

    # If current version is < 20, warn user
    if [ "$CURRENT_NODE_VERSION" -lt 20 ]; then
        warn "Installed Node.js version $CURRENT_NODE_VERSION is < 20. Consider upgrading to Node 22 via nvm."
        warn "Run: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash"
        warn "Then: nvm install 22 && nvm use 22 && nvm alias default 22"
    fi
fi

# Detect if npm needs sudo
log "Detecting npm permissions..."
NPM_PREFIX=$(npm config get prefix 2>/dev/null || echo "/usr/local")
if mkdir -p "$NPM_PREFIX/lib/node_modules/.npm-test" 2>/dev/null; then
    rm -rf "$NPM_PREFIX/lib/node_modules/.npm-test" 2>/dev/null
    NPM_NEEDS_SUDO=false
    log "npm can install globally without sudo"
else
    NPM_NEEDS_SUDO=true
    log "npm requires sudo for global installs"
fi

# Wrapper function for npm global installs
npm_install() {
    if [ "$NPM_NEEDS_SUDO" = "true" ]; then
        sudo npm "$@"
    else
        npm "$@"
    fi
}

#############################################################################
# PHASE 2: Install/Update LLM Core
#############################################################################

log "Installing/updating llm..."

# Check if llm is already installed
if command -v llm &> /dev/null; then
    log "llm is already installed, upgrading..."
    uv tool upgrade llm
else
    log "Installing llm..."
    uv tool install llm
fi

# Ensure llm is in PATH
export PATH=$HOME/.local/bin:$PATH

# Define the extra models file path early so we can check/preserve existing config
EXTRA_MODELS_FILE="$(command llm logs path | xargs dirname)/extra-openai-models.yaml"

# Configure Azure OpenAI API
# Check if Azure key is already set
if ! command llm keys get azure &> /dev/null; then
    log "Configuring Azure OpenAI API..."
    echo ""
    read -p "Enter your Azure Foundry resource URL (e.g., https://YOUR-RESOURCE.openai.azure.com/openai/v1/): " AZURE_API_BASE
    command llm keys set azure
else
    log "Azure API key already configured, preserving existing settings"
    # Try to read existing config for API base
    if [ -f "$EXTRA_MODELS_FILE" ]; then
        # Extract the api_base from the first model entry in the YAML
        EXISTING_API_BASE=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" | sed 's/.*api_base:\s*//;s/\s*$//')
        if [ -n "$EXISTING_API_BASE" ]; then
            AZURE_API_BASE="$EXISTING_API_BASE"
            log "Preserving existing API base: $AZURE_API_BASE"
        else
            AZURE_API_BASE="https://REPLACE-ME.openai.azure.com/openai/v1/"
            warn "Could not read existing API base, using placeholder"
        fi
    else
        AZURE_API_BASE="https://REPLACE-ME.openai.azure.com/openai/v1/"
        log "No existing config found, using placeholder"
    fi
fi

# Create extra-openai-models.yaml
log "Creating Azure OpenAI models configuration..."

cat > "$EXTRA_MODELS_FILE" <<EOF
- model_id: azure/gpt-5
  model_name: gpt-5
  api_base: ${AZURE_API_BASE}
  api_key_name: azure
  supports_tools: true
  supports_schema: true
  vision: true

- model_id: azure/gpt-5-mini
  model_name: gpt-5-mini
  api_base: ${AZURE_API_BASE}
  api_key_name: azure
  supports_tools: true
  supports_schema: true
  vision: true

- model_id: azure/gpt-5-nano
  model_name: gpt-5-nano
  api_base: ${AZURE_API_BASE}
  api_key_name: azure
  supports_tools: true
  supports_schema: true
  vision: true

- model_id: azure/o4-mini
  model_name: o4-mini
  api_base: ${AZURE_API_BASE}
  api_key_name: azure
  supports_tools: true
  supports_schema: true
  vision: true
  
- model_id: azure/gpt-4.1
  model_name: gpt-4.1
  api_base: ${AZURE_API_BASE}
  api_key_name: azure
  supports_tools: true
  supports_schema: true
  vision: true
EOF

# Only set default model if no custom default has been configured
DEFAULT_MODEL_FILE="$(command llm logs path | xargs dirname)/default_model.txt"
if [ ! -f "$DEFAULT_MODEL_FILE" ]; then
    log "Setting default model to azure/gpt-5-mini..."
    command llm models default azure/gpt-5-mini
else
    log "Default model already configured, skipping..."
fi

#############################################################################
# PHASE 3: Install/Update LLM Plugins
#############################################################################

log "Installing/updating llm plugins..."

PLUGINS=(
    "llm-gemini"
    "llm-openrouter"
    "llm-anthropic"
    "llm-cmd"
    "llm-cmd-comp"
    "llm-tools-quickjs"
    "llm-tools-sqlite"
    "llm-fragments-site-text"
    "llm-fragments-pdf"
    "llm-fragments-github"
    "llm-jq"
    "git+https://github.com/damonmcminn/llm-templates-fabric"
    "$SCRIPT_DIR/llm-tools-context"
)

for plugin in "${PLUGINS[@]}"; do
    log "Installing/updating $plugin..."
    command llm install "$plugin" --upgrade 2>/dev/null || command llm install "$plugin"
done

#############################################################################
# PHASE 4: Install/Update LLM Templates
#############################################################################

log "Installing/updating llm templates..."

# Get templates directory path
TEMPLATES_DIR="$(command llm logs path | xargs dirname)/templates"

# Create templates directory if it doesn't exist
mkdir -p "$TEMPLATES_DIR"

# Copy assistant.yaml template from repository (with smart update check)
if [ -f "$SCRIPT_DIR/llm-template/assistant.yaml" ]; then
    if [ -f "$TEMPLATES_DIR/assistant.yaml" ]; then
        # Both files exist - compare them
        if ! cmp -s "$SCRIPT_DIR/llm-template/assistant.yaml" "$TEMPLATES_DIR/assistant.yaml"; then
            log "Template has changed in repository"
            echo ""
            read -p "The assistant.yaml template in the repository differs from your installed version. Update it? (y/N): " UPDATE_TEMPLATE
            if [[ "$UPDATE_TEMPLATE" =~ ^[Yy]$ ]]; then
                cp "$SCRIPT_DIR/llm-template/assistant.yaml" "$TEMPLATES_DIR/assistant.yaml"
                log "Template updated to $TEMPLATES_DIR/assistant.yaml"
            else
                log "Keeping existing template"
            fi
        else
            log "Template is up to date"
        fi
    else
        # Only repo version exists - install it
        log "Installing assistant.yaml template..."
        cp "$SCRIPT_DIR/llm-template/assistant.yaml" "$TEMPLATES_DIR/assistant.yaml"
        log "Template installed to $TEMPLATES_DIR/assistant.yaml"
    fi
else
    warn "Template not found at $SCRIPT_DIR/llm-template/assistant.yaml"
fi

#############################################################################
# PHASE 5: Shell Integration
#############################################################################

log "Setting up shell integration..."

# Create integration files in the script directory
BASH_INTEGRATION="$SCRIPT_DIR/integration/llm-integration.bash"
ZSH_INTEGRATION="$SCRIPT_DIR/integration/llm-integration.zsh"
COMMON_CONFIG="$SCRIPT_DIR/integration/llm-common.sh"

# These files will be created by separate script files
# For now, we'll check if they exist and source them

# Function to prompt for session log directory preference (called once for both shells)
prompt_for_session_log_dir() {
    # Only prompt if not already set
    if [ -z "$SESSION_LOG_DIR_VALUE" ]; then
        log "Configuring terminal session history storage..."
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Terminal sessions are logged for AI context retrieval."
        echo "Choose storage location:"
        echo ""
        echo "  1) Temporary - Store in /tmp/session_logs/asciinema (cleared on reboot)"
        echo "  2) Permanent - Store in ~/session_logs/asciinema (survives reboots)"
        echo ""
        read -p "Choice (1/2) [default: 1]: " session_choice
        echo ""

        if [ "$session_choice" = "2" ]; then
            SESSION_LOG_DIR_VALUE="\$HOME/session_logs/asciinema"
        else
            SESSION_LOG_DIR_VALUE="/tmp/session_logs/asciinema"
        fi
    fi
}

# Update .bashrc
BASHRC="$HOME/.bashrc"
if [ -f "$BASHRC" ]; then
    # Check if SESSION_LOG_DIR export already exists (first-run detection)
    if ! grep -q "export SESSION_LOG_DIR=" "$BASHRC"; then
        prompt_for_session_log_dir
        log "Adding session log configuration and llm integration to .bashrc..."
        cat >> "$BASHRC" <<EOF

# LLM Session Log Directory
export SESSION_LOG_DIR="$SESSION_LOG_DIR_VALUE"

# LLM Tools Integration
if [ -f "$SCRIPT_DIR/integration/llm-integration.bash" ]; then
    source "$SCRIPT_DIR/integration/llm-integration.bash"
fi
EOF
    elif ! grep -q "llm-integration.bash" "$BASHRC"; then
        log "Adding llm integration to .bashrc..."
        cat >> "$BASHRC" <<EOF

# LLM Tools Integration
if [ -f "$SCRIPT_DIR/integration/llm-integration.bash" ]; then
    source "$SCRIPT_DIR/integration/llm-integration.bash"
fi
EOF
    else
        log "llm integration already present in .bashrc"
    fi
fi

# Update .zshrc
ZSHRC="$HOME/.zshrc"
if [ -f "$ZSHRC" ]; then
    # Check if SESSION_LOG_DIR export already exists (first-run detection)
    if ! grep -q "export SESSION_LOG_DIR=" "$ZSHRC"; then
        prompt_for_session_log_dir
        log "Adding session log configuration and llm integration to .zshrc..."
        cat >> "$ZSHRC" <<EOF

# LLM Session Log Directory
export SESSION_LOG_DIR="$SESSION_LOG_DIR_VALUE"

# LLM Tools Integration
if [ -f "$SCRIPT_DIR/integration/llm-integration.zsh" ]; then
    source "$SCRIPT_DIR/integration/llm-integration.zsh"
fi
EOF
    elif ! grep -q "llm-integration.zsh" "$ZSHRC"; then
        log "Adding llm integration to .zshrc..."
        cat >> "$ZSHRC" <<EOF

# LLM Tools Integration
if [ -f "$SCRIPT_DIR/integration/llm-integration.zsh" ]; then
    source "$SCRIPT_DIR/integration/llm-integration.zsh"
fi
EOF
    else
        log "llm integration already present in .zshrc"
    fi
fi

# Install context script
log "Installing context script..."
mkdir -p "$HOME/.local/bin"
cp "$SCRIPT_DIR/context/context" "$HOME/.local/bin/context"
chmod +x "$HOME/.local/bin/context"

#############################################################################
# PHASE 6: Additional Tools
#############################################################################

log "Installing/updating additional tools..."

# Install/update repomix
log "Installing/updating repomix..."
npm_install install -g repomix

# Install/update gitingest
log "Installing/updating gitingest..."
if uv tool list | grep -q "gitingest"; then
    uv tool upgrade gitingest
else
    uv tool install gitingest
fi

# Install/update files-to-prompt
log "Installing/updating files-to-prompt..."
if uv tool list | grep -q "files-to-prompt"; then
    uv tool upgrade files-to-prompt
else
    uv tool install git+https://github.com/danmackinlay/files-to-prompt
fi

#############################################################################
# PHASE 7: Claude Code & Router
#############################################################################

log "Installing/updating Claude Code and Claude Code Router..."

# Install/update Claude Code
log "Installing/updating Claude Code..."
npm_install install -g @anthropic-ai/claude-code

# Install/update Claude Code Router
log "Installing/updating Claude Code Router..."
npm_install install -g @musistudio/claude-code-router

# Configure Claude Code Router for Azure OpenAI
CCR_CONFIG_DIR="$HOME/.claude-code-router"
CCR_CONFIG_FILE="$CCR_CONFIG_DIR/config.json"

# Commented out: Claude Code Router configuration (configure manually if needed)

# mkdir -p "$CCR_CONFIG_DIR"
# 
# if [ -f "$CCR_CONFIG_FILE" ]; then
#     log "Claude Code Router config already exists"
#     read -p "Do you want to reconfigure Claude Code Router for Azure? (y/N): " RECONFIG_CCR
#     if [[ ! "$RECONFIG_CCR" =~ ^[Yy]$ ]]; then
#         log "Skipping Claude Code Router configuration"
#         # Skip to the next phase
#         CCR_CONFIGURED=1
#     fi
# fi
# 
# if [ -z "$CCR_CONFIGURED" ]; then
#     log "Configuring Claude Code Router for Azure OpenAI..."
#     echo ""
#     read -p "Enter your Azure OpenAI endpoint for Claude Code (e.g., https://YOUR-RESOURCE.openai.azure.com): " CCR_AZURE_ENDPOINT
#     read -p "Enter your Azure OpenAI API key for Claude Code: " CCR_AZURE_API_KEY
#     read -p "Enter your deployment name (e.g., gpt-4o, claude-sonnet-4): " CCR_DEPLOYMENT_NAME
#     read -p "Enter API version [2024-10-21]: " CCR_API_VERSION
#     CCR_API_VERSION=${CCR_API_VERSION:-2024-10-21}
#
#     # Construct the full API base URL
#     CCR_API_BASE_URL="${CCR_AZURE_ENDPOINT}/openai/deployments/${CCR_DEPLOYMENT_NAME}/chat/completions?api-version=${CCR_API_VERSION}"
#
#     # Create config.json
#     cat > "$CCR_CONFIG_FILE" <<EOF
# {
#   "Providers": [
#     {
#       "name": "azure",
#       "api_base_url": "${CCR_API_BASE_URL}",
#       "api_key": "${CCR_AZURE_API_KEY}",
#       "models": ["${CCR_DEPLOYMENT_NAME}"]
#     }
#   ],
#   "Router": {
#     "default": "azure,${CCR_DEPLOYMENT_NAME}"
#   }
# }
# EOF
#
#     log "Claude Code Router configuration created at $CCR_CONFIG_FILE"
# fi

log "Claude Code Router installed. Configure manually in $CCR_CONFIG_FILE if needed."

# Install/update OpenCode
log "Installing/updating OpenCode..."
npm_install install -g opencode-ai@latest

#############################################################################
# COMPLETE
#############################################################################

log ""
log "============================================="
log "Installation/Update Complete!"
log "============================================="
log ""
log "Installed tools:"
log "  - llm (Simon Willison's CLI tool)"
log "  - llm plugins (gemini, anthropic, tools, fragments, jq, fabric templates, context)"
log "  - repomix (repository packager)"
log "  - gitingest (Git repository to LLM-friendly text)"
log "  - files-to-prompt (file content formatter)"
log "  - asciinema (terminal session recorder)"
log "  - Claude Code (Anthropic's agentic coding CLI)"
log "  - Claude Code Router (proxy for Claude Code with Azure OpenAI)"
log "  - OpenCode (AI coding agent for terminal)"
log ""
log "Shell integration files created in: $SCRIPT_DIR/integration"
log "  - integration/llm-integration.bash (for Bash)"
log "  - integration/llm-integration.zsh (for Zsh)"
log ""
log "Next steps:"
log "  1. Restart your shell or run: source ~/.bashrc (or ~/.zshrc)"
log "  2. Test llm: llm 'Hello, how are you?'"
log "  3. Use Ctrl+N in your shell for AI command completion"
# log "  4. Test Claude Code Router: routed-claude"
log "  4. Test and configure OpenCode: opencode and configure https://opencode.ai/docs/providers"
log ""
log "To update all tools in the future, simply re-run this script:"
log "  ./install-llm-tools.sh"
log ""
