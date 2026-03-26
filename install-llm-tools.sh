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
FORCE_LLM=false
INSTALL_LEVEL=""  # 1=minimal, 2=standard, 3=full (empty=use saved/default)
WSL_FLAG=""       # "", "force", or "disable"
CCR_FLAG=false

# Preserve original args before parsing consumes them via shift.
# Used by self-update (exec "$0") to re-run with the same flags.
ORIGINAL_ARGS=("$@")

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
        --force-llm)
            FORCE_LLM=true
            shift
            ;;
        --minimal)
            INSTALL_LEVEL=1
            shift
            ;;
        --standard|--no-additional-tools)
            INSTALL_LEVEL=2
            shift
            ;;
        --full)
            INSTALL_LEVEL=3
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
        --ccr)
            CCR_FLAG=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "LLM Tools Installation Script for Linux (Debian/Ubuntu/Kali)"
            echo ""
            echo "Options:"
            echo "  --minimal      Level 1: LLM core tools only (persists for future runs)"
            echo "  --standard     Level 2: Core + agentic tools, skip extras (persists)"
            echo "  --full         Level 3: Everything (default, persists)"
            echo "  --no-additional-tools  Alias for --standard (backward compatibility)"
            echo "  --wsl          Force WSL mode (skip session recording, prompt for CCR)"
            echo "  --no-wsl       Disable WSL auto-detection (run full install even in WSL)"
            echo "  --ccr          Full Claude Code Router setup (config, profile exports, systemd service)"
            echo "  --azure        Force (re)configuration of Azure OpenAI, even if already configured"
            echo "  --gemini       Force (re)configuration of Google Gemini, even if already configured"
            echo "  --force-llm    Force LLM reinstall even if plugins/sources haven't changed"
            echo "  --clear-cache  Clear package caches (npm, go, pip, pipx, cargo, uv) to reclaim disk space"
            echo "  --help         Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0              # Normal installation/update (level 3)"
            echo "  $0 --minimal    # Install only LLM core tools (level 1)"
            echo "  $0 --standard   # Core + agentic tools, skip extras (level 2)"
            echo "  $0 --full       # Install everything (level 3)"
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

# Note: --minimal/--standard/--full all set INSTALL_LEVEL; last flag wins.
# This is safe — no conflict possible since they write to the same variable.

#############################################################################
# Installation Level Resolution (1=minimal, 2=standard, 3=full)
#############################################################################

INSTALL_LEVEL_FILE="$LLM_TOOLS_CONFIG_DIR/install-level"

# Migrate old format (install-mode + no-additional-tools files)
OLD_MODE_FILE="$LLM_TOOLS_CONFIG_DIR/install-mode"
OLD_NAT_FILE="$LLM_TOOLS_CONFIG_DIR/no-additional-tools"
if [ -z "$INSTALL_LEVEL" ] && [ ! -f "$INSTALL_LEVEL_FILE" ] && [ -f "$OLD_MODE_FILE" ]; then
    old_mode=$(cat "$OLD_MODE_FILE")
    mkdir -p "$LLM_TOOLS_CONFIG_DIR"
    if [ "$old_mode" = "minimal" ]; then
        echo "1" > "$INSTALL_LEVEL_FILE"
    elif [ -f "$OLD_NAT_FILE" ]; then
        echo "2" > "$INSTALL_LEVEL_FILE"
    else
        echo "3" > "$INSTALL_LEVEL_FILE"
    fi
    rm -f "$OLD_MODE_FILE" "$OLD_NAT_FILE"
    log "Migrated install preferences to new format"
fi

# Resolve: CLI flag > persisted file > default (3=full)
if [ -n "$INSTALL_LEVEL" ]; then
    mkdir -p "$LLM_TOOLS_CONFIG_DIR"
    echo "$INSTALL_LEVEL" > "$INSTALL_LEVEL_FILE"
    log "Installation level: $INSTALL_LEVEL (saved for future runs)"
elif [ -f "$INSTALL_LEVEL_FILE" ]; then
    INSTALL_LEVEL=$(cat "$INSTALL_LEVEL_FILE")
    log "Using saved installation level: $INSTALL_LEVEL (use --full to override)"
else
    INSTALL_LEVEL=3
fi

# Validate
if ! [[ "$INSTALL_LEVEL" =~ ^[123]$ ]]; then
    warn "Invalid install level '$INSTALL_LEVEL', defaulting to 3 (full)"
    INSTALL_LEVEL=3
fi

#############################################################################
# Environment Detection (cached for use throughout script)
#############################################################################

# WSL detection with override
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

# Desktop environment detection (only in standard/full mode, non-WSL)
HAS_DESKTOP=false
HAS_X11=false
HAS_SOUNDCARD=false
TERMINATOR_INSTALLED=false

if [ "$INSTALL_LEVEL" -ge 2 ] && [ "$IS_WSL" != true ]; then
    has_desktop_environment && HAS_DESKTOP=true
    is_x11 && HAS_X11=true
    has_soundcard && HAS_SOUNDCARD=true
    if command -v terminator &> /dev/null; then
        TERMINATOR_INSTALLED=true
        log "Terminator detected - will install assistant integration"
    else
        log "Terminator not found - skipping assistant components"
    fi
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

# Extract Azure API base URL from extra-openai-models.yaml
# Returns empty string on failure
get_azure_api_base() {
    local extra_models_file="$(get_llm_config_dir)/extra-openai-models.yaml"
    grep -m 1 "^\s*api_base:" "$extra_models_file" 2>/dev/null | sed 's/.*api_base:\s*//;s/\s*$//' || true
}

# Configure Azure OpenAI with prompts
configure_azure_openai() {
    log "Configuring Azure OpenAI API..."
    echo ""
    read -p "Enter your Azure Foundry resource URL (e.g., https://YOUR-RESOURCE.cognitiveservices.azure.com/openai/v1/): " AZURE_API_BASE

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

    local api_base=$(get_azure_api_base)

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

    local api_base=$(get_azure_api_base)

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
    local websearch_plugin_file="$plugin_dir/web-search-inject.js"

    log "Configuring Claude Code Router..."

    # Create directories
    mkdir -p "$plugin_dir"

    # Snapshot existing plugin content for change detection
    local old_plugins_hash=""
    [ -d "$plugin_dir" ] && old_plugins_hash=$(cat "$plugin_dir"/*.js 2>/dev/null | md5sum)

    # Always write transformer plugins (ensures they exist for any config variant that references them)
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

  async transformResponseOut(response, provider) {
    return response;
  }
}

module.exports = StripReasoningTransformer;
EOF

    cat > "$websearch_plugin_file" <<'EOF'
class WebSearchInjectTransformer {
  name = "web-search-inject";

  async transformRequestIn(request, provider) {
    // This plugin runs AFTER openai-responses in the chain:
    //   ["openai-responses", "web-search-inject", "strip-reasoning"]
    // So the request is already in Responses API format at this point.
    //
    // openai-responses maps Anthropic web_search -> {"type": "web_search"},
    // but Azure only supports "web_search_preview". Rename it.
    if (Array.isArray(request.tools)) {
      for (const tool of request.tools) {
        if (tool.type === "web_search") {
          tool.type = "web_search_preview";
        }
      }
    }
    // If no web search tool exists yet, inject one so the model can search.
    if (!request.tools) request.tools = [];
    const hasWebSearch = request.tools.some(
      (t) => t.type === "web_search_preview" || t.type === "web_search"
    );
    if (!hasWebSearch) {
      request.tools.push({ type: "web_search_preview" });
    }
    return request;
  }

  async transformResponseOut(response, provider) {
    return response;
  }
}

module.exports = WebSearchInjectTransformer;
EOF

    # Detect plugin changes for restart tracking
    local new_plugins_hash=$(cat "$plugin_dir"/*.js 2>/dev/null | md5sum)
    if [ "$old_plugins_hash" != "$new_plugins_hash" ]; then
        CCR_NEEDS_RESTART=true
        log "CCR plugins updated"
    fi

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
        # Dual-provider config: Azure primary (Responses API), Gemini fallback
        log "Generating dual-provider config (Azure primary, Azure web search)"

        local azure_api_base=$(get_azure_api_base)
        azure_api_base="${azure_api_base%/}"

        config_content=$(cat <<EOF
{
  "LOG": true,
  "LOG_LEVEL": "warn",
  "Providers": [
    {
      "name": "azure",
      "api_base_url": "${azure_api_base}/responses",
      "api_key": "\$AZURE_OPENAI_API_KEY",
      "models": [
        "gpt-5.4",
        "gpt-5.4-mini"
      ],
      "transformer": {
        "use": [
          "openai-responses",
          "strip-reasoning"
        ]
      }
    },
    {
      "name": "azure-search",
      "api_base_url": "${azure_api_base}/responses",
      "api_key": "\$AZURE_OPENAI_API_KEY",
      "models": [
        "gpt-5.2"
      ],
      "transformer": {
        "use": [
          "openai-responses",
          "web-search-inject",
          "strip-reasoning"
        ]
      }
    },
    {
      "name": "gemini",
      "api_base_url": "https://generativelanguage.googleapis.com/v1beta/models/",
      "api_key": "\$GEMINI_API_KEY",
      "models": [
        "gemini-2.5-flash"
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
    },
    {
      "path": "${HOME}/.claude-code-router/plugins/web-search-inject.js"
    }
  ],
  "Router": {
    "default": "azure,gpt-5.4",
    "background": "azure,gpt-5.4-mini",
    "think": "azure,gpt-5.4",
    "longContext": "azure,gpt-5.4",
    "webSearch": "azure-search,gpt-5.2"
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

    # Track config content for restart detection
    local old_config=""
    [ -f "$config_file" ] && old_config=$(cat "$config_file")

    update_tracked_config "ccr-config" "$config_file" "$config_content" \
        "Claude Code Router config.json" "N" "true"

    # Detect config changes for restart tracking
    local new_config=""
    [ -f "$config_file" ] && new_config=$(cat "$config_file")
    if [ "$old_config" != "$new_config" ]; then
        CCR_NEEDS_RESTART=true
    fi
}

# Configure Claude Code Router (full setup including systemd service)
# Sets up: CCR installation, config, profile environment, systemd service
configure_ccr() {
    local CCR_PORT=3456
    CCR_NEEDS_RESTART=false

    # Skip CCR update if Claude Code is actively running to avoid session disruption
    if pgrep -x "claude" &>/dev/null; then
        warn "Claude Code is running — skipping CCR update to avoid session disruption"
        return 0
    fi

    # 0. Stop CCR before binary upgrade to prevent port/binary races
    if systemctl --user is-active claude-code-router &>/dev/null; then
        systemctl --user stop claude-code-router
        log "CCR service stopped for upgrade"
    fi

    # 1. Install/update Claude Code Router
    install_or_upgrade_global @musistudio/claude-code-router ccr

    # 2. Generate CCR config
    update_ccr_config

    # 3. Configure ~/.profile environment variables and create env file
    configure_ccr_profile "$CCR_PORT"

    # 4. Set up systemd user service for auto-start
    configure_ccr_systemd_service "$CCR_PORT"

    log "Claude Code Router configured"
    log "  Config:  ~/.claude-code-router/config.json"
    log "  Env:     ~/.config/claude-code-router/env"
    log "  Service: systemctl --user {start|stop|status} claude-code-router"
}

# Add CCR-specific environment variables to ~/.profile and create env file
configure_ccr_profile() {
    local port="$1"
    local env_file="${HOME}/.config/claude-code-router/env"

    # Retrieve API keys from llm key store
    local llm_cmd="${HOME}/.local/bin/llm"
    [ ! -x "$llm_cmd" ] && llm_cmd="llm"

    local azure_key=""
    local gemini_key=""
    if command -v "$llm_cmd" &>/dev/null; then
        azure_key=$("$llm_cmd" keys get azure 2>/dev/null || true)
        gemini_key=$("$llm_cmd" keys get gemini 2>/dev/null || true)
    fi

    # Create env file for systemd service (simple KEY=value format)
    mkdir -p "$(dirname "$env_file")"
    local old_env=""
    [ -f "$env_file" ] && old_env=$(cat "$env_file")
    : > "$env_file"  # Truncate/create
    chmod 600 "$env_file"  # Restrict permissions (contains secrets)
    [ -n "$azure_key" ] && echo "AZURE_OPENAI_API_KEY=${azure_key}" >> "$env_file"
    [ -n "$gemini_key" ] && echo "GEMINI_API_KEY=${gemini_key}" >> "$env_file"
    [ -n "$GOOGLE_CLOUD_PROJECT" ] && echo "GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}" >> "$env_file"
    [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ] && echo "GOOGLE_APPLICATION_CREDENTIALS=${GOOGLE_APPLICATION_CREDENTIALS}" >> "$env_file"
    [ -n "$GOOGLE_CLOUD_LOCATION" ] && echo "GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION}" >> "$env_file"
    # Detect env file changes for restart tracking
    if [ "$(cat "$env_file")" != "$old_env" ]; then
        CCR_NEEDS_RESTART=true
        log "CCR environment file updated"
    else
        log "CCR environment file unchanged"
    fi

    # Add/update CCR routing exports to ~/.profile
    # Based on CCR's createEnvVariables.ts - set both auth vars for compatibility
    update_profile_export "ANTHROPIC_BASE_URL" "http://127.0.0.1:${port}"
    update_profile_export "ANTHROPIC_AUTH_TOKEN" "test"  # Dummy token; CCR handles actual auth
    update_profile_export "NO_PROXY" "127.0.0.1"
    update_profile_export "API_TIMEOUT_MS" "600000"
    update_profile_export "DISABLE_COST_WARNINGS" "true"  # Costs don't apply via CCR

    # Also export API keys to ~/.profile for general shell availability
    [ -n "$azure_key" ] && update_profile_export "AZURE_OPENAI_API_KEY" "$azure_key"
    [ -n "$gemini_key" ] && update_profile_export "GEMINI_API_KEY" "$gemini_key"

    log "Added CCR routing to ~/.profile"
    log "  ANTHROPIC_BASE_URL=http://127.0.0.1:${port}"
}

# Create and enable systemd user service for CCR (idempotent)
configure_ccr_systemd_service() {
    local port="$1"
    local service_dir="$HOME/.config/systemd/user"
    local service_file="$service_dir/claude-code-router.service"
    # CCR is installed via npm global, so fallback to NPM_PREFIX if 'which' fails
    local ccr_path
    ccr_path=$(which ccr 2>/dev/null) || ccr_path=""
    if [ -z "$ccr_path" ]; then
        if [ "$JS_PKG_MGR" = "bun" ]; then
            ccr_path="$(_bun_prefix)/bin/ccr"
        else
            ccr_path="${NPM_PREFIX:-/usr/local}/bin/ccr"
        fi
    fi

    mkdir -p "$service_dir"

    # Enable lingering BEFORE any systemctl --user commands.
    # On first install, the user systemd manager may not be running without lingering.
    if command -v loginctl > /dev/null 2>&1; then
        if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
            sudo loginctl enable-linger "$USER" 2>/dev/null || \
                warn "Could not enable lingering. CCR may not auto-start on WSL boot."
            sleep 2  # Brief wait for user manager to start
        fi
    fi

    # Capture current PATH (includes NVM, cargo, etc.) for systemd
    # Systemd services don't inherit shell environment, so we must pass PATH explicitly
    local current_path="$PATH"

    # Generate expected service content
    # API keys are loaded from EnvironmentFile (created by configure_ccr_profile)
    local env_file="${HOME}/.config/claude-code-router/env"
    local expected_content="[Unit]
Description=Claude Code Router - Multi-provider proxy for Claude Code
After=network.target

[Service]
Type=simple
ExecStart=${ccr_path} start
Restart=on-failure
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=60
Environment=PORT=${port}
Environment=PATH=${current_path}
EnvironmentFile=${env_file}

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

    # Start or restart service (restart if service file, config, plugins, or env changed)
    if systemctl --user is-active claude-code-router &>/dev/null; then
        if [ "$needs_update" = true ] || [ "$CCR_NEEDS_RESTART" = true ]; then
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

    # Verify CCR is actually serving requests
    wait_for_ccr "$port" || warn "CCR may not be ready — Claude CLI calls could fail until it starts"
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
        exec "$0" "${ORIGINAL_ARGS[@]}"
        exit 0
    else
        log "Script is up to date"
    fi
else
    warn "Not running from a git repository. Self-update disabled."
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

    # Stop systemd service first (if enabled) - this also stops the daemon process
    local systemd_stopped=false
    if command -v systemctl &> /dev/null && systemctl --user is-active llm-assistant.service &> /dev/null; then
        log "Stopping llm-assistant systemd service..."
        systemctl --user stop llm-assistant.service && systemd_stopped=true && stopped=true
    fi

    # Only kill daemon processes manually if systemd didn't handle it
    if [ "$systemd_stopped" = "false" ]; then
        graceful_stop_process "llm-assistant.*--daemon" 10 "llm-assistant daemon" && stopped=true
        graceful_stop_process "python.*llm_assistant.*--daemon" 10 "Python llm-assistant" && stopped=true
    fi

    graceful_stop_process "llm-guiassistant" 10 "llm-guiassistant" && stopped=true
    graceful_stop_process "python.*llm_guiassistant" 10 "Python llm-guiassistant" && stopped=true

    if [ "$stopped" = "true" ]; then
        log "Assistant processes stopped. They will use updated code when restarted."
    fi
}

# Only stop processes at level 2+ (assistant components)
if [ "$INSTALL_LEVEL" -ge 2 ]; then
    stop_assistant_processes
fi

#############################################################################
# PHASE 1: Install Prerequisites
#############################################################################

log "Installing prerequisites..."

sudo apt-get update

# Install basic prerequisites (batch install for efficiency)
log "Installing basic prerequisites..."
install_apt_packages git jq python3 curl

# Clipboard support (X11 only — degrades gracefully if absent)
if [ "$IS_WSL" != true ]; then
    install_apt_package xsel
fi

# Install bubblewrap (provides bwrap for sandboxing, used by llm-functions and code execution tools)
install_apt_package bubblewrap bwrap
configure_bwrap_apparmor

# Check for sha256sum (required for template checksum tracking in Phase 4)
install_apt_package coreutils sha256sum

# PDF text extraction (used by llm-fragments-pdf plugin, installed in all modes)
install_apt_package poppler-utils pdftotext

# Heavy document processors — only needed for full mode features
if [ "$INSTALL_LEVEL" -ge 2 ]; then
    # pandoc: report export to Word (llm-assistant, Terminator-only feature)
    # ffmpeg: audio/video processing (speech-to-text, desktop integration)
    install_apt_packages pandoc ffmpeg
fi

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
if [ "$INSTALL_LEVEL" -ge 2 ] && [ "$IS_WSL" != true ]; then
    # Install/update Rust (with intelligent version detection and rustup fallback)
    install_or_upgrade_rust

    # Install/update asciinema (with commit-hash checking to avoid unnecessary rebuilds)
    install_or_upgrade_cargo_git_tool asciinema https://github.com/asciinema/asciinema
fi

# Detect available JS package manager (bun or npm) — needed for all modes
# because the upgrade section at end of script uses this in all modes
detect_js_package_manager

# Node.js/bun setup — level 2+ only
if [ "$INSTALL_LEVEL" -ge 2 ]; then
    if [ "$JS_PKG_MGR" = "bun" ]; then
        log "Bun detected, skipping Node.js installation"
        # Set NPM defaults for any code that references them
        if command -v npm &>/dev/null; then
            detect_npm_permissions
        else
            NPM_NEEDS_SUDO=false
            NPM_PREFIX="$(_bun_prefix)"
            export NPM_NEEDS_SUDO NPM_PREFIX
        fi
    else
        # Install/update Node.js (with intelligent version detection and nvm fallback)
        install_or_upgrade_nodejs

        # Detect if npm needs sudo for global installs
        detect_npm_permissions

        # Install/update bun via official installer (not npm — npm-installed bun
        # breaks bun's own global package management)
        install_or_upgrade_bun

        # Re-detect package manager now that bun is available
        detect_js_package_manager
    fi
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
    "llm-fragments-dir"

    # Utility plugins
    "llm-jq"
    "git+https://github.com/c0ffee0wl/llm-templates-fabric"
    "llm-git-commit"
    "llm-sort"
    "llm-classify"
)

# Optional plugins (level 3 only)
if [ "$INSTALL_LEVEL" -ge 3 ]; then
    REMOTE_PLUGINS+=(
        "llm-openrouter"
        "llm-anthropic"
        "git+https://github.com/c0ffee0wl/llm-tools-fragment-bridge"
    )
fi

# Local plugins (in-repo packages)
# These use --reinstall-package to force rebuild when an install is triggered
LOCAL_PLUGINS=(
    "$SCRIPT_DIR/llm-tools-core"
    "$SCRIPT_DIR/llm-tools-context"
)

# Full mode plugins
if [ "$INSTALL_LEVEL" -ge 2 ]; then
    # Core assistant packages (always in full mode)
    LOCAL_PLUGINS+=(
        "$SCRIPT_DIR/llm-assistant"
        "$SCRIPT_DIR/llm-inlineassistant"
    )

    # X11 desktop-only plugins
    if [ "$HAS_X11" = "true" ]; then
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

LLM_SOURCE="git+https://github.com/c0ffee0wl/llm"

# Fingerprint of the plugin list + source URL (not local code).
# Changes only when plugins are added/removed/changed — triggers full reinstall.
# Local code changes are handled by `uv tool upgrade --reinstall-package`.
compute_plugin_list_fingerprint() {
    { printf 'llm:%s\n' "$LLM_SOURCE"
      printf '%s\n' "${ALL_PLUGINS[@]}" | sort
    } | sha256sum | awk '{print $1}'
}

# Detect user-installed plugins (added via `llm install`, not in ALL_PLUGINS).
# Reads uv-tool-packages.json before we overwrite it.
detect_user_plugins() {
    USER_PLUGINS=()
    local packages_file
    packages_file="$(get_llm_config_dir)/uv-tool-packages.json"
    [ -f "$packages_file" ] || return 0

    local -A managed
    for p in "${ALL_PLUGINS[@]}"; do managed["$p"]=1; done
    managed["git+https://github.com/c0ffee0wl/llm-uv-tool"]=1

    while IFS= read -r pkg; do
        [ -z "$pkg" ] && continue
        [ -z "${managed[$pkg]+_}" ] && USER_PLUGINS+=("$pkg")
    done < <(jq -r '.[]' "$packages_file" 2>/dev/null)

    if [ ${#USER_PLUGINS[@]} -gt 0 ]; then
        log "Preserving ${#USER_PLUGINS[@]} user-installed plugin(s)"
    fi
}

# Write uv-tool-packages.json so that future `llm install <user-plugin>` calls
# (handled by llm-uv-tool) preserve our git-fork URLs.  llm-uv-tool's get_plugins()
# only sees distribution names (e.g. "llm-gemini"), not source URLs, so without
# this file it would fall back to PyPI on reinstall.
update_uv_tool_packages_json() {
    local config_dir packages_file
    config_dir="$(get_llm_config_dir)"
    packages_file="$config_dir/uv-tool-packages.json"
    mkdir -p "$config_dir"
    {
        printf '%s\n' "${ALL_PLUGINS[@]}" | grep -v "llm-uv-tool"
        [ ${#USER_PLUGINS[@]} -gt 0 ] && printf '%s\n' "${USER_PLUGINS[@]}"
    } | sort -u | jq -R . | jq -s . > "$packages_file"
}

LLM_PLUGIN_FINGERPRINT=$(compute_plugin_list_fingerprint)
LLM_FINGERPRINT_FILE="$LLM_TOOLS_CONFIG_DIR/llm-install-fingerprint"
STORED_FINGERPRINT=$(cat "$LLM_FINGERPRINT_FILE" 2>/dev/null || echo "")

# Build --reinstall-package args for local plugins (forces rebuild from source)
REINSTALL_ARGS=()
for local_path in "${LOCAL_PLUGINS[@]}"; do
    pkg_name=$(basename "$local_path")
    REINSTALL_ARGS+=(--reinstall-package "${pkg_name//-/_}")
done

if [ "$FORCE_LLM" = "true" ] || ! command -v llm &>/dev/null || \
   [ "$LLM_PLUGIN_FINGERPRINT" != "$STORED_FINGERPRINT" ]; then

    # Full install: plugin list changed, first run, or forced
    detect_user_plugins

    INSTALL_ARGS=(uv tool install --force "${REINSTALL_ARGS[@]}")
    for plugin in "${ALL_PLUGINS[@]}" "${USER_PLUGINS[@]}"; do
        INSTALL_ARGS+=(--with "$plugin")
    done
    INSTALL_ARGS+=("$LLM_SOURCE")

    log "Installing llm with $(( ${#ALL_PLUGINS[@]} + ${#USER_PLUGINS[@]} )) plugins (${#LOCAL_PLUGINS[@]} local)..."
    "${INSTALL_ARGS[@]}"

    update_uv_tool_packages_json

    mkdir -p "$LLM_TOOLS_CONFIG_DIR"
    echo "$LLM_PLUGIN_FINGERPRINT" > "$LLM_FINGERPRINT_FILE"
    log "LLM and plugins ready"

else
    # Incremental upgrade: pull latest git commits, update PyPI packages,
    # and rebuild local plugins — no venv recreation
    log "Upgrading llm and plugins..."
    uv tool upgrade "${REINSTALL_ARGS[@]}" llm
    log "LLM and plugins upgraded"
fi

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
        install_or_upgrade_global chrome-devtools-mcp
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
    }'"$chrome_devtools_config"'
  }
}'

    update_tracked_config "mcp-config" "$MCP_CONFIG_FILE" "$expected_config" \
        "MCP configuration" "Y" "false"
}

# MCP servers — level 2+ only
if [ "$INSTALL_LEVEL" -ge 2 ]; then
    update_mcp_config

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
        AZURE_API_BASE="https://REPLACE-ME.cognitiveservices.azure.com/openai/v1/"
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

    # Create extra-responses-models.yaml for Responses API models (gpt-5.1-codex)
    # These models require the Responses API and do NOT work with Chat Completions
    log "Creating Azure OpenAI Responses API models configuration..."

    EXTRA_RESPONSES_FILE="$LLM_CONFIG_DIR/extra-responses-models.yaml"
    cat > "$EXTRA_RESPONSES_FILE" <<EOF
# Azure OpenAI models that require the Responses API
# These models do NOT work with Chat Completions API

- model_id: gpt-5.1-codex
  model_name: gpt-5.1-codex
  api_base: "${AZURE_API_BASE}"
  api_key_name: azure
  vision: true
  reasoning: true
  aliases:
    - azure/gpt-5.1-codex
EOF
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

# Cache provider key existence for Phases 4-7 (avoid repeated subprocess calls)
HAS_AZURE_KEY=false
HAS_GEMINI_KEY=false
command llm keys get azure &>/dev/null && HAS_AZURE_KEY=true
command llm keys get gemini &>/dev/null && HAS_GEMINI_KEY=true

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
if [ "$INSTALL_LEVEL" -ge 2 ] && [ "$IS_WSL" != true ]; then
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

# Enable systemd user service for faster daemon startup (if systemd available)
if command -v systemctl &> /dev/null && systemctl --user status &> /dev/null 2>&1; then
    log "Installing llm-assistant systemd user service..."
    if "$HOME/.local/bin/llm-assistant" --service 2>/dev/null; then
        log "llm-assistant systemd service enabled (faster @ command startup)"
    else
        warn "Could not install systemd service, using traditional daemon mode"
    fi
fi

if [ "$HAS_DESKTOP" = "true" ]; then

    # --- Always installed at level 2+ ---

    # Install imagemage - Gemini image generation CLI (only if Gemini configured)
    if [ "$HAS_GEMINI_KEY" = "true" ]; then
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
        "espanso" "x86_64" || true

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

    # llm-guiassistant (GTK popup for llm-assistant daemon)
    # Only on X11 for now (uses xdotool, xclip, maim, xprop)
    # Note: Package and llm-tools-capture-screen are installed via ALL_PLUGINS in Phase 2
    if [ "$HAS_X11" = "true" ]; then
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

    # --- Level 3 only: desktop extras ---
    if [ "$INSTALL_LEVEL" -ge 3 ]; then

    # Audio-related installations (STT/TTS) - only if soundcard available
    if [ "$HAS_SOUNDCARD" = "true" ]; then
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
                if ! curl_secure -fL --progress-bar "$HF_BASE/$file" -o "$MODEL_DIR/$file"; then
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
        install_github_deb_package "handy" "0.8.1" \
            "https://github.com/cjpais/Handy/releases/download/v{VERSION}/Handy_{VERSION}_amd64.deb" \
            "handy" "x86_64" || true

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

    # Install Ulauncher (application launcher)
    install_github_deb_package "ulauncher" "5.15.15" \
        "https://github.com/Ulauncher/Ulauncher/releases/download/{VERSION}/ulauncher_{VERSION}_all.deb" \
        "" "" || true  # No process kill needed, architecture-independent

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

    fi  # End of INSTALL_LEVEL >= 3 check for desktop tools
fi

fi  # End of INSTALL_LEVEL >= 2 block for Phase 5

#############################################################################
# PHASE 6: Additional Tools
#############################################################################

log "Installing/updating additional tools..."

# --- Always installed at level 2+ ---

# Install/update gitingest
install_or_upgrade_uv_tool gitingest

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

# --- Level 3 only: core extras ---
if [ "$INSTALL_LEVEL" -ge 3 ]; then

# Install/update llm-observability (log viewer for llm conversations)
install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/llm-observability"

# Install/update toko (LLM token counter with cost estimation)
# Requires Python 3.14 - installs with isolated Python environment
install_or_upgrade_uv_tool toko 3.14

# Install/update md2cb (Markdown to rich HTML clipboard, requires X11)
if [ "$IS_WSL" != true ]; then
    install_or_upgrade_github_release "md2cb" "letientai299/md2cb" "linux-x64.tar.gz" || true
fi

fi  # End of INSTALL_LEVEL >= 3 check for core tools

# Full mode tools
if [ "$INSTALL_LEVEL" -ge 2 ]; then

    # --- Always installed at level 2+ ---

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

    # --- Level 3 only: full-mode extras ---
    if [ "$INSTALL_LEVEL" -ge 3 ]; then

    # Install/update tldr (community-driven man pages with practical examples)
    install_or_upgrade_uv_tool tldr

    # Install transcribe script (uses onnx-asr from llm environment) - only if soundcard available
    if [ "$HAS_SOUNDCARD" = "true" ]; then
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

    fi  # End of INSTALL_LEVEL >= 3 check for full-mode extras

fi  # End of INSTALL_LEVEL >= 2 block for Phase 6

#############################################################################
# PHASE 7: Agentic CLI (coding) tools
#############################################################################

# Level 2+: Install agentic CLI tools
if [ "$INSTALL_LEVEL" -ge 2 ]; then

# Install or update Claude Code
NATIVE_CLAUDE="$HOME/.local/bin/claude"
if [ -x "$NATIVE_CLAUDE" ]; then
    # Fast pre-check avoids slow 'claude update' (~60s) when already up-to-date
    # Returns: 0=update available, 1=up-to-date, 2=check failed (fallback to update)
    set +e
    check_claude_code_update_available "$NATIVE_CLAUDE"
    check_result=$?
    set -e
    if [ $check_result -eq 0 ] || [ $check_result -eq 2 ]; then
        [ $check_result -eq 2 ] && warn "Fast update check failed, falling back to native update..."
        "$NATIVE_CLAUDE" update || warn "Claude Code update failed, continuing..."
    fi
else
    log "Installing Claude Code..."
    curl_secure -fsSL https://claude.ai/install.sh | bash
fi

# Clean up legacy npm version if it exists (migration from older installs)
if command -v npm &>/dev/null && npm list -g @anthropic-ai/claude-code --depth=0 &>/dev/null; then
    log "Removing legacy npm Claude Code package..."
    npm_uninstall_global @anthropic-ai/claude-code claude || warn "Failed to remove legacy npm package"
fi

# WSL mode: CCR only with --ccr flag, skip Codex
if [ "$IS_WSL" = true ]; then
    if [ "$CCR_FLAG" = true ]; then
        # --ccr flag: install/configure CCR with systemd service
        if [ "$HAS_AZURE_KEY" = "true" ] || [ "$HAS_GEMINI_KEY" = "true" ]; then
            log "Setting up Claude Code Router for WSL..."
            configure_ccr
        else
            warn "Cannot configure CCR: no providers configured (run --azure or --gemini first)"
        fi
    elif pkg_is_installed_global @musistudio/claude-code-router ccr; then
        if systemctl --user is-active claude-code-router &>/dev/null; then
            log "Claude Code Router is running (use --ccr to reconfigure)"
        else
            log "Claude Code Router is installed but not running (use --ccr to reconfigure)"
        fi
    else
        log "Claude Code Router not installed (use --ccr to install)"
    fi

# Non-WSL mode: CCR (auto), Codex CLI
else

# Install/update Claude Code Router with flexible provider support
# Only install CCR if at least one provider key exists
if [ "$HAS_AZURE_KEY" = "true" ] || [ "$HAS_GEMINI_KEY" = "true" ]; then
    # Export environment variables for providers with keys
    if [ "$AZURE_CONFIGURED" = "true" ]; then
        export_azure_env_vars
    fi

    if [ "$HAS_GEMINI_KEY" = "true" ]; then
        export_gemini_env_vars
    fi

    if [ "$CCR_FLAG" = true ]; then
        # Full CCR setup with profile exports (for VS Code extension etc.)
        log "Setting up Claude Code Router with profile exports..."
        configure_ccr
    else
        # Basic CCR install without profile exports
        log "Installing/updating Claude Code Router..."
        install_or_upgrade_global @musistudio/claude-code-router ccr
        update_ccr_config
        log "Claude Code Router installed"
        log "  Tip: Use --ccr flag to set up profile exports for VS Code integration"
    fi
else
    log "Skipping Claude Code Router installation (no providers configured)"
fi

# Install/update Codex CLI if Azure is configured (level 3 only)
if [ "$INSTALL_LEVEL" -ge 3 ]; then
if [ "$AZURE_CONFIGURED" = "true" ]; then
    log "Installing/updating Codex CLI..."
    install_or_upgrade_global @openai/codex

    # Configure Codex CLI with Azure OpenAI credentials
    if [ ! -f "$HOME/.codex/config.toml" ]; then
        configure_codex_cli
    fi

    log "Codex CLI installed and configured with Azure OpenAI"
else
    log "Skipping Codex installation (Azure OpenAI not configured)"
fi
fi  # End of INSTALL_LEVEL >= 3 check for Codex

fi  # End of WSL/non-WSL block

fi  # End of INSTALL_LEVEL >= 2 block for Phase 7

#############################################################################
# Global environment defaults (regardless of install mode)
#############################################################################

# Set telemetry/privacy defaults for dev tools
# These apply even in minimal mode in case tools are installed manually later
log "Setting environment defaults..."
# Universal
update_profile_export "DO_NOT_TRACK" "1"
# Claude Code
update_profile_export "DISABLE_TELEMETRY" "1"
update_profile_export "DISABLE_ERROR_REPORTING" "1"
update_profile_export "DISABLE_BUG_COMMAND" "1"
update_profile_export "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY" "1"
update_profile_export "DISABLE_INSTALL_GITHUB_APP_COMMAND" "1"
# VS Code / .NET / PowerShell
update_profile_export "VSCODE_TELEMETRY_DISABLE" "1"
update_profile_export "VSCODE_CRASH_REPORTER_DISABLE" "1"
update_profile_export "DOTNET_CLI_TELEMETRY_OPTOUT" "1"
update_profile_export "POWERSHELL_TELEMETRY_OPTOUT" "1"
# AI/ML
update_profile_export "HF_HUB_DISABLE_TELEMETRY" "1"
# Scripting languages packaging
update_profile_export "PYPI_DISABLE_TELEMETRY" "1"
update_profile_export "UV_NO_TELEMETRY" "1"
update_profile_export "SCARF_ANALYTICS" "false"
ensure_zprofile_sources_profile

#############################################################################
# Update existing CLI tools (regardless of install mode)
# This ensures tools installed previously get updates even in minimal mode
#############################################################################

# Update optional uv tools if already installed (single uv tool list call)
UV_TOOLS_INSTALLED=$(uv tool list 2>/dev/null || true)
for optional_tool in claudechic notebooklm-mcp-cli youtube-transcript-api yt-dlp; do
    if echo "$UV_TOOLS_INSTALLED" | grep -q "^${optional_tool} "; then
        install_or_upgrade_uv_tool "$optional_tool"
    fi
done

# Install Claude Code skills and statusline (level 3 only)
# Skills are copied on every run to ensure latest versions are always available
if command -v claude &>/dev/null && [ "$INSTALL_LEVEL" -ge 3 ]; then
    SKILLS_SOURCE_DIR="$SCRIPT_DIR/skills"
    SKILLS_DEST_DIR="$HOME/.claude/skills"

    # Fetch/update external skills before copying
    if [ -x "$SKILLS_SOURCE_DIR/update-external-skills.sh" ]; then
        log "Updating external skills..."
        "$SKILLS_SOURCE_DIR/update-external-skills.sh"
    fi

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

    # Install Claude Code statusline (Kali-style prompt with model/context info)
    STATUSLINE_SOURCE="$SCRIPT_DIR/integration/claude-statusline/statusline.sh"
    STATUSLINE_DEST="$HOME/.claude/statusline.sh"
    SETTINGS_FILE="$HOME/.claude/settings.json"

    if [ -f "$STATUSLINE_SOURCE" ]; then
        log "Installing Claude Code statusline..."
        mkdir -p "$HOME/.claude"
        cp -f "$STATUSLINE_SOURCE" "$STATUSLINE_DEST"
        chmod +x "$STATUSLINE_DEST"

        # Update settings.json with statusLine config (preserving existing settings)
        if [ -f "$SETTINGS_FILE" ]; then
            # Merge statusLine into existing settings using jq
            jq '.statusLine = {"type": "command", "command": "~/.claude/statusline.sh"}' \
                "$SETTINGS_FILE" > "${SETTINGS_FILE}.tmp" && mv "${SETTINGS_FILE}.tmp" "$SETTINGS_FILE"
        else
            # Create new settings file
            cat > "$SETTINGS_FILE" << 'SETTINGS_EOF'
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.sh"
  }
}
SETTINGS_EOF
        fi
        log "Claude Code statusline installed"
    fi
fi

# Install/update blaude (bubblewrap sandbox for Claude Code)
log "Installing/updating blaude..."
mkdir -p "$HOME/.local/bin"
if curl_secure -fsSL -H "Cache-Control: no-cache" https://raw.githubusercontent.com/c0ffee0wl/blaude/main/blaude -o "$HOME/.local/bin/blaude"; then
    chmod +x "$HOME/.local/bin/blaude"
    log "blaude installed to ~/.local/bin/blaude"
else
    warn "Failed to download blaude"
fi

# Update JS-based tools if a package manager is available
if [ -n "$JS_PKG_MGR" ]; then
    # Update Gemini CLI if already installed (no automatic installation)
    upgrade_global_if_installed @google/gemini-cli gemini

    # Update OpenCode if already installed (no automatic installation)
    upgrade_global_if_installed opencode-ai opencode

    # Update Claude Agent ACP if already installed (no automatic installation)
    upgrade_global_if_installed @zed-industries/claude-agent-acp claude-agent-acp

    # Update Claude Code Router and Codex CLI if already installed
    upgrade_global_if_installed @musistudio/claude-code-router ccr
    upgrade_global_if_installed @openai/codex codex
fi

# Clean up package caches to reclaim disk space (runs regardless of install mode)
clear_package_caches

# Final CCR health verification — only if installed and the systemd service is enabled
if pkg_is_installed_global @musistudio/claude-code-router ccr && \
   systemctl --user is-enabled claude-code-router &>/dev/null; then
    verify_ccr_or_recover
fi

#############################################################################
# COMPLETE
#############################################################################

log ""
log "============================================="
log "Installation/Update Complete!"
log "============================================="
log ""

if [ "$IS_WSL" = true ] && [ "$INSTALL_LEVEL" -ge 2 ]; then
    log "Installed tools (WSL mode):"
    log ""
    log "  AI Assistants:"
    log "    - llm              Simon Willison's LLM CLI tool"
    log "    - Claude Code      Anthropic's agentic coding CLI"
    log ""
    log "  LLM Plugins:"
    log "    - Providers: gemini, vertex"
    log "    - Tools: sandboxed-shell, sandboxed-python, patch, quickjs, sqlite"
    log "    - Tools: context, google-search, web-fetch, fabric, mcp, rag, skills"
    log "    - Fragments: pdf, github, youtube-transcript, site-text, dir"
    log "    - Utilities: cmd, cmd-comp, jq, git-commit, sort, classify"
    log ""
    log "  CLI Utilities:"
    log "    - gitingest        Git repository to LLM-friendly text"
    log "    - llm-server       OpenAI-compatible HTTP wrapper (if systemd detected)"
    log ""
    if pkg_is_installed_global @musistudio/claude-code-router ccr; then
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
        log "    - Run with --ccr to install CCR for external clients"
        log ""
        log "  Next steps:"
        log "    1. Test Claude Code: claude"
    fi
    log ""
    log "  Skipped in WSL mode:"
    log "    - Session recording (asciinema)"
    log "    - Shell integration (llm-integration.bash/zsh)"
    log "    - Desktop tools (Handy, espanso, Ulauncher)"
    log "    - Codex CLI"
    log ""
elif [ "$INSTALL_LEVEL" -ge 3 ]; then
    log "Installed tools (level 3 — full):"
    log ""
    log "  AI Assistants:"
    log "    - llm              Simon Willison's LLM CLI tool"
    log "    - llm-inlineassistant  Inline AI assistant (@ syntax, espanso triggers)"
    log "    - llm-assistant    Terminator AI assistant (if Terminator installed)"
    log "    - Claude Code      Anthropic's agentic coding CLI"
    log "    - Claude Code skills  Custom skills installed to ~/.claude/skills/"
    log "    - blaude           Bubblewrap sandbox for Claude Code"
    log "    - Claude Code Router  Multi-provider proxy for Claude Code"
    log "    - Codex CLI        OpenAI's coding agent (if Azure configured)"
    log ""
    log "  LLM Plugins:"
    log "    - Providers: gemini, vertex, openrouter, anthropic"
    log "    - Tools: sandboxed-shell, sandboxed-python, patch, quickjs, sqlite"
    log "    - Tools: context, google-search, web-fetch, fabric, mcp, rag, skills"
    log "    - Tools: capture-screen, imagemage, fragment-bridge, llm-functions"
    log "    - Fragments: pdf, github, youtube-transcript, site-text, dir"
    log "    - Utilities: cmd, cmd-comp, jq, git-commit, sort, classify"
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
    log "    - md2cb            Markdown to rich HTML clipboard"
    log "    - micro            Terminal text editor with LLM plugin"
    log "    - context          Terminal history extractor"
    log ""
    log "  MCP Servers:"
    log "    - microsoft-learn    Microsoft documentation search and fetch"
    log "    - aws-knowledge      AWS documentation and best practices"
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
elif [ "$INSTALL_LEVEL" -ge 2 ]; then
    log "Installed tools (level 2 — standard):"
    log ""
    log "  AI Assistants:"
    log "    - llm              Simon Willison's LLM CLI tool"
    log "    - llm-inlineassistant  Inline AI assistant (@ syntax)"
    log "    - llm-assistant    Terminator AI assistant (if Terminator installed)"
    log "    - Claude Code      Anthropic's agentic coding CLI"
    log "    - blaude           Bubblewrap sandbox for Claude Code"
    log "    - Claude Code Router  Multi-provider proxy for Claude Code"
    log ""
    log "  LLM Plugins:"
    log "    - Providers: gemini, vertex"
    log "    - Tools: sandboxed-shell, sandboxed-python, patch, quickjs, sqlite"
    log "    - Tools: context, google-search, web-fetch, fabric, mcp, rag, skills"
    log "    - Fragments: pdf, github, youtube-transcript, site-text, dir"
    log "    - Utilities: cmd, cmd-comp, jq, git-commit, sort, classify"
    log ""
    log "  CLI Utilities:"
    log "    - gitingest        Git repository to LLM-friendly text"
    log "    - yek              Fast repository to LLM-friendly text"
    log "    - llm-server       OpenAI-compatible HTTP wrapper (if systemd detected)"
    log "    - asciinema        Terminal session recorder"
    log "    - micro            Terminal text editor with LLM plugin"
    log "    - context          Terminal history extractor"
    log ""
    log "Shell integration: $SCRIPT_DIR/integration/"
    log ""
    log "Next steps:"
    log "  1. Restart your shell or run: source ~/.bashrc (or ~/.zshrc)"
    log "  2. Test llm: llm 'Hello, how are you?'"
    log "  3. Test Claude Code Router: routed-claude"
    log ""
    log "To install everything: ./install-llm-tools.sh --full"
else
    log "Installed tools (level 1 — minimal):"
    log ""
    log "  Core LLM:"
    log "    - llm              Simon Willison's LLM CLI tool"
    log ""
    log "  LLM Plugins:"
    log "    - Providers: gemini, vertex"
    log "    - Tools: sandboxed-shell, sandboxed-python, patch, quickjs, sqlite"
    log "    - Tools: context, google-search, web-fetch, fabric, mcp, rag, skills"
    log "    - Fragments: pdf, github, youtube-transcript, site-text, dir"
    log "    - Utilities: cmd, cmd-comp, jq, git-commit, sort, classify"
    log ""
    log "  CLI Utilities:"
    log "    - gitingest        Git repository to LLM-friendly text"
    log "    - llm-server       OpenAI-compatible HTTP wrapper (if systemd detected)"
    log ""
    log "Next steps:"
    log "  1. Test llm: llm 'Hello, how are you?'"
    log ""
    log "To install all tools: ./install-llm-tools.sh --full"
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
