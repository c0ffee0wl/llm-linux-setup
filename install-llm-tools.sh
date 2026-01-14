#!/bin/bash
#
# LLM Tools Installation Script for Linux (Debian/Ubuntu/Kali)
# Installs Simon Willison's llm CLI tool and related AI/LLM command-line utilities
#
# Usage: ./install-llm-tools.sh [--azure] [--gemini] [--clear-cache]
#
# Options:
#   --azure        Force (re)configuration of Azure OpenAI, even if already configured
#   --gemini       Force (re)configuration of Google Gemini, even if already configured
#   --clear-cache  Clear package caches (npm, go, pip, pipx, cargo, uv) to reclaim disk space
#   --help         Show help message
#
# Re-run to update all tools

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared utility functions
source "$SCRIPT_DIR/shared/common.sh"

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
CLEAR_CACHE=false
MINIMAL_FLAG=false
FULL_FLAG=false
WSL_FLAG=""  # "", "force", or "disable"

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
        --clear-cache)
            CLEAR_CACHE=true
            shift
            ;;
        --minimal)
            MINIMAL_FLAG=true
            shift
            ;;
        --full)
            FULL_FLAG=true
            shift
            ;;
        --wsl)
            WSL_FLAG="force"
            shift
            ;;
        --no-wsl)
            WSL_FLAG="disable"
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "LLM Tools Installation Script for Linux (Debian/Ubuntu/Kali)"
            echo ""
            echo "Options:"
            echo "  --minimal      Install only LLM core tools (persists for future runs)"
            echo "  --full         Install all tools (overrides saved --minimal preference)"
            echo "  --wsl          Force WSL mode (skip session recording, prompt for CCR)"
            echo "  --no-wsl       Disable WSL auto-detection (run full install even in WSL)"
            echo "  --azure        Force (re)configuration of Azure OpenAI, even if already configured"
            echo "  --gemini       Force (re)configuration of Google Gemini, even if already configured"
            echo "  --clear-cache  Clear package caches (npm, go, pip, pipx, cargo, uv) to reclaim disk space"
            echo "  --help         Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0              # Normal installation/update"
            echo "  $0 --minimal    # Install only LLM core tools"
            echo "  $0 --full       # Install all tools (override saved minimal mode)"
            echo "  $0 --azure      # Reconfigure Azure OpenAI settings"
            echo "  $0 --gemini     # Reconfigure Google Gemini settings"
            echo "  $0 --clear-cache  # Clear all package caches"
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

if [ "$MINIMAL_FLAG" = "true" ] && [ "$FULL_FLAG" = "true" ]; then
    error "Cannot specify both --minimal and --full flags simultaneously."
    exit 1
fi

#############################################################################
# Installation Mode Resolution
#############################################################################

INSTALL_MODE_FILE="$LLM_TOOLS_CONFIG_DIR/install-mode"
INSTALL_MODE="full"  # Default to full installation

# Determine installation mode (flag takes precedence over saved mode)
if [ "$MINIMAL_FLAG" = "true" ]; then
    INSTALL_MODE="minimal"
    mkdir -p "$LLM_TOOLS_CONFIG_DIR"
    echo "minimal" > "$INSTALL_MODE_FILE"
    log "Installation mode: minimal (saved for future runs)"
elif [ "$FULL_FLAG" = "true" ]; then
    INSTALL_MODE="full"
    mkdir -p "$LLM_TOOLS_CONFIG_DIR"
    echo "full" > "$INSTALL_MODE_FILE"
    log "Installation mode: full (saved for future runs)"
elif [ -f "$INSTALL_MODE_FILE" ]; then
    INSTALL_MODE=$(cat "$INSTALL_MODE_FILE")
    log "Using saved installation mode: $INSTALL_MODE (use --full to override)"
fi

#############################################################################
# WSL Mode Detection
#############################################################################

# WSL mode detection with override
# Note: IS_WSL is independent of INSTALL_MODE - allows WSL+full or WSL+minimal
IS_WSL=false

if [ "$WSL_FLAG" = "force" ]; then
    IS_WSL=true
    log "WSL mode forced via --wsl flag"
elif [ "$WSL_FLAG" = "disable" ]; then
    IS_WSL=false
    log "WSL mode disabled via --no-wsl flag"
elif is_wsl; then
    IS_WSL=true
    log "WSL environment auto-detected (session recording will be skipped)"
fi

#############################################################################
# Helper Functions
#############################################################################

# Get llm config directory (cached for performance)
# Usage: config_dir=$(get_llm_config_dir)
# Note: Only caches successful results to avoid caching failures
# Falls back to XDG default if llm command fails (e.g., not in PATH after fresh install)
_LLM_CONFIG_DIR_CACHE=""
get_llm_config_dir() {
    if [ -z "$_LLM_CONFIG_DIR_CACHE" ]; then
        local result=""
        # Try llm command first (may need full path after fresh install)
        local llm_bin="${HOME}/.local/bin/llm"
        if [ -x "$llm_bin" ]; then
            result="$("$llm_bin" logs path 2>/dev/null | tail -n1 | xargs dirname 2>/dev/null || true)"
        fi
        # Fallback: try command in PATH
        if [ -z "$result" ]; then
            result="$(command llm logs path 2>/dev/null | tail -n1 | xargs dirname 2>/dev/null || true)"
        fi
        # Final fallback: XDG default location
        if [ -z "$result" ]; then
            result="${HOME}/.config/io.datasette.llm"
        fi
        _LLM_CONFIG_DIR_CACHE="$result"
    fi
    echo "$_LLM_CONFIG_DIR_CACHE"
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
    local EXTRA_MODELS_FILE="$(get_llm_config_dir)/extra-openai-models.yaml"
    local api_base=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" 2>/dev/null | sed 's/.*api_base:\s*//;s/\s*$//' || true)

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
    log "Exporting Azure environment variables..."

    # Extract api_base and resource name
    local EXTRA_MODELS_FILE="$(get_llm_config_dir)/extra-openai-models.yaml"
    local api_base=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" 2>/dev/null | sed 's/.*api_base:\s*//;s/\s*$//' || true)

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

    # Add/update exports (idempotent)
    update_profile_export "AZURE_OPENAI_API_KEY" "$api_key"
    update_profile_export "AZURE_RESOURCE_NAME" "$resource_name"

    log "Azure environment variables configured in ~/.profile"
}

# Export Gemini environment variables to ~/.profile
export_gemini_env_vars() {
    log "Exporting Gemini environment variables..."

    # Retrieve API key
    local api_key=$(command llm keys get gemini 2>/dev/null || echo "")

    if [ -z "$api_key" ]; then
        log "WARNING: Could not retrieve Gemini API key for environment variables"
        return 1
    fi

    # Add/update export (idempotent)
    update_profile_export "GEMINI_API_KEY" "$api_key"

    log "Gemini environment variables configured in ~/.profile"
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
        local EXTRA_MODELS_FILE="$(get_llm_config_dir)/extra-openai-models.yaml"
        if [ -f "$EXTRA_MODELS_FILE" ]; then
            azure_api_base=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" 2>/dev/null | sed 's/.*api_base:\s*//;s/\s*$//' || true)
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

    update_tracked_config "ccr-config" "$config_file" "$config_content" \
        "Claude Code Router config.json" "N" "true"
}

# Configure Claude Code Router for WSL environment
# Sets up: CCR installation, systemd service, profile environment
configure_wsl_ccr() {
    local CCR_PORT=3456

    # 1. Install/update Claude Code Router (uses existing helper)
    install_or_upgrade_npm_global @musistudio/claude-code-router

    # 2. Generate CCR config (reuse existing function)
    update_ccr_config

    # 3. Configure ~/.profile environment variables
    configure_wsl_profile "$CCR_PORT"

    # 4. Set up systemd user service
    configure_wsl_systemd_service "$CCR_PORT"

    log "Claude Code Router configured for WSL"
    log "Service will start automatically on WSL boot"
    log ""
    log "To manually control: systemctl --user {start|stop|status} claude-code-router"
}

# Add CCR-specific environment variables to ~/.profile
configure_wsl_profile() {
    local port="$1"

    # Add/update CCR routing exports (DISABLE_TELEMETRY/AUTOUPDATER set globally)
    update_profile_export "ANTHROPIC_BASE_URL" "http://127.0.0.1:${port}"
    update_profile_export "NO_PROXY" "127.0.0.1"

    log "Added CCR routing to ~/.profile"
    log "  ANTHROPIC_BASE_URL=http://127.0.0.1:${port}"
}

# Create and enable systemd user service for CCR (idempotent)
configure_wsl_systemd_service() {
    local port="$1"
    local service_dir="$HOME/.config/systemd/user"
    local service_file="$service_dir/claude-code-router.service"
    # CCR is installed via npm global, so fallback to NPM_PREFIX if 'which' fails
    local ccr_path=$(which ccr 2>/dev/null || echo "${NPM_PREFIX:-/usr/local}/bin/ccr")

    mkdir -p "$service_dir"

    # Generate expected service content
    local expected_content="[Unit]
Description=Claude Code Router - Multi-provider proxy for Claude Code
After=network.target

[Service]
Type=simple
ExecStart=${ccr_path} start
Restart=on-failure
RestartSec=5
Environment=PORT=${port}

[Install]
WantedBy=default.target"

    # Check if service file needs updating
    local needs_update=false
    if [ ! -f "$service_file" ]; then
        needs_update=true
    elif [ "$(cat "$service_file")" != "$expected_content" ]; then
        needs_update=true
    fi

    if [ "$needs_update" = true ]; then
        echo "$expected_content" > "$service_file"
        systemctl --user daemon-reload
        log "Systemd service file updated"
    fi

    # Enable service if not already enabled
    if ! systemctl --user is-enabled claude-code-router &>/dev/null; then
        systemctl --user enable claude-code-router
        log "Systemd service enabled"
    fi

    # Start or restart service
    if systemctl --user is-active claude-code-router &>/dev/null; then
        if [ "$needs_update" = true ]; then
            systemctl --user restart claude-code-router
            log "Claude Code Router service restarted"
        else
            log "Claude Code Router service already running"
        fi
    else
        if systemctl --user start claude-code-router; then
            log "Claude Code Router service started"
        else
            warn "Failed to start CCR service. Start manually: systemctl --user start claude-code-router"
        fi
    fi

    # Enable lingering so service starts without login (idempotent)
    if command -v loginctl > /dev/null 2>&1; then
        if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
            sudo loginctl enable-linger "$USER" 2>/dev/null || \
                warn "Could not enable lingering. CCR may not auto-start on WSL boot."
        fi
    fi
}

# Set or migrate default model (handles automatic migration from old defaults)
set_or_migrate_default_model() {
    local new_default="$1"
    local DEFAULT_MODEL_FILE="$(get_llm_config_dir)/default_model.txt"

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

# Check if a non-Azure/Gemini default model is already configured
# Returns 0 (true) if another provider is configured, 1 (false) otherwise
has_other_provider_configured() {
    local DEFAULT_MODEL_FILE="$(get_llm_config_dir)/default_model.txt"

    if [ ! -f "$DEFAULT_MODEL_FILE" ]; then
        return 1  # No default model configured
    fi

    local current_default=$(cat "$DEFAULT_MODEL_FILE" 2>/dev/null || echo "")

    if [ -z "$current_default" ]; then
        return 1  # Empty or no default
    fi

    # Check if it's Azure or Gemini (models we configure via this script)
    # Note: vertex/* models are configured separately via llm-vertex plugin
    case "$current_default" in
        azure/*)
            return 1  # Azure model - we handle this
            ;;
        gemini-*)
            return 1  # Gemini model - we handle this
            ;;
        *)
            # Another provider (Anthropic, OpenRouter, Vertex, local, etc.)
            return 0
            ;;
    esac
}

# Update template file with smart checksum-based update logic
update_template_file() {
    local template_name="$1"
    local source_file="$SCRIPT_DIR/llm-templates/${template_name}.yaml"
    local dest_file="$TEMPLATES_DIR/${template_name}.yaml"

    if [ ! -f "$source_file" ]; then
        warn "Template not found at $source_file"
        return
    fi

    # Note: Use cat to preserve exact file contents including trailing newlines
    update_tracked_config "$template_name" "$dest_file" "$(cat "$source_file")" \
        "${template_name}.yaml template" "N" "false"
}

# Extract normalized plugin name from various source formats
# Git URLs: git+https://github.com/user/llm-foo → llm-foo
# Local paths: /path/to/llm-foo → llm-foo
# PyPI names: llm-foo → llm-foo (passthrough)
# Usage: extract_plugin_name "source"
extract_plugin_name() {
    local source="$1"

    if [[ "$source" =~ ^git\+ ]]; then
        # Git URL: extract repo name from URL
        # git+https://github.com/user/llm-foo → llm-foo
        echo "$source" | sed 's|.*[/]||; s|\.git$||'
    elif [[ "$source" =~ ^/ ]]; then
        # Local path: extract directory name
        # /path/to/llm-foo → llm-foo
        basename "$source"
    else
        # PyPI name: passthrough
        echo "$source"
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
                # Stale if: doesn't exist OR missing pyproject.toml/setup.py (not a valid Python project)
                if [ ! -d "$entry" ] || { [ ! -f "$entry/pyproject.toml" ] && [ ! -f "$entry/setup.py" ]; }; then
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
    # Note: TOML has name and directory on separate lines, so we track state across lines
    if [ -f "$uv_receipt_file" ]; then
        local stale_plugins=()
        local current_name=""

        # Pass 1: Find plugins with stale local paths
        while IFS= read -r line; do
            # Track plugin name (comes before directory in TOML)
            if [[ "$line" =~ name[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
                current_name="${BASH_REMATCH[1]}"
            fi
            # Check if directory is a valid Python project (directory line comes after name line)
            if [[ "$line" =~ directory[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
                local dir_path="${BASH_REMATCH[1]}"
                # Stale if: doesn't exist OR missing pyproject.toml/setup.py (not a valid Python project)
                if [ -n "$current_name" ]; then
                    if [ ! -d "$dir_path" ] || { [ ! -f "$dir_path/pyproject.toml" ] && [ ! -f "$dir_path/setup.py" ]; }; then
                        log "Found stale local path for $current_name: $dir_path"
                        stale_plugins+=("$current_name")
                    fi
                fi
            fi
        done < "$uv_receipt_file"

        # Pass 2: Remove lines containing stale plugin names
        # Handles both inline tables { name = "..." } and multi-line entries
        if [ ${#stale_plugins[@]} -gt 0 ]; then
            local temp_file="${uv_receipt_file}.tmp"

            while IFS= read -r line || [ -n "$line" ]; do
                local skip_line=false
                for plugin in "${stale_plugins[@]}"; do
                    # Match: name = "plugin-name" (with optional surrounding content)
                    if [[ "$line" =~ name[[:space:]]*=[[:space:]]*\"${plugin}\" ]]; then
                        log "Removing line for stale plugin: $plugin"
                        skip_line=true
                        break
                    fi
                done
                if [ "$skip_line" = false ]; then
                    printf '%s\n' "$line"
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

# Run cache cleanup if requested as standalone operation
if [ "$CLEAR_CACHE" = "true" ]; then
    clear_package_caches
    exit 0
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
    if [ "$INSTALL_MODE" = "full" ]; then
        log "Terminator detected - will install assistant integration"
    fi
else
    if [ "$INSTALL_MODE" = "full" ]; then
        log "Terminator not found - skipping assistant components"
    fi
fi

# Detect VM environment (for PipeWire audio fix)
IS_VM=false
if command -v systemd-detect-virt &>/dev/null; then
    VIRT_TYPE=$(systemd-detect-virt 2>/dev/null || true)
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

# Apply PipeWire VM audio fix for VM + PipeWire
if [ "$IS_VM" = "true" ] && [ "$PIPEWIRE_INSTALLED" = "true" ]; then
    apply_pipewire_vm_fix
fi

#############################################################################
# Stop Running Assistant Processes (for clean update)
#############################################################################

# Gracefully stop llm-assistant daemon and llm-guiassistant to allow updates
stop_assistant_processes() {
    local stopped=false

    graceful_stop_process "llm-assistant.*--daemon" 10 "llm-assistant daemon" && stopped=true
    graceful_stop_process "llm-guiassistant" 10 "llm-guiassistant" && stopped=true
    graceful_stop_process "python.*llm_assistant.*--daemon" 10 "Python llm-assistant" && stopped=true
    graceful_stop_process "python.*llm_guiassistant" 10 "Python llm-guiassistant" && stopped=true

    if [ "$stopped" = "true" ]; then
        log "Assistant processes stopped. They will use updated code when restarted."
    fi
}

# Only stop processes in full mode (assistant components)
if [ "$INSTALL_MODE" = "full" ]; then
    stop_assistant_processes
fi

#############################################################################
# PHASE 1: Install Prerequisites
#############################################################################

log "Installing prerequisites..."

sudo apt-get update

# Install basic prerequisites (batch install for efficiency)
log "Installing basic prerequisites..."
install_apt_packages git jq xsel python3 pipx curl

# Install bubblewrap (provides bwrap for sandboxing, used by llm-functions and code execution tools)
install_apt_package bubblewrap bwrap

# Check for sha256sum (required for template checksum tracking in Phase 4)
install_apt_package coreutils sha256sum

# Install document processors
log "Installing document processors..."
install_apt_package poppler-utils pdftotext
install_apt_packages pandoc ffmpeg

# Install PyGObject and build dependencies for Terminator assistant integration (conditional)
if [ "$TERMINATOR_INSTALLED" = "true" ]; then
    # Runtime packages (for system Python)
    log "Installing PyGObject runtime packages..."
    install_apt_packages python3-gi python3-gi-cairo python3-dbus python3-dev gir1.2-vte-2.91

    # Build dependencies (for pip installations in isolated environments)
    log "Installing PyGObject build dependencies..."
    install_apt_packages build-essential libdbus-glib-1-dev libcairo2-dev libgirepository-2.0-dev

    # Python file format parsing libraries (for llm-assistant structured data handling)
    log "Installing Python file format parsing libraries..."
    install_apt_packages python3-lxml python3-yaml python3-openpyxl

    # Screen capture dependencies (for llm-tools-capture-screen)
    log "Installing screen capture tools..."
    install_apt_packages maim xdotool flameshot
    # freerdp3 on newer distros (Kali 2024+), freerdp2 on older
    if apt-cache show freerdp3-x11 &>/dev/null; then
        install_apt_packages freerdp3-x11
    else
        install_apt_packages freerdp2-x11
    fi
fi

# Install/update uv
install_or_upgrade_uv

# Session recording tools - only in full mode and NOT in WSL
# (Rust is only needed for asciinema, skip both in WSL)
if [ "$INSTALL_MODE" = "full" ] && [ "$IS_WSL" != true ]; then
    # Install/update Rust (with intelligent version detection and rustup fallback)
    install_or_upgrade_rust

    # Install/update asciinema (with commit-hash checking to avoid unnecessary rebuilds)
    install_or_upgrade_cargo_git_tool asciinema https://github.com/asciinema/asciinema
fi

# Node.js - needed in full mode for Claude Code, and in WSL mode for CCR
if [ "$INSTALL_MODE" = "full" ]; then
    # Install/update Node.js (with intelligent version detection and nvm fallback)
    install_or_upgrade_nodejs

    # Detect if npm needs sudo for global installs
    detect_npm_permissions
fi

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

# Remove llm-azure plugin (deprecated - using OpenAI-compatible endpoint for embeddings)
remove_plugin_from_tracking "llm-azure"

# Remove local packages that depend on llm-tools-core from tracking before llm upgrade
# llm-tools-core is a local package not on PyPI, so dependencies can't be resolved during upgrade
# These will be reinstalled after llm-tools-core is available
remove_plugin_from_tracking "llm-tools-core"
remove_plugin_from_tracking "llm-tools-context"
remove_plugin_from_tracking "llm-assistant"
remove_plugin_from_tracking "llm-inlineassistant"

# Install llm-tools-core to user site-packages BEFORE llm upgrade
# This is needed by: terminator plugin (system Python)
log "Installing llm-tools-core to user site-packages..."
if ! uv pip install --system --break-system-packages -e "$SCRIPT_DIR/llm-tools-core" --quiet 2>/dev/null; then
    pip install --user --break-system-packages -e "$SCRIPT_DIR/llm-tools-core" 2>/dev/null || \
    pip install --user -e "$SCRIPT_DIR/llm-tools-core"
fi

# Clean up legacy llm_tools directory (replaced by llm-tools-core)
PYTHON_USER_SITE=$(python3 -m site --user-site)
if [ -d "$PYTHON_USER_SITE/llm_tools" ]; then
    log "Cleaning up legacy llm_tools directory..."
    rm -rf "$PYTHON_USER_SITE/llm_tools"
fi

# Ensure llm is in PATH
export PATH=$HOME/.local/bin:$PATH

#############################################################################
# Define ALL plugins to be installed with LLM
#############################################################################

# Remote plugins (from PyPI or git repositories)
REMOTE_PLUGINS=(
    # Plugin management (must be first)
    "git+https://github.com/c0ffee0wl/llm-uv-tool"

    # Provider plugins
    "git+https://github.com/c0ffee0wl/llm-gemini"
    "git+https://github.com/c0ffee0wl/llm-vertex"
    "llm-openrouter"
    "llm-anthropic"

    # Command plugins
    "git+https://github.com/c0ffee0wl/llm-cmd"
    "git+https://github.com/c0ffee0wl/llm-cmd-comp"

    # Tool plugins
    "llm-tools-quickjs"
    "llm-tools-sqlite"
    "git+https://github.com/c0ffee0wl/llm-tools-sandboxed-shell"
    "git+https://github.com/c0ffee0wl/llm-tools-sandboxed-python"
    "git+https://github.com/c0ffee0wl/llm-tools-patch"
    "git+https://github.com/c0ffee0wl/llm-tools-llm-functions"
    "git+https://github.com/c0ffee0wl/llm-tools-fragment-bridge"
    "git+https://github.com/c0ffee0wl/llm-tools-google-search"
    "git+https://github.com/c0ffee0wl/llm-tools-web-fetch"
    "git+https://github.com/c0ffee0wl/llm-tools-fabric"
    "git+https://github.com/c0ffee0wl/llm-tools-mcp"
    "git+https://github.com/c0ffee0wl/llm-tools-rag"
    "git+https://github.com/c0ffee0wl/llm-tools-skills"

    # Fragment plugins
    "llm-fragments-site-text"
    "pymupdf_layout"
    "git+https://github.com/c0ffee0wl/llm-fragments-pdf"
    "llm-fragments-github"
    "git+https://github.com/c0ffee0wl/llm-fragments-youtube-transcript"
    "git+https://github.com/c0ffee0wl/llm-arxiv"
    "llm-fragments-dir"

    # Utility plugins
    "llm-jq"
    "git+https://github.com/c0ffee0wl/llm-templates-fabric"
    "llm-git-commit"
    "llm-sort"
    "llm-classify"
    "llm-consortium"
)

# Local plugins (in-repo packages that should always be reinstalled)
# These use --reinstall-package to force rebuild on every run
LOCAL_PLUGINS=(
    "$SCRIPT_DIR/llm-tools-core"
    "$SCRIPT_DIR/llm-tools-context"
)

# Full mode plugins
if [ "$INSTALL_MODE" = "full" ]; then
    # Core assistant packages (always in full mode)
    LOCAL_PLUGINS+=(
        "$SCRIPT_DIR/llm-assistant"
        "$SCRIPT_DIR/llm-inlineassistant"
    )

    # X11 desktop-only plugins
    if has_desktop_environment && is_x11; then
        REMOTE_PLUGINS+=("git+https://github.com/c0ffee0wl/llm-tools-capture-screen")
        LOCAL_PLUGINS+=("$SCRIPT_DIR/llm-guiassistant")
    fi

    # Terminator-specific plugins
    if [ "$TERMINATOR_INSTALLED" = "true" ]; then
        LOCAL_PLUGINS+=("$SCRIPT_DIR/llm-assistant/llm-tools-assistant")
        REMOTE_PLUGINS+=("git+https://github.com/c0ffee0wl/llm-tools-imagemage")
    fi
fi

# Combine into ALL_PLUGINS for --with flags
ALL_PLUGINS=("${REMOTE_PLUGINS[@]}" "${LOCAL_PLUGINS[@]}")

#############################################################################
# Install/Update LLM with ALL Plugins (consolidated for performance)
#############################################################################

log "Installing/upgrading LLM with all plugins..."

# Build --with flags from ALL plugins
WITH_FLAGS=""
for plugin in "${ALL_PLUGINS[@]}"; do
    WITH_FLAGS+=" --with \"$plugin\""
done

# Build --reinstall-package flags for local plugins
# This forces uv to always rebuild in-repo packages regardless of version number
REINSTALL_FLAGS=""
for local_path in "${LOCAL_PLUGINS[@]}"; do
    # Extract package name from path (last directory component, replace - with _)
    pkg_name=$(basename "$local_path")
    # Convert package name to Python package format (hyphens to underscores)
    pkg_name_normalized="${pkg_name//-/_}"
    REINSTALL_FLAGS+=" --reinstall-package $pkg_name_normalized"
done

# Install llm with all plugins
# --force: required because uv tool upgrade doesn't support --with flags
# --reinstall-package: forces rebuild of local plugins even if version unchanged
log "Installing llm with ${#ALL_PLUGINS[@]} plugins (${#LOCAL_PLUGINS[@]} local)..."
eval "uv tool install --force $REINSTALL_FLAGS $WITH_FLAGS \"git+https://github.com/c0ffee0wl/llm\""

# Update uv-tool-packages.json for llm-uv-tool compatibility
LLM_CONFIG_DIR_TEMP="$(get_llm_config_dir)"
PACKAGES_FILE="$LLM_CONFIG_DIR_TEMP/uv-tool-packages.json"
mkdir -p "$LLM_CONFIG_DIR_TEMP"

# Generate JSON array using jq (skip llm-uv-tool itself from tracking)
# jq handles special characters in paths correctly
printf '%s\n' "${ALL_PLUGINS[@]}" | grep -v "llm-uv-tool" | jq -R . | jq -s . > "$PACKAGES_FILE"

log "LLM and plugins ready"

# Define the extra models file path early so we can check/preserve existing config
LLM_CONFIG_DIR="$(get_llm_config_dir)"
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

# Update MCP configuration with checksum tracking (same pattern as templates)
MCP_CONFIG_DIR="$HOME/.llm-tools-mcp"
MCP_CONFIG_FILE="$MCP_CONFIG_DIR/mcp.json"

update_mcp_config() {
    mkdir -p "$MCP_CONFIG_DIR"

    # Detect Chrome/Chromium for chrome-devtools MCP
    local chrome_devtools_config=""
    if command -v google-chrome &>/dev/null || command -v chromium &>/dev/null || command -v chromium-browser &>/dev/null; then
        log "Chrome/Chromium detected, adding chrome-devtools MCP"
        # Pre-install chrome-devtools-mcp for faster first use (npx will use cached version)
        install_or_upgrade_npm_global chrome-devtools-mcp
        chrome_devtools_config=',
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9222"],
      "optional": true,
      "include_tools": [
        "get_network_request",
        "list_network_requests",
        "evaluate_script",
        "get_console_message",
        "list_console_messages",
        "take_screenshot",
        "take_snapshot",
        "close_page",
        "list_pages",
        "navigate_page",
        "new_page",
        "select_page",
        "wait_for"
      ]
    }'
    fi

    # Generate expected config content
    local expected_config='{
  "mcpServers": {
    "microsoft-learn": {
      "type": "http",
      "url": "https://learn.microsoft.com/api/mcp"
    },
    "aws-knowledge": {
      "type": "http",
      "url": "https://knowledge-mcp.global.api.aws",
      "exclude_tools": ["*list_regions", "*get_regional_availability"],
      "optional": true
    },
    "arxiv": {
      "command": "arxiv-mcp-server",
      "optional": true
    }'"$chrome_devtools_config"'
  }
}'

    update_tracked_config "mcp-config" "$MCP_CONFIG_FILE" "$expected_config" \
        "MCP configuration" "Y" "false"
}

# MCP servers only in full mode
if [ "$INSTALL_MODE" = "full" ]; then
    update_mcp_config

    # Install/update arxiv-mcp-server (optional MCP server for arXiv paper search)
    # This is installed for all users since MCP works with llm CLI, not just llm-assistant
    install_or_upgrade_uv_tool arxiv-mcp-server
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
elif [ -f "$EXTRA_MODELS_FILE" ]; then
    # Previously configured Azure (YAML exists) - preserve existing configuration
    log "Azure OpenAI was previously configured, preserving existing configuration"

    # Extract the api_base from the first model entry in the YAML
    EXISTING_API_BASE=$(grep -m 1 "^\s*api_base:" "$EXTRA_MODELS_FILE" 2>/dev/null | sed 's/.*api_base:\s*//;s/\s*$//' || true)
    if [ -n "$EXISTING_API_BASE" ]; then
        AZURE_API_BASE="$EXISTING_API_BASE"
        log "Using existing API base: $AZURE_API_BASE"
    else
        AZURE_API_BASE="https://REPLACE-ME.openai.azure.com/openai/v1/"
        warn "Could not read existing API base, using placeholder"
    fi
    AZURE_CONFIGURED=true
elif has_other_provider_configured; then
    # User has another provider configured (Anthropic, OpenRouter, Vertex, etc.) - don't prompt
    current_model=$(cat "$(get_llm_config_dir)/default_model.txt" 2>/dev/null || echo "unknown")
    log "Another provider already configured (default model: $current_model)"
    log "Skipping Azure OpenAI configuration (use --azure to configure)"
    AZURE_CONFIGURED=false
elif [ "$IS_FIRST_RUN" = "true" ]; then
    # First run with no provider - ask if user wants to configure Azure OpenAI
    log "Azure OpenAI Configuration"
    echo ""
    if ask_yes_no "Do you want to configure Azure OpenAI?" Y; then
        configure_azure_openai
    else
        log "Skipping Azure OpenAI configuration"
        AZURE_CONFIGURED=false
    fi
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
elif command llm keys get gemini &>/dev/null; then
    # Gemini key already exists - preserve configuration
    log "Google Gemini was previously configured, preserving existing configuration"
    GEMINI_CONFIGURED=true
elif [ "$AZURE_CONFIGURED" = "true" ]; then
    # Azure configured but no Gemini - ask for Gemini as secondary
    log "Google Gemini as Secondary Provider (Optional)"
    echo ""
    echo "Azure OpenAI is configured as your primary provider."
    echo "Gemini can be added as a secondary provider for:"
    echo "  - Claude Code Router web search (Azure doesn't support web search)"
    echo "  - llm chat-google-search (or -o google_search 1)"
    echo "  - imagemage (Gemini 'Nano Banana' Pro image generation CLI)"
    echo ""
    if ask_yes_no "Would you like to configure Gemini as a secondary provider?" N; then
        configure_gemini
        # Note: AZURE_CONFIGURED stays true - Gemini is secondary, not replacement
    else
        log "Skipping Gemini configuration (secondary provider declined)"
        GEMINI_CONFIGURED=false
    fi
elif has_other_provider_configured; then
    # User has another provider configured (Anthropic, OpenRouter, etc.) - don't prompt
    log "Skipping Gemini configuration (another provider already configured)"
    GEMINI_CONFIGURED=false
elif [ "$IS_FIRST_RUN" = "true" ]; then
    # First run with no provider - ask if user wants to configure Gemini
    log "Google Gemini Configuration"
    echo ""
    if ask_yes_no "Do you want to configure Google Gemini?" N; then
        configure_gemini
    else
        log "Skipping Google Gemini configuration"
        GEMINI_CONFIGURED=false
    fi
else
    # Subsequent run - no Gemini configured
    log "Google Gemini not configured"
    GEMINI_CONFIGURED=false
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
TEMPLATES_DIR="$(get_llm_config_dir)/templates"

# Create templates directory if it doesn't exist
mkdir -p "$TEMPLATES_DIR"

# Copy templates from repository (with smart update check)
update_template_file "llm"
update_template_file "llm-code"
update_template_file "llm-wut"
# Note: llm-assistant and llm-assistant-report templates are now bundled
# as Jinja2 templates inside the llm-assistant package

# Clean up old Terminator assistant templates if they exist
if [ "$TERMINATOR_INSTALLED" = "true" ]; then
    rm -f "$TEMPLATES_DIR/terminator-sidechat.yaml"
    rm -f "$TEMPLATES_DIR/terminator-assistant.yaml"
    rm -f "$TEMPLATES_DIR/llm-assistant.yaml"
    rm -f "$TEMPLATES_DIR/llm-assistant-report.yaml"
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
        if ask_yes_no "Suppress session log messages at shell startup?" N; then
            SESSION_LOG_SILENT_VALUE="1"
        else
            SESSION_LOG_SILENT_VALUE="0"
            echo "You can enable this later by setting SESSION_LOG_SILENT=1 in your .bashrc/.zshrc"
        fi
        echo ""
    fi
}

# Shell integration and assistant tools are only in full mode and NOT in WSL
# (WSL mode skips session recording/logging and desktop assistant tools)
if [ "$INSTALL_MODE" = "full" ] && [ "$IS_WSL" != true ]; then
    # Update shell RC files
    update_shell_rc_file "$HOME/.bashrc" "$SCRIPT_DIR/integration/llm-integration.bash" ".bashrc"
    update_shell_rc_file "$HOME/.zshrc" "$SCRIPT_DIR/integration/llm-integration.zsh" ".zshrc"

    # Create context wrapper script (CLI is now part of llm-tools-context package)
    log "Installing context wrapper..."
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/context" << 'EOF'
#!/bin/sh
exec "$HOME/.local/share/uv/tools/llm/bin/python3" -m llm_tools_context.cli "$@"
EOF
    chmod +x "$HOME/.local/bin/context"

    # Note: llm-tools-core is already installed in the ALL_PLUGINS array (before llm-tools-context)
    # and also to user site-packages in Phase 2 for terminator plugin

    # Note: llm-assistant package is already installed via ALL_PLUGINS in Phase 2
    # Create wrapper script that calls into llm's environment
    cat > "$HOME/.local/bin/llm-assistant" << 'EOF'
#!/bin/sh
exec "$HOME/.local/share/uv/tools/llm/bin/python3" -m llm_assistant "$@"
EOF
    chmod +x "$HOME/.local/bin/llm-assistant"

# Install Terminator-specific components (conditional)
# Note: Terminator plugins (llm-tools-assistant, llm-tools-imagemage) are installed via ALL_PLUGINS in Phase 2
if [ "$TERMINATOR_INSTALLED" = "true" ]; then
    log "Installing Terminator assistant integration..."

    # Remove old application and plugin
    rm -f "$HOME/.local/bin/llm-sidechat"
    rm -f "$HOME/.config/terminator/plugins/terminator_sidechat.py"

    # Install Terminator assistant plugin (symlink to repository)
    mkdir -p "$HOME/.config/terminator/plugins"
    if [ -L "$HOME/.config/terminator/plugins/terminator_assistant.py" ]; then
        log "Terminator assistant plugin already linked"
    else
        rm -f "$HOME/.config/terminator/plugins/terminator_assistant.py"
        ln -sfn "$SCRIPT_DIR/llm-assistant/terminator-assistant-plugin/terminator_assistant.py" \
           "$HOME/.config/terminator/plugins/terminator_assistant.py"
        log "Terminator assistant plugin installed (symlinked)"
    fi
    warn "Restart Terminator and enable plugin: Preferences → Plugins → ☑ TerminatorAssistant"
fi

# llm-inlineassistant (thin client for llm-assistant daemon)
# Works in any terminal (not just Terminator) and espanso text expander
# Note: Package is already installed via ALL_PLUGINS in Phase 2

# Create wrapper script for llm-inlineassistant
cat > "$HOME/.local/bin/llm-inlineassistant" << 'EOF'
#!/bin/sh
exec "$HOME/.local/share/uv/tools/llm/bin/python3" -m llm_inlineassistant "$@"
EOF
chmod +x "$HOME/.local/bin/llm-inlineassistant"

# Clean up old daemon wrapper (now uses llm-assistant --daemon instead)
if [ -f "$HOME/.local/bin/llm-inlineassistant-daemon" ]; then
    rm -f "$HOME/.local/bin/llm-inlineassistant-daemon"
    log "Removed obsolete llm-inlineassistant-daemon wrapper"
fi

# Create daemon socket directory for llm-assistant
mkdir -p "/tmp/llm-assistant-$(id -u)"

if has_desktop_environment; then
    # Audio-related installations (STT/TTS) - only if soundcard available
    if has_soundcard; then
        # Download INT8 Parakeet model to shared location (used by Handy and llm-assistant)
        MODEL_DIR="$HOME/.local/share/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8"
        HF_BASE="https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main"

        # Required model files
        MODEL_FILES=(
            "config.json"
            "vocab.txt"
            "nemo128.onnx"
            "decoder_joint-model.int8.onnx"
            "encoder-model.int8.onnx"
        )

        # Check if model already exists (encoder is largest file, use as marker)
        if [ -f "$MODEL_DIR/encoder-model.int8.onnx" ] && \
           [ "$(stat -c%s "$MODEL_DIR/encoder-model.int8.onnx" 2>/dev/null)" -gt 100000000 ]; then
            log "Parakeet model already downloaded"
        else
            log "Downloading Parakeet speech model (this may take a few minutes)..."
            mkdir -p "$MODEL_DIR"

            # Download and verify each file
            download_failed=false
            for file in "${MODEL_FILES[@]}"; do
                log "  Downloading $file..."
                if ! curl -fL --progress-bar "$HF_BASE/$file" -o "$MODEL_DIR/$file"; then
                    warn "  Failed to download $file"
                    download_failed=true
                elif [ ! -s "$MODEL_DIR/$file" ]; then
                    warn "  Downloaded $file is empty"
                    download_failed=true
                fi
            done

            if [ "$download_failed" = "false" ]; then
                log "Parakeet model downloaded to $MODEL_DIR"
            else
                warn "Model download incomplete - run install-llm-tools.sh again"
            fi
        fi

        # Install Handy (system-wide STT) via .deb package
        install_github_deb_package "handy" "0.6.10" \
            "https://github.com/cjpais/Handy/releases/download/v{VERSION}/Handy_{VERSION}_amd64.deb" \
            "handy" "x86_64"

        # Configure Handy settings (Handy overwrites settings on first start, so we must let it create them first)
        if command -v handy &>/dev/null; then
            HANDY_SETTINGS="$HOME/.local/share/com.pais.handy/settings_store.json"

            # Ensure settings file exists (Handy creates it on first run)
            if [ ! -f "$HANDY_SETTINGS" ]; then
                # Start Handy to create default settings
                log "Starting Handy to create default settings..."
                if ! pgrep -x handy >/dev/null 2>&1; then
                    nohup handy >/dev/null 2>&1 &
                    disown
                fi

                # Wait 5 seconds, then check for file (up to 10 more seconds)
                sleep 5
                for i in {1..10}; do
                    [ -f "$HANDY_SETTINGS" ] && break
                    sleep 1
                done

                # Kill Handy so we can modify settings
                pkill -x handy || true
                sleep 1
            fi

            # Modify settings if file exists
            if [ -f "$HANDY_SETTINGS" ]; then
                python3 -c "
import json
from pathlib import Path

settings_file = Path.home() / '.local/share/com.pais.handy/settings_store.json'
if settings_file.exists():
    data = json.loads(settings_file.read_text())
    settings = data.get('settings', {})

    changed = False
    if settings.get('update_checks_enabled') != False:
        settings['update_checks_enabled'] = False
        changed = True
        print('Disabled Handy update checks')
    if settings.get('push_to_talk') != False:
        settings['push_to_talk'] = False
        changed = True
        print('Disabled Handy push-to-talk mode')
    if settings.get('start_hidden') != True:
        settings['start_hidden'] = True
        changed = True
        print('Enabled Handy start hidden')
    if settings.get('autostart_enabled') != True:
        settings['autostart_enabled'] = True
        changed = True
        print('Enabled Handy autostart')
    if changed:
        data['settings'] = settings
        settings_file.write_text(json.dumps(data, indent=2))
    else:
        print('Handy already configured, skipping')
"
            else
                warn "Handy settings file not created - skipping configuration"
            fi

            # Start Handy with configured settings
            if ! pgrep -x handy >/dev/null 2>&1; then
                log "Starting Handy..."
                nohup handy >/dev/null 2>&1 &
                disown
            fi
        fi
    else
        log "No soundcard detected - skipping audio tools (Handy, Parakeet model)"
    fi

    # Install imagemage - Gemini image generation CLI (only if Gemini configured)
    if command llm keys get gemini &>/dev/null; then
        if command -v imagemage &>/dev/null; then
            log "imagemage is already installed"
        elif install_go; then
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

    # Install espanso (text expander) - X11 or Wayland variant
    ESPANSO_VERSION="2.3.0"
    if is_wayland; then
        ESPANSO_DEB="espanso-debian-wayland-amd64.deb"
    else
        ESPANSO_DEB="espanso-debian-x11-amd64.deb"
    fi

    install_github_deb_package "espanso" "$ESPANSO_VERSION" \
        "https://github.com/espanso/espanso/releases/download/v{VERSION}/$ESPANSO_DEB" \
        "espanso" "x86_64"

    # Install espanso-llm package (uses llm-inlineassistant daemon, no external dependencies)
    if command -v espanso &>/dev/null; then
        # Get packages directory (with fallback to default path)
        ESPANSO_PACKAGES_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/espanso/match/packages"

        # Create packages directory if it doesn't exist
        mkdir -p "$ESPANSO_PACKAGES_DIR"

        # Remove old espanso-llm-ask-llm if present (replaced by espanso-llm)
        OLD_LLM_ASK_AI_DIR="$ESPANSO_PACKAGES_DIR/espanso-llm-ask-llm"
        if [ -d "$OLD_LLM_ASK_AI_DIR" ]; then
            log "Removing old espanso-llm-ask-llm package (replaced by espanso-llm)..."
            rm -rf "$OLD_LLM_ASK_AI_DIR"
        fi

        # Symlink espanso-llm package from repository
        ESPANSO_LLM_DIR="$ESPANSO_PACKAGES_DIR/espanso-llm"
        if [ -L "$ESPANSO_LLM_DIR" ]; then
            log "espanso-llm package already linked"
        else
            rm -rf "$ESPANSO_LLM_DIR"  # Remove if exists as directory
            ln -sfn "$SCRIPT_DIR/espanso-llm" "$ESPANSO_LLM_DIR"
            log "Installed espanso-llm package (symlinked)"
        fi

        # Register and start espanso service if newly installed
        if ! espanso status &>/dev/null; then
            log "Registering espanso service..."
            espanso service register || true
            # Start in background to avoid blocking (espanso start opens a GUI window)
            nohup espanso start &>/dev/null &
            log "espanso started in background"
        fi

    fi

    # Install Ulauncher (application launcher)
    install_github_deb_package "ulauncher" "5.15.15" \
        "https://github.com/Ulauncher/Ulauncher/releases/download/{VERSION}/ulauncher_{VERSION}_all.deb" \
        "" ""  # No process kill needed, architecture-independent

    # Install ulauncher-llm extension (symlink to repository)
    if command -v ulauncher &>/dev/null; then
        ULAUNCHER_EXT_DIR="${HOME}/.local/share/ulauncher/extensions"
        mkdir -p "$ULAUNCHER_EXT_DIR"

        # Create symlink to repository extension
        if [ -L "$ULAUNCHER_EXT_DIR/ulauncher-llm" ]; then
            log "ulauncher-llm extension already linked"
        else
            rm -rf "$ULAUNCHER_EXT_DIR/ulauncher-llm"  # Remove if exists as directory
            ln -sfn "$SCRIPT_DIR/ulauncher-llm" "$ULAUNCHER_EXT_DIR/ulauncher-llm"
            log "Installed ulauncher-llm extension (symlinked)"
        fi

        # Configure Ulauncher hotkey to Super+Space (first-time only)
        python3 -c "
import json
from pathlib import Path

settings_file = Path.home() / '.config/ulauncher/settings.json'
settings_file.parent.mkdir(parents=True, exist_ok=True)

if settings_file.exists():
    settings = json.loads(settings_file.read_text())
else:
    settings = {}

changed = False
if 'hotkey-show-app' not in settings:
    settings['hotkey-show-app'] = '<Super>space'
    changed = True
    print('Configured Ulauncher hotkey: Super+Space')
if 'show-indicator-icon' not in settings:
    settings['show-indicator-icon'] = False
    changed = True
    print('Disabled Ulauncher indicator icon')
if changed:
    settings_file.write_text(json.dumps(settings, indent=4))
else:
    print('Ulauncher already configured, skipping')
"

        # Enable and start Ulauncher service (user service)
        if command -v systemctl &>/dev/null && [ -d /run/systemd/system ]; then
            if ! systemctl --user is-active ulauncher.service &>/dev/null; then
                log "Enabling Ulauncher service..."
                systemctl --user enable --now ulauncher.service || warn "Failed to enable Ulauncher service"
            fi
        fi
    fi

    # llm-guiassistant (GTK popup for llm-assistant daemon)
    # Only on X11 for now (uses xdotool, xclip, maim, xprop)
    # Note: Package and llm-tools-capture-screen are installed via ALL_PLUGINS in Phase 2
    if is_x11; then
        log "Setting up llm-guiassistant..."

        # Install X11 dependencies (xprop from x11-utils for window detection)
        install_apt_package x11-utils xprop

        # Create wrapper script
        cat > "$HOME/.local/bin/llm-guiassistant" << 'EOF'
#!/bin/bash
exec "$HOME/.local/share/uv/tools/llm/bin/python3" -m llm_guiassistant "$@"
EOF
        chmod +x "$HOME/.local/bin/llm-guiassistant"

        # JavaScript assets (marked.js, highlight.js, purify.min.js) are bundled in git
        # at llm-assistant/llm_assistant/static/ - no runtime download needed

        # Configure XFCE keyboard shortcuts for llm-guiassistant
        if command -v xfconf-query &>/dev/null; then
            log "Configuring XFCE keyboard shortcuts for llm-guiassistant..."

            # Detect keyboard layout to choose appropriate shortcut key
            # German/European keyboards: dead_circumflex (^ key, top-left)
            # US keyboards fallback: grave (` backtick key, top-left)
            LAYOUT=$(setxkbmap -query 2>/dev/null | grep layout | awk '{print $2}')
            if [[ "$LAYOUT" == "de" || "$LAYOUT" == "at" || "$LAYOUT" == "ch" ]]; then
                SHORTCUT_KEY="dead_circumflex"
                KEY_DISPLAY="Super+^"
            else
                SHORTCUT_KEY="grave"
                KEY_DISPLAY="Super+\`"
            fi

            # Super+^ (or Super+`): Open llm-guiassistant
            # Use full path because XFCE shortcuts don't inherit user's PATH
            GUIASSISTANT_CMD="$HOME/.local/bin/llm-guiassistant"
            if xfconf-query -c xfce4-keyboard-shortcuts \
                -p "/commands/custom/<Super>$SHORTCUT_KEY" \
                -n -t string -s "$GUIASSISTANT_CMD" 2>/dev/null || \
               xfconf-query -c xfce4-keyboard-shortcuts \
                -p "/commands/custom/<Super>$SHORTCUT_KEY" \
                -s "$GUIASSISTANT_CMD" 2>/dev/null; then
                log "  $KEY_DISPLAY: Open llm-guiassistant"
            fi

            # Super+Shift+^ (or Super+Shift+`): Open with selection
            if xfconf-query -c xfce4-keyboard-shortcuts \
                -p "/commands/custom/<Super><Shift>$SHORTCUT_KEY" \
                -n -t string -s "$GUIASSISTANT_CMD --with-selection" 2>/dev/null || \
               xfconf-query -c xfce4-keyboard-shortcuts \
                -p "/commands/custom/<Super><Shift>$SHORTCUT_KEY" \
                -s "$GUIASSISTANT_CMD --with-selection" 2>/dev/null; then
                log "  ${KEY_DISPLAY/Super/Super+Shift}: Open with selection"
            fi
        else
            log "llm-guiassistant installed (configure keyboard shortcut manually)"
        fi

        # Create XDG autostart entry for hidden start on login
        # This pre-loads the GUI and daemon for instant activation via hotkey
        AUTOSTART_DIR="$HOME/.config/autostart"
        mkdir -p "$AUTOSTART_DIR"
        cat > "$AUTOSTART_DIR/llm-guiassistant.desktop" << EOF
[Desktop Entry]
Type=Application
Name=LLM GUI Assistant
Comment=Pre-load LLM GUI Assistant and daemon on login
Exec=$HOME/.local/bin/llm-guiassistant --hidden
Icon=utilities-terminal
Terminal=false
Categories=Utility;
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF
        log "Installed llm-guiassistant autostart entry"
    else
        log "Skipping llm-guiassistant: X11 required (Wayland support planned)"
    fi
fi

fi  # End of INSTALL_MODE = "full" block for Phase 5

#############################################################################
# PHASE 6: Additional Tools
#############################################################################

log "Installing/updating additional tools..."

# Install/update gitingest
install_or_upgrade_uv_tool gitingest

# Install/update llm-observability (log viewer for llm conversations)
install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/llm-observability"

# Install/update llm-server (OpenAI-compatible HTTP wrapper for llm library)
# Requires systemd for socket activation - skip on systems without systemd
if command -v systemctl &>/dev/null && [ -d /run/systemd/system ]; then
    # Ensure libsystemd-dev is installed (required to build pystemd dependency)
    install_apt_package libsystemd-dev

    # Stop llm-server service before updating (if running)
    if systemctl --user is-active llm-server.service &>/dev/null; then
        log "Stopping llm-server service for update..."
        systemctl --user stop llm-server.service 2>/dev/null || true
    fi

    install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/llm-server"

    # Register llm-server as systemd socket-activated user service
    # Socket activation enables on-demand startup when port 11435 is accessed
    if command -v llm-server &>/dev/null; then
        if ! systemctl --user is-active llm-server.socket &>/dev/null; then
            log "Registering llm-server systemd service..."
            llm-server --service || warn "Failed to register llm-server service, continuing..."
        else
            log "llm-server service is already running"
        fi
    fi
else
    log "Skipping llm-server (requires systemd)"
fi

# Install/update toko (LLM token counter with cost estimation)
# Requires Python 3.14 - installs with isolated Python environment
install_or_upgrade_uv_tool toko 3.14

# Additional tools only in full mode
if [ "$INSTALL_MODE" = "full" ]; then
    # Configure VS Code for local LLM mode (if any VS Code variant is installed)
    # configure-vscode disables telemetry and cloud-dependent features
    if command -v configure-vscode &>/dev/null; then
        if command -v code &>/dev/null || \
           command -v code-insiders &>/dev/null || \
           command -v codium &>/dev/null || \
           command -v code-oss &>/dev/null; then
            log "Configuring VS Code for local LLM mode..."
            configure-vscode --all || warn "Failed to configure VS Code settings, continuing..."
        fi
    fi

    # Install/update tldr (community-driven man pages with practical examples)
    install_or_upgrade_uv_tool tldr

    # Install transcribe script (uses onnx-asr from llm environment) - only if soundcard available
    if has_soundcard; then
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
    fi

    # Install/update files-to-prompt (from fork)
    install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/files-to-prompt"

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
fi  # End of INSTALL_MODE = "full" block for Phase 6

#############################################################################
# PHASE 7: Agentic CLI (coding) tools
#############################################################################

# Full mode: Install agentic CLI tools
if [ "$INSTALL_MODE" = "full" ]; then

# Install/update Claude Code using native installation (both WSL and non-WSL)
NATIVE_CLAUDE="$HOME/.local/bin/claude"
NPM_CLAUDE="$NPM_PREFIX/bin/claude"

if [ -x "$NATIVE_CLAUDE" ]; then
    # Native version exists - just update
    log "Updating Claude Code (native)..."
    "$NATIVE_CLAUDE" update || warn "Claude Code update check failed (network issue?), continuing..."

    # Clean up npm version if it still exists (migration from older script)
    if npm list -g @anthropic-ai/claude-code --depth=0 &>/dev/null; then
        log "Removing legacy npm Claude Code package..."
        npm_uninstall_global @anthropic-ai/claude-code claude || warn "Failed to remove legacy npm package, continuing..."
    fi
else
    # First run: bootstrap via npm, then install native, then remove npm version
    log "Installing Claude Code (native bootstrap)..."

    # Check if npm version exists (for migration from previous installs)
    if ! npm list -g @anthropic-ai/claude-code --depth=0 &>/dev/null; then
        # Install npm version temporarily to get the `claude install` command
        log "Installing npm bootstrap package..."
        npm_install install -g @anthropic-ai/claude-code
    fi

    # Verify npm binary exists before running
    if [ ! -x "$NPM_CLAUDE" ]; then
        warn "npm claude binary not found at $NPM_CLAUDE, cannot install native version"
    # Run native installation (use full path, handle failure gracefully)
    elif "$NPM_CLAUDE" install; then
        # Verify native installation succeeded
        if [ -x "$NATIVE_CLAUDE" ]; then
            # Remove npm version
            log "Removing npm bootstrap package..."
            npm_uninstall_global @anthropic-ai/claude-code claude || warn "Failed to remove npm bootstrap package, continuing..."
            log "Claude Code native installation complete"
        else
            warn "Native Claude binary not found after install, keeping npm version"
        fi
    else
        warn "Native Claude installation failed, keeping npm version"
    fi
fi

# WSL mode: Prompt for CCR with systemd service, skip claudo/Codex
if [ "$IS_WSL" = true ]; then
    log ""
    log "========================================"
    log "WSL INTEGRATION"
    log "========================================"

    # Check if CCR is already installed
    ccr_installed=false
    if npm list -g @musistudio/claude-code-router --depth=0 &>/dev/null; then
        ccr_installed=true
    fi

    echo ""
    log "WSL environment detected."
    log "Claude Code Router enables Obsidian and other external clients"
    log "to use your configured LLM providers (Azure/Gemini)."
    echo ""

    if [ "$ccr_installed" = true ]; then
        # Already installed - offer to update/reconfigure
        if ask_yes_no "Claude Code Router is installed. Update and reconfigure for WSL?" "Y"; then
            configure_wsl_ccr
        else
            log "Keeping existing Claude Code Router configuration"
        fi
    else
        # Fresh install
        if ask_yes_no "Install Claude Code Router for external client integration?" "N"; then
            configure_wsl_ccr
        else
            log "Skipping Claude Code Router installation"
        fi
    fi

# Non-WSL mode: Install claudo, CCR (auto), Codex CLI
else

# Install/update claudo (Claude in Podman) if Podman is installed
if command -v podman &> /dev/null; then
    log "Installing/updating claudo (Claude Code in Podman)..."
    mkdir -p "$HOME/.local/bin"
    if curl -fsSL https://raw.githubusercontent.com/c0ffee0wl/claudo/main/claudo -o "$HOME/.local/bin/claudo"; then
        chmod +x "$HOME/.local/bin/claudo"
        log "claudo installed to ~/.local/bin/claudo"
    else
        warn "Failed to download claudo"
    fi
else
    log "Skipping claudo installation (Podman not installed)"
fi

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

fi  # End of WSL/non-WSL block

fi  # End of INSTALL_MODE = "full" block for Phase 7

#############################################################################
# Global environment defaults (regardless of install mode)
#############################################################################

# Set Claude Code and VS Code defaults for privacy/noise reduction
# These apply even in minimal mode in case tools are installed manually later
log "Setting environment defaults..."
# Claude Code
update_profile_export "DISABLE_TELEMETRY" "1"
update_profile_export "DISABLE_AUTOUPDATER" "1"
update_profile_export "DISABLE_ERROR_REPORTING" "1"
update_profile_export "DISABLE_BUG_COMMAND" "1"
update_profile_export "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY" "1"
# VS Code / .NET
update_profile_export "VSCODE_TELEMETRY_DISABLE" "1"
update_profile_export "VSCODE_CRASH_REPORTER_DISABLE" "1"
update_profile_export "DOTNET_CLI_TELEMETRY_OPTOUT" "1"
ensure_zprofile_sources_profile

# Clean up package caches to reclaim disk space (runs regardless of install mode)
clear_package_caches

#############################################################################
# Update existing CLI tools (regardless of install mode)
# This ensures tools installed previously get updates even in minimal mode
#############################################################################

# Update Claude Code if already installed (native version)
NATIVE_CLAUDE="$HOME/.local/bin/claude"
if [ "$INSTALL_MODE" != "full" ] && [ -x "$NATIVE_CLAUDE" ]; then
    log "Updating Claude Code (native)..."
    "$NATIVE_CLAUDE" update || warn "Claude Code update check failed (network issue?), continuing..."
fi

# Install Claude Code skills from repository (regardless of install mode)
# Skills are copied on every run to ensure latest versions are always available
if command -v claude &>/dev/null; then
    SKILLS_SOURCE_DIR="$SCRIPT_DIR/skills"
    SKILLS_DEST_DIR="$HOME/.claude/skills"

    if [ -d "$SKILLS_SOURCE_DIR" ] && [ -n "$(ls -A "$SKILLS_SOURCE_DIR" 2>/dev/null)" ]; then
        log "Installing Claude Code skills..."
        mkdir -p "$SKILLS_DEST_DIR"

        # Copy each skill directory to destination
        for skill_dir in "$SKILLS_SOURCE_DIR"/*/; do
            if [ -d "$skill_dir" ]; then
                skill_name=$(basename "$skill_dir")
                log "  Installing skill: $skill_name"
                # Use cp -r to copy entire skill directory, -f to overwrite
                cp -rf "$skill_dir" "$SKILLS_DEST_DIR/"
            fi
        done

        log "Claude Code skills installed to $SKILLS_DEST_DIR"
    fi
fi

# Update claudo if already installed (no automatic installation in minimal mode)
CLAUDO_BIN="$HOME/.local/bin/claudo"
if [ "$INSTALL_MODE" != "full" ] && [ -x "$CLAUDO_BIN" ]; then
    log "Updating claudo..."
    curl -fsSL https://raw.githubusercontent.com/c0ffee0wl/claudo/main/claudo -o "$CLAUDO_BIN" || warn "claudo update failed, continuing..."
fi

# Update npm-based tools if npm is available (skip silently if npm not installed)
if command -v npm &>/dev/null; then
    # Update Gemini CLI if already installed (no automatic installation)
    upgrade_npm_global_if_installed @google/gemini-cli

    # Update OpenCode if already installed (no automatic installation)
    upgrade_npm_global_if_installed opencode-ai

    # Update Claude Code Router if already installed (no automatic installation in minimal mode)
    if [ "$INSTALL_MODE" != "full" ]; then
        upgrade_npm_global_if_installed @musistudio/claude-code-router
    fi

    # Update Codex CLI if already installed (no automatic installation in minimal mode)
    if [ "$INSTALL_MODE" != "full" ]; then
        upgrade_npm_global_if_installed @openai/codex
    fi
fi

#############################################################################
# COMPLETE
#############################################################################

log ""
log "============================================="
log "Installation/Update Complete!"
log "============================================="
log ""

if [ "$IS_WSL" = true ] && [ "$INSTALL_MODE" = "full" ]; then
    log "Installed tools (WSL mode):"
    log ""
    log "  AI Assistants:"
    log "    - llm              Simon Willison's LLM CLI tool"
    log "    - Claude Code      Anthropic's agentic coding CLI"
    log ""
    log "  LLM Plugins:"
    log "    - Providers: gemini, vertex, openrouter, anthropic"
    log "    - Tools: sandboxed-shell, sandboxed-python, patch, quickjs, sqlite"
    log "    - Tools: context, google-search, web-fetch, fabric, mcp, rag, skills"
    log "    - Fragments: pdf, github, youtube-transcript, site-text, dir"
    log "    - Utilities: cmd, cmd-comp, jq, git-commit, sort, classify, consortium"
    log ""
    log "  CLI Utilities:"
    log "    - gitingest        Git repository to LLM-friendly text"
    log "    - llm-observability  Log viewer for llm conversations"
    log "    - llm-server       OpenAI-compatible HTTP wrapper (if systemd detected)"
    log "    - toko             LLM token counter with cost estimation"
    log ""
    if npm list -g @musistudio/claude-code-router --depth=0 &>/dev/null 2>&1; then
        log "  WSL Integration:"
        log "    - Claude Code Router  Multi-provider proxy (systemd service enabled)"
        log ""
        log "  CCR Environment (in ~/.profile, sourced by ~/.zprofile for ZSH):"
        log "    - ANTHROPIC_BASE_URL=http://127.0.0.1:3456"
        log ""
        log "  Next steps:"
        log "    1. Restart WSL or start a new shell"
        log "    2. Test Claude Code: claude"
        log "    3. Verify CCR is running: systemctl --user status claude-code-router"
        log "    4. Configure external clients to use WSL's CCR endpoint"
    else
        log "  WSL Integration:"
        log "    - Claude Code Router was not installed"
        log "    - Re-run with --wsl to configure CCR for external clients"
        log ""
        log "  Next steps:"
        log "    1. Test Claude Code: claude"
    fi
    log ""
    log "  Skipped in WSL mode:"
    log "    - Session recording (asciinema)"
    log "    - Shell integration (llm-integration.bash/zsh)"
    log "    - Desktop tools (Handy, espanso, Ulauncher)"
    log "    - claudo, Codex CLI"
    log ""
elif [ "$INSTALL_MODE" = "full" ]; then
    log "Installed tools (full mode):"
    log ""
    log "  AI Assistants:"
    log "    - llm              Simon Willison's LLM CLI tool"
    log "    - llm-inlineassistant  Inline AI assistant (@ syntax, espanso triggers)"
    log "    - llm-assistant    Terminator AI assistant (if Terminator installed)"
    log "    - Claude Code      Anthropic's agentic coding CLI"
    log "    - Claude Code skills  Custom skills installed to ~/.claude/skills/"
    log "    - claudo           Claude Code in Docker (if Docker installed)"
    log "    - Claude Code Router  Multi-provider proxy for Claude Code"
    log "    - Codex CLI        OpenAI's coding agent (if Azure configured)"
    log ""
    log "  LLM Plugins:"
    log "    - Providers: gemini, vertex, openrouter, anthropic"
    log "    - Tools: sandboxed-shell, sandboxed-python, patch, quickjs, sqlite"
    log "    - Tools: context, google-search, web-fetch, fabric, mcp, rag, skills"
    log "    - Tools: capture-screen, imagemage, fragment-bridge, llm-functions"
    log "    - Fragments: pdf, github, youtube-transcript, site-text, dir"
    log "    - Utilities: cmd, cmd-comp, jq, git-commit, sort, classify, consortium"
    log ""
    log "  CLI Utilities:"
    log "    - gitingest        Git repository to LLM-friendly text"
    log "    - yek              Fast repository to LLM-friendly text"
    log "    - files-to-prompt  File content formatter for LLMs"
    log "    - llm-observability  Log viewer for llm conversations"
    log "    - llm-server       OpenAI-compatible HTTP wrapper (if systemd detected)"
    log "    - toko             LLM token counter with cost estimation"
    log "    - tldr             Community-driven man pages"
    log "    - argc             Bash CLI framework"
    log "    - asciinema        Terminal session recorder"
    log "    - transcribe       Speech-to-text (25 European languages)"
    log "    - micro            Terminal text editor with LLM plugin"
    log "    - context          Terminal history extractor"
    log ""
    log "  MCP Servers:"
    log "    - microsoft-learn    Microsoft documentation search and fetch"
    log "    - aws-knowledge      AWS documentation and best practices"
    log "    - arxiv-mcp-server   arXiv paper search and retrieval"
    log "    - chrome-devtools    Browser automation (if Chrome/Chromium detected)"
    log ""
    log "  Desktop Tools (if GUI detected):"
    log "    - Handy            System-wide speech-to-text input"
    log "    - espanso          Text expander with LLM integration (:llm:, :llmc:, :@:, :@c:)"
    log "    - Ulauncher        Application launcher with LLM extension (llm, llmc, @, @c)"
    log "    - llm-guiassistant GTK popup assistant (Super+^, X11/XFCE only)"
    log ""
    log "Shell integration: $SCRIPT_DIR/integration/"
    log "  - llm-integration.bash (Bash)"
    log "  - llm-integration.zsh (Zsh)"
    log ""
    log "Next steps:"
    log "  1. Restart your shell or run: source ~/.bashrc (or ~/.zshrc)"
    log "  2. Test llm: llm 'Hello, how are you?'"
    log "  3. Test llm-inlineassistant: @ What date is it?"
    log "  4. Use Ctrl+N for AI command completion, Ctrl+G to apply suggested commands"
    log "  5. Test Claude Code Router: routed-claude"
    log ""
    log "Note: If llm-assistant daemon or llm-guiassistant were running,"
    log "      they were stopped for the update. Restart them as needed:"
    log "        llm-assistant --daemon   # Start daemon"
    log "        llm-guiassistant         # Open GUI assistant"
else
    log "Installed tools (minimal mode):"
    log ""
    log "  Core LLM:"
    log "    - llm              Simon Willison's LLM CLI tool"
    log ""
    log "  LLM Plugins:"
    log "    - Providers: gemini, vertex, openrouter, anthropic"
    log "    - Tools: sandboxed-shell, sandboxed-python, patch, quickjs, sqlite"
    log "    - Tools: context, google-search, web-fetch, fabric, mcp, rag, skills"
    log "    - Tools: capture-screen, imagemage, fragment-bridge, llm-functions"
    log "    - Fragments: pdf, github, youtube-transcript, site-text, dir"
    log "    - Utilities: cmd, cmd-comp, jq, git-commit, sort, classify, consortium"
    log ""
    log "  CLI Utilities:"
    log "    - gitingest        Git repository to LLM-friendly text"
    log "    - llm-observability  Log viewer for llm conversations"
    log "    - llm-server       OpenAI-compatible HTTP wrapper (if systemd detected)"
    log "    - toko             LLM token counter with cost estimation"
    log ""
    log "Next steps:"
    log "  1. Test llm: llm 'Hello, how are you?'"
    log ""
    log "To install all tools (Claude Code, llm-assistant, MCP servers, shell integration, etc.):"
    log "  ./install-llm-tools.sh --full"
fi
log ""
log "To update all tools in the future, simply re-run this script:"
log "  ./install-llm-tools.sh"
log ""
log "To (re)configure Azure OpenAI settings:"
log "  ./install-llm-tools.sh --azure"
log ""
log "To (re)configure Gemini settings:"
log "  ./install-llm-tools.sh --gemini"
