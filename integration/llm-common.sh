#!/bin/bash
#
# Common LLM Configuration
# Shared settings for both Bash and Zsh

# Configure terminal session log directory (can be overridden before sourcing this file)
export SESSION_LOG_DIR="${SESSION_LOG_DIR:-/tmp/session_logs/asciinema}"

# Privacy/telemetry defaults are set in ~/.profile (see install-llm-tools.sh)

# Default --cl (context limit) for llm wrapper — centralised so bumps are one-line
LLM_CONTEXT_LIMIT="${LLM_CONTEXT_LIMIT:-15}"

# Helper: check if user supplied flags that suppress the default template
__llm_should_skip_template() {
    for arg in "$@"; do
        case "$arg" in
            -c|--continue|--cid|--conversation) return 0 ;;
            -s*|--system*|--sf)                 return 0 ;;
            -t*|--template*)                    return 0 ;;
        esac
    done
    return 1
}

# Helper: detect -o google_search (incompatible with non-search tools)
__llm_has_google_search() {
    local skip_next=false
    for arg in "$@"; do
        if $skip_next; then
            [[ "$arg" == *"google_search"* ]] && return 0
            skip_next=false
            continue
        fi
        case "$arg" in
            -o|--option) skip_next=true ;;
            -o*google_search*|--option*google_search*) return 0 ;;
        esac
    done
    return 1
}

# Run `command llm [subcmd] …` applying the default template + tools unless the
# user supplied their own -t/-s/-c or -o google_search (incompatible with tools).
__llm_dispatch() {
    local -a prefix=()
    [ -n "$1" ] && prefix=("$1")
    shift
    if __llm_should_skip_template "$@"; then
        command llm "${prefix[@]}" --cl "$LLM_CONTEXT_LIMIT" "$@"
    elif __llm_has_google_search "$@"; then
        command llm "${prefix[@]}" -t llm --cl "$LLM_CONTEXT_LIMIT" "$@"
    else
        command llm "${prefix[@]}" -t llm --cl "$LLM_CONTEXT_LIMIT" \
            --tool context --tool sandboxed_shell --tool execute_python "$@"
    fi
}

# Custom llm wrapper function to set default template
llm() {
    # Check for help flags - pass through to original llm
    if [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ "$1" = "--version" ]; then
        command llm "$@"
        return $?
    fi

    local first_arg="$1"

    # Special handling for chat-google-search (Google Search grounding)
    if [ "$first_arg" = "chat-google-search" ]; then
        shift  # Remove 'chat-google-search' from arguments
        local keys_file="$HOME/.config/io.datasette.llm/keys.json"
        if [ -f "$keys_file" ]; then
            local keys_content model=""
            keys_content="$(<"$keys_file")" 2>/dev/null || keys_content=""
            if [[ "$keys_content" == *'"vertex"'* ]]; then
                model="vertex/gemini-2.5-flash"
            elif [[ "$keys_content" == *'"gemini"'* ]]; then
                model="gemini-2.5-flash"
            fi
            if [ -n "$model" ]; then
                command llm chat -t llm --cl "$LLM_CONTEXT_LIMIT" -m "$model" -o google_search 1 --md "$@"
                return $?
            fi
        fi
        echo "Error: chat-google-search requires Gemini or Vertex to be configured" >&2
        return 1
    fi

    # Management/configuration subcommands that must not receive -t/template:
    # pass straight through to the real `llm` binary.
    case "$first_arg" in
        models|keys|plugins|templates|tools|schemas|fragments|\
        collections|embed|embed-models|embed-multi|similar|\
        aliases|logs|install|uninstall|\
        openai|gemini|openrouter|vertex|\
        cmd|cmdcomp|jq|\
        rag|git-commit|sort|classify|\
        arxiv|arxiv-search)
            command llm "$@"
            return $?
            ;;
    esac

    # Prompt commands (chat, code, default): apply the appropriate template
    # unless the user specified their own (-t/-s/-c). Default tools are added
    # only when the default template is used AND -o google_search is absent
    # (google_search is incompatible with non-search tools).
    case "$1" in
        chat)
            shift
            __llm_dispatch chat "$@"
            ;;
        code)
            shift
            command llm -t llm-code --cl "$LLM_CONTEXT_LIMIT" "$@"
            ;;
        sidechat)
            # `;&` falls through into the `assistant` body (bash & zsh support this).
            echo "Warning: 'llm sidechat' is deprecated, use 'llm assistant' instead" >&2
            ;&
        assistant)
            shift
            llm-assistant "$@"
            ;;
        *)
            __llm_dispatch "" "$@"
            ;;
    esac
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

# @ function - Shell-native AI assistant with conversation continuity
# Usage: @ What's new about space travel?
#        @ Tell me more                    -> Continues conversation
#        @ /new                            -> Start fresh conversation
#        @ /help                           -> Show commands
#
# Terminal session ID resolution is delegated to llm_tools_core.get_terminal_session_id()
# (called by llm-inlineassistant). Exported env vars like SESSION_LOG_FILE / TMUX_PANE
# are inherited by the subprocess, so duplicating the priority ladder here is dead work.
@() {
    if ! command -v llm-inlineassistant &> /dev/null; then
        echo "Error: llm-inlineassistant is not installed. Run install-llm-tools.sh to install." >&2
        return 1
    fi

    # Pass query via stdin to avoid shell parsing of special characters
    # (matches zsh widget approach: printf | --stdin)
    if [ $# -eq 0 ]; then
        llm-inlineassistant
    else
        printf '%s' "$*" | llm-inlineassistant --stdin
    fi
}

# Read and consume a queued suggestion from llm-assistant's suggest_command tool.
# Writes the command to stdout and deletes the file; returns non-zero if absent.
# Path is the daemon↔widget contract — must match what llm-assistant writes.
__llm_read_suggest() {
    local suggest_file="/tmp/llm-assistant-${UID}/suggest"
    [ -f "$suggest_file" ] || return 1
    cat "$suggest_file"
    rm -f "$suggest_file"
}

# Alias for Claude Code Router
alias routed-claude='ccr code'

# Linux version of macOS pbcopy and pbpaste
alias pbcopy='xsel --clipboard --input'
alias pbpaste='xsel --clipboard --output'

# VTE-only integration: directory tracking via OSC 7 (for new tabs/windows to
# inherit CWD in GNOME Terminal, Terminator, Tilix, Guake, etc.) and zero-width
# Unicode markers for 100% reliable prompt detection by llm-assistant's
# PromptDetector. Markers are NOT injected in non-VTE terminals (Kitty, etc.)
# because those render zero-width chars as visible spaces.
if [ -n "$VTE_VERSION" ]; then
    for vte_script in /etc/profile.d/vte.sh /etc/profile.d/vte-*.sh; do
        if [ -f "$vte_script" ]; then
            . "$vte_script"
            break
        fi
    done

    # \u200B = Zero Width Space, \u200D = Zero Width Joiner
    _PROMPT_START_MARKER=$'\u200B\u200D\u200B'  # Before PS1
    _INPUT_START_MARKER=$'\u200D\u200B\u200D'   # After PS1

    # Temp dir for shell↔llm-assistant metadata. Resolved once at source time
    # to avoid forking $(id -u) on every prompt; __add_prompt_markers only
    # re-runs mkdir if the dir was removed (self-heal against tmpwatch).
    # Must match llm_tools_core.xdg.get_temp_dir() convention.
    _LLM_TEMP_DIR="${TMPDIR:-${TMP:-${TEMP:-/tmp}}}/llm-assistant/${UID}"

    # Preexec hook: capture command start time (uses $SECONDS - no subshell!)
    # Called BEFORE each command executes
    __capture_cmd_start() {
        _CMD_START_SECONDS=$SECONDS
    }

    # Prompt hook: write metadata and add markers
    # Called via PROMPT_COMMAND (bash) or precmd_functions (zsh)
    # CRITICAL: Must APPEND to run LAST (after Starship/Powerlevel10k modify PS1)
    # Note: Exit code is passed as $1 to avoid $? being clobbered by preceding commands
    __add_prompt_markers() {
        local last_exit=${1:-$?}  # Use passed exit code, or $? as fallback (zsh)

        # Calculate duration since command start (0 if no command ran)
        local duration=0
        if [ -n "$_CMD_START_SECONDS" ]; then
            duration=$((SECONDS - _CMD_START_SECONDS))
            unset _CMD_START_SECONDS
            # Protect against negative duration (clock issues, SECONDS reset)
            [ "$duration" -lt 0 ] && duration=0
        fi

        # Format timestamp via shell builtins to avoid forking `date` per prompt.
        # Fallback path keeps the hook safe on unexpected shells.
        local ts
        if [ -n "${BASH_VERSION:-}" ]; then
            printf -v ts '%(%Y-%m-%d %H:%M:%S)T' -1
        elif [ -n "${ZSH_VERSION:-}" ] && (( ${+builtins[strftime]} )); then
            strftime -s ts '%Y-%m-%d %H:%M:%S' "$EPOCHSECONDS"
        else
            ts="$(date '+%Y-%m-%d %H:%M:%S')"
        fi

        # Write metadata to temp file for llm-assistant to read
        # File is named by shell PID for disambiguation between terminals.
        # [ -d ] short-circuits mkdir (external in bash, forks) on the common
        # path — only the first prompt after tmpwatch pays the subprocess cost.
        [ -d "$_LLM_TEMP_DIR" ] || mkdir -p "$_LLM_TEMP_DIR" 2>/dev/null
        printf '%s\n' "E${last_exit}T${ts}D${duration}" > "$_LLM_TEMP_DIR/.prompt-meta-$$" 2>/dev/null

        # Add markers to PS1/PROMPT (idempotent - only once)
        if [ -n "${BASH_VERSION:-}" ]; then
            if [[ "$PS1" != *"$_PROMPT_START_MARKER"* ]]; then
                PS1="${_PROMPT_START_MARKER}${PS1}${_INPUT_START_MARKER}"
            fi
        elif [ -n "${ZSH_VERSION:-}" ]; then
            if [[ "$PROMPT" != *"$_PROMPT_START_MARKER"* ]]; then
                PROMPT="${_PROMPT_START_MARKER}${PROMPT}${_INPUT_START_MARKER}"
            fi
        fi
    }

    # DEBUG trap handler for Bash timing
    # Called BEFORE each simple command - captures start time for user commands only
    # Sets flag after capturing timer to prevent reset during PROMPT_COMMAND
    __debug_handler() {
        local last_exit=$?  # Save FIRST before any commands change it
        # Only capture start time for USER commands, not PROMPT_COMMAND internals
        if [[ -z "$_IN_PROMPT_COMMAND" ]]; then
            __capture_cmd_start
            _IN_PROMPT_COMMAND=1  # Set flag so PROMPT_COMMAND's DEBUG traps skip
        fi
        return $last_exit   # Restore $? to original value
    }

    # Zsh: Save exit code at VERY START of precmd (before Starship/P10k clobber it)
    __zsh_save_exit() {
        _LAST_EXIT=$?
    }

    # Zsh: Wrapper that passes saved exit code to __add_prompt_markers
    __zsh_add_markers() {
        __add_prompt_markers "$_LAST_EXIT"
        unset _LAST_EXIT
    }

    # Register hooks with shell
    if [ -n "${BASH_VERSION:-}" ]; then
        # DEBUG trap captures timer and sets flag for PROMPT_COMMAND
        trap '__debug_handler' DEBUG
        # Capture exit code FIRST, run hooks, then unset flag for next command
        PROMPT_COMMAND='_LAST_EXIT=$?; '"${PROMPT_COMMAND:-:}"'; __add_prompt_markers "$_LAST_EXIT"; unset _IN_PROMPT_COMMAND _LAST_EXIT'
    elif [ -n "${ZSH_VERSION:-}" ]; then
        # Load strftime / EPOCHSECONDS so __add_prompt_markers can format
        # timestamps without forking `date` on every prompt.
        zmodload -F zsh/datetime +b:strftime +b:EPOCHSECONDS 2>/dev/null
        # preexec runs BEFORE each user command - capture start time
        preexec_functions=($preexec_functions __capture_cmd_start)
        # PREPEND exit code capture to run FIRST (before Starship/P10k)
        precmd_functions=(__zsh_save_exit $precmd_functions)
        # APPEND our markers to run LAST (after Starship/P10k modify PROMPT)
        precmd_functions=($precmd_functions __zsh_add_markers)
    fi
fi

# True when the user has opted out of session-recording startup banners.
# Error warnings (pty probe failure) ignore this and always print.
__llm_session_silent() {
    [ "$SESSION_LOG_SILENT" = "true" ] || [ "$SESSION_LOG_SILENT" = "1" ]
}

# -- Automatic asciinema session recording --
if command -v asciinema &> /dev/null; then
  # Only run if this is an interactive shell and we're not already in asciinema
  # NOTE: In tmux/screen, each pane/window gets its own recording (intentional - separate workflows = separate contexts).
  # Nested multiplexers (screen inside tmux, tmux inside screen) are identified by combining both layers.

  PANE_SUFFIX=""
  if [ -n "$TMUX_PANE" ]; then
    PANE_SUFFIX+="_tmux${TMUX_PANE//[^[:alnum:]]/}"
  fi
  if [ -n "$STY" ] && [ -n "$WINDOW" ]; then
    PANE_SUFFIX+="_screen${WINDOW//[^[:alnum:]]/}"
  fi
  SESSION_MARKER="IN_ASCIINEMA_SESSION${PANE_SUFFIX}"

  # Check if this specific session is already recording
  # Use eval for bash/zsh compatibility (bash uses ${!var}, zsh uses ${(P)var})
  eval "is_recording=\${$SESSION_MARKER}"
  if [[ $- == *i* && -z "$is_recording" ]]; then
    # Probe whether asciinema can allocate a pty in this environment
    # (fails in chroot/rescue/restricted shells). Cache the result in /tmp so
    # only the first shell per boot pays the ~50–100ms fork cost; /tmp tmpfs
    # clears on reboot, so chroots entered later still get a fresh probe.
    if [ -r "/tmp/llm-asciinema-probe-${UID}" ]; then
      _asciinema_ok=$(<"/tmp/llm-asciinema-probe-${UID}")
    else
      if asciinema rec -c "true" /dev/null --quiet 2>/dev/null; then
        _asciinema_ok=1
      else
        _asciinema_ok=0
      fi
      printf '%s' "$_asciinema_ok" > "/tmp/llm-asciinema-probe-${UID}" 2>/dev/null
    fi

    if [ "$_asciinema_ok" = 1 ]; then
      unset _asciinema_ok
      export "$SESSION_MARKER=1"

      # Create log directory and define filename with pane identifier
      mkdir -p "$SESSION_LOG_DIR"
      export SESSION_LOG_FILE="$SESSION_LOG_DIR/$(date +"%Y-%m-%d_%H-%M-%S-%3N")_$$${PANE_SUFFIX}.cast"

      # Show environment variable export command (unless SESSION_LOG_SILENT is set)
      if ! __llm_session_silent; then
        echo "Session is logged for 'context'. To query this session in another terminal, execute there:"
        echo "export SESSION_LOG_FILE='$SESSION_LOG_FILE'"
        echo ""
      fi

      # Replace current shell with asciinema process
      exec asciinema rec "$SESSION_LOG_FILE" --quiet
    else
      unset _asciinema_ok
      # Always show warning (ignore SESSION_LOG_SILENT for errors)
      echo "Warning: Session recording disabled (cannot create pty in this environment)" >&2
    fi
  fi
else
  if ! __llm_session_silent; then
    echo "Warning: asciinema not found. Session recording disabled." >&2
    echo "Run the installation script to install asciinema and enable the 'context' tool." >&2
  fi
  # Skip recording - asciinema not available
fi
