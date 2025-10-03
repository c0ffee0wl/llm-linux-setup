#!/bin/bash
#
# Common LLM Configuration
# Shared settings for both Bash and Zsh
#

# Ensure llm tools are in PATH
export PATH="$HOME/.local/bin:$PATH"

# Ensure cargo/rust tools are in PATH
export PATH="$HOME/.cargo/bin:$PATH"

# Custom llm wrapper function to set default template
llm() {
    # Check if the first argument is exactly "chat"
    if [ "$1" = "chat" ]; then
        # Remove "chat" from arguments and call with assistant template
        shift
        command llm chat -t assistant "$@"
    else
        # Default behavior with assistant template
        command llm -t assistant "$@"
    fi
}

# Alias for Claude Code Router
alias azure-claude='ccr code'

# -- Automatic asciinema session recording --
# Only run if this is an interactive shell and we're not already in asciinema
if [[ -o interactive && -z "$IN_ASCIINEMA_SESSION" ]]; then
  # Set variable to prevent recursion
  export IN_ASCIINEMA_SESSION=1

  # Create log directory and define filename
  LOG_DIR="/tmp/session_logs/asciinema"
  mkdir -p "$LOG_DIR"
  CAST_FILE="$LOG_DIR/$(date +"%Y-%m-%d_%H-%M-%S")_$$.cast"

  # Store cast file location for context script to find
  export ASCIINEMA_CAST_FILE="$CAST_FILE"

  echo "INFO: Shell session is being recorded with asciinema. Cast file: $CAST_FILE"

  # Replace current shell with asciinema process
  exec asciinema rec "$CAST_FILE" --quiet
fi
