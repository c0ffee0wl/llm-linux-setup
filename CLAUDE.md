# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Information

**GitHub Repository**: https://github.com/c0ffee0wl/llm-linux-setup

## Repository Overview

This is an installation and configuration system for Simon Willison's `llm` CLI tool and related AI/LLM command-line utilities on Linux (Debian/Ubuntu/Kali). The repository consists of a main installation script and shell integration files that work together to provide a complete LLM tooling environment.

## Architecture

### Self-Updating Installation Pattern

The core design uses a **self-updating script pattern** with safe execution:

1. **Phase 0 (Self-Update)**: The script checks if it's running in a git repo, fetches updates, compares local vs remote HEAD
2. **Critical**: If updates exist, the script does `git pull` then `exec "$0" "$@"` to replace the current process with the updated script
3. This prevents the script from executing with partially-updated code mid-run

**When modifying `install-llm-tools.sh`**: The self-update logic in Phase 0 must ALWAYS run before any other operations. Never move or remove this section.

### Session Recording & Context System

The repository includes an **automatic session recording and context extraction system**:

1. **Automatic Recording** (`integration/llm-common.sh`): Interactive shells automatically start asciinema recording
   - Only triggers in interactive shells (not scripts or nested sessions)
   - Prevents recursion by checking `$IN_ASCIINEMA_SESSION` env vars
   - Stores recordings in configurable directory (default: `/tmp/session_logs/asciinema/`) via `$TERMINAL_LOG_DIR`
   - Creates timestamp-based filenames
   - Exports `$TERMINAL_LOG_FILE` for the context tool to locate the current recording

2. **Context Extraction** (`context/context`): Python script that parses asciinema recordings
   - Finds current session's `.cast` file via `$TERMINAL_LOG_FILE` or most recent file in log directory
   - Converts binary `.cast` format to text using `asciinema convert`
   - Uses regex patterns to detect shell prompts (bash `$/#`, zsh `%/❯/→/➜`, etc.)
   - Extracts commands and their outputs, returns last N commands or full session

3. **LLM Integration** (`llm-tools-context/`): LLM plugin exposing context as a tool
   - Registered as a tool that LLMs can call during conversations
   - Allows AI to query recent terminal history including command outputs
   - Usage: `llm --tool context "what did I just run?"`

**Architecture Flow**: Shell starts → asciinema records → `$TERMINAL_LOG_FILE` points to recording → `context` script parses it → `llm-tools-context` exposes it to AI

**Configuration**: The session log directory can be customized by setting `$TERMINAL_LOG_DIR` before shell integration loads (e.g., in `.bashrc` before sourcing `llm-integration.bash`). This allows persistent storage outside `/tmp` if desired.

**Behavior with Terminal Multiplexers (tmux/screen)**:
- **Each pane/window gets its own independent recording** - This is intentional design for workflow isolation
- When you create a new tmux pane or screen window, that new shell starts its own asciinema recording
- The `context` command in each pane shows only that pane's history, not other panes
- This matches the mental model: different panes = different workflows = separate contexts
- **If you want unified recording**: Start asciinema manually before launching tmux: `asciinema rec --command "tmux attach"`
- The auto-recording system does NOT prevent per-pane recordings; each new shell in a multiplexer will trigger its own recording session

### Shell Integration Architecture

The shell integration uses a **three-file pattern** located in the `integration/` subdirectory:
- `integration/llm-common.sh` - Shared configuration (PATH, env vars, aliases)
- `integration/llm-integration.bash` - Bash-specific integration (sources common, defines Bash widgets)
- `integration/llm-integration.zsh` - Zsh-specific integration (sources common, defines Zsh widgets)

Both shell files source the common file at the top. The main installation script appends source statements to `.bashrc` and `.zshrc` that reference these files by absolute path.

**When adding new shell features**:
- Shell-agnostic features → `integration/llm-common.sh`
- Bash-specific features (readline bindings) → `integration/llm-integration.bash`
- Zsh-specific features (zle widgets) → `integration/llm-integration.zsh`

### Installation Phases

The script is organized into numbered phases:

0. **Self-Update**: Git fetch/pull/exec pattern
1. **Prerequisites**: Install pipx, uv, Node.js (via apt-get from NodeSource), Rust/Cargo, asciinema
2. **LLM Core**: Install/upgrade llm, configure Azure OpenAI, create `extra-openai-models.yaml`
3. **LLM Plugins**: Install/upgrade all plugins using `llm install --upgrade`
4. **LLM Templates**: Install/update custom templates from `llm-template/` directory to `~/.config/io.datasette.llm/templates/`
5. **Shell Integration**: Add source statements to `.bashrc`/`.zshrc` (idempotent checks)
6. **Additional Tools**: Install/update repomix (npm), gitingest (pipx), files-to-prompt (uv), context script
7. **Claude Code & Router**: Install Claude Code, Claude Code Router (with Azure config), and OpenCode

### Azure OpenAI Configuration

The system is specifically configured for **Azure Foundry** (not standard OpenAI):
- Model IDs use `azure/` prefix (e.g., `azure/gpt-5-mini`)
- Configuration stored in `~/.config/io.datasette.llm/extra-openai-models.yaml`
- API keys managed via `llm keys set azure` (not `openai`)
- Each model entry requires: `model_id`, `model_name`, `api_base`, `api_key_name: azure`

**When adding new models**: Follow the Azure OpenAI format in the YAML file, not standard OpenAI format.

## Key Files & Components

- **`install-llm-tools.sh`**: Main installation/update script with self-update logic
- **`integration/llm-common.sh`**: Shared shell configuration, llm wrapper function, asciinema auto-recording, azure-claude alias
- **`integration/llm-integration.bash`** / **`integration/llm-integration.zsh`**: Shell-specific keybindings (Ctrl+N for command completion)
- **`context/context`**: Python script for extracting terminal history from asciinema recordings
- **`llm-tools-context/`**: LLM plugin package that exposes `context` tool to AI models
- **`llm-template/assistant.yaml`**: Custom assistant template with security/IT expertise configuration (German-language template)

## Common Tasks

### Testing the Context System

```bash
# Test context script directly
context          # Show last command and output
context 5        # Show last 5 commands
context all      # Show entire session

# Test with LLM (requires llm-tools-context installed)
llm --tool context "what was the output of my last command?"
```

### Developing the llm-tools-context Plugin

```bash
# Install in editable mode for development
cd llm-tools-context
llm install -e '.[test]'

# Run tests
python -m pytest

# Test the tool
llm --tool context "test" --tools-debug
```

### Testing the Installation Script

```bash
# Dry-run approach: Comment out sudo and actual installations, test flow
# The script uses `set -e` so any error will halt execution

# Test self-update mechanism
git commit --allow-empty -m "test"
./install-llm-tools.sh  # Should detect update and re-exec

# Test idempotency
./install-llm-tools.sh  # Run twice, should skip already-installed items
```

### Adding New LLM Plugins

Edit the `PLUGINS` array in Phase 3 of `install-llm-tools.sh`:

```bash
PLUGINS=(
    "llm-gemini"
    "llm-anthropic"
    # Add new plugin here
    "git+https://github.com/user/repo"  # For git-based plugins
)
```

The loop handles both PyPI packages and git URLs automatically.

### Modifying Shell Integration

The Ctrl+N keybinding is implemented differently in Bash vs Zsh:

**Bash** (`integration/llm-integration.bash`):
- Uses `bind -x` to bind `\C-n` to a function
- Function manipulates `READLINE_LINE` and `READLINE_POINT`

**Zsh** (`integration/llm-integration.zsh`):
- Defines a widget function
- Registers with `zle -N __llm_cmdcomp`
- Binds with `bindkey '^N' __llm_cmdcomp`
- Manipulates `BUFFER` and `CURSOR`

**To change the keybinding**: Update both files with the new key code (e.g., `\C-k` for Ctrl+K).

### Updating Tool Versions

The script automatically upgrades tools on re-run:
- `llm`: `uv tool upgrade llm`
- Plugins: `llm install <plugin> --upgrade`
- `repomix`: `npm install -g repomix` (npm always installs latest)
- `gitingest`: `uv tool upgrade gitingest`
- `files-to-prompt`: `uv tool upgrade files-to-prompt`
- `asciinema`: `cargo install --locked --force --git https://github.com/asciinema/asciinema`
- Claude Code: `npm install -g @anthropic-ai/claude-code`
- Claude Code Router: `npm install -g @musistudio/claude-code-router`
- OpenCode: `npm install -g opencode-ai@latest`

### Using Claude Code with Azure OpenAI

The installation configures **Claude Code Router** as a proxy to use Azure OpenAI with Claude Code:

1. Configuration stored in `~/.claude-code-router/config.json`
2. Requires Azure OpenAI endpoint, API key, deployment name, and API version
3. Use the `azure-claude` alias (defined in `integration/llm-common.sh`) to launch Claude Code via the router
4. Command: `azure-claude` (equivalent to `ccr code`)

**Claude Code Router Config Structure**:
```json
{
  "Providers": [{
    "name": "azure",
    "api_base_url": "https://YOUR-RESOURCE.openai.azure.com/openai/deployments/DEPLOYMENT/chat/completions?api-version=2024-10-21",
    "api_key": "YOUR_KEY",
    "models": ["DEPLOYMENT_NAME"]
  }],
  "Router": {
    "default": "azure,DEPLOYMENT_NAME"
  }
}
```

### Testing Shell Integration

```bash
# Test that files source correctly
bash -c "source integration/llm-integration.bash && type __llm_cmdcomp"
zsh -c "source integration/llm-integration.zsh && which __llm_cmdcomp"

# Test the llm wrapper function
bash -c "source integration/llm-integration.bash && type llm"  # Should show function

# Test Ctrl+N binding is registered
bash -c "source integration/llm-integration.bash && bind -P | grep llm"
zsh -c "source integration/llm-integration.zsh && bindkey | grep llm"
```

## Key Constraints

1. **Azure Foundry Only**: This setup is NOT for standard OpenAI API - all model configs use Azure format
2. **Debian/Ubuntu/Kali**: Uses `apt-get` for system packages; would need modification for RHEL/Arch
3. **Interactive Prompts**: The script prompts for Azure API key and resource URL (for both llm and Claude Code Router); not fully automated
4. **Git Repository Required**: Self-update only works when cloned from git (not if downloaded as ZIP)
5. **Path Assumptions**: The script assumes it can write to `~/.bashrc`, `~/.zshrc`, `~/.config/io.datasette.llm/`, and `~/.claude-code-router/`
6. **Asciinema Dependency**: Context system requires `asciinema` to be installed for session recording
7. **Context Script Location**: The `context` script must be in `$PATH` for the `llm-tools-context` plugin to work
8. **NPM Permissions**: The script detects if npm requires sudo for global installs and adapts accordingly
9. **Rust Required**: asciinema is installed via cargo (Rust's package manager)

## Special Packages & Forks

Note that several packages use **forks** or specific sources:
- **llm-templates-fabric**: Uses Damon McMinn's fork: `git+https://github.com/damonmcminn/llm-templates-fabric`
- **files-to-prompt**: Uses Dan Mackinlay's fork: `git+https://github.com/danmackinlay/files-to-prompt`
- **asciinema**: Installed from git source via cargo: `cargo install --locked --git https://github.com/asciinema/asciinema`
- **llm-tools-context**: Installed from local directory: `$SCRIPT_DIR/llm-tools-context`

