#!/bin/bash
#
# LLM Tools Installation Script for Linux (Debian/Ubuntu/Kali)
# Installs Simon Willison's llm CLI tool and related AI/LLM command-line utilities
#
# Usage: ./install-llm-tools.sh [--azure] [--gemini]
#
# Options:
#   --azure    Force (re)configuration of Azure OpenAI, even if already configured
#   --gemini   Force (re)configuration of Google Gemini, even if already configured
#   --help     Show help message
#
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
# Parse Command-Line Arguments
#############################################################################

FORCE_AZURE_CONFIG=false
FORCE_GEMINI_CONFIG=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --azure)
            FORCE_AZURE_CONFIG=true
            shift
            ;;
        --gemini)
            FORCE_GEMINI_CONFIG=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "LLM Tools Installation Script for Linux (Debian/Ubuntu/Kali)"
            echo ""
            echo "Options:"
            echo "  --azure    Force (re)configuration of Azure OpenAI, even if already configured"
            echo "  --gemini   Force (re)configuration of Google Gemini, even if already configured"
            echo "  --help     Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0              # Normal installation/update"
            echo "  $0 --azure      # Reconfigure Azure OpenAI settings"
            echo "  $0 --gemini     # Reconfigure Google Gemini settings"
            exit 0
            ;;
        *)
            error "Unknown option: $1. Use --help for usage information."
            ;;
    esac
done

# Validate mutually exclusive flags
if [ "$FORCE_AZURE_CONFIG" = "true" ] && [ "$FORCE_GEMINI_CONFIG" = "true" ]; then
    error "Cannot specify both --azure and --gemini flags simultaneously."
    echo "Please choose one provider at a time:" >&2
    echo "  $0 --azure    # Configure Azure OpenAI" >&2
    echo "  $0 --gemini   # Configure Google Gemini" >&2
    exit 1
fi

#############################################################################
# Helper Functions
#############################################################################

# Install apt package with existence check
install_apt_package() {
    local package="$1"
    if ! command -v "$package" &> /dev/null; then
        log "Installing $package..."
        sudo apt-get install -y "$package"
    else
        log "$package is already installed"
    fi
}

# Install or upgrade a uv tool
install_or_upgrade_uv_tool() {
    local tool_name="$1"
    local tool_source="${2:-$tool_name}"  # Default to tool_name if source not provided

    if uv tool list 2>/dev/null | grep -q "^$tool_name "; then
        log "$tool_name is already installed, upgrading..."
        uv tool upgrade "$tool_name"
    else
        log "Installing $tool_name..."
        uv tool install "$tool_source" --force
    fi
}

# Install Rust via rustup
install_rust_via_rustup() {
    log "Installing Rust via rustup (official Rust installer)..."

    # Download and run rustup-init
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable

    # Source cargo environment for this script
    export CARGO_HOME="$HOME/.cargo"
    export RUSTUP_HOME="$HOME/.rustup"
    export PATH="$CARGO_HOME/bin:$PATH"

    if [ -f "$HOME/.cargo/env" ]; then
        source "$HOME/.cargo/env"
    fi

    log "Rust installed successfully via rustup"
}

# Update Rust via rustup
update_rust_via_rustup() {
    log "Updating Rust via rustup..."
    rustup update stable
}

# Get stored checksum for a template
get_stored_checksum() {
    local template_name="$1"
    local checksum_file="$HOME/.config/llm-tools/template-checksums"

    if [ ! -f "$checksum_file" ]; then
        echo ""
        return
    fi

    grep "^${template_name}:" "$checksum_file" 2>/dev/null | cut -d: -f2
}

# Store checksum for a template
store_checksum() {
    local template_name="$1"
    local file_path="$2"
    local checksum_file="$HOME/.config/llm-tools/template-checksums"

    # Create directory if needed
    mkdir -p "$(dirname "$checksum_file")"

    # Calculate SHA256 checksum
    local checksum=$(sha256sum "$file_path" | awk '{print $1}')

    # Remove old entry if exists
    if [ -f "$checksum_file" ]; then
        sed -i "/^${template_name}:/d" "$checksum_file"
    fi

    # Add new entry
    echo "${template_name}:${checksum}" >> "$checksum_file"
}

# Update shell RC file with integration
update_shell_rc_file() {
    local rc_file="$1"
    local integration_file="$2"
    local shell_name="$3"

    if [ ! -f "$rc_file" ]; then
        return
    fi

    # Check if SESSION_LOG_DIR export already exists (first-run detection)
    if ! grep -q "export SESSION_LOG_DIR=" "$rc_file"; then
        prompt_for_session_log_dir
        log "Adding session log configuration and llm integration to $shell_name..."
        cat >> "$rc_file" <<EOF

# LLM Session Log Directory
export SESSION_LOG_DIR="$SESSION_LOG_DIR_VALUE"

# LLM Tools Integration
if [ -f "$integration_file" ]; then
    source "$integration_file"
fi
EOF
    elif ! grep -q "$(basename "$integration_file")" "$rc_file"; then
        log "Adding llm integration to $shell_name..."
        cat >> "$rc_file" <<EOF

# LLM Tools Integration
if [ -f "$integration_file" ]; then
    source "$integration_file"
fi
EOF
    else
        log "llm integration already present in $shell_name"
    fi
}

# Configure Azure OpenAI with prompts
configure_azure_openai() {
    log "Configuring Azure OpenAI API..."
    echo ""
    read -p "Enter your Azure Foundry resource URL (e.g., https://YOUR-RESOURCE.openai.azure.com/openai/v1/): " AZURE_API_BASE
    command llm keys set azure
    AZURE_CONFIGURED=true
}

# Configure Google Gemini with prompts
configure_gemini() {
    log "Configuring Google Gemini API..."
    echo ""
    echo "Get your free API key from: https://ai.google.dev/gemini-api/docs/api-key"
    echo ""
    command llm keys set gemini
    GEMINI_CONFIGURED=true
}

# Set or migrate default model (handles automatic migration from old defaults)
set_or_migrate_default_model() {
    local new_default="$1"
    local DEFAULT_MODEL_FILE="$(command llm logs path | xargs dirname)/default_model.txt"

    if [ ! -f "$DEFAULT_MODEL_FILE" ]; then
        log "Setting default model to ${new_default}..."
        command llm models default "$new_default"
    else
        # Check if the current default is an GPT-5 model and migrate to gpt-4.1-mini
        local CURRENT_DEFAULT=$(cat "$DEFAULT_MODEL_FILE" 2>/dev/null || echo "")

        # Migrate any gpt-5 variant to gpt-4.1-mini
        if [[ "$CURRENT_DEFAULT" =~ ^azure/gpt-5 ]] && [ "$new_default" = "azure/gpt-4.1-mini" ]; then
            log "Migrating default model from $CURRENT_DEFAULT to azure/gpt-4.1-mini..."
            command llm models default "$new_default"
        else
            log "Default model already configured, skipping..."
        fi
    fi
}

# Update template file with smart checksum-based update logic
update_template_file() {
    local template_name="$1"
    local source_file="$SCRIPT_DIR/llm-template/${template_name}.yaml"
    local dest_file="$TEMPLATES_DIR/${template_name}.yaml"

    if [ ! -f "$source_file" ]; then
        warn "Template not found at $source_file"
        return
    fi

    # Calculate repo file checksum
    local repo_checksum=$(sha256sum "$source_file" | awk '{print $1}')

    if [ ! -f "$dest_file" ]; then
        # No installed file - install from repo
        log "Installing ${template_name}.yaml template..."
        cp "$source_file" "$dest_file"
        store_checksum "$template_name" "$dest_file"
        log "Template installed to $dest_file"
        return
    fi

    # Calculate installed file checksum
    local installed_checksum=$(sha256sum "$dest_file" | awk '{print $1}')

    # Check if already up to date
    if [ "$installed_checksum" = "$repo_checksum" ]; then
        log "${template_name}.yaml template is up to date"
        store_checksum "$template_name" "$dest_file"
        return
    fi

    # Get stored checksum (what we last installed)
    local stored_checksum=$(get_stored_checksum "$template_name")

    if [ -n "$stored_checksum" ] && [ "$installed_checksum" = "$stored_checksum" ]; then
        # User hasn't modified the file - auto-update silently
        log "${template_name}.yaml template updated from repository (no local modifications detected)"
        cp "$source_file" "$dest_file"
        store_checksum "$template_name" "$dest_file"
    else
        # User has modified the file OR no stored checksum (legacy) - prompt
        log "${template_name}.yaml template has changed in repository"
        if [ -z "$stored_checksum" ]; then
            log "Cannot determine if you have local modifications (legacy installation)"
        else
            log "Local modifications detected"
        fi
        echo ""
        read -p "Update ${template_name}.yaml template? This will overwrite your version. (y/N): " UPDATE_TEMPLATE
        if [[ "$UPDATE_TEMPLATE" =~ ^[Yy]$ ]]; then
            cp "$source_file" "$dest_file"
            store_checksum "$template_name" "$dest_file"
            log "${template_name}.yaml template updated to $dest_file"
        else
            log "Keeping existing ${template_name}.yaml template"
            # Update stored checksum to current installed version to avoid prompting next time
            store_checksum "$template_name" "$dest_file"
        fi
    fi
}

# Install or upgrade a Rust/Cargo tool
# Usage: install_or_upgrade_cargo_tool tool_name [git_url]
# Examples:
#   install_or_upgrade_cargo_tool aichat
#   install_or_upgrade_cargo_tool asciinema https://github.com/asciinema/asciinema
install_or_upgrade_cargo_tool() {
    local tool_name="$1"
    local git_source="$2"

    if [ -n "$git_source" ]; then
        # Installing from git repository (always force to get latest commit)
        log "Installing/updating $tool_name from git..."
        cargo install --locked --force --git "$git_source"
    else
        # Installing from crates.io
        if ! command -v "$tool_name" &> /dev/null; then
            log "Installing $tool_name via cargo..."
            cargo install "$tool_name"
        else
            log "$tool_name is already installed, checking for updates..."
            cargo install "$tool_name"
        fi
    fi
}

# Install or upgrade a Rust/Cargo tool from git with commit-hash checking
# Only rebuilds when upstream has new commits (avoids unnecessary recompilation)
# Usage: install_or_upgrade_cargo_git_tool tool_name git_url
# Examples:
#   install_or_upgrade_cargo_git_tool asciinema https://github.com/asciinema/asciinema
#   install_or_upgrade_cargo_git_tool yek https://github.com/bodo-run/yek
install_or_upgrade_cargo_git_tool() {
    local tool_name="$1"
    local git_url="$2"

    if ! command -v "$tool_name" &> /dev/null; then
        log "Installing $tool_name from git..."
        cargo install --locked --git "$git_url"

        # Store the commit hash for future update checks
        local version_file="$HOME/.config/llm-tools/${tool_name}-commit"
        mkdir -p "$(dirname "$version_file")"
        local latest_commit=$(git ls-remote "${git_url}.git" HEAD 2>/dev/null | awk '{print $1}')
        if [ -n "$latest_commit" ]; then
            echo "$latest_commit" > "$version_file"
        fi
    else
        log "$tool_name is already installed, checking for updates..."

        # Get latest commit from GitHub
        local latest_commit=$(git ls-remote "${git_url}.git" HEAD 2>/dev/null | awk '{print $1}')

        # Check stored commit hash
        local version_file="$HOME/.config/llm-tools/${tool_name}-commit"
        local installed_commit=$(cat "$version_file" 2>/dev/null || echo "")

        if [ -z "$latest_commit" ]; then
            warn "Could not check for $tool_name updates (network issue?). Skipping rebuild."
        elif [ "$latest_commit" != "$installed_commit" ]; then
            log "New version available, rebuilding $tool_name..."
            cargo install --locked --force --git "$git_url"
            mkdir -p "$(dirname "$version_file")"
            echo "$latest_commit" > "$version_file"
        else
            log "$tool_name is up to date (commit: ${latest_commit:0:7}), skipping rebuild"
        fi
    fi
}

#############################################################################
# PHASE 0: Self-Update
#############################################################################

log "Checking for script updates..."
cd "$SCRIPT_DIR"

if git rev-parse --git-dir > /dev/null 2>&1; then
    log "Git repository detected, checking for updates..."

    # Fetch latest changes
    git fetch origin 2>/dev/null || true

    # Check if we're behind the remote (not just different)
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "$LOCAL")

    # Count commits we don't have that remote has
    BEHIND=$(git rev-list HEAD..@{u} 2>/dev/null | wc -l)

    if [ "$BEHIND" -gt 0 ]; then
        log "Updates found! Pulling latest changes..."
        git pull --ff-only
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

# Install basic prerequisites
install_apt_package git
install_apt_package jq
install_apt_package xsel
install_apt_package python3
install_apt_package pipx

# Install curl (needed for nvm installer if required)
install_apt_package curl

# Install bubblewrap (provides bwrap for sandboxing, used by llm-functions and code execution tools)
if ! command -v bwrap &> /dev/null; then
    log "Installing bubblewrap (provides sandboxing)..."
    sudo apt-get install -y bubblewrap
else
    log "bubblewrap is already installed"
fi

# Check for sha256sum (required for template checksum tracking in Phase 4)
if ! command -v sha256sum &> /dev/null; then
    log "Installing coreutils (provides sha256sum)..."
    sudo apt-get install -y coreutils
else
    log "sha256sum is already installed"
fi

# Install document processors for aichat RAG
log "Installing document processors for RAG..."
if ! command -v pdftotext &> /dev/null; then
    log "Installing poppler-utils (provides pdftotext for PDF processing)..."
    sudo apt-get install -y poppler-utils
else
    log "pdftotext is already installed"
fi

install_apt_package pandoc

# Install/update uv
export PATH=$HOME/.local/bin:$PATH
if ! command -v uv &> /dev/null; then
    log "Installing uv..."
    pipx install uv
else
    log "uv is already installed, upgrading..."
    pipx upgrade uv
fi

# Check what Rust version is available in repositories
log "Checking Rust version in repositories..."
REPO_RUST_VERSION=$(apt-cache policy rustc 2>/dev/null | grep -oP 'Candidate:\s*\K[0-9]+\.[0-9]+' | head -1)

if [ -z "$REPO_RUST_VERSION" ]; then
    REPO_RUST_VERSION="0.0"
    warn "Could not determine repository Rust version"
fi

# Convert version to comparable number (e.g., "1.85" -> 185)
REPO_RUST_VERSION_NUM=$(echo "$REPO_RUST_VERSION" | awk -F. '{print ($1 * 100) + $2}')
MINIMUM_RUST_VERSION=185  # Rust 1.85 (aichat/edition2024 requirement)

log "Repository has Rust version: $REPO_RUST_VERSION (numeric: $REPO_RUST_VERSION_NUM, minimum required: $MINIMUM_RUST_VERSION)"

# Install Rust - either from repo (if >= 1.85) or via rustup (if < 1.85)
if ! command -v cargo &> /dev/null; then
    # Check if rustup is already managing Rust (rustup might be installed but not active)
    if command -v rustup &> /dev/null; then
        log "rustup is installed but cargo not found, installing Rust via rustup..."
        rustup toolchain install stable
        rustup default stable
    elif [ "$REPO_RUST_VERSION_NUM" -ge "$MINIMUM_RUST_VERSION" ]; then
        log "Installing Rust from repositories (version $REPO_RUST_VERSION)..."
        sudo apt-get install -y cargo rustc
    else
        log "Repository version $REPO_RUST_VERSION is < 1.85, installing Rust via rustup..."
        install_rust_via_rustup
    fi
else
    # Rust is already installed - determine if it's managed by rustup or apt
    if command -v rustup &> /dev/null; then
        CURRENT_RUST_VERSION=$(rustc --version | grep -oP 'rustc \K[0-9]+\.[0-9]+' | head -1)
        log "Rust is already installed via rustup (version $CURRENT_RUST_VERSION)"

        # Check if we need to update
        update_rust_via_rustup
    else
        CURRENT_RUST_VERSION=$(rustc --version | grep -oP 'rustc \K[0-9]+\.[0-9]+' | head -1)
        CURRENT_RUST_VERSION_NUM=$(echo "$CURRENT_RUST_VERSION" | awk -F. '{print ($1 * 100) + $2}')

        log "Rust is already installed from system packages (version $CURRENT_RUST_VERSION)"

        # If current version is < 1.85, offer to upgrade via rustup
        if [ "$CURRENT_RUST_VERSION_NUM" -lt "$MINIMUM_RUST_VERSION" ]; then
            warn "Installed Rust version $CURRENT_RUST_VERSION is too old (requires 1.85+)"
            echo ""
            read -p "Install Rust 1.85+ via rustup? This will shadow the system installation. (Y/n): " UPGRADE_RUST
            UPGRADE_RUST=${UPGRADE_RUST:-Y}

            if [[ "$UPGRADE_RUST" =~ ^[Yy]$ ]]; then
                install_rust_via_rustup
                log "Rust upgraded successfully. rustup version will take precedence over system version."
            else
                warn "Continuing with old Rust version. aichat build will fail."
                warn "To upgrade manually later, run: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
            fi
        fi
    fi
fi

# Install/update asciinema (with commit-hash checking to avoid unnecessary rebuilds)
install_or_upgrade_cargo_git_tool asciinema https://github.com/asciinema/asciinema

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

    # Ensure npm is installed even if node was already present
    if ! command -v npm &> /dev/null; then
        # Check if node is from nvm - if so, don't install apt npm
        if which node 2>/dev/null | grep -q "\.nvm"; then
            warn "Node.js is from nvm but npm is not found. Please fix your nvm installation."
            warn "Try: nvm reinstall \$(node --version | tr -d 'v')"
        else
            log "npm is not installed, installing from repository..."
            sudo apt-get install -y npm
        fi
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

# Install/upgrade llm
# Note: We use `llm install -U llm` instead of `uv tool upgrade llm` to preserve plugins.
# `uv tool upgrade` destroys the virtual environment, causing all plugins to be lost.
# See: https://news.ycombinator.com/item?id=44110584
if command -v llm &>/dev/null; then
    log "Upgrading llm (preserves plugins)..."
    command llm install -U llm
else
    log "Installing llm..."
    uv tool install llm
fi

# Ensure llm is in PATH
export PATH=$HOME/.local/bin:$PATH

# Define the extra models file path early so we can check/preserve existing config
EXTRA_MODELS_FILE="$(command llm logs path | xargs dirname)/extra-openai-models.yaml"

# Detect if this is the first run
# Check for: new flag, OR YAML config exists, OR shell integration already present
if [ -f "$EXTRA_MODELS_FILE" ] || \
   grep -q "llm-integration" "$HOME/.bashrc" 2>/dev/null || \
   grep -q "llm-integration" "$HOME/.zshrc" 2>/dev/null; then
    IS_FIRST_RUN=false
else
    IS_FIRST_RUN=true
fi

# Configure Azure OpenAI API
if [ "$FORCE_AZURE_CONFIG" = "true" ]; then
    # --azure flag was passed - force (re)configuration
    log "Azure OpenAI Configuration (forced via --azure flag)"
    echo ""
    configure_azure_openai
    # When forcing Azure, disable Gemini (mutually exclusive)
    GEMINI_CONFIGURED=false
elif [ "$IS_FIRST_RUN" = "true" ]; then
    # First run - ask if user wants to configure Azure OpenAI
    log "Azure OpenAI Configuration"
    echo ""
    read -p "Do you want to configure Azure OpenAI? (Y/n): " CONFIG_AZURE
    CONFIG_AZURE=${CONFIG_AZURE:-Y}

    if [[ "$CONFIG_AZURE" =~ ^[Yy]$ ]]; then
        configure_azure_openai
    else
        log "Skipping Azure OpenAI configuration"
        AZURE_CONFIGURED=false
    fi
elif [ -f "$EXTRA_MODELS_FILE" ]; then
    # Subsequent run - user previously configured Azure (YAML exists)
    log "Azure OpenAI was previously configured, preserving existing configuration"

    # Extract the api_base from the first model entry in the YAML
    EXISTING_API_BASE=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" | sed 's/.*api_base:\s*//;s/\s*$//')
    if [ -n "$EXISTING_API_BASE" ]; then
        AZURE_API_BASE="$EXISTING_API_BASE"
        log "Using existing API base: $AZURE_API_BASE"
    else
        AZURE_API_BASE="https://REPLACE-ME.openai.azure.com/openai/v1/"
        warn "Could not read existing API base, using placeholder"
    fi
    AZURE_CONFIGURED=true
else
    # Subsequent run - user declined Azure configuration on first run
    log "Azure OpenAI not configured (skipped during initial setup)"
    AZURE_CONFIGURED=false
fi

# Only create YAML configuration if Azure was configured
if [ "$AZURE_CONFIGURED" = "true" ]; then
    # Create extra-openai-models.yaml
    log "Creating Azure OpenAI models configuration..."

    cat > "$EXTRA_MODELS_FILE" <<EOF
- model_id: azure/gpt-4.1
  model_name: gpt-4.1
  api_base: ${AZURE_API_BASE}
  api_key_name: azure
  supports_tools: true
  supports_schema: true
  vision: true

- model_id: azure/gpt-4.1-mini
  model_name: gpt-4.1-mini
  api_base: ${AZURE_API_BASE}
  api_key_name: azure
  supports_tools: true
  supports_schema: true
  vision: true

- model_id: azure/gpt-4.1-nano
  model_name: gpt-4.1-nano
  api_base: ${AZURE_API_BASE}
  api_key_name: azure
  supports_tools: true
  supports_schema: true
  vision: true

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
EOF

    # Set default model with automatic migration from old default
    set_or_migrate_default_model "azure/gpt-4.1-mini"
else
    log "Azure OpenAI not configured, skipping model configuration"
fi

#############################################################################
# Configure Google Gemini API
#############################################################################

if [ "$FORCE_GEMINI_CONFIG" = "true" ]; then
    # --gemini flag was passed - force (re)configuration
    log "Google Gemini Configuration (forced via --gemini flag)"
    echo ""
    configure_gemini
    # When forcing Gemini, disable Azure (mutually exclusive)
    AZURE_CONFIGURED=false
elif [ "$IS_FIRST_RUN" = "true" ] && [ "$AZURE_CONFIGURED" != "true" ]; then
    # First run AND Azure was declined - ask if user wants to configure Gemini
    log "Google Gemini Configuration"
    echo ""
    read -p "Do you want to configure Google Gemini? (y/N): " CONFIG_GEMINI
    CONFIG_GEMINI=${CONFIG_GEMINI:-N}

    if [[ "$CONFIG_GEMINI" =~ ^[Yy]$ ]]; then
        configure_gemini
    else
        log "Skipping Google Gemini configuration"
        GEMINI_CONFIGURED=false
    fi
elif [ "$IS_FIRST_RUN" = "true" ] && [ "$AZURE_CONFIGURED" = "true" ]; then
    # First run but Azure was configured - skip Gemini (mutually exclusive)
    log "Skipping Google Gemini (Azure OpenAI configured)"
    GEMINI_CONFIGURED=false
else
    # Subsequent run - check if Gemini key exists
    if command llm keys get gemini &>/dev/null; then
        log "Google Gemini was previously configured, preserving existing configuration"
        GEMINI_CONFIGURED=true
        # If Gemini is configured, Azure should not be (mutually exclusive)
        AZURE_CONFIGURED=false
    else
        log "Google Gemini not configured (skipped during initial setup)"
        GEMINI_CONFIGURED=false
    fi
fi

#############################################################################
# Set Default Model and Configure AIChat
#############################################################################

# Set default model based on configured provider
if [ "$AZURE_CONFIGURED" = "true" ]; then
    set_or_migrate_default_model "azure/gpt-4.1-mini"
elif [ "$GEMINI_CONFIGURED" = "true" ]; then
    set_or_migrate_default_model "gemini-2.5-flash"
fi

# Configure aichat with provider settings (mutually exclusive: either Azure OR Gemini)
aichat_config_dir="$HOME/.config/aichat"
aichat_config_file="$aichat_config_dir/config.yaml"
mkdir -p "$aichat_config_dir"

if [ "$AZURE_CONFIGURED" = "true" ]; then
    # Configure aichat with Azure OpenAI
    llm_config_dir="$(command llm logs path | xargs dirname)"
    extra_models_file="$llm_config_dir/extra-openai-models.yaml"

    # Extract API base from llm config
    api_base=$(grep -m 1 "^\s*api_base:" "$extra_models_file" | sed 's/.*api_base:\s*//;s/\s*$//')
    api_base_domain=$(echo "$api_base" | sed 's|\(https\?://[^/]*\).*|\1|')

    # Get API key
    azure_api_key=$(command llm keys get azure 2>/dev/null || echo "")

    if [ -n "$azure_api_key" ]; then
        SHOULD_WRITE=true

        if [ -f "$aichat_config_file" ]; then
            # If using --azure flag, automatically overwrite (user explicitly requested switch)
            if [ "$FORCE_AZURE_CONFIG" = "true" ]; then
                cp "$aichat_config_file" "$aichat_config_file.backup.$(date +%Y%m%d-%H%M%S)"
                log "Backed up existing aichat config"
                log "Switching to Azure OpenAI configuration..."
            else
                # Normal setup - keep existing config, don't prompt
                log "aichat config already exists, keeping it"
                SHOULD_WRITE=false
            fi
        fi

        if [ "$SHOULD_WRITE" = "true" ]; then
            # Create Azure-only aichat configuration file (inline)
            cat > "$aichat_config_file" <<EOF
# AIChat Configuration - Auto-generated by llm-linux-setup
# Azure OpenAI Integration

model: azure-openai:gpt-4.1-mini

clients:
  - type: azure-openai
    name: azure-openai
    api_base: ${api_base_domain}
    api_key: ${azure_api_key}
    models:
      - name: gpt-4.1
        max_input_tokens: 1047576
        supports_vision: true
        supports_function_calling: true
      - name: gpt-4.1-mini
        max_input_tokens: 1047576
        supports_vision: true
        supports_function_calling: true
      - name: gpt-4.1-nano
        max_input_tokens: 1047576
        supports_vision: true
        supports_function_calling: true
      - name: gpt-5
        max_input_tokens: 272000
        supports_vision: true
        supports_function_calling: true
      - name: gpt-5-mini
        max_input_tokens: 272000
        supports_vision: true
        supports_function_calling: true
      - name: gpt-5-nano
        max_input_tokens: 272000
        supports_vision: true
        supports_function_calling: true
      - name: o4-mini
        max_input_tokens: 200000
        supports_vision: true
        supports_function_calling: true
      - name: text-embedding-3-small
        type: embedding
        max_tokens_per_chunk: 8192
        default_chunk_size: 1000
        max_batch_size: 50

# RAG Configuration
rag_embedding_model: azure-openai:text-embedding-3-small
rag_reranker_model: null
rag_top_k: 5
rag_chunk_size: 2000
rag_chunk_overlap: 200
EOF
            log "aichat configured with Azure OpenAI at: $aichat_config_file"
        else
            log "Keeping existing aichat config"
        fi
    else
        warn "Could not retrieve Azure API key from llm"
        warn "Run: llm keys set azure"
    fi

elif [ "$GEMINI_CONFIGURED" = "true" ]; then
    # Configure aichat with Gemini
    gemini_api_key=$(command llm keys get gemini 2>/dev/null || echo "")

    if [ -n "$gemini_api_key" ]; then
        SHOULD_WRITE=true

        if [ -f "$aichat_config_file" ]; then
            # If using --gemini flag, automatically overwrite (user explicitly requested switch)
            if [ "$FORCE_GEMINI_CONFIG" = "true" ]; then
                cp "$aichat_config_file" "$aichat_config_file.backup.$(date +%Y%m%d-%H%M%S)"
                log "Backed up existing aichat config"
                log "Switching to Gemini configuration..."
            else
                # Normal setup - keep existing config, don't prompt
                log "aichat config already exists, keeping it"
                SHOULD_WRITE=false
            fi
        fi

        if [ "$SHOULD_WRITE" = "true" ]; then
            # Create Gemini-only aichat configuration file (inline)
            cat > "$aichat_config_file" <<EOF
# AIChat Configuration - Auto-generated by llm-linux-setup
# Google Gemini Integration

model: gemini:gemini-2.5-flash

clients:
  - type: gemini
    api_key: ${gemini_api_key}

# RAG Configuration
rag_embedding_model: gemini:text-embedding-004
rag_reranker_model: null
rag_top_k: 5
rag_chunk_size: 2000
rag_chunk_overlap: 200
EOF
            log "aichat configured with Gemini at: $aichat_config_file"
        else
            log "Keeping existing aichat config"
        fi
    else
        warn "Could not retrieve Gemini API key from llm"
        warn "Run: llm keys set gemini"
    fi
fi

#############################################################################
# PHASE 3: Install/Update LLM Plugins
#############################################################################

log "Installing/updating llm plugins..."

PLUGINS=(
    "llm-gemini"
    "git+https://github.com/c0ffee0wl/llm-vertex"
    "llm-openrouter"
    "llm-anthropic"
    "git+https://github.com/c0ffee0wl/llm-cmd"
    "git+https://github.com/c0ffee0wl/llm-cmd-comp"
    "llm-tools-quickjs"
    "llm-tools-sqlite"
    "git+https://github.com/c0ffee0wl/llm-tools-sandboxed-shell"
    "llm-fragments-site-text"
    "llm-fragments-pdf"
    "llm-fragments-github"
    "llm-jq"
    "git+https://github.com/c0ffee0wl/llm-templates-fabric"
    "git+https://github.com/c0ffee0wl/llm-tools-llm-functions"
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

# Copy templates from repository (with smart update check)
update_template_file "assistant"
update_template_file "code"

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

# Update shell RC files
update_shell_rc_file "$HOME/.bashrc" "$SCRIPT_DIR/integration/llm-integration.bash" ".bashrc"
update_shell_rc_file "$HOME/.zshrc" "$SCRIPT_DIR/integration/llm-integration.zsh" ".zshrc"

# Install context script
log "Installing context script..."
mkdir -p "$HOME/.local/bin"
cp "$SCRIPT_DIR/context/context" "$HOME/.local/bin/context"
chmod +x "$HOME/.local/bin/context"

#############################################################################
# PHASE 6: Additional Tools
#############################################################################

log "Installing/updating additional tools..."

# Install/update gitingest
install_or_upgrade_uv_tool gitingest

# Install/update files-to-prompt
install_or_upgrade_uv_tool files-to-prompt "git+https://github.com/c0ffee0wl/files-to-prompt"

# Install/update aichat
install_or_upgrade_cargo_tool aichat

# Install/update argc (prerequisite for llm-functions if users want to install it)
install_or_upgrade_cargo_tool argc

# Install/update yek (with commit-hash checking to avoid unnecessary rebuilds)
install_or_upgrade_cargo_git_tool yek https://github.com/bodo-run/yek

#############################################################################
# PHASE 7: Claude Code & OpenCode
#############################################################################

# Install/update Claude Code
log "Installing/updating Claude Code..."
npm_install install -g @anthropic-ai/claude-code

# # Install/update Claude Code Router
# log "Installing/updating Claude Code Router..."
# npm_install install -g @musistudio/claude-code-router

# # Configure Claude Code Router for Azure OpenAI
# CCR_CONFIG_DIR="$HOME/.claude-code-router"
# CCR_CONFIG_FILE="$CCR_CONFIG_DIR/config.json"

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

# log "Claude Code Router installed. Configure manually in $CCR_CONFIG_FILE if needed."

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
log "  - llm plugins (gemini, anthropic, tools, sandboxed-shell, fragments, jq, fabric templates, context, llm-functions bridge)"
log "  - aichat (All-in-one LLM CLI with RAG functionality)"
log "  - Claude Code (Anthropic's agentic coding CLI)"
# log "  - Claude Code Router (proxy for Claude Code with Azure OpenAI)"
log "  - OpenCode (AI coding agent for terminal)"
log "  - gitingest (Git repository to LLM-friendly text)"
log "  - yek (fast repository to LLM-friendly text converter)"
log "  - files-to-prompt (file content formatter)"
log "  - argc (Bash CLI framework, enables optional llm-functions)"
log "  - asciinema (terminal session recorder)"
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
log "To (re)configure Azure OpenAI settings:"
log "  ./install-llm-tools.sh --azure"
log ""
log "To (re)configure Gemini settings:"
log "  ./install-llm-tools.sh --gemini"