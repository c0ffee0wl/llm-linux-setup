#!/bin/bash
#
# LLM Shell Integration for Bash
# - Ctrl+N: AI-powered command completion
# - Ctrl+G: Apply suggested command from llm-assistant
#

# Source common configuration
source "$(dirname "${BASH_SOURCE[0]}")/llm-common.sh"

# Exclude @ and llm-inlineassistant commands from shell history
HISTIGNORE="${HISTIGNORE:+$HISTIGNORE:}@ *:llm-inlineassistant *"

# Bind Ctrl+N to the LLM command completion
bind -x '"\C-n": __llm_cmdcomp'

# Bind Ctrl+G to apply suggested command from llm-assistant
bind -x '"\C-g": __llm_apply_suggest'

__llm_cmdcomp() {
    local old_cmd="${READLINE_LINE}"
    local cursor_pos="${READLINE_POINT}"
    local result

    echo

    if result="$(command llm cmdcomp "${old_cmd}")"; then
        READLINE_LINE="${result}"
        READLINE_POINT="${#result}"
        # Move down a line to prevent bash from overwriting output
        echo
    else
        READLINE_LINE="${old_cmd}"
        READLINE_POINT="${cursor_pos}"
        echo "Command completion failed" >&2
    fi
}

__llm_apply_suggest() {
    local cmd
    cmd="$(__llm_read_suggest)" || return
    if [[ -n "$cmd" ]]; then
        READLINE_LINE="$cmd"
        READLINE_POINT="${#cmd}"
    fi
}
