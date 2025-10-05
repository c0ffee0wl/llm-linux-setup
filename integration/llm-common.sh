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
        "openai" "gemini" "openrouter"
        "cmd" "cmdcomp" "jq"
    )

    # Check if first argument is an excluded subcommand
    local first_arg="$1"
    for cmd in "${exclude_commands[@]}"; do
        if [ "$first_arg" = "$cmd" ]; then
            # Pass through directly without template
            command llm "$@"
            return $?
        fi
    done

    # For prompt commands (prompt, chat, cmd, cmdcomp, jq) or default prompt
    # apply the assistant template
    if [ "$1" = "chat" ]; then
        # Remove "chat" from arguments and call with assistant template
        shift
        command llm chat -t assistant "$@"
    else
        # Default behavior with assistant template (includes default prompt command)
        command llm -t assistant "$@"
    fi
}

# Alias for Claude Code Router
alias routed-claude='ccr code'

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

# Use indirect expansion to check if this specific session is already recording
# Note: ${!SESSION_MARKER} expands to the value of the variable named by $SESSION_MARKER
if [[ $- == *i* && -z "${!SESSION_MARKER}" ]]; then
  # Mark this specific session as recording
  export "$SESSION_MARKER=1"

  # Create log directory and define filename with pane identifier
  mkdir -p "$SESSION_LOG_DIR"
  export SESSION_LOG_FILE="$SESSION_LOG_DIR/$(date +"%Y-%m-%d_%H-%M-%S-%3N")_$$${PANE_SUFFIX}.cast"

  # Show environment variable export command
  echo "Session is logged for 'context'. To query this session in another terminal, execute there:"
  echo "export SESSION_LOG_FILE='$SESSION_LOG_FILE'"
  echo ""

  # Replace current shell with asciinema process
  exec asciinema rec "$SESSION_LOG_FILE" --quiet
fi
