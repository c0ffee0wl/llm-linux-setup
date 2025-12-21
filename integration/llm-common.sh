#!/bin/bash
#
# Common LLM Configuration
# Shared settings for both Bash and Zsh

# Configure terminal session log directory (can be overridden before sourcing this file)
export SESSION_LOG_DIR="${SESSION_LOG_DIR:-/tmp/session_logs/asciinema}"

# Disable Claude Code auto-update checker
export DISABLE_AUTOUPDATER=1

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
        "cmd" "cmdcomp" "jq"
        "rag" "git-commit" "sort" "consortium" "classify"
    )

    # Check if first argument is an excluded subcommand
    local first_arg="$1"

    # Special handling for chat-google-search (Google Search grounding)
    if [ "$first_arg" = "chat-google-search" ]; then
        shift  # Remove 'chat-google-search' from arguments
        local keys_file="$HOME/.config/io.datasette.llm/keys.json"
        if [ -f "$keys_file" ]; then
            local model=""
            if grep -q '"vertex"' "$keys_file" 2>/dev/null; then
                model="vertex/gemini-2.5-flash"
            elif grep -q '"gemini"' "$keys_file" 2>/dev/null; then
                model="gemini-2.5-flash"
            fi
            if [ -n "$model" ]; then
                command llm chat -t llm --cl 15 -m "$model" -o google_search 1 --md "$@"
                return $?
            fi
        fi
        echo "Error: chat-google-search requires Gemini or Vertex to be configured" >&2
        return 1
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

    # Helper function to check if google_search option is present (incompatible with non-search tools)
    has_google_search() {
        local skip_next=false
        for arg in "$@"; do
            if $skip_next; then
                if [[ "$arg" == *"google_search"* ]]; then
                    return 0  # google_search found
                fi
                skip_next=false
                continue
            fi
            case "$arg" in
                -o|--option)
                    skip_next=true
                    ;;
                -o*google_search*|--option*google_search*)
                    return 0  # google_search found (combined flag format)
                    ;;
            esac
        done
        return 1  # no google_search
    }

    # For prompt commands (prompt, chat, code) or default prompt
    # apply the appropriate template unless user specified their own
    # Default tools (context, sandboxed_shell) are added only when:
    # 1. Using default assistant template (no -t, -s, -c flags)
    # 2. AND google_search option is not present (incompatible with non-search tools)
    if [ "$1" = "chat" ]; then
        shift
        if should_skip_template "$@"; then
            # User specified -t, -s, or -c: no default template, no default tools
            command llm chat --cl 15 "$@"
        elif has_google_search "$@"; then
            # google_search incompatible with non-search tools: template only, no tools
            command llm chat -t llm --cl 15 "$@"
        else
            # Default: apply assistant template with default tools
            command llm chat -t llm --cl 15 --tool context --tool sandboxed_shell --tool execute_python "$@"
        fi
    elif [ "$1" = "code" ]; then
        shift
        command llm -t llm-code --cl 15 "$@"
    elif [ "$1" = "assistant" ]; then
        shift
        llm-assistant "$@"
    elif [ "$1" = "sidechat" ]; then
        echo "Warning: 'llm sidechat' is deprecated, use 'llm assistant' instead" >&2
        shift
        llm-assistant "$@"
    else
        if should_skip_template "$@"; then
            command llm --cl 15 "$@"
        elif has_google_search "$@"; then
            command llm -t llm --cl 15 "$@"
        else
            command llm -t llm --cl 15 --tool context --tool sandboxed_shell --tool execute_python "$@"
        fi
    fi
}

# wut function - Explains terminal command output using context tool
# Usage: wut                          -> Explains last command output
#        wut "how do I fix this?"     -> Answers specific question about last command
wut() {
    if ! command -v llm &> /dev/null; then
        echo "Error: llm is not installed. Install it with the installation script." >&2
        return 1
    fi

    # If no arguments provided, ask for explanation of last command
    if [ $# -eq 0 ]; then
        command llm -t llm-wut "Explain the output of my last command" --md
    else
        # Pass user's question to llm with context tool
        command llm -t llm-wut "$*" --md
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

# Invisible Unicode markers for 100% reliable prompt detection in VTE terminals
# ONLY inject in VTE terminals (Terminator, GNOME Terminal, Tilix, etc.)
# Other terminals (Kitty) render zero-width chars as visible spaces
# These markers are detected by llm-assistant via PromptDetector.has_unicode_markers()
if [ -n "$VTE_VERSION" ]; then
    # \u200B = Zero Width Space, \u200D = Zero Width Joiner
    _PROMPT_START_MARKER=$'\u200B\u200D\u200B'  # Before PS1
    _INPUT_START_MARKER=$'\u200D\u200B\u200D'   # After PS1

    # Pre-computed tag characters for metadata encoding (U+E0000 + ASCII code)
    # These are invisible in terminal but survive VTE text capture
    # Only the chars we use: E, T, 0-9, space, dash, colon
    # Computed at parse time via $'\U...' - zero runtime cost
    _TAG_E=$'\U000E0045'    # 'E' for Exit
    _TAG_T=$'\U000E0054'    # 'T' for Timestamp
    _TAG_0=$'\U000E0030'    # '0'
    _TAG_1=$'\U000E0031'    # '1'
    _TAG_2=$'\U000E0032'    # '2'
    _TAG_3=$'\U000E0033'    # '3'
    _TAG_4=$'\U000E0034'    # '4'
    _TAG_5=$'\U000E0035'    # '5'
    _TAG_6=$'\U000E0036'    # '6'
    _TAG_7=$'\U000E0037'    # '7'
    _TAG_8=$'\U000E0038'    # '8'
    _TAG_9=$'\U000E0039'    # '9'
    _TAG_SPACE=$'\U000E0020'  # ' '
    _TAG_DASH=$'\U000E002D'   # '-'
    _TAG_COLON=$'\U000E003A'  # ':'

    # Fast tag encoding using pre-computed lookup (no subshells!)
    # Only handles chars used in "E<exit>T<YYYY-MM-DD HH:MM:SS>" format
    __encode_tags() {
        local result=""
        local str="$1"
        while [ -n "$str" ]; do
            case "${str%"${str#?}"}" in
                E) result="${result}${_TAG_E}" ;;
                T) result="${result}${_TAG_T}" ;;
                0) result="${result}${_TAG_0}" ;;
                1) result="${result}${_TAG_1}" ;;
                2) result="${result}${_TAG_2}" ;;
                3) result="${result}${_TAG_3}" ;;
                4) result="${result}${_TAG_4}" ;;
                5) result="${result}${_TAG_5}" ;;
                6) result="${result}${_TAG_6}" ;;
                7) result="${result}${_TAG_7}" ;;
                8) result="${result}${_TAG_8}" ;;
                9) result="${result}${_TAG_9}" ;;
                ' ') result="${result}${_TAG_SPACE}" ;;
                -) result="${result}${_TAG_DASH}" ;;
                :) result="${result}${_TAG_COLON}" ;;
            esac
            str="${str#?}"
        done
        printf '%s' "$result"
    }

    # Unified marker function with metadata injection
    # Called via PROMPT_COMMAND (bash) or precmd_functions (zsh)
    # CRITICAL: Must APPEND to run LAST (after Starship/Powerlevel10k modify PS1)
    __add_prompt_markers() {
        local last_exit=$?  # MUST be first line to capture exit code

        # Print invisible metadata BEFORE prompt (fresh timestamp each time)
        # Format: E<exit>T<YYYY-MM-DD HH:MM:SS> encoded as tag characters
        printf '%s' "$(__encode_tags "E${last_exit}T$(date '+%Y-%m-%d %H:%M:%S')")"

        # Add markers to PS1/PROMPT (idempotent - only once)
        if [ -n "${BASH_VERSION:-}" ]; then
            if [[ "$PS1" != *$'\u200B\u200D\u200B'* ]]; then
                PS1="${_PROMPT_START_MARKER}${PS1}${_INPUT_START_MARKER}"
            fi
        elif [ -n "${ZSH_VERSION:-}" ]; then
            if [[ "$PROMPT" != *$'\u200B\u200D\u200B'* ]]; then
                PROMPT="${_PROMPT_START_MARKER}${PROMPT}${_INPUT_START_MARKER}"
            fi
        fi
    }

    # Register with shell's prompt hook
    if [ -n "${BASH_VERSION:-}" ]; then
        # APPEND to run LAST (after Starship/Powerlevel10k)
        PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND; }__add_prompt_markers"
    elif [ -n "${ZSH_VERSION:-}" ]; then
        # APPEND to run LAST in precmd_functions array
        precmd_functions=($precmd_functions __add_prompt_markers)
    fi
fi

# -- Automatic asciinema session recording --
if command -v asciinema &> /dev/null; then
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
    # Test if asciinema can actually record in this environment
    # This prevents shell initialization failures in chroot/rescue environments
    if asciinema rec -c "true" /dev/null --quiet 2>/dev/null; then
      # Success - asciinema works, set up recording
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
    else
      # Failure - cannot create pty in this environment (chroot/rescue/restricted)
      # Always show warning (ignore SESSION_LOG_SILENT for errors)
      echo "Warning: Session recording disabled (cannot create pty in this environment)" >&2
    fi
  fi
else
  if [ "$SESSION_LOG_SILENT" != "true" ] && [ "$SESSION_LOG_SILENT" != "1" ]; then
    echo "Warning: asciinema not found. Session recording disabled." >&2
    echo "Run the installation script to install asciinema and enable the 'context' tool." >&2
  fi
  # Skip recording - asciinema not available
fi
