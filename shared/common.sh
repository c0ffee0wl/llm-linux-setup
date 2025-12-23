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
[[ -n "${_LLM_COMMON_SOURCED:-}" ]] && return
_LLM_COMMON_SOURCED=1

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
# APT Package Management
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
        if [ "$(printf '%s\n' "$MIN_GO_VERSION" "$current_version" | sort -V | head -n1)" = "$MIN_GO_VERSION" ]; then
            log "Go $current_version is already installed (>= $MIN_GO_VERSION)"
            return 0
        else
            warn "Go $current_version installed but >= $MIN_GO_VERSION required for imagemage"
            return 1
        fi
    fi

    # Check apt repository version
    local repo_version=$(apt-cache policy golang-go 2>/dev/null | grep -oP 'Candidate:\s*\K[0-9]+\.[0-9]+' | head -1)
    if [ -n "$repo_version" ] && [ "$(printf '%s\n' "$MIN_GO_VERSION" "$repo_version" | sort -V | head -n1)" = "$MIN_GO_VERSION" ]; then
        log "Installing Go $repo_version from apt..."
        sudo apt-get install -y golang-go
        return 0
    else
        warn "Go >= $MIN_GO_VERSION not available from apt (found: ${repo_version:-none})"
        warn "imagemage will be skipped. Install Go manually if needed."
        return 1
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
