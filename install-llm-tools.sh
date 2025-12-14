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

# Install or upgrade a uv tool with intelligent source detection
# Usage: install_or_upgrade_uv_tool tool_name_or_source [is_git_package]
# Examples:
#   install_or_upgrade_uv_tool gitingest                                    # PyPI package
#   install_or_upgrade_uv_tool "git+https://github.com/user/repo" true     # Git package
install_or_upgrade_uv_tool() {
    local tool_source="$1"
    local is_git_package="${2:-false}"  # Default to false (PyPI package)

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
            local tool_info=$(uv tool list --show-version-specifiers 2>/dev/null | grep "^$tool_name ")

            # Extract current git URL if present
            local current_git_url=$(echo "$tool_info" | grep -oP '\[required:\s+git\+\K[^\]]+' || echo "")

            # Extract new git URL (remove git+ prefix)
            local new_git_url="${tool_source#git+}"

            if [ -n "$current_git_url" ] && [ "$current_git_url" = "$new_git_url" ]; then
                # Already from the same git source - just check for updates
                log "$tool_name is already from git source, checking for updates..."
                uv tool upgrade "$tool_name"
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
                uv tool install --force "$tool_source"
            fi
        else
            # PyPI package - just upgrade
            log "$tool_name is already installed, upgrading..."
            uv tool upgrade "$tool_name"
        fi
    else
        # Not installed - install it
        log "Installing $tool_name..."
        uv tool install "$tool_source"
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
        prompt_for_session_log_silent
        log "Adding session log configuration and llm integration to $shell_name..."
        cat >> "$rc_file" <<EOF

# LLM Session Log Directory
export SESSION_LOG_DIR="$SESSION_LOG_DIR_VALUE"

# LLM Session Log Silent Mode (1=suppress messages, 0=show messages)
export SESSION_LOG_SILENT=$SESSION_LOG_SILENT_VALUE

# LLM Tools Integration
if [ -f "$integration_file" ]; then
    source "$integration_file"
fi
EOF
    elif ! grep -q "export SESSION_LOG_SILENT=" "$rc_file"; then
        # Existing installation without SESSION_LOG_SILENT - add it
        prompt_for_session_log_silent
        log "Adding session log silent configuration to $shell_name..."
        # Insert SESSION_LOG_SILENT before the integration source line
        sed -i "/# LLM Tools Integration/i # LLM Session Log Silent Mode (1=suppress messages, 0=show messages)\nexport SESSION_LOG_SILENT=$SESSION_LOG_SILENT_VALUE\n" "$rc_file"
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

    # Validate URL is not empty and starts with https://
    if [ -z "$AZURE_API_BASE" ]; then
        error "Azure API base URL cannot be empty"
    fi
    if [[ ! "$AZURE_API_BASE" =~ ^https:// ]]; then
        error "Azure API base URL must start with https://"
    fi

    # Set the API key
    command llm keys set azure

    # Verify the key was actually set
    if ! command llm keys get azure &>/dev/null; then
        warn "Azure API key was not set successfully"
        AZURE_CONFIGURED=false
        return 1
    fi

    AZURE_CONFIGURED=true
}

# Configure Google Gemini with prompts
configure_gemini() {
    log "Configuring Google Gemini API..."
    echo ""
    echo "Get your free API key from: https://ai.google.dev/gemini-api/docs/api-key"
    echo ""

    # Set the API key
    command llm keys set gemini

    # Verify the key was actually set
    if ! command llm keys get gemini &>/dev/null; then
        warn "Gemini API key was not set successfully"
        GEMINI_CONFIGURED=false
        return 1
    fi

    GEMINI_CONFIGURED=true
}

# Configure Codex CLI with Azure OpenAI credentials
configure_codex_cli() {
    log "Configuring Codex CLI with Azure OpenAI..."

    # Extract api_base from extra-openai-models.yaml
    local EXTRA_MODELS_FILE="$(command llm logs path 2>/dev/null | tail -n1 | xargs dirname)/extra-openai-models.yaml"
    local api_base=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" | sed 's/.*api_base:\s*//;s/\s*$//')

    if [ -z "$api_base" ]; then
        log "WARNING: Could not extract Azure API base from llm config"
        return 1
    fi

    # Retrieve API key from llm keys storage
    local api_key=$(command llm keys get azure 2>/dev/null || echo "")

    if [ -z "$api_key" ]; then
        log "WARNING: Could not retrieve Azure API key from llm keys storage"
        return 1
    fi

    # Create ~/.codex directory if needed
    mkdir -p ~/.codex

    # Generate config.toml
    cat > ~/.codex/config.toml <<EOF
model = "gpt-5.1-codex"
model_provider = "azure"
model_reasoning_effort = "medium"

[model_providers.azure]
name = "Azure OpenAI"
base_url = "${api_base}"
env_key = "AZURE_OPENAI_API_KEY"
wire_api = "responses"
EOF

    log "Codex CLI configuration created at ~/.codex/config.toml"
}

# Export Azure environment variables to ~/.profile
export_azure_env_vars() {
    log "Exporting Azure environment variables to ~/.profile..."

    # Extract api_base and resource name
    local EXTRA_MODELS_FILE="$(command llm logs path 2>/dev/null | tail -n1 | xargs dirname)/extra-openai-models.yaml"
    local api_base=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" | sed 's/.*api_base:\s*//;s/\s*$//')

    if [ -z "$api_base" ]; then
        log "WARNING: Could not extract Azure API base for environment variables"
        return 1
    fi

    # Extract resource name from URL (e.g., https://your-resource.openai.azure.com/... -> your-resource)
    local resource_name=$(echo "$api_base" | sed 's|https://\([^.]*\)\..*|\1|')

    # Retrieve API key
    local api_key=$(command llm keys get azure 2>/dev/null || echo "")

    if [ -z "$api_key" ]; then
        log "WARNING: Could not retrieve Azure API key for environment variables"
        return 1
    fi

    # Create ~/.profile if it doesn't exist
    touch ~/.profile

    # Add or update AZURE_OPENAI_API_KEY export (idempotent)
    if grep -q "^export AZURE_OPENAI_API_KEY=" ~/.profile; then
        # Update existing export
        sed -i "s|^export AZURE_OPENAI_API_KEY=.*|export AZURE_OPENAI_API_KEY=\"${api_key}\"|" ~/.profile
        log "Updated AZURE_OPENAI_API_KEY in ~/.profile"
    else
        # Add new export
        echo "" >> ~/.profile
        echo "# Azure OpenAI configuration (added by llm-linux-setup)" >> ~/.profile
        echo "export AZURE_OPENAI_API_KEY=\"${api_key}\"" >> ~/.profile
        log "Added AZURE_OPENAI_API_KEY to ~/.profile"
    fi

    # Add or update AZURE_RESOURCE_NAME export (idempotent)
    if grep -q "^export AZURE_RESOURCE_NAME=" ~/.profile; then
        # Update existing export
        sed -i "s|^export AZURE_RESOURCE_NAME=.*|export AZURE_RESOURCE_NAME=\"${resource_name}\"|" ~/.profile
        log "Updated AZURE_RESOURCE_NAME in ~/.profile"
    else
        # Add new export
        echo "export AZURE_RESOURCE_NAME=\"${resource_name}\"" >> ~/.profile
        log "Added AZURE_RESOURCE_NAME to ~/.profile"
    fi

    log "Azure environment variables configured in ~/.profile"
    log "Note: Run 'source ~/.profile' to load them in current session"
}

# Export Gemini environment variables to ~/.profile
export_gemini_env_vars() {
    log "Exporting Gemini environment variables to ~/.profile..."

    # Retrieve API key
    local api_key=$(command llm keys get gemini 2>/dev/null || echo "")

    if [ -z "$api_key" ]; then
        log "WARNING: Could not retrieve Gemini API key for environment variables"
        return 1
    fi

    # Create ~/.profile if it doesn't exist
    touch ~/.profile

    # Add or update GEMINI_API_KEY export (idempotent)
    if grep -q "^export GEMINI_API_KEY=" ~/.profile; then
        # Update existing export
        sed -i "s|^export GEMINI_API_KEY=.*|export GEMINI_API_KEY=\"${api_key}\"|" ~/.profile
        log "Updated GEMINI_API_KEY in ~/.profile"
    else
        # Add new export
        echo "" >> ~/.profile
        echo "# Gemini API configuration (added by llm-linux-setup)" >> ~/.profile
        echo "export GEMINI_API_KEY=\"${api_key}\"" >> ~/.profile
        log "Added GEMINI_API_KEY to ~/.profile"
    fi

    log "Gemini environment variables configured in ~/.profile"
    log "Note: Run 'source ~/.profile' to load them in current session"
}

# Update Claude Code Router configuration with checksum tracking
update_ccr_config() {
    local ccr_dir="$HOME/.claude-code-router"
    local plugin_dir="$ccr_dir/plugins"
    local config_file="$ccr_dir/config.json"
    local plugin_file="$plugin_dir/strip-reasoning.js"

    log "Configuring Claude Code Router..."

    # Create directories
    mkdir -p "$plugin_dir"

    # Check provider availability
    # Azure: use AZURE_CONFIGURED (ensures YAML file exists)
    # Gemini: check key store directly (no file dependency)
    local has_azure_key="$AZURE_CONFIGURED"
    local has_gemini_key=false
    if command llm keys get gemini &>/dev/null; then
        has_gemini_key=true
    fi

    # Generate config based on available providers (use actual key existence)
    local config_content

    if [ "$has_azure_key" = "true" ] && [ "$has_gemini_key" = "true" ]; then
        # Dual-provider config: Azure primary + Gemini web search
        log "Generating dual-provider config (Azure primary, Gemini web search)"

        # Create strip-reasoning.js plugin for Azure
        cat > "$plugin_file" <<'EOF'
class StripReasoningTransformer {
  name = "strip-reasoning";

  constructor(options) {
    this.options = options || {};
  }

  async transformRequestIn(request, provider) {
    delete request.reasoning;

    return request;
  }

  // Optional: transformResponseOut if needed for response transformation
  async transformResponseOut(response, provider) {
    return response;
  }
}

module.exports = StripReasoningTransformer;
EOF
        log "Created strip-reasoning.js transformer plugin"

        # Extract Azure API base
        local azure_api_base=""
        local EXTRA_MODELS_FILE="$(command llm logs path 2>/dev/null | tail -n1 | xargs dirname)/extra-openai-models.yaml"
        if [ -f "$EXTRA_MODELS_FILE" ]; then
            azure_api_base=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" | sed 's/.*api_base:\s*//;s/\s*$//')
        fi

        # Strip trailing slash if present
        azure_api_base="${azure_api_base%/}"

        config_content=$(cat <<EOF
{
  "LOG": true,
  "LOG_LEVEL": "warn",
  "Providers": [
    {
      "name": "azure-gpt4",
      "api_base_url": "${azure_api_base}/chat/completions",
      "api_key": "\$AZURE_OPENAI_API_KEY",
      "models": [
        "gpt-4.1",
        "gpt-4.1-mini"
      ]
    },
    {
      "name": "azure-gpt5",
      "api_base_url": "${azure_api_base}/chat/completions",
      "api_key": "\$AZURE_OPENAI_API_KEY",
      "models": [
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5.1"
      ],
      "transformer": {
        "use": [
          "maxcompletiontokens",
          "strip-reasoning"
        ]
      }
    },
    {
      "name": "azure-codex",
      "api_base_url": "${azure_api_base}/responses",
      "api_key": "\$AZURE_OPENAI_API_KEY",
      "models": [
        "gpt-5.1-codex"
      ],
      "transformer": {
        "use": [
          "openai-responses",
          "strip-reasoning"
        ]
      }
    },
    {
      "name": "gemini",
      "api_base_url": "https://generativelanguage.googleapis.com/v1beta/models/",
      "api_key": "\$GEMINI_API_KEY",
      "models": [
        "gemini-2.5-flash",
        "gemini-2.5-pro"
      ],
      "transformer": {
        "use": [
          "gemini"
        ]
      }
    }
  ],
  "transformers": [
    {
      "path": "${HOME}/.claude-code-router/plugins/strip-reasoning.js"
    }
  ],
  "Router": {
    "default": "azure-codex,gpt-5.1-codex",
    "background": "azure-gpt4,gpt-4.1-mini",
    "think": "azure-codex,gpt-5.1-codex",
    "longContext": "azure-codex,gpt-5.1-codex",
    "webSearch": "gemini,gemini-2.5-flash"
  },
  "NON_INTERACTIVE_MODE": false
}
EOF
)

    elif [ "$has_gemini_key" = "true" ]; then
        # Gemini-only config
        log "Generating Gemini-only config"

        config_content=$(cat <<EOF
{
  "LOG": true,
  "LOG_LEVEL": "warn",
  "Providers": [
    {
      "name": "gemini",
      "api_base_url": "https://generativelanguage.googleapis.com/v1beta/models/",
      "api_key": "\$GEMINI_API_KEY",
      "models": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3-pro-preview"
      ],
      "transformer": {
        "use": [
          "gemini"
        ]
      }
    }
  ],
  "Router": {
    "default": "gemini,gemini-3-pro-preview",
    "background": "gemini,gemini-2.5-flash",
    "think": "gemini,gemini-3-pro-preview",
    "longContext": "gemini,gemini-3-pro-preview",
    "webSearch": "gemini,gemini-2.5-flash"
  },
  "NON_INTERACTIVE_MODE": false
}
EOF
)

    else
        log "ERROR: No providers configured for Claude Code Router"
        return 1
    fi

    # Calculate checksum of new config
    local new_checksum=$(echo "$config_content" | sha256sum | awk '{print $1}')

    # Check if config exists
    if [ ! -f "$config_file" ]; then
        # No existing config - create it
        echo "$config_content" > "$config_file"
        store_checksum "ccr-config" "$config_file"
        log "Created Claude Code Router config.json"
        return
    fi

    # Calculate installed file checksum
    local installed_checksum=$(sha256sum "$config_file" | awk '{print $1}')

    # Check if already up to date
    if [ "$installed_checksum" = "$new_checksum" ]; then
        log "Claude Code Router config.json is up to date"
        store_checksum "ccr-config" "$config_file"
        return
    fi

    # Get stored checksum (what we last installed)
    local stored_checksum=$(get_stored_checksum "ccr-config")

    if [ -n "$stored_checksum" ] && [ "$installed_checksum" = "$stored_checksum" ]; then
        # User hasn't modified the file - auto-update silently
        log "Updating Claude Code Router config.json (no local modifications detected)"
        echo "$config_content" > "$config_file"
        store_checksum "ccr-config" "$config_file"
    else
        # User has modified the file OR no stored checksum - prompt
        log "Claude Code Router config.json has changed"
        if [ -z "$stored_checksum" ]; then
            log "Cannot determine if you have local modifications (legacy installation)"
        else
            log "Local modifications detected in config.json"
        fi
        echo ""
        read -p "Update Claude Code Router config.json? This will overwrite your version. (y/N): " UPDATE_CCR
        if [[ "$UPDATE_CCR" =~ ^[Yy]$ ]]; then
            # Backup existing config
            cp "$config_file" "$config_file.backup-$(date +%Y%m%d-%H%M%S)"
            log "Backed up existing config to $config_file.backup-$(date +%Y%m%d-%H%M%S)"
            echo "$config_content" > "$config_file"
            store_checksum "ccr-config" "$config_file"
            log "Claude Code Router config.json updated"
        else
            log "Keeping existing Claude Code Router config.json"
            # Update stored checksum to current installed version to avoid prompting next time
            store_checksum "ccr-config" "$config_file"
        fi
    fi
}

# Set or migrate default model (handles automatic migration from old defaults)
set_or_migrate_default_model() {
    local new_default="$1"
    local DEFAULT_MODEL_FILE="$(command llm logs path 2>/dev/null | tail -n1 | xargs dirname)/default_model.txt"

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

# Install or upgrade an LLM plugin with intelligent skip logic
# Skips already-installed plugins unless:
#   - Plugin is not installed (missing from llm plugins output)
#   - Plugin is local/editable (always reinstall, may have changed)
#   - Plugin needs source migration (git URL not in uv-tool-packages.json)
# Usage: install_or_upgrade_llm_plugin "plugin_source"
install_or_upgrade_llm_plugin() {
    local plugin_source="$1"
    local plugin_name
    local uv_packages_file="$HOME/.config/io.datasette.llm/uv-tool-packages.json"

    plugin_name=$(extract_plugin_name "$plugin_source")

    # Check if plugin is loaded (uses cached result from INSTALLED_PLUGINS)
    local is_installed=false
    if echo "$INSTALLED_PLUGINS" | grep -q "^${plugin_name}$"; then
        is_installed=true
    fi

    # Check if exact source is tracked in uv-tool-packages.json
    local source_tracked=false
    if [ -f "$uv_packages_file" ] && grep -q "\"${plugin_source}\"" "$uv_packages_file" 2>/dev/null; then
        source_tracked=true
    fi

    # Decision logic
    if [ "$is_installed" = "false" ]; then
        # Not installed -> install
        log "Installing $plugin_name..."
        command llm install "$plugin_source" 2>/dev/null || command llm install "$plugin_source"

    elif [[ "$plugin_source" =~ ^/ ]]; then
        # Local/editable package -> always reinstall
        log "Reinstalling local plugin $plugin_name..."
        command llm install "$plugin_source" 2>/dev/null || true

    elif [[ "$plugin_source" =~ ^git[+] ]] && [ "$source_tracked" = "false" ]; then
        # Git source but URL not tracked -> migration needed
        log "Migrating $plugin_name to git source..."
        command llm install "$plugin_source" --upgrade 2>/dev/null || command llm install "$plugin_source"

    else
        # Already installed with correct source -> skip
        log "$plugin_name is already installed"
    fi
}

# Clean up stale local paths from both tracking files:
# 1. uv-tool-packages.json - llm-uv-tool's tracking (for llm install interception)
# 2. uv-receipt.toml - uv's internal tracking (for uv tool upgrade)
# Must be called BEFORE llm upgrade, otherwise uv tries to reinstall missing local paths
cleanup_stale_local_plugin_paths() {
    local uv_packages_file="$HOME/.config/io.datasette.llm/uv-tool-packages.json"
    local uv_receipt_file="$HOME/.local/share/uv/tools/llm/uv-receipt.toml"

    # Clean up uv-tool-packages.json (llm-uv-tool tracking)
    if [ -f "$uv_packages_file" ]; then
        local stale_paths=()
        while IFS= read -r entry; do
            # Check if it's a local path (starts with /)
            if [[ "$entry" =~ ^/ ]]; then
                if [ ! -d "$entry" ]; then
                    stale_paths+=("$entry")
                    log "Removing stale local path from uv-tool-packages.json: $entry"
                fi
            fi
        done < <(jq -r '.[]' "$uv_packages_file" 2>/dev/null)

        # Remove stale entries from JSON (both path and derived plugin name)
        for path in "${stale_paths[@]}"; do
            # Extract plugin name from path (basename)
            local plugin_name
            plugin_name=$(basename "$path")

            # Remove both the path and the name from JSON
            jq --arg path "$path" --arg name "$plugin_name" \
                'map(select(. != $path and . != $name))' \
                "$uv_packages_file" > "${uv_packages_file}.tmp" && \
                mv "${uv_packages_file}.tmp" "$uv_packages_file"

            log "Also removing plugin name from tracking: $plugin_name"
        done
    fi

    # Clean up uv-receipt.toml (uv's internal tracking)
    # Two-pass approach: first identify stale plugins, then remove ALL their entries
    if [ -f "$uv_receipt_file" ]; then
        local stale_plugins=()

        # Pass 1: Find plugins with stale local paths
        while IFS= read -r line; do
            # Check if line contains a directory reference (flexible spacing)
            if [[ "$line" =~ directory[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
                local dir_path="${BASH_REMATCH[1]}"
                if [ ! -d "$dir_path" ]; then
                    # Extract plugin name from same line
                    if [[ "$line" =~ name[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
                        local plugin_name="${BASH_REMATCH[1]}"
                        log "Found stale local path for $plugin_name: $dir_path"
                        stale_plugins+=("$plugin_name")
                    fi
                fi
            fi
        done < "$uv_receipt_file"

        # Pass 2: Remove ALL entries for stale plugins (not just the directory entry)
        if [ ${#stale_plugins[@]} -gt 0 ]; then
            local temp_file="${uv_receipt_file}.tmp"

            while IFS= read -r line; do
                local skip_line=false
                for plugin in "${stale_plugins[@]}"; do
                    # Match any line containing this plugin name
                    if [[ "$line" =~ name[[:space:]]*=[[:space:]]*\"${plugin}\" ]]; then
                        log "Removing entry for stale plugin from uv-receipt.toml: $plugin"
                        skip_line=true
                        break
                    fi
                done
                if [ "$skip_line" = false ]; then
                    echo "$line"
                fi
            done < "$uv_receipt_file" > "$temp_file"

            mv "$temp_file" "$uv_receipt_file"
            log "Cleaned ${#stale_plugins[@]} stale plugin(s) from uv-receipt.toml"
        fi
    fi
}

# Remove a specific plugin from both tracking files by name
# Must clean both files to prevent llm-uv-tool from re-adding entries
# Usage: remove_plugin_from_tracking "plugin-name"
remove_plugin_from_tracking() {
    local plugin_name="$1"
    local uv_packages_file="$HOME/.config/io.datasette.llm/uv-tool-packages.json"
    local uv_receipt_file="$HOME/.local/share/uv/tools/llm/uv-receipt.toml"

    # Remove from uv-tool-packages.json (llm-uv-tool tracking)
    if [ -f "$uv_packages_file" ]; then
        if jq -e --arg name "$plugin_name" 'any(. == $name or endswith("/" + $name))' "$uv_packages_file" >/dev/null 2>&1; then
            jq --arg name "$plugin_name" 'map(select(. != $name and (. | tostring | endswith("/" + $name) | not)))' \
                "$uv_packages_file" > "${uv_packages_file}.tmp" && \
                mv "${uv_packages_file}.tmp" "$uv_packages_file"
            log "Removed $plugin_name from uv-tool-packages.json"
        fi
    fi

    # Remove from uv-receipt.toml (uv internal tracking)
    if [ -f "$uv_receipt_file" ]; then
        if grep -q "name[[:space:]]*=[[:space:]]*\"${plugin_name}\"" "$uv_receipt_file"; then
            grep -v "name[[:space:]]*=[[:space:]]*\"${plugin_name}\"" "$uv_receipt_file" > "${uv_receipt_file}.tmp"
            mv "${uv_receipt_file}.tmp" "$uv_receipt_file"
            log "Removed $plugin_name from uv-receipt.toml"
        fi
    fi
}

# Install Go if not present or version is insufficient
# Returns 0 if Go is available (>= MIN_GO_VERSION), 1 otherwise
# Only installs from apt - warns and skips if repo version is insufficient
install_go() {
    local MIN_GO_VERSION="1.22"

    # Check if already installed with sufficient version
    if command -v go &> /dev/null; then
        local current_version=$(go version | grep -oP 'go\K[0-9]+\.[0-9]+')
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
    #LOCAL=$(git rev-parse HEAD)
    #REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "$LOCAL")

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
# Detect Terminator Installation
#############################################################################

# Check if Terminator is installed (used for conditional assistant installation)
TERMINATOR_INSTALLED=false
if command -v terminator &> /dev/null; then
    TERMINATOR_INSTALLED=true
    log "Terminator detected - will install assistant integration"
else
    log "Terminator not found - skipping assistant components"
fi

# Detect VM environment (for PipeWire audio fix)
IS_VM=false
if command -v systemd-detect-virt &>/dev/null; then
    VIRT_TYPE=$(systemd-detect-virt 2>/dev/null)
    if [ "$VIRT_TYPE" != "none" ] && [ -n "$VIRT_TYPE" ]; then
        IS_VM=true
        log "VM environment detected ($VIRT_TYPE)"
    fi
fi

# Detect PipeWire
PIPEWIRE_INSTALLED=false
if command -v pipewire &>/dev/null; then
    PIPEWIRE_INSTALLED=true
fi

# Apply PipeWire VM audio fix for Terminator + VM + PipeWire
if [ "$TERMINATOR_INSTALLED" = "true" ] && [ "$IS_VM" = "true" ] && \
   [ "$PIPEWIRE_INSTALLED" = "true" ]; then
    apply_pipewire_vm_fix
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

# Install document processors
log "Installing document processors..."
if ! command -v pdftotext &> /dev/null; then
    log "Installing poppler-utils (provides pdftotext for PDF processing)..."
    sudo apt-get install -y poppler-utils
else
    log "pdftotext is already installed"
fi

install_apt_package pandoc
install_apt_package ffmpeg

# Install PyGObject and build dependencies for Terminator assistant integration (conditional)
if [ "$TERMINATOR_INSTALLED" = "true" ]; then
    # Runtime packages (for system Python)
    log "Installing PyGObject runtime packages..."
    sudo apt-get install -y python3-gi python3-gi-cairo python3-dbus python3-dev gir1.2-vte-2.91

    # Build dependencies (for pip installations in isolated environments)
    log "Installing PyGObject build dependencies..."
    sudo apt-get install -y build-essential libdbus-glib-1-dev libcairo2-dev libgirepository-2.0-dev # gobject-introspection

    # Screen capture dependencies (for llm-tools-capture-screen)
    log "Installing screen capture tools..."
    install_apt_package maim
    install_apt_package xdotool
    install_apt_package flameshot
    # freerdp3 on newer distros (Kali 2024+), freerdp2 on older
    if apt-cache show freerdp3-x11 &>/dev/null; then
        install_apt_package freerdp3-x11
    else
        install_apt_package freerdp2-x11
    fi
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

# Check what Rust version is available in repositories
log "Checking Rust version in repositories..."
REPO_RUST_VERSION=$(apt-cache policy rustc 2>/dev/null | grep -oP 'Candidate:\s*\K[0-9]+\.[0-9]+' | head -1)

if [ -z "$REPO_RUST_VERSION" ]; then
    REPO_RUST_VERSION="0.0"
    warn "Could not determine repository Rust version"
fi

# Convert version to comparable number (e.g., "1.85" -> 185)
REPO_RUST_VERSION_NUM=$(echo "$REPO_RUST_VERSION" | awk -F. '{print ($1 * 100) + $2}')
MINIMUM_RUST_VERSION=185  # Rust 1.85 (edition2024 requirement for some cargo tools)

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
                warn "Continuing with old Rust version. Some cargo tool builds may fail."
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
# PHASE 2: Install/Update LLM Core
#############################################################################

# Initialize configuration state variables (used throughout Phase 2-7)
AZURE_CONFIGURED=false
GEMINI_CONFIGURED=false

# Install/upgrade llm from fork with llm-uv-tool for persistent plugin management
# Using c0ffee0wl/llm fork which includes markdown markup enhancements
# Installed via uv tool from git repository with llm-uv-tool bundled
# llm-uv-tool intercepts `llm install` commands to make plugins persist across LLM upgrades

# Clear uv cache to remove stale build artifacts (e.g., renamed local plugins)
uv cache clean --quiet 2>/dev/null || true

# Clean up stale local plugin paths before upgrade (handles migration from local to git)
cleanup_stale_local_plugin_paths

# Remove old llm plugin from both tracking files
# Must clean BEFORE any llm operations - invalid local paths cause failures
remove_plugin_from_tracking "llm-tools-sidechat"

# Check if llm is already installed
if uv tool list 2>/dev/null | grep -q "^llm "; then
    log "Upgrading llm (with llm-uv-tool)..."
    uv tool upgrade llm
else
    log "Installing llm with llm-uv-tool for persistent plugin management..."
    uv tool install --with "git+https://github.com/c0ffee0wl/llm-uv-tool" "git+https://github.com/c0ffee0wl/llm"
fi

# Install pymupdf_layout (for improved PDF processing with llm-fragments-pdf)
log "Installing/updating pymupdf_layout for improved PDF processing..."
install_or_upgrade_llm_plugin pymupdf_layout

# Ensure llm is in PATH
export PATH=$HOME/.local/bin:$PATH

# Define the extra models file path early so we can check/preserve existing config
LLM_CONFIG_DIR="$(command llm logs path 2>/dev/null | tail -n1 | xargs dirname)"
if [ -z "$LLM_CONFIG_DIR" ] || [ ! -d "$LLM_CONFIG_DIR" ]; then
    error "Failed to get llm configuration directory. Is llm installed correctly?"
fi
EXTRA_MODELS_FILE="$LLM_CONFIG_DIR/extra-openai-models.yaml"

# Detect if this is the first run
# Check for: new flag, OR YAML config exists, OR shell integration already present
if [ -f "$EXTRA_MODELS_FILE" ] || \
   grep -q "llm-integration" "$HOME/.bashrc" 2>/dev/null || \
   grep -q "llm-integration" "$HOME/.zshrc" 2>/dev/null; then
    IS_FIRST_RUN=false
else
    IS_FIRST_RUN=true
fi

#############################################################################
# PHASE 2.5: Install/Update LLM Plugins
#############################################################################

log "Checking llm plugins..."

# Cache installed plugins list once (avoid calling llm plugins 20+ times)
INSTALLED_PLUGINS=$(command llm plugins 2>/dev/null | grep -o '"name": *"[^"]*"' | sed 's/"name": *"//;s/"$//' || echo "")

PLUGINS=(
    "git+https://github.com/c0ffee0wl/llm-gemini"
    "git+https://github.com/c0ffee0wl/llm-vertex"
    "llm-openrouter"
    "llm-anthropic"
    "git+https://github.com/c0ffee0wl/llm-cmd"
    "git+https://github.com/c0ffee0wl/llm-cmd-comp"
    "llm-tools-quickjs"
    "llm-tools-sqlite"
    "git+https://github.com/c0ffee0wl/llm-tools-sandboxed-shell"
    "git+https://github.com/c0ffee0wl/llm-tools-sandboxed-python"
    "git+https://github.com/c0ffee0wl/llm-tools-patch"
    "llm-fragments-site-text"
    "git+https://github.com/c0ffee0wl/llm-fragments-pdf"
    "llm-fragments-github"
    "git+https://github.com/c0ffee0wl/llm-fragments-youtube-transcript"
    "llm-fragments-dir"
    "llm-jq"
    "git+https://github.com/c0ffee0wl/llm-templates-fabric"
    "git+https://github.com/c0ffee0wl/llm-tools-llm-functions"
    "llm-git-commit"
    "llm-sort"
    "llm-classify"
    "llm-consortium"
    "$SCRIPT_DIR/llm-tools-context"
    "git+https://github.com/c0ffee0wl/llm-tools-fragment-bridge"
    "git+https://github.com/c0ffee0wl/llm-tools-google-search"
    "git+https://github.com/c0ffee0wl/llm-tools-web-fetch"
    "git+https://github.com/c0ffee0wl/llm-tools-fabric"
    "git+https://github.com/c0ffee0wl/llm-tools-mcp"
    "git+https://github.com/c0ffee0wl/llm-tools-rag"
)

for plugin in "${PLUGINS[@]}"; do
    install_or_upgrade_llm_plugin "$plugin"
done

# Create default MCP configuration if not exists
MCP_CONFIG_DIR="$HOME/.llm-tools-mcp"
MCP_CONFIG_FILE="$MCP_CONFIG_DIR/mcp.json"

if [ ! -f "$MCP_CONFIG_FILE" ]; then
    log "Creating default MCP configuration with Microsoft Learn..."
    mkdir -p "$MCP_CONFIG_DIR"
    cat > "$MCP_CONFIG_FILE" <<'EOF'
{
  "mcpServers": {
    "microsoft-learn": {
      "type": "http",
      "url": "https://learn.microsoft.com/api/mcp"
    }
  }
}
EOF
    log "MCP configuration created at $MCP_CONFIG_FILE"
else
    log "MCP configuration already exists, preserving"
fi

#############################################################################
# PHASE 3: Configuring LLM
#############################################################################

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

- model_id: azure/gpt-5.1
  model_name: gpt-5.1
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

    # Create azure embedding config (llm-azure plugin)
    # Must be created BEFORE llm-azure plugin is installed (plugin crashes without config)
    log "Creating Azure embedding model configuration..."
    AZURE_CONFIG_DIR="$LLM_CONFIG_DIR/azure"
    mkdir -p "$AZURE_CONFIG_DIR"

    # llm-azure uses AzureOpenAI client which expects base endpoint without /openai/v1/
    # Strip /openai/v1/ suffix if present (chat models in extra-openai-models.yaml use full path)
    AZURE_EMBEDDING_BASE=$(echo "$AZURE_API_BASE" | sed 's|/openai/v1/$||; s|/openai/v1$||')

    cat > "$AZURE_CONFIG_DIR/config.yaml" <<EOF
- model_id: azure/text-embedding-3-small
  model_name: text-embedding-3-small
  embedding_model: true
  api_base: ${AZURE_EMBEDDING_BASE}
  api_version: '2024-10-21'
EOF

    # Install llm-azure plugin now that config exists
    install_or_upgrade_llm_plugin "git+https://github.com/c0ffee0wl/llm-azure"
    # Set as default embedding model (azure/ prefix avoids conflict with OpenAI's model)
    command llm embed-models default azure/text-embedding-3-small
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
elif [ "$AZURE_CONFIGURED" = "true" ]; then
    # Azure is configured - check if Gemini is already configured
    if command llm keys get gemini &>/dev/null; then
        log "Google Gemini was previously configured, preserving existing configuration"
        GEMINI_CONFIGURED=true
    else
        # Azure configured but no Gemini - always ask for Gemini as secondary
        log "Google Gemini as Secondary Provider (Optional)"
        echo ""
        echo "Azure OpenAI is configured as your primary provider."
        echo "Gemini can be added as a secondary provider for:"
        echo "  - Claude Code Router web search (Azure doesn't support web search)"
        echo "  - llm chat-google-search (or -o google_search 1)"
        echo "  - imagemage (Gemini 'Nano Banana' Pro image generation CLI)"
        echo ""
        read -p "Would you like to configure Gemini as a secondary provider? (y/N): " CONFIG_GEMINI_SECONDARY
        CONFIG_GEMINI_SECONDARY=${CONFIG_GEMINI_SECONDARY:-N}

        if [[ "$CONFIG_GEMINI_SECONDARY" =~ ^[Yy]$ ]]; then
            configure_gemini
            # Note: AZURE_CONFIGURED stays true - Gemini is secondary, not replacement
        else
            log "Skipping Gemini configuration (secondary provider declined)"
            GEMINI_CONFIGURED=false
        fi
    fi
else
    # Azure not configured - check if Gemini key exists
    if command llm keys get gemini &>/dev/null; then
        log "Google Gemini was previously configured, preserving existing configuration"
        GEMINI_CONFIGURED=true
    else
        log "Google Gemini not configured"
        GEMINI_CONFIGURED=false
    fi
fi

#############################################################################
# Set Default Model
#############################################################################

# Set default model based on configured provider
if [ "$AZURE_CONFIGURED" = "true" ]; then
    set_or_migrate_default_model "azure/gpt-4.1-mini"
    # Azure embedding default is set above when llm-azure plugin is installed
elif [ "$GEMINI_CONFIGURED" = "true" ]; then
    set_or_migrate_default_model "gemini-2.5-flash"
    # Set Gemini embedding as default when Gemini is the primary provider
    command llm embed-models default gemini-embedding-001-1536
fi

#############################################################################
# PHASE 4: Install/Update LLM Templates
#############################################################################

log "Installing/updating llm templates..."

# Get templates directory path
TEMPLATES_DIR="$(command llm logs path 2>/dev/null | tail -n1 | xargs dirname)/templates"

# Create templates directory if it doesn't exist
mkdir -p "$TEMPLATES_DIR"

# Copy templates from repository (with smart update check)
update_template_file "assistant"
update_template_file "code"
update_template_file "wut"

# Conditionally install Terminator assistant template
if [ "$TERMINATOR_INSTALLED" = "true" ]; then
    update_template_file "terminator-assistant"
    # Remove old template if it exists
    rm -f "$TEMPLATES_DIR/terminator-sidechat.yaml"
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

# Function to prompt for session log silent mode preference (called once for both shells)
prompt_for_session_log_silent() {
    # Only prompt if not already set
    if [ -z "$SESSION_LOG_SILENT_VALUE" ]; then
        echo ""
        read -p "Suppress session log messages at shell startup? (y/N): " silent_choice
        echo ""

        if [ "$silent_choice" = "y" ] || [ "$silent_choice" = "Y" ]; then
            SESSION_LOG_SILENT_VALUE="1"
        else
            SESSION_LOG_SILENT_VALUE="0"
            echo "You can enable this later by setting SESSION_LOG_SILENT=1 in your .bashrc/.zshrc"
            echo ""
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

# Install shared Python module for prompt detection (PEP 420 namespace package)
log "Installing shared prompt detection module..."
PYTHON_USER_SITE=$(python3 -m site --user-site)
mkdir -p "$PYTHON_USER_SITE/llm_tools"
cp "$SCRIPT_DIR/context/prompt_detection.py" "$PYTHON_USER_SITE/llm_tools/"

# Install Terminator assistant components (conditional)
if [ "$TERMINATOR_INSTALLED" = "true" ]; then
    PLUGINS=(
        "$SCRIPT_DIR/llm-tools-assistant"
        "git+https://github.com/c0ffee0wl/llm-tools-capture-screen"
        "git+https://github.com/c0ffee0wl/llm-tools-imagemage"
    )

    for plugin in "${PLUGINS[@]}"; do
        install_or_upgrade_llm_plugin "$plugin"
    done

    log "Installing Terminator assistant integration..."

    # Remove old application and plugin
    rm -f "$HOME/.local/bin/llm-sidechat"
    rm -f "$HOME/.config/terminator/plugins/terminator_sidechat.py"

    # Install Terminator assistant plugin
    mkdir -p "$HOME/.config/terminator/plugins"
    cp "$SCRIPT_DIR/integration/terminator-assistant-plugin/terminator_assistant.py" \
       "$HOME/.config/terminator/plugins/terminator_assistant.py"
    log "Terminator assistant plugin installed"
    warn "Enable plugin: Terminator → Preferences → Plugins → ☑ TerminatorAssistant"

    # Install llm-assistant application and its dependencies
    cp "$SCRIPT_DIR/integration/llm-assistant" "$HOME/.local/bin/llm-assistant"
    chmod +x "$HOME/.local/bin/llm-assistant"
    cp "$SCRIPT_DIR/integration/system_info.py" "$HOME/.local/bin/system_info.py"
    cp "$SCRIPT_DIR/context/prompt_detection.py" "$HOME/.local/bin/prompt_detection.py"

    # Install dbus-python dependency into llm tool environment
    log "Installing dbus-python into llm tool environment..."
    install_or_upgrade_llm_plugin dbus-python

    # Install voice input and prompt_toolkit dependencies
    log "Installing voice input dependencies into llm tool environment..."
    install_or_upgrade_llm_plugin prompt_toolkit
    install_or_upgrade_llm_plugin sounddevice
    install_or_upgrade_llm_plugin numpy
    install_or_upgrade_llm_plugin pydub
    # Install onnx-asr with HuggingFace hub support for model downloads
    if install_or_upgrade_llm_plugin "onnx-asr[hub]" 2>/dev/null; then
        log "onnx-asr installed successfully"
        # Preload the Parakeet speech model to avoid first-use delay
        log "Preloading Parakeet speech model (this may take a minute)..."
        "$HOME/.local/share/uv/tools/llm/bin/python3" -c "
import onnx_asr
print('Downloading model...')
model = onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3')
print('Model loaded successfully')
" 2>&1 || warn "Model preload failed (will download on first use)"
    else
        warn "onnx-asr installation failed - voice input will be unavailable"
        warn "You can try manually: llm install 'onnx-asr[hub]'"
    fi

    # Install TTS (text-to-speech) dependencies for /speech command
    log "Installing TTS dependencies into llm tool environment..."
    install_or_upgrade_llm_plugin google-cloud-texttospeech
    install_or_upgrade_llm_plugin strip-markdown

    # Install imagemage - Gemini image generation CLI (only if Gemini configured)
    if command llm keys get gemini &>/dev/null; then
        if install_go; then
            log "Installing imagemage (Gemini image generation CLI)..."
            IMAGEMAGE_DIR="/tmp/imagemage-build"
            rm -rf "$IMAGEMAGE_DIR"
            git clone --depth 1 https://github.com/quinnypig/imagemage.git "$IMAGEMAGE_DIR"
            (cd "$IMAGEMAGE_DIR" && go build -o "$HOME/.local/bin/imagemage" .)
            rm -rf "$IMAGEMAGE_DIR"
            log "imagemage installed to ~/.local/bin/imagemage"
        fi
    else
        log "Skipping imagemage: Gemini not configured"
    fi
fi

#############################################################################
# PHASE 6: Additional Tools
#############################################################################

log "Installing/updating additional tools..."

# Install/update gitingest
install_or_upgrade_uv_tool gitingest

# Install/update tldr (community-driven man pages with practical examples)
install_or_upgrade_uv_tool tldr

# Install transcribe script (uses onnx-asr from llm environment)
log "Installing transcribe script..."
if [ -f "$SCRIPT_DIR/scripts/transcribe" ]; then
    # Copy script with modified shebang to use llm environment Python
    echo "#!$HOME/.local/share/uv/tools/llm/bin/python3" > "$HOME/.local/bin/transcribe"
    tail -n +2 "$SCRIPT_DIR/scripts/transcribe" >> "$HOME/.local/bin/transcribe"
    chmod +x "$HOME/.local/bin/transcribe"
    log "transcribe script installed to ~/.local/bin/transcribe"
else
    warn "transcribe script not found at $SCRIPT_DIR/scripts/transcribe"
fi

# Install/update files-to-prompt (from fork)
install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/files-to-prompt" true

# Install/update argc (prerequisite for llm-functions if users want to install it)
install_or_upgrade_cargo_tool argc

# Install/update yek (with commit-hash checking to avoid unnecessary rebuilds)
install_or_upgrade_cargo_git_tool yek https://github.com/bodo-run/yek

# Install clipboard tooling
install_apt_package xclip

# Install Micro text editor
install_apt_package micro

# Install llm-micro plugin
log "Installing llm-micro plugin..."
MICRO_PLUGIN_DIR="$HOME/.config/micro/plug"
mkdir -p "$MICRO_PLUGIN_DIR"

if [ ! -d "$MICRO_PLUGIN_DIR/llm" ]; then
    log "Cloning llm-micro plugin..."
    git clone https://github.com/shamanicvocalarts/llm-micro "$MICRO_PLUGIN_DIR/llm"
    log "llm-micro plugin installed to $MICRO_PLUGIN_DIR/llm"
else
    log "llm-micro plugin already installed, checking for updates..."
    (cd "$MICRO_PLUGIN_DIR/llm" && git pull)
fi

#############################################################################
# PHASE 7: Agentic CLI (coding) tools
#############################################################################

# Install/update Claude Code
log "Installing/updating Claude Code..."
install_or_upgrade_npm_global @anthropic-ai/claude-code

# Install/update Claude Code Router with flexible provider support
# Only install CCR if at least one provider key exists
if command llm keys get azure &>/dev/null || command llm keys get gemini &>/dev/null; then
    log "Installing/updating Claude Code Router..."
    install_or_upgrade_npm_global @musistudio/claude-code-router

    # Export environment variables for providers with keys
    if [ "$AZURE_CONFIGURED" = "true" ]; then
        export_azure_env_vars
    fi

    if command llm keys get gemini &>/dev/null; then
        export_gemini_env_vars
    fi

    # Generate CCR configuration (auto-adapts based on AZURE_CONFIGURED/GEMINI_CONFIGURED)
    update_ccr_config

    log "Claude Code Router installed"
else
    log "Skipping Claude Code Router installation (no providers configured)"
fi

# Install/update Codex CLI if Azure is configured
if [ "$AZURE_CONFIGURED" = "true" ]; then
    log "Installing/updating Codex CLI..."
    install_or_upgrade_npm_global @openai/codex

    # Configure Codex CLI with Azure OpenAI credentials
    if [ ! -f "$HOME/.codex/config.toml" ]; then
        configure_codex_cli
    fi

    log "Codex CLI installed and configured with Azure OpenAI"
else
    log "Skipping Codex installation (Azure OpenAI not configured)"
fi

# Update Gemini CLI if already installed (no automatic installation)
upgrade_npm_global_if_installed @google/gemini-cli

# Update OpenCode if already installed (no automatic installation)
upgrade_npm_global_if_installed opencode-ai

# Clean up uv cache to reclaim disk space
log "Cleaning uv cache..."
uv cache clean

#############################################################################
# PHASE 8: Browser Automation (Blueprint MCP) - only if Terminator is installed
#############################################################################

# Browser MCP commented out - not currently used
# if [ "$TERMINATOR_INSTALLED" = "true" ]; then
#     # Firefox is pre-installed on Kali, install if missing
#     if ! command -v firefox &>/dev/null; then
#         log "Installing Firefox..."
#         install_apt_package firefox-esr
#     fi
#
#     # Install/upgrade Blueprint MCP server
#     log "Installing/updating Blueprint MCP server..."
#     install_or_upgrade_npm_global @railsblueprint/blueprint-mcp
# else
#     log "Skipping Browser Automation (Terminator not installed)"
# fi

# TODO: Add Blueprint MCP to MCP config - commented out until MCP integration is working
# MCP_CONFIG_FILE="$HOME/.llm-tools-mcp/mcp.json"
# if [ -f "$MCP_CONFIG_FILE" ]; then
#     # Use jq to check if browser key exists (more robust than grep)
#     if ! jq -e '.mcpServers.browser' "$MCP_CONFIG_FILE" &>/dev/null; then
#         log "Adding Blueprint MCP to MCP configuration..."
#         jq '.mcpServers.browser = {
#             "command": "npx",
#             "args": ["@railsblueprint/blueprint-mcp@latest"]
#         }' "$MCP_CONFIG_FILE" > "$MCP_CONFIG_FILE.tmp" && mv "$MCP_CONFIG_FILE.tmp" "$MCP_CONFIG_FILE"
#         log ""
#         log "NOTE: Install the Blueprint Firefox extension for browser automation:"
#         log "  1. Open Firefox"
#         log "  2. Visit: https://addons.mozilla.org/firefox/addon/blueprint-mcp-for-firefox/"
#         log "  3. Click 'Add to Firefox'"
#     fi
# fi

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
log "  - llm plugins (gemini, anthropic, tools, sandboxed-shell, sandboxed-python, fragments, jq, fabric templates, context, llm-functions bridge)"
log "  - Claude Code (Anthropic's agentic coding CLI)"
log "  - Claude Code Router (proxy for Claude Code)"
log "  - Blueprint MCP (browser automation for Firefox/Chrome with stealth mode)"
log "  - micro (modern terminal text editor with llm-micro plugin for AI integration)"
log "  - gitingest (Git repository to LLM-friendly text)"
log "  - yek (fast repository to LLM-friendly text converter)"
log "  - files-to-prompt (file content formatter)"
log "  - tldr (community-driven man pages with practical examples)"
log "  - argc (Bash CLI framework, enables optional llm-functions)"
log "  - asciinema (terminal session recorder)"
log "  - transcribe (speech-to-text using Parakeet TDT, 25 European languages)"
log ""
log "Shell integration files created in: $SCRIPT_DIR/integration"
log "  - integration/llm-integration.bash (for Bash)"
log "  - integration/llm-integration.zsh (for Zsh)"
log ""
log "Next steps:"
log "  1. Restart your shell or run: source ~/.bashrc (or ~/.zshrc)"
log "  2. Test llm: llm 'Hello, how are you?'"
log "  3. Use Ctrl+N in your shell for AI command completion"
log "  4. Test Claude Code Router: routed-claude"
log "  5. For browser automation, install Firefox extension:"
log "     https://addons.mozilla.org/firefox/addon/blueprint-mcp-for-firefox/"
log ""
log "To update all tools in the future, simply re-run this script:"
log "  ./install-llm-tools.sh"
log ""
log "To (re)configure Azure OpenAI settings:"
log "  ./install-llm-tools.sh --azure"
log ""
log "To (re)configure Gemini settings:"
log "  ./install-llm-tools.sh --gemini"
