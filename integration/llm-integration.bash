#!/bin/bash
#
# LLM Shell Integration for Bash
# - Ctrl+N: AI-powered command completion
# - Ctrl+G: Apply suggested command from llm-inlineassistant
#

# Source common configuration
source "$(dirname "${BASH_SOURCE[0]}")/llm-common.sh"

# Exclude @ and llm-inlineassistant commands from shell history
HISTIGNORE="${HISTIGNORE:+$HISTIGNORE:}@ *:llm-inlineassistant *"

# Bind Ctrl+N to the LLM command completion
bind -x '"\C-n": __llm_cmdcomp'

# Bind Ctrl+G to apply suggested command from llm-inlineassistant
bind -x '"\C-g": __llm_apply_suggest'

__llm_cmdcomp() {
    # Store the current command line
    local old_cmd="${READLINE_LINE}"
    local cursor_pos="${READLINE_POINT}"
    local result

    # Move to a new line
    echo

    # Get the LLM completion
    if result="$(command llm cmdcomp "${old_cmd}")"; then
        # Replace the command line with the result
        READLINE_LINE="${result}"
        READLINE_POINT="${#result}"
        # Move down a line to prevent bash from overwriting output
        echo
    else
        # Restore original command on error
        READLINE_LINE="${old_cmd}"
        READLINE_POINT="${cursor_pos}"
        echo "Command completion failed" >&2
    fi
}

__llm_apply_suggest() {
    # Apply suggested command from llm-inlineassistant's suggest_command tool
    local suggest_file="/tmp/llm-inlineassistant-$(id -u)/suggest"

    if [[ -f "$suggest_file" ]]; then
        local cmd
        cmd="$(cat "$suggest_file")"
        rm -f "$suggest_file"

        if [[ -n "$cmd" ]]; then
            READLINE_LINE="$cmd"
            READLINE_POINT="${#cmd}"
        fi
    fi
}
