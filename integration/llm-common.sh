#!/bin/bash
#
# Common LLM Configuration
# Shared settings for both Bash and Zsh

# Ensure llm tools are in PATH
export PATH="$HOME/.local/bin:$PATH"

# Ensure cargo/rust tools are in PATH
export PATH="$HOME/.cargo/bin:$PATH"

# Configure terminal session log directory (can be overridden before sourcing this file)
export SESSION_LOG_DIR="${SESSION_LOG_DIR:-/tmp/session_logs/asciinema}"

# Custom llm wrapper function to set default template
llm() {
    # Check for help flags - pass through to original llm
    if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ "$1" = "--version" ]; then
        command llm "$@"
        return $?
    fi

    # List of subcommands that should NOT get the -t template parameter
    # These are management/configuration commands, not prompt commands
    local exclude_commands=(
        "models" "keys" "plugins" "templates" "tools" "schemas" "fragments"
        "collections" "embed" "embed-models" "embed-multi" "similar"
        "aliases" "logs" "install" "uninstall"
        "openai" "gemini" "openrouter" "vertex"
        "cmd" "cmdcomp" "jq" "rag"
    )

    # Check if first argument is an excluded subcommand
    local first_arg="$1"

    # Special handling for RAG subcommand - route to aichat
    if [ "$first_arg" = "rag" ]; then
        shift  # Remove 'rag' from arguments

        if ! command -v aichat &> /dev/null; then
            echo "Error: aichat is not installed. RAG functionality requires aichat." >&2
            echo "Run the installation script to install aichat." >&2
            return 1
        fi

        # If no arguments, show error (RAG name is required)
        if [ $# -eq 0 ]; then
            echo "Error: RAG name required. Usage: llm rag <name>" >&2
            echo "For interactive mode without RAG, use: aichat" >&2
            return 1
        fi

        # Pass all arguments to aichat with --rag flag
        aichat --rag "$@"
        return $?
    fi

    for cmd in "${exclude_commands[@]}"; do
        if [ "$first_arg" = "$cmd" ]; then
            # Pass through directly without template
            command llm "$@"
            return $?
        fi
    done

    # Helper function to check if template should be skipped
    should_skip_template() {
        for arg in "$@"; do
            case "$arg" in
                -c|--continue|--cid|--conversation)
                    return 0  # Continuing conversation (has context)
                    ;;
                -s*|--system*|--sf)
                    return 0  # Custom system prompt
                    ;;
                -t*|--template*)
                    return 0  # Custom template
                    ;;
            esac
        done
        return 1  # Add template
    }

    # For prompt commands (prompt, chat, code) or default prompt
    # apply the appropriate template unless user specified their own
    if [ "$1" = "chat" ]; then
        shift
        if should_skip_template "$@"; then
            command llm chat --cl 15 "$@"
        else
            command llm chat -t assistant --cl 15 "$@"
        fi
    elif [ "$1" = "code" ]; then
        shift
        command llm -t code --cl 15 "$@"
    else
        if should_skip_template "$@"; then
            command llm --cl 15 "$@"
        else
            command llm -t assistant --cl 15 "$@"
        fi
    fi
}

# Alias for Claude Code Router
alias routed-claude='ccr code'

# Linux version of macOS pbcopy and pbpaste
alias pbcopy='xsel --clipboard --input'
alias pbpaste='xsel --clipboard --output'

# VTE Integration for directory tracking in terminal emulators
# Enables proper working directory preservation when opening new tabs/windows
# Supports: GNOME Terminal, Terminator, Tilix, Guake, and other VTE-based terminals
# Works by emitting OSC 7 escape sequences that asciinema transparently passes through
if [ -n "$VTE_VERSION" ]; then
  for vte_script in /etc/profile.d/vte.sh /etc/profile.d/vte-*.sh; do
    if [ -f "$vte_script" ]; then
      . "$vte_script"
      break
    fi
  done
fi

# -- Automatic asciinema session recording --
# Only run if this is an interactive shell and we're not already in asciinema
# NOTE: In tmux/screen, each pane/window gets its own recording (intentional - separate workflows = separate contexts)

# Determine unique session identifier based on multiplexer type
# NOTE: Only tmux needs special handling due to environment variable inheritance between panes
# Screen windows are isolated and don't need pane-specific markers
if [ -n "$TMUX_PANE" ]; then
  # In tmux, use pane ID (e.g., "%0", "%1", "%2")
  # Clean it for use in variable name (remove % and other special chars)
  PANE_ID=$(echo "$TMUX_PANE" | tr -cd '[:alnum:]')
  SESSION_MARKER="IN_ASCIINEMA_SESSION_tmux_${PANE_ID}"
  PANE_SUFFIX="_tmux${PANE_ID}"
else
  # Default for regular terminals and screen (no special handling needed)
  SESSION_MARKER="IN_ASCIINEMA_SESSION"
  PANE_SUFFIX=""
fi

# Check if this specific session is already recording
# Use eval for bash/zsh compatibility (bash uses ${!var}, zsh uses ${(P)var})
eval "is_recording=\${$SESSION_MARKER}"
if [[ $- == *i* && -z "$is_recording" ]]; then
  # Mark this specific session as recording
  export "$SESSION_MARKER=1"

  # Create log directory and define filename with pane identifier
  mkdir -p "$SESSION_LOG_DIR"
  export SESSION_LOG_FILE="$SESSION_LOG_DIR/$(date +"%Y-%m-%d_%H-%M-%S-%3N")_$$${PANE_SUFFIX}.cast"

  # Show environment variable export command (unless SESSION_LOG_SILENT is set)
  if [ "$SESSION_LOG_SILENT" != "true" ] && [ "$SESSION_LOG_SILENT" != "1" ]; then
    echo "Session is logged for 'context'. To query this session in another terminal, execute there:"
    echo "export SESSION_LOG_FILE='$SESSION_LOG_FILE'"
    echo ""
  fi

  # Replace current shell with asciinema process
  exec asciinema rec "$SESSION_LOG_FILE" --quiet
fi
