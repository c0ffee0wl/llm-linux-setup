# LLM plugin for Oh My Zsh
# Provides completion support for the LLM CLI tool fork with markdown rendering
# Fork: https://github.com/c0ffee0wl/llm | Docs: https://llm.datasette.io/

# Add the completion script to the Zsh function path
fpath=(${ZSH_CUSTOM:-$ZSH/custom}/plugins/llm/completions $fpath)

# Check if the llm command exists
if ! command -v llm &>/dev/null; then
  echo "Warning: llm command not found. Please install from https://github.com/c0ffee0wl/llm"
fi

# Initialize completion
autoload -U compinit
compinit