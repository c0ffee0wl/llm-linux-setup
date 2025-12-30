#!/bin/zsh
#
# LLM Shell Integration for Zsh
# - Ctrl+N: AI-powered command completion
# - Ctrl+G: Apply suggested command from llm-assistant
# - Smart @: LLM mode when @ is at line start, literal @ elsewhere
# - Tab completion for @ /slash-commands and @fragments
#

# Source common configuration
source "${0:A:h}/llm-common.sh"

# Exclude @ and llm-inlineassistant commands from shell history
if [[ -n "$HISTORY_IGNORE" ]]; then
    HISTORY_IGNORE="(${HISTORY_IGNORE//[()]/}|@ *|llm-inlineassistant *)"
else
    HISTORY_IGNORE="(@ *|llm-inlineassistant *)"
fi

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
  # Apply suggested command from llm-assistant's suggest_command tool
  local suggest_file="/tmp/llm-assistant-$(id -u)/suggest"

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

# Smart @ widget: LLM mode at line start, literal @ elsewhere
__llm_smart_at() {
  local before="${BUFFER:0:$CURSOR}"

  # Check if buffer is empty or contains only whitespace before cursor
  if [[ -z "${before// /}" ]]; then
    # @ at line start - enter LLM mode
    BUFFER="@ "
    CURSOR=2
    zle -M "LLM inline-assistant: Tab for completions, Enter to send"
  else
    # @ elsewhere - insert literal @
    BUFFER="${before}@${BUFFER:$CURSOR}"
    CURSOR=$((CURSOR + 1))
  fi
}
zle -N __llm_smart_at
bindkey '@' __llm_smart_at

# Tab completion for @ prefix commands
_llm_at_complete() {
  local prefix="${words[2,-1]}"

  # Get completions from daemon
  local -a completions
  local line
  while IFS=$'\t' read -r text desc; do
    if [[ -n "$text" ]]; then
      if [[ -n "$desc" ]]; then
        completions+=("${text}:${desc}")
      else
        completions+=("$text")
      fi
    fi
  done < <(llm-inlineassistant --complete "$prefix" 2>/dev/null)

  if (( ${#completions} > 0 )); then
    _describe 'llm completions' completions
  fi
}

# Register @ as a command with its own completions
# Only do this if compdef is available (completion system loaded)
if (( $+functions[compdef] )); then
  compdef _llm_at_complete @
fi
