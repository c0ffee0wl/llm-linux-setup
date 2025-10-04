#!/bin/bash
#
# Common LLM Configuration
# Shared settings for both Bash and Zsh

# Ensure llm tools are in PATH
export PATH="$HOME/.local/bin:$PATH"

# Ensure cargo/rust tools are in PATH
export PATH="$HOME/.cargo/bin:$PATH"

# Configure terminal session log directory (can be overridden before sourcing this file)
export TERMINAL_LOG_DIR="${TERMINAL_LOG_DIR:-/tmp/session_logs/asciinema}"

# Custom llm wrapper function to set default template
llm() {
    # Check for help flags - pass through to original llm
    if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
        command llm "$@"
        return $?
    fi

    # List of subcommands that should NOT get the -t template parameter
    # These are management/configuration commands, not prompt commands
    local exclude_commands=(
        "models" "keys" "plugins" "templates" "tools" "schemas" "fragments"
        "embed" "embed-models" "embed-multi" "collections" "similar"
        "logs" "aliases" "install" "uninstall"
        "openai" "gemini" "openrouter"
        "cmd" "cmdcomp"
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
alias azure-claude='ccr code'

# -- Automatic asciinema session recording --
# Only run if this is an interactive shell and we're not already in asciinema
# NOTE: In tmux/screen, each pane/window gets its own recording (intentional - separate workflows = separate contexts)
if [[ -o interactive && -z "$IN_ASCIINEMA_SESSION" ]]; then
  # Set variable to prevent recursion
  export IN_ASCIINEMA_SESSION=1

  # Create log directory and define filename
  mkdir -p "$TERMINAL_LOG_DIR"
  export TERMINAL_LOG_FILE="$TERMINAL_LOG_DIR/$(date +"%Y-%m-%d_%H-%M-%S-%3N")_$$.cast"

  # Show environment variable export command
  echo "Session is logged for 'context'. To query this session in another terminal, execute there:"
  echo "export TERMINAL_LOG_FILE='$TERMINAL_LOG_FILE'"
  echo ""

  # Replace current shell with asciinema process
  exec asciinema rec "$TERMINAL_LOG_FILE" --quiet
fi
