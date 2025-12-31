#!/bin/bash
#
# Shared utility functions for llm-linux-setup scripts
#
# Usage: source "$SCRIPT_DIR/shared/common.sh"
#
# Required variables (set by caller before sourcing):
#   SCRIPT_DIR - Directory containing this repository
#
# Optional variables (set by caller if using npm functions):
#   NPM_NEEDS_SUDO - Whether npm needs sudo for global installs
#   NPM_PREFIX - npm prefix path (from npm config get prefix)
#
# Configuration:
#   LLM_TOOLS_CONFIG_DIR - Config directory (default: ~/.config/llm-tools)
#

# Source guard - prevent double-sourcing
[[ -n "${_SETUP_COMMON_SOURCED:-}" ]] && return
_SETUP_COMMON_SOURCED=1

#############################################################################
# Colors
#############################################################################

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

#############################################################################
# Configuration
#############################################################################

LLM_TOOLS_CONFIG_DIR="${LLM_TOOLS_CONFIG_DIR:-$HOME/.config/llm-tools}"

# Global mode flags for non-interactive script execution
# Set these in your script before sourcing common.sh or calling ask_yes_no()
YES_MODE=${YES_MODE:-false}
NO_MODE=${NO_MODE:-false}

#############################################################################
# Logging Functions
#############################################################################

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

#############################################################################
# Version Comparison
#############################################################################

# Compare two semantic versions
# Returns: 0 if equal, 1 if v1 > v2, 2 if v1 < v2
# Usage: compare_versions "1.85.0" "1.80.0"
compare_versions() {
    [[ $# -lt 2 ]] && return 2
    [[ "$1" == "$2" ]] && return 0

    local IFS=.
    local i v1=($1) v2=($2)

    # Compare each component
    for ((i=0; i<${#v1[@]} || i<${#v2[@]}; i++)); do
        local n1=${v1[i]:-0}
        local n2=${v2[i]:-0}
        ((n1 > n2)) && return 1
        ((n1 < n2)) && return 2
    done
    return 0
}

# Check if version is at least minimum required
# Returns: 0 (true) if v1 >= v2, 1 (false) otherwise
# Usage: if version_at_least "$current" "1.85"; then
version_at_least() {
    compare_versions "$1" "$2"
    [[ $? -le 1 ]]
}

# Check if version is less than target
# Returns: 0 (true) if v1 < v2, 1 (false) otherwise
# Usage: if version_less_than "$current" "1.85"; then
version_less_than() {
    compare_versions "$1" "$2"
    [[ $? -eq 2 ]]
}

#############################################################################
# Interactive Prompts
#############################################################################

# Prompt for yes/no confirmation with default
# Respects YES_MODE and NO_MODE global flags for non-interactive execution
# Returns: 0 for yes, 1 for no
# Usage: if ask_yes_no "Install Rust?" Y; then
#        if ask_yes_no "Overwrite?" N; then
ask_yes_no() {
    local prompt="$1"
    local default="${2:-Y}"
    local hint response

    # In yes mode, automatically answer yes
    if [[ "$YES_MODE" == "true" ]]; then
        log "Yes mode: Auto-answering 'Yes' to: $prompt"
        return 0
    fi

    # In no mode, automatically answer no
    if [[ "$NO_MODE" == "true" ]]; then
        log "No mode: Auto-answering 'No' to: $prompt"
        return 1
    fi

    if [[ "$default" =~ ^[Yy] ]]; then
        hint="(Y/n)"
    else
        hint="(y/N)"
    fi

    read -p "$prompt $hint: " response
    response=${response:-$default}
    [[ "$response" =~ ^[Yy] ]]
}

#############################################################################
# Checksum Functions
#############################################################################

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

#############################################################################
# Distribution Detection
#############################################################################

# Check if running on Kali Linux
is_kali_linux() {
    grep -q "Kali" /etc/os-release 2>/dev/null
}

# Check if running on Ubuntu or Ubuntu-based distribution
is_ubuntu() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        [ "$ID" = "ubuntu" ] || [ "$ID_LIKE" = "ubuntu" ] || echo "$ID_LIKE" | grep -q "ubuntu"
    else
        return 1
    fi
}

# Check if a desktop environment is available
# Detects xsessions, wayland-sessions, display managers, or common DE packages
has_desktop_environment() {
    # Check for desktop session files (most reliable)
    if [ -d /usr/share/xsessions ] && [ -n "$(ls -A /usr/share/xsessions 2>/dev/null)" ]; then
        return 0
    fi

    if [ -d /usr/share/wayland-sessions ] && [ -n "$(ls -A /usr/share/wayland-sessions 2>/dev/null)" ]; then
        return 0
    fi

    # Check for display manager configuration
    if [ -f /etc/X11/default-display-manager ] && [ -s /etc/X11/default-display-manager ]; then
        return 0
    fi

    # Check for common DE packages (Kali uses XFCE)
    if dpkg -l 2>/dev/null | grep -qE '^ii\s+(xfce4|gnome-shell|kde-plasma-desktop|plasma-desktop|lxde-core)' || false; then
        return 0
    fi

    return 1
}

# Check if running on Wayland display server
# Returns 0 if Wayland, 1 if X11 or unknown
is_wayland() {
    if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
        return 0
    fi
    if [ -n "${WAYLAND_DISPLAY:-}" ]; then
        return 0
    fi
    return 1
}

# Check if running on X11 display server
# Returns 0 if X11, 1 if Wayland or unknown
is_x11() {
    if [ "${XDG_SESSION_TYPE:-}" = "x11" ]; then
        return 0
    fi
    if [ -n "${DISPLAY:-}" ] && ! is_wayland; then
        return 0
    fi
    return 1
}

#############################################################################
# File Management
#############################################################################

# Create a timestamped backup of a file
# Usage: backup_file /path/to/file
backup_file() {
    local file_path="$1"
    if [ -f "$file_path" ]; then
        local backup_path="${file_path}.backup.$(date +'%Y-%m-%d_%H-%M-%S')"
        cp "$file_path" "$backup_path"
        log "Backed up to: $backup_path"
    fi
}

#############################################################################
# APT Package Management
#############################################################################

# Install apt package with existence check
# Usage: install_apt_package package_name [command_name]
# If command_name is provided, checks for that command instead of package_name
# Examples:
#   install_apt_package git                    # checks for 'git' command
#   install_apt_package bubblewrap bwrap       # checks for 'bwrap' command
#   install_apt_package poppler-utils pdftotext # checks for 'pdftotext' command
install_apt_package() {
    local package="$1"
    local command="${2:-$1}"  # Use second arg if provided, else package name
    if ! command -v "$command" &> /dev/null; then
        log "Installing $package..."
        sudo apt-get install -y "$package"
    else
        log "$package is already installed"
    fi
}

# Install multiple apt packages efficiently in a single apt call
# Usage: install_apt_packages package1 package2 package3 ...
# Checks each package via dpkg and installs only missing ones
# More efficient than multiple install_apt_package calls for batch installs
# Examples:
#   install_apt_packages python3-gi python3-gi-cairo python3-dbus
#   install_apt_packages build-essential libdbus-glib-1-dev libcairo2-dev
install_apt_packages() {
    local missing=()
    for pkg in "$@"; do
        if ! dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
            missing+=("$pkg")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        log "Installing ${missing[*]}..."
        sudo apt-get install -y "${missing[@]}"
    else
        log "All packages already installed: $*"
    fi
}

#############################################################################
# UV Tool Management
#############################################################################

# Configure uv to prefer system Python over managed Python versions
# This prevents issues with bleeding-edge Python versions (e.g., 3.14) lacking package wheels
# Idempotent: only adds config if not already present
configure_uv_system_python() {
    local uv_config_dir="$HOME/.config/uv"
    local uv_config_file="$uv_config_dir/uv.toml"

    # Skip if already configured
    if [ -f "$uv_config_file" ] && grep -q "python-preference" "$uv_config_file" 2>/dev/null; then
        return 0
    fi

    log "Configuring uv to prefer system Python..."
    mkdir -p "$uv_config_dir"

    # Ensure newline before appending if file exists and doesn't end with one
    if [ -f "$uv_config_file" ] && [ -s "$uv_config_file" ] && [ -n "$(tail -c1 "$uv_config_file")" ]; then
        echo "" >> "$uv_config_file"
    fi

    echo 'python-preference = "system"' >> "$uv_config_file"
}

# Install or upgrade a uv tool with intelligent source detection
# Usage: install_or_upgrade_uv_tool tool_name_or_source [python_version]
# Examples:
#   install_or_upgrade_uv_tool gitingest                             # PyPI package
#   install_or_upgrade_uv_tool "git+https://github.com/user/repo"    # Git package (auto-detected)
#   install_or_upgrade_uv_tool toko 3.14                             # With specific Python version
install_or_upgrade_uv_tool() {
    local tool_source="$1"
    local python_version="${2:-}"  # Optional Python version

    # Auto-detect git package from URL prefix
    local is_git_package=false
    if [[ "$tool_source" =~ ^git\+ ]]; then
        is_git_package=true
    fi

    # Build python flag if specified
    local python_flag=""
    if [ -n "$python_version" ]; then
        python_flag="--python $python_version"
    fi

    # Extract tool name from source (handles both PyPI names and git URLs)
    # Example: "git+https://github.com/c0ffee0wl/llm" -> "llm"
    local tool_name
    if [[ "$tool_source" =~ git\+https://.+/([^/]+?)(\.git)?$ ]]; then
        tool_name="${BASH_REMATCH[1]}"
    else
        tool_name="$tool_source"
    fi

    # Check if tool is already installed
    if uv tool list 2>/dev/null | grep -q "^$tool_name "; then
        if [ "$is_git_package" = "true" ]; then
            # For git packages, check if already from the same git source using uv command
            # Format: "llm v0.27.1 [required:  git+https://github.com/c0ffee0wl/llm]"
            local tool_info=$(uv tool list --show-version-specifiers 2>/dev/null | grep "^$tool_name " || true)

            # Extract current git URL if present
            local current_git_url=$(echo "$tool_info" | grep -oP '\[required:\s+git\+\K[^\]]+' || echo "")

            # Extract new git URL (remove git+ prefix)
            local new_git_url="${tool_source#git+}"

            if [ -n "$current_git_url" ] && [ "$current_git_url" = "$new_git_url" ]; then
                # Already from the same git source - just check for updates
                log "$tool_name is already from git source, checking for updates..."
                uv tool upgrade $python_flag "$tool_name"
            else
                # Different source (PyPI or different git repo) - force reinstall to migrate
                if [ -n "$current_git_url" ]; then
                    log "Migrating $tool_name git source:"
                    log "  Old: $current_git_url"
                    log "  New: $new_git_url"
                    log "Force reinstalling..."
                else
                    log "Migrating $tool_name from PyPI to git source..."
                    log "  Git: $new_git_url"
                fi
                uv tool install --force $python_flag "$tool_source"
            fi
        else
            # PyPI package - just upgrade
            log "$tool_name is already installed, upgrading..."
            uv tool upgrade $python_flag "$tool_name"
        fi
    else
        # Not installed - install it
        log "Installing $tool_name..."
        uv tool install $python_flag "$tool_source"
    fi
}

#############################################################################
# Rust/Cargo Management
#############################################################################

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

# Install or upgrade a Rust/Cargo tool from crates.io
# For git-based packages, use install_or_upgrade_cargo_git_tool instead
# Usage: install_or_upgrade_cargo_tool tool_name
# Examples:
#   install_or_upgrade_cargo_tool argc
install_or_upgrade_cargo_tool() {
    local tool_name="$1"

    # Installing from crates.io
    if ! command -v "$tool_name" &> /dev/null; then
        log "Installing $tool_name via cargo..."
        cargo install "$tool_name"
    else
        log "$tool_name is already installed, checking for updates..."
        cargo install "$tool_name"
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
# NPM Management
#############################################################################

# Wrapper function for npm global installs
# Requires: NPM_NEEDS_SUDO variable set by caller
npm_install() {
    local max_attempts=3
    local attempt=1
    local npm_stderr
    local npm_exit_code

    while [ $attempt -le $max_attempts ]; do
        # Create temp file for stderr
        npm_stderr=$(mktemp)

        # Disable exit-on-error to capture npm failures
        set +e
        # Run npm and capture stderr
        if [ "$NPM_NEEDS_SUDO" = "true" ]; then
            sudo npm "$@" 2>"$npm_stderr"
            npm_exit_code=$?
        else
            npm "$@" 2>"$npm_stderr"
            npm_exit_code=$?
        fi
        set -e  # Re-enable exit-on-error

        # Success - clean up and return
        if [ $npm_exit_code -eq 0 ]; then
            rm -f "$npm_stderr"
            return 0
        fi

        # Check for ENOTEMPTY error
        if grep -q "ENOTEMPTY" "$npm_stderr"; then
            # Extract the path from error message
            # Format: "ENOTEMPTY: directory not empty, rename '/path/to/dir' -> ..."
            local failed_path
            failed_path=$(grep "ENOTEMPTY" "$npm_stderr" | sed -n "s/.*rename '\([^']*\)'.*/\1/p" | head -1)

            if [ -n "$failed_path" ] && [ -d "$failed_path" ]; then
                warn "Detected ENOTEMPTY error for: $failed_path"
                warn "Cleaning up directory before retry..."

                if [ "$NPM_NEEDS_SUDO" = "true" ]; then
                    sudo rm -rf "$failed_path"
                else
                    rm -rf "$failed_path"
                fi
            fi
        fi

        # Show stderr to user
        cat "$npm_stderr" >&2
        rm -f "$npm_stderr"

        # Retry logic
        if [ $attempt -lt $max_attempts ]; then
            warn "npm install failed (attempt $attempt/$max_attempts), retrying in 2 seconds..."
            sleep 2
        fi
        attempt=$((attempt + 1))
    done

    warn "npm install failed after $max_attempts attempts"
    return 1
}

# Wrapper function for npm global uninstalls with ENOTEMPTY handling
# Usage: npm_uninstall_global package_name [binary_name]
# binary_name defaults to basename of package (e.g., "claude-code" from "@anthropic-ai/claude-code")
# Requires: NPM_NEEDS_SUDO, NPM_PREFIX variables set by caller
npm_uninstall_global() {
    local package="$1"
    local bin_name="${2:-$(basename "$package")}"
    local npm_stderr pkg_dir

    npm_stderr=$(mktemp)

    # First attempt
    set +e
    if [ "$NPM_NEEDS_SUDO" = "true" ]; then
        sudo npm uninstall -g "$package" 2>"$npm_stderr"
    else
        npm uninstall -g "$package" 2>"$npm_stderr"
    fi
    local exit_code=$?
    set -e

    if [ $exit_code -eq 0 ]; then
        rm -f "$npm_stderr"
        return 0
    fi

    # Check for ENOTEMPTY error
    if grep -q "ENOTEMPTY" "$npm_stderr"; then
        warn "ENOTEMPTY error during uninstall, force-removing package directory..."
        pkg_dir="$NPM_PREFIX/lib/node_modules/$package"

        if [ -d "$pkg_dir" ]; then
            if [ "$NPM_NEEDS_SUDO" = "true" ]; then
                sudo rm -rf "$pkg_dir"
            else
                rm -rf "$pkg_dir"
            fi
        fi

        # Also remove the binary
        if [ -f "$NPM_PREFIX/bin/$bin_name" ]; then
            if [ "$NPM_NEEDS_SUDO" = "true" ]; then
                sudo rm -f "$NPM_PREFIX/bin/$bin_name"
            else
                rm -f "$NPM_PREFIX/bin/$bin_name"
            fi
        fi

        rm -f "$npm_stderr"
        return 0
    fi

    # Other error - show it
    cat "$npm_stderr" >&2
    rm -f "$npm_stderr"
    return $exit_code
}

# Install or upgrade npm global package only if newer version available
# Usage: install_or_upgrade_npm_global package_name
install_or_upgrade_npm_global() {
    local package="$1"
    local installed_version latest_version

    # Get installed version (empty if not installed)
    installed_version=$(npm list -g "$package" --depth=0 2>/dev/null | grep -oP "$package@\K[0-9.]+") || installed_version=""

    if [ -z "$installed_version" ]; then
        log "Installing $package..."
        npm_install install -g "$package"
    else
        # Get latest version from npm registry
        latest_version=$(npm view "$package" version 2>/dev/null) || latest_version=""

        if [ -n "$latest_version" ] && [ "$installed_version" != "$latest_version" ]; then
            log "Upgrading $package: $installed_version -> $latest_version"
            npm_install install -g "$package"
        else
            log "$package is up to date ($installed_version)"
        fi
    fi
}

# Upgrade npm global package only if already installed (no installation)
# Usage: upgrade_npm_global_if_installed package_name
upgrade_npm_global_if_installed() {
    local package="$1"
    local installed_version latest_version

    # Get installed version (empty if not installed)
    installed_version=$(npm list -g "$package" --depth=0 2>/dev/null | grep -oP "$package@\K[0-9.]+") || installed_version=""

    if [ -z "$installed_version" ]; then
        log "Skipping $package update (not installed)"
        return 0
    fi

    # Get latest version from npm registry
    latest_version=$(npm view "$package" version 2>/dev/null) || latest_version=""

    if [ -n "$latest_version" ] && [ "$installed_version" != "$latest_version" ]; then
        log "Upgrading $package: $installed_version -> $latest_version"
        npm_install install -g "$package"
    else
        log "$package is up to date ($installed_version)"
    fi
}

#############################################################################
# UV Package Manager
#############################################################################

# Install or upgrade uv via pipx
# Sets up PATH and configures uv to prefer system Python
install_or_upgrade_uv() {
    export PATH=$HOME/.local/bin:$PATH
    if ! command -v uv &> /dev/null; then
        log "Installing uv..."
        pipx install uv
    else
        log "uv is already installed, upgrading..."
        pipx upgrade uv
    fi

    # Configure uv to prefer system Python (prevents issues with Python 3.14+ lacking package wheels)
    configure_uv_system_python
}

#############################################################################
# Rust Version Management
#############################################################################

# Minimum Rust version required (1.85 for edition2024 cargo tools)
MINIMUM_RUST_VERSION="1.85"

# Install or upgrade Rust with intelligent version detection
# Uses apt if repo version >= 1.85, otherwise falls back to rustup
# Prompts user if upgrade needed from old apt version to rustup
install_or_upgrade_rust() {
    # Check what Rust version is available in repositories
    log "Checking Rust version in repositories..."
    local repo_rust_version=$(apt-cache policy rustc 2>/dev/null | grep -oP 'Candidate:\s*\K[0-9]+\.[0-9]+' | head -1)

    if [ -z "$repo_rust_version" ]; then
        repo_rust_version="0.0"
        warn "Could not determine repository Rust version"
    fi

    log "Repository has Rust version: $repo_rust_version (minimum required: $MINIMUM_RUST_VERSION)"

    # Install Rust - either from repo (if >= 1.85) or via rustup (if < 1.85)
    if ! command -v cargo &> /dev/null; then
        # Check if rustup is already managing Rust (rustup might be installed but not active)
        if command -v rustup &> /dev/null; then
            log "rustup is installed but cargo not found, installing Rust via rustup..."
            rustup toolchain install stable
            rustup default stable
        elif version_at_least "$repo_rust_version" "$MINIMUM_RUST_VERSION"; then
            log "Installing Rust from repositories (version $repo_rust_version)..."
            sudo apt-get install -y cargo rustc
        else
            log "Repository version $repo_rust_version is < $MINIMUM_RUST_VERSION, installing Rust via rustup..."
            install_rust_via_rustup
        fi
    else
        # Rust is already installed - determine if it's managed by rustup or apt
        if command -v rustup &> /dev/null; then
            local current_rust_version=$(rustc --version | grep -oP 'rustc \K[0-9]+\.[0-9]+' | head -1)
            log "Rust is already installed via rustup (version $current_rust_version)"

            # Check if we need to update
            update_rust_via_rustup
        else
            local current_rust_version=$(rustc --version | grep -oP 'rustc \K[0-9]+\.[0-9]+' | head -1)
            log "Rust is already installed from system packages (version $current_rust_version)"

            # If current version is < 1.85, offer to upgrade via rustup
            if version_less_than "$current_rust_version" "$MINIMUM_RUST_VERSION"; then
                warn "Installed Rust version $current_rust_version is too old (requires $MINIMUM_RUST_VERSION+)"
                if ask_yes_no "Install Rust $MINIMUM_RUST_VERSION+ via rustup? This will shadow the system installation." Y; then
                    install_rust_via_rustup
                    log "Rust upgraded successfully. rustup version will take precedence over system version."
                else
                    warn "Continuing with old Rust version. Some cargo tool builds may fail."
                    warn "To upgrade manually later, run: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
                fi
            fi
        fi
    fi
}

#############################################################################
# Node.js Version Management
#############################################################################

# Minimum Node.js version required (20 for Claude Code)
MINIMUM_NODE_VERSION="20"

# Install or upgrade Node.js with intelligent version detection
# Uses apt if repo version >= 20, otherwise falls back to nvm (Node 22)
# Also ensures npm is installed
install_or_upgrade_nodejs() {
    # Check what Node.js version is available in repositories
    log "Checking Node.js version in repositories..."
    local repo_node_version=$(apt-cache policy nodejs 2>/dev/null | grep -oP 'Candidate:\s*\K[0-9]+' | head -1)

    if [ -z "$repo_node_version" ]; then
        repo_node_version="0"
        warn "Could not determine repository Node.js version"
    fi

    log "Repository has Node.js version: $repo_node_version (minimum required: $MINIMUM_NODE_VERSION)"

    # Install Node.js - either from repo (if >= 20) or via nvm (if < 20)
    if ! command -v node &> /dev/null; then
        if version_at_least "$repo_node_version" "$MINIMUM_NODE_VERSION"; then
            log "Installing Node.js from repositories (version $repo_node_version)..."
            sudo apt-get install -y nodejs

            # Install npm separately for repository installations
            if ! command -v npm &> /dev/null; then
                log "Installing npm..."
                sudo apt-get install -y npm
            fi
        else
            log "Repository version $repo_node_version is < $MINIMUM_NODE_VERSION, installing Node 22 via nvm..."

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
        local current_node_version=$(node --version | grep -oP 'v\K[0-9]+' || true)
        log "Node.js is already installed (version $current_node_version)"

        # If current version is < 20, warn user
        if version_less_than "$current_node_version" "$MINIMUM_NODE_VERSION"; then
            warn "Installed Node.js version $current_node_version is < $MINIMUM_NODE_VERSION. Consider upgrading to Node 22 via nvm."
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
}

# Detect if npm needs sudo for global installs
# Sets NPM_NEEDS_SUDO and NPM_PREFIX variables
detect_npm_permissions() {
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
    export NPM_NEEDS_SUDO NPM_PREFIX
}

#############################################################################
# GitHub Deb Package Management
#############################################################################

# Install or upgrade a deb package from GitHub releases
# Supports version checking, process killing before upgrade, and architecture filtering
#
# Usage: install_github_deb_package package_name version github_url [kill_process] [arch_filter]
#
# Parameters:
#   package_name  - Name of the package as registered in dpkg (e.g., "handy")
#   version       - Version to install (e.g., "0.6.8")
#   github_url    - URL template with {VERSION} and {ARCH} placeholders
#                   Example: "https://github.com/user/repo/releases/download/v{VERSION}/Package_{VERSION}_{ARCH}.deb"
#   kill_process  - Optional process name to kill before upgrade (case-insensitive)
#   arch_filter   - Optional required architecture (default: "x86_64"). Set to "" to allow all.
#
# Returns: 0 on success or skip, 1 on failure
#
# Example:
#   install_github_deb_package "handy" "0.6.8" \
#     "https://github.com/cjpais/Handy/releases/download/v{VERSION}/Handy_{VERSION}_amd64.deb" \
#     "handy" "x86_64"
#
install_github_deb_package() {
    local package_name="$1"
    local version="$2"
    local github_url_template="$3"
    local kill_process="${4:-}"
    local arch_filter="${5:-x86_64}"

    # Check architecture if filter is specified
    if [ -n "$arch_filter" ]; then
        local current_arch=$(uname -m)
        if [ "$current_arch" != "$arch_filter" ]; then
            log "Skipping $package_name: only $arch_filter deb package available (current: $current_arch)"
            return 0
        fi
    fi

    # Check installed version via dpkg
    local installed_version=$(dpkg-query -W -f='${Version}' "$package_name" 2>/dev/null || echo "")

    if [ -z "$installed_version" ]; then
        log "Installing $package_name $version..."
    elif [ "$installed_version" = "$version" ]; then
        log "$package_name $version is already installed"
        return 0
    elif dpkg --compare-versions "$installed_version" ge "$version"; then
        log "$package_name $installed_version is already installed (>= $version)"
        return 0
    else
        log "Upgrading $package_name from $installed_version to $version..."
    fi

    # Kill process if specified and running (required for clean upgrade)
    if [ -n "$kill_process" ]; then
        if pgrep -xi "$kill_process" >/dev/null 2>&1; then
            log "Stopping $kill_process process for upgrade..."
            pkill -xi "$kill_process" || true
            sleep 1
        fi
    fi

    # Substitute version and architecture in URL template
    local arch_deb="amd64"  # deb package architecture naming
    local github_url="${github_url_template//\{VERSION\}/$version}"
    github_url="${github_url//\{ARCH\}/$arch_deb}"

    # Download and install
    local deb_file="/tmp/${package_name}_${version}_${arch_deb}.deb"
    curl -fL "$github_url" -o "$deb_file"

    if [ -f "$deb_file" ]; then
        # dpkg -i may leave package unconfigured if dependencies are missing
        # Always run apt-get install -f to resolve dependencies and configure
        sudo dpkg -i "$deb_file" || true
        sudo apt-get install -f -y
        rm -f "$deb_file"
        log "$package_name $version installed via deb package"
        return 0
    else
        warn "Failed to download $package_name"
        return 1
    fi
}

#############################################################################
# Go Management
#############################################################################

# Install Go if not present or version is insufficient
# Returns 0 if Go is available (>= MIN_GO_VERSION), 1 otherwise
# Only installs from apt - warns and skips if repo version is insufficient
install_go() {
    local MIN_GO_VERSION="1.22"

    # Check if already installed with sufficient version
    if command -v go &> /dev/null; then
        local current_version=$(go version | grep -oP 'go\K[0-9]+\.[0-9]+' || true)
        if version_at_least "$current_version" "$MIN_GO_VERSION"; then
            log "Go $current_version is already installed (>= $MIN_GO_VERSION)"
            return 0
        else
            warn "Go $current_version installed but >= $MIN_GO_VERSION required for imagemage"
            return 1
        fi
    fi

    # Check apt repository version
    local repo_version=$(apt-cache policy golang-go 2>/dev/null | grep -oP 'Candidate:\s*\K[0-9]+\.[0-9]+' | head -1)
    if [ -n "$repo_version" ] && version_at_least "$repo_version" "$MIN_GO_VERSION"; then
        log "Installing Go $repo_version from apt..."
        sudo apt-get install -y golang-go
        return 0
    else
        warn "Go >= $MIN_GO_VERSION not available from apt (found: ${repo_version:-none})"
        warn "imagemage will be skipped. Install Go manually if needed."
        return 1
    fi
}

# Install a Go tool via go install
# Usage: install_go_tool <tool-name> <go-package-path>
# Example: install_go_tool "lazygit" "github.com/jesseduffield/lazygit@latest"
install_go_tool() {
    local tool_name="$1"
    local package_path="$2"

    log "Installing ${tool_name}..."
    if ! command -v "$tool_name" &> /dev/null; then
        export PATH=$HOME/go/bin:$PATH
        go install -v "$package_path"
    else
        log "${tool_name} is already installed"
    fi
}

#############################################################################
# Utility Functions
#############################################################################

# Extract normalized plugin name from various source formats
# Examples:
#   "git+https://github.com/c0ffee0wl/llm-vertex" -> "llm-vertex"
#   "/opt/llm-linux-setup/llm-tools-context" -> "llm-tools-context"
#   "llm-gemini" -> "llm-gemini"
extract_plugin_name() {
    local source="$1"

    # Git URL: git+https://github.com/user/llm-foo -> llm-foo
    # Note: Use [+] not \+ for ERE compatibility in bash
    if [[ "$source" =~ ^git[+]https:// ]]; then
        # Extract last path component, strip optional .git suffix
        echo "$source" | sed 's|.*/||; s|\.git$||'
        return
    fi

    # Local path: /path/to/llm-foo -> llm-foo
    if [[ "$source" =~ ^/ ]]; then
        basename "$source"
        return
    fi

    # PyPI name: llm-foo -> llm-foo
    echo "$source"
}

# Apply PipeWire VM audio fix (WirePlumber configuration)
# Increases buffer size to prevent audio stuttering in virtual machines
# Reference: https://gitlab.freedesktop.org/pipewire/pipewire/-/wikis/Troubleshooting#stuttering-audio-in-virtual-machine
apply_pipewire_vm_fix() {
    local config_dir="$HOME/.config/wireplumber/wireplumber.conf.d"
    local config_file="$config_dir/50-alsa-config.conf"

    # Skip if already configured
    if [ -f "$config_file" ]; then
        log "PipeWire VM audio fix already applied"
        return 0
    fi

    log "Applying PipeWire VM audio fix..."
    mkdir -p "$config_dir"

    cat > "$config_file" << 'EOF'
monitor.alsa.rules = [
  {
    matches = [
      {
        node.name = "~alsa_output.*"
      }
    ]
    actions = {
      update-props = {
        api.alsa.period-size   = 1024
        api.alsa.headroom      = 8192
      }
    }
  }
]
EOF

    # Restart PipeWire services if running
    if systemctl --user is-active pipewire &>/dev/null; then
        log "Restarting PipeWire services..."
        systemctl --user restart wireplumber pipewire pipewire-pulse 2>/dev/null || true
    fi

    log "PipeWire VM audio fix applied successfully"
}

# Clear package manager caches to reclaim disk space
clear_package_caches() {
    log "Clearing package manager caches..."

    # npm cache (~/.npm/_cacache)
    if command -v npm &>/dev/null; then
        log "Clearing npm cache..."
        npm cache clean --force 2>/dev/null || warn "npm cache clean failed"
    fi

    # Go module cache (~/go/pkg/mod)
    if command -v go &>/dev/null; then
        log "Clearing Go module cache..."
        go clean -modcache 2>/dev/null || warn "go clean -modcache failed"
    fi

    # pip cache (~/.cache/pip)
    # Using python3 -m pip for robustness (works even if only pip3 exists)
    if python3 -m pip --version &>/dev/null 2>&1; then
        log "Clearing pip cache..."
        python3 -m pip cache purge 2>/dev/null || warn "pip cache purge failed"
    fi

    # pipx cache (~/.cache/pipx for XDG-compliant pipx 1.2.0+) - no built-in command yet
    if command -v pipx &>/dev/null && [ -d "$HOME/.cache/pipx" ]; then
        log "Clearing pipx cache..."
        rm -rf "$HOME/.cache/pipx" 2>/dev/null || warn "pipx cache removal failed"
    fi

    # cargo cache (~/.cargo/registry, ~/.cargo/git)
    # Note: cargo clean gc exists but is unstable (requires -Z gc nightly flag)
    # Using manual removal as it works on all Rust versions
    if command -v cargo &>/dev/null; then
        log "Clearing cargo cache..."
        rm -rf "$HOME/.cargo/registry/cache" "$HOME/.cargo/registry/src" "$HOME/.cargo/git/checkouts" 2>/dev/null || warn "cargo cache removal failed"
    fi

    # uv cache
    if command -v uv &>/dev/null; then
        log "Clearing uv cache..."
        uv cache clean 2>/dev/null || warn "uv cache clean failed"
    fi

    log "Cache cleanup complete!"
}
