#!/bin/zsh
#
# LLM Command Completion for Zsh
# Provides AI-powered command completion using Ctrl+N
# and tab completion for llm commands
#

# Source common configuration
source "${0:A:h}/llm-common.sh"

# Set up llm tab completion

# Add the completion directory to fpath
#fpath=("${0:A:h}/llm-zsh-plugin/completions" $fpath)

# Initialize completion system if not already done
#autoload -Uz compinit
#compinit -i

# Define the command completion widget
__llm_cmdcomp() {
  local old_cmd=$BUFFER
  local cursor_pos=$CURSOR # Store original cursor position

  # Optional: Show a temporary indicator while processing
  BUFFER+=" ⌛"
  zle -I && zle redisplay
  BUFFER=$old_cmd # Restore original buffer before executing llm cmdcomp

  # Clear the current line and move to a new line for llm's output if any
  # (This is good practice if llm cmdcomp itself might print status messages)
  echo

  # Call llm cmdcomp to get the suggested command
  local result=$(command llm cmdcomp "$old_cmd")

  if [ $? -eq 0 ] && [ -n "$result" ]; then
    # If a command is returned successfully and is not empty:
    BUFFER=$result         # Replace the current buffer with the suggested command
    CURSOR=${#BUFFER}      # Move cursor to the end of the new command
    #zle accept-line        # Execute the command immediately
  else
    # If no command is returned, or an error occurred:
    BUFFER=$old_cmd        # Restore the original buffer content
    CURSOR=$cursor_pos     # Restore the original cursor position
    #BUFFER=""              # Clear the buffer completely
    #CURSOR=0               # Reset cursor to the beginning
    #zle reset-prompt       # Refresh the prompt to show the original command
  fi
  zle reset-prompt       # Refresh the prompt to show the original command
}

# Register the widget
zle -N __llm_cmdcomp

# Bind Ctrl+N to the widget
bindkey '^N' __llm_cmdcomp
