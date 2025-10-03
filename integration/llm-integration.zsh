#!/bin/zsh
#
# LLM Command Completion for Zsh
# Provides AI-powered command completion using Ctrl+N
#

# Source common configuration
source "${0:A:h}/llm-common.sh"

# Define the command completion widget
__llm_cmdcomp() {
    local old_cmd=$BUFFER
    local cursor_pos=$CURSOR # Store original cursor position

    # Optional: Show a temporary indicator while processing
    BUFFER+=" ⌛"
    zle -I && zle redisplay
    BUFFER=$old_cmd # Restore original buffer before executing llm cmdcomp

    # Clear the current line and move to a new line for llm's output if any
    echo

    # Call llm cmdcomp to get the suggested command
    local result=$(command llm cmdcomp "$old_cmd" 2>/dev/null)

    if [ $? -eq 0 ] && [ -n "$result" ]; then
        # If a command is returned successfully and is not empty:
        BUFFER=$result         # Replace the current buffer with the suggested command
        CURSOR=${#BUFFER}      # Move cursor to the end of the new command
        zle accept-line        # Execute the command immediately
    else
        # If no command is returned, or an error occurred:
        BUFFER=""              # Clear the buffer completely
        CURSOR=0               # Reset cursor to the beginning
        zle reset-prompt       # Refresh the prompt
    fi
}

# Register the widget
zle -N __llm_cmdcomp

# Bind Ctrl+N to the widget
bindkey '^N' __llm_cmdcomp
