#!/bin/zsh
#
# LLM Shell Integration for Zsh
# - Ctrl+N: AI-powered command completion
# - Ctrl+G: Apply suggested command from llm-shell
#

# Source common configuration
source "${0:A:h}/llm-common.sh"

# Define the command completion widget
__llm_cmdcomp() {
  local old_cmd=$BUFFER
  local cursor_pos=$CURSOR

  # Show a temporary indicator while processing
  BUFFER+=" ⌛"
  zle -I && zle redisplay
  BUFFER=$old_cmd

  # Move to a new line for llm's output
  echo

  # Call llm cmdcomp to get the suggested command
  local result=$(command llm cmdcomp "$old_cmd")

  if [ $? -eq 0 ] && [ -n "$result" ]; then
    BUFFER=$result
    CURSOR=${#BUFFER}
  else
    BUFFER=$old_cmd
    CURSOR=$cursor_pos
  fi
  zle reset-prompt
}

# Define the apply suggestion widget
__llm_apply_suggest() {
  # Apply suggested command from llm-shell's suggest_command tool
  local suggest_file="/tmp/llm-shell-$(id -u)/suggest"

  if [[ -f "$suggest_file" ]]; then
    local cmd
    cmd="$(cat "$suggest_file")"
    rm -f "$suggest_file"

    if [[ -n "$cmd" ]]; then
      BUFFER="$cmd"
      CURSOR=${#BUFFER}
    fi
  fi
  zle reset-prompt
}

# Register the widgets
zle -N __llm_cmdcomp
zle -N __llm_apply_suggest

# Bind Ctrl+N to command completion
bindkey '^N' __llm_cmdcomp

# Bind Ctrl+G to apply suggested command
bindkey '^G' __llm_apply_suggest
