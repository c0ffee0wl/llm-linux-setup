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
2. **Critical Check**: Uses `git rev-list HEAD..@{u}` to count how many commits **behind** the remote we are (not just different from it)
   - If behind > 0: pulls updates and re-execs with `exec "$0" "$@"` to replace the current process
   - If equal or ahead: continues normally without pulling
3. This prevents the script from executing with partially-updated code mid-run and avoids infinite loops when local commits exist

**When modifying `install-llm-tools.sh`**: The self-update logic in Phase 0 must ALWAYS run before any other operations. Never move or remove this section.

### Session Recording & Context System

The repository includes an **automatic session recording and context extraction system**:

1. **Automatic Recording** (`integration/llm-common.sh`): Interactive shells automatically start asciinema recording
   - Only triggers in interactive shells (not scripts or nested sessions)
   - Prevents recursion by checking session-specific environment markers
   - Stores recordings in configurable directory (default: `/tmp/session_logs/asciinema/`) via `$SESSION_LOG_DIR`
   - Creates timestamp-based filenames
   - Exports `$SESSION_LOG_FILE` for the context tool to locate the current recording

2. **Context Extraction** (`context/context`): Python script that parses asciinema recordings
   - Finds current session's `.cast` file via `$SESSION_LOG_FILE` or most recent file in log directory
   - Converts binary `.cast` format to text using `asciinema convert`
   - Uses regex patterns to detect shell prompts (bash `$/#`, zsh `%/❯/→/➜`, etc.)
   - Extracts commands and their outputs, returns last N commands or full session

3. **LLM Integration** (`llm-tools-context/`): LLM plugin exposing context as a tool
   - Registered as a tool that LLMs can call during conversations
   - Allows AI to query recent terminal history including command outputs
   - Usage: `llm --tool context "what did I just run?"`

**Architecture Flow**: Shell starts → asciinema records → `$SESSION_LOG_FILE` points to recording → `context` script parses it → `llm-tools-context` exposes it to AI

**Configuration**:
- **First-Run Setup**: On first installation, the script prompts for session history storage preference:
  - **Permanent**: Stores in `~/session_logs/asciinema` (survives reboots)
  - **Temporary**: Stores in `/tmp/session_logs/asciinema` (cleared on reboot, default)
- The preference is saved as `export SESSION_LOG_DIR="..."` in your `.bashrc`/`.zshrc`
- **To change preference**: Edit your shell rc file and modify the `SESSION_LOG_DIR` export line
- The installation script detects existing `SESSION_LOG_DIR` export and skips the prompt on subsequent runs

**Context Output Format**: The `context` command prefixes all output lines with `#c#` for easy identification and filtering.

**Behavior with Terminal Multiplexers (tmux/screen)**:
- **Each pane/window gets its own independent recording** - Intentional design for workflow isolation
- When you create a new tmux pane or screen window, that new shell starts its own asciinema recording
- The `context` command in each pane shows only that pane's history, not other panes
- This matches the mental model: different panes = different workflows = separate contexts
- **If you want unified recording**: Start asciinema manually before launching tmux: `asciinema rec --command "tmux attach"`

**Technical Implementation**:
- Uses **pane-specific environment variables** to enable per-pane recording in tmux
- In **tmux**: Uses `$TMUX_PANE` to create unique markers (e.g., `IN_ASCIINEMA_SESSION_tmux_0`) to prevent environment variable inheritance between panes
- In **regular terminals and screen**: Uses generic `IN_ASCIINEMA_SESSION` marker (screen windows are already isolated and don't inherit env vars)
- Session filenames include pane identifiers in tmux: `2025-10-05_14-30-45-123_12345_tmux0.cast`
- This design allows each tmux pane to maintain its own independent recording session

### Shell Integration Architecture

The shell integration uses a **three-file pattern** located in the `integration/` subdirectory:
- `integration/llm-common.sh` - Shared configuration (PATH, env vars, aliases)
- `integration/llm-integration.bash` - Bash-specific integration (sources common, defines Bash widgets)
- `integration/llm-integration.zsh` - Zsh-specific integration (sources common, defines Zsh widgets)

Both shell files source the common file at the top. The main installation script appends source statements to `.bashrc` and `.zshrc` that reference these files by absolute path.

**Command Completion (Ctrl+N)**:
- The Ctrl+N keybinding uses `llm cmdcomp` command provided by the **llm-cmd-comp** plugin
- The **llm-cmd** plugin provides command execution functionality
- Both are installed from git repositories: `github.com/c0ffee0wl/llm-cmd` and `github.com/c0ffee0wl/llm-cmd-comp`

**When adding new shell features**:
- Shell-agnostic features → `integration/llm-common.sh`
- Bash-specific features (readline bindings) → `integration/llm-integration.bash`
- Zsh-specific features (zle widgets) → `integration/llm-integration.zsh`

### Installation Phases

The script is organized into numbered phases:

0. **Self-Update**: Git fetch/pull/exec pattern
1. **Prerequisites**: Install pipx, uv, Node.js, Rust/Cargo, asciinema
2. **LLM Core**: Install/upgrade llm, configure Azure OpenAI, create `extra-openai-models.yaml`
3. **LLM Plugins**: Install/upgrade all plugins using `llm install --upgrade`
4. **LLM Templates**: Install/update custom templates from `llm-template/` directory to `~/.config/io.datasette.llm/templates/`
5. **Shell Integration**: Add source statements to `.bashrc`/`.zshrc` (idempotent checks)
6. **Additional Tools**: Install/update gitingest (uv), files-to-prompt (uv), context script
7. **Claude Code & Router**: Install Claude Code, Claude Code Router (with Azure config), and OpenCode

### Helper Functions (Code Reusability)

The installation script uses **helper functions** to eliminate code duplication and follow KISS principles:

- **`install_apt_package(package_name)`**: Installs apt packages with existence checks (used in Phase 1)
- **`install_or_upgrade_uv_tool(tool_name, [source])`**: Unified uv tool installation/upgrade (used in Phase 2, 6)
- **`update_shell_rc_file(rc_file, integration_file, shell_name)`**: Updates bash/zsh RC files with integration (used in Phase 5)
- **`configure_azure_openai()`**: Centralized Azure OpenAI configuration prompts (used in Phase 2)
- **`install_rust_via_rustup()`**: Installs Rust via official rustup installer with non-interactive flags (used in Phase 1)
- **`update_rust_via_rustup()`**: Updates Rust via rustup when rustup-managed installation is detected (used in Phase 1)

**When modifying the installation script**: Use these helper functions for consistency rather than duplicating installation logic.

### Rust/Cargo Installation Strategy

The script uses **intelligent version detection** similar to the Node.js approach:

**Version Detection Pattern:**
1. Check repository Rust version via `apt-cache policy rustc`
2. Extract and compare version (minimum required: 1.75 for asciinema)
3. Choose installation method based on availability and current state

**Installation Logic:**
- **If Rust not installed:**
  - Uses rustup if already available
  - Installs from apt if repo version ≥ 1.75
  - Falls back to rustup if repo version < 1.75
- **If Rust already installed via rustup:**
  - Automatically updates via `rustup update stable`
- **If Rust already installed via apt and version < 1.75:**
  - Prompts user: `"Install Rust 1.75+ via rustup? This will shadow the system installation. (Y/n)"`
  - Default: Yes (critical for asciinema build)
  - If accepted: Installs rustup (shadows system packages via PATH)
  - If declined: Warns that asciinema build may fail

**Coexistence Strategy:**
- rustup installs to `~/.cargo/bin` (already prioritized in PATH)
- System packages remain in `/usr/bin` (harmless when shadowed)
- No package removal needed (consistent with Node.js handling)
- Uses `-y` flag in rustup installer to prevent blocking prompts

**Why This Matters:** Prevents `cargo install` failures like "hyper-rustls requires rustc 1.71+" on older Debian/Ubuntu/Kali systems with outdated Rust packages.

### Azure OpenAI Configuration

The system is specifically configured for **Azure Foundry** (not standard OpenAI):
- Model IDs use `azure/` prefix (e.g., `azure/gpt-5-mini`)
- Configuration stored in `~/.config/io.datasette.llm/extra-openai-models.yaml`
- API keys managed via `llm keys set azure` (not `openai`)
- Each model entry requires: `model_id`, `model_name`, `api_base`, `api_key_name: azure`

**When adding new models**: Follow the Azure OpenAI format in the YAML file, not standard OpenAI format.

## Key Files & Components

- **`install-llm-tools.sh`**: Main installation/update script with self-update logic
- **`integration/llm-common.sh`**: Shared shell configuration, llm wrapper function, asciinema auto-recording, routed-claude alias
- **`integration/llm-integration.bash`** / **`integration/llm-integration.zsh`**: Shell-specific keybindings (Ctrl+N for command completion)
- **`context/context`**: Python script for extracting terminal history from asciinema recordings
- **`llm-tools-context/`**: LLM plugin package that exposes `context` tool to AI models
- **`llm-template/assistant.yaml`**: Custom assistant template with security/IT expertise configuration (German-language template)

## Common Commands

### Installation and Updates

```bash
# First-time installation
./install-llm-tools.sh

# Update all tools (pulls git updates, upgrades packages, preserves config)
./install-llm-tools.sh
```

### Context System Commands

```bash
# Show last command and output
context

# Show last 5 commands
context 5

# Show entire session
context all

# Get export command for current session file
context -e

# Use context with LLM
llm --tool context "what was the output of my last command?"
```

## Common Development Tasks

### Testing the Installation Script

```bash
# Test the full installation (will update existing tools)
./install-llm-tools.sh

# Test self-update mechanism (script detects git updates and re-execs)
git commit --allow-empty -m "test"
./install-llm-tools.sh  # Should detect update and re-exec

# Test idempotency (should skip already-configured items)
./install-llm-tools.sh  # Run twice, should handle gracefully

# Check syntax after modifications
bash -n install-llm-tools.sh
```

### Troubleshooting Installation Script

**Issue: Infinite loop on script start ("Updates found! Pulling latest changes...")**

This happens when you have local commits that haven't been pushed to origin:

```bash
# Check if you're ahead of origin
git status

# If "Your branch is ahead of 'origin/main' by N commits":
# Option 1: Push your changes
git push

# Option 2: Reset to origin (WARNING: loses local commits)
git reset --hard origin/main

# The script only pulls when BEHIND remote, not when ahead or equal
```

**Issue: Rust version too old / asciinema build fails**

If you encounter errors like "package X cannot be built because it requires rustc 1.71 or newer":

```bash
# Check current Rust version
rustc --version

# Check if rustup is installed
command -v rustup

# Manual upgrade via rustup (if not already done)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Verify rustup installation takes precedence
which rustc  # Should show ~/.cargo/bin/rustc

# Update Rust via rustup
rustup update stable

# Re-run the installation script
./install-llm-tools.sh
```

**Issue: Both rustup and apt Rust installed, wrong version used**

```bash
# Check which rust is being used
which rustc
rustc --version

# Verify PATH order (rustup should come first)
echo $PATH | tr ':' '\n' | grep -E 'cargo|rust'

# Expected order:
# /home/user/.cargo/bin  <- rustup (should be first)
# /usr/bin               <- system packages

# Fix PATH if needed (add to ~/.bashrc or ~/.zshrc)
export PATH="$HOME/.cargo/bin:$PATH"
source ~/.bashrc  # or ~/.zshrc
```

### Testing the Context System

```bash
# Test context script directly
context          # Show last command and output
context 5        # Show last 5 commands
context all      # Show entire session
context -e       # Output SESSION_LOG_FILE export command

# Test with LLM (requires llm-tools-context installed)
llm --tool context "what was the output of my last command?"
```

### Testing the llm-tools-context Plugin

```bash
# Install in editable mode for development
cd llm-tools-context
llm install -e '.[test]'

# Run tests
python -m pytest tests/

# Test the tool with debug output
llm --tool context "test" --tools-debug

# Verify plugin is loaded
llm plugins | grep context
```

### Troubleshooting Session Recording

**Issue: New tmux panes don't create separate recordings**

If you're running an older version or experiencing issues:

```bash
# 1. Check if pane-specific variables are being set
env | grep IN_ASCIINEMA_SESSION

# Expected in tmux pane 0:
# IN_ASCIINEMA_SESSION_tmux_0=1
#
# Expected in regular terminal or screen:
# IN_ASCIINEMA_SESSION=1

# 2. Verify session log files have pane identifiers
ls -la "$SESSION_LOG_DIR"
# Should see: 2025-10-05_14-30-45-123_12345_tmux0.cast

# 3. Re-source the updated integration
source ~/.bashrc  # or ~/.zshrc

# 4. Create a new tmux pane and check if it starts recording
# You should see "Session is logged for 'context'..." message
```

**Issue: Recording not starting in new shells**

```bash
# Verify the integration file is being sourced
grep -r "llm-integration" ~/.bashrc ~/.zshrc

# Check if asciinema is in PATH
which asciinema

# Manually trigger recording test
SESSION_LOG_DIR=/tmp/test_recording \
  bash -c 'source integration/llm-common.sh'
```

**Issue: Context command shows wrong session**

```bash
# Check which session file is being used
echo $SESSION_LOG_FILE

# Set specific session manually
export SESSION_LOG_FILE="/path/to/specific/session.cast"
context
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

### Modifying Installation Script

When adding new functionality to `install-llm-tools.sh`:

1. **Use existing helper functions** where possible:
   - For apt packages: `install_apt_package package_name`
   - For uv tools: `install_or_upgrade_uv_tool tool_name [source]`
   - For shell RC updates: `update_shell_rc_file rc_file integration_file shell_name`
   - For Azure config: `configure_azure_openai`

2. **Follow the phase structure**: Add new installations to the appropriate phase
3. **Maintain idempotency**: Check if tools/configs exist before installing/modifying
4. **Test with syntax check**: `bash -n install-llm-tools.sh`
5. **Preserve self-update logic**: Never move or remove Phase 0

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
- `gitingest`: `uv tool upgrade gitingest`
- `files-to-prompt`: `uv tool upgrade files-to-prompt`
- `asciinema`: `cargo install --locked --force --git https://github.com/asciinema/asciinema`
- Claude Code: `npm install -g @anthropic-ai/claude-code`
- Claude Code Router: `npm install -g @musistudio/claude-code-router`
- OpenCode: `npm install -g opencode-ai@latest`

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

## Important File Locations

### Configuration Files

- `~/.config/io.datasette.llm/extra-openai-models.yaml` - Azure OpenAI model definitions
- `~/.config/io.datasette.llm/templates/assistant.yaml` - Custom assistant template (installed from `llm-template/` directory)
- `~/.config/io.datasette.llm/default_model.txt` - Default model selection
- `~/.config/io.datasette.llm/keys.json` - Encrypted API keys (managed via `llm keys`)
- `~/.config/llm-tools/first-run-complete` - Flag file indicating installation script has completed successfully at least once
- `~/.config/llm-tools/asciinema-commit` - Tracks installed asciinema version for update detection
- `~/.claude-code-router/config.json` - Claude Code Router configuration (manually configured)
- `$SESSION_LOG_DIR/*.cast` - Asciinema session recordings (default: `/tmp/session_logs/asciinema/`)

### Shell Integration Files

- `integration/llm-common.sh` - Shared configuration (PATH, env vars, aliases, asciinema auto-recording)
- `integration/llm-integration.bash` - Bash-specific integration (sources common, defines Bash widgets)
- `integration/llm-integration.zsh` - Zsh-specific integration (sources common, defines Zsh widgets)
- `~/.bashrc` / `~/.zshrc` - Modified by installation script to source integration files

### Installed Tools Locations

- `~/.local/bin/llm` - LLM CLI tool (installed via uv)
- `~/.local/bin/context` - Context extraction script (copied from `context/context`)
- `~/.local/bin/gitingest` - Git repository converter (installed via uv)
- `~/.local/bin/files-to-prompt` - File formatter (installed via uv)
- `~/.cargo/bin/asciinema` - Terminal recorder (built from git via cargo)
- Global npm packages (location varies by system): `@anthropic-ai/claude-code`, `@musistudio/claude-code-router`, `opencode-ai`

## Key Constraints & Design Decisions

1. **Azure Foundry Only**: This setup is NOT for standard OpenAI API - all model configs use Azure format with `azure/` prefix
2. **Debian/Ubuntu/Kali**: Uses `apt-get` for system packages; would need modification for RHEL/Arch
3. **Interactive Prompts on First Run**: The script prompts for Azure API key and resource URL on first run only; subsequent runs preserve existing configuration automatically
4. **Git Repository Required**: Self-update only works when cloned from git (not if downloaded as ZIP)
5. **Path Assumptions**: The script assumes it can write to `~/.bashrc`, `~/.zshrc`, `~/.config/io.datasette.llm/`, and `~/.claude-code-router/`
6. **Asciinema Dependency**: Context system requires `asciinema` to be installed for session recording
7. **Context Script Location**: The `context` script must be in `$PATH` for the `llm-tools-context` plugin to work
8. **NPM Permissions**: The script detects if npm requires sudo for global installs and adapts accordingly
9. **Rust Required**: asciinema is installed via cargo (Rust's package manager); minimum Rust 1.75 required
10. **Rust Version Management**: Script automatically detects outdated Rust and offers to upgrade via rustup with user approval (default: Yes)
11. **rustup vs apt Coexistence**: rustup and apt-installed Rust can coexist safely; rustup takes precedence via PATH
12. **Per-Pane Recording in tmux**: Each tmux pane gets its own independent recording session (intentional design for workflow isolation)

## Special Packages & Forks

Note that several packages use **forks** or specific sources:
- **llm-cmd**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-cmd`
- **llm-cmd-comp**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-cmd-comp`
- **llm-templates-fabric**: Uses Damon McMinn's fork: `git+https://github.com/c0ffee0wl/llm-templates-fabric`
- **files-to-prompt**: Uses Dan Mackinlay's fork: `git+https://github.com/c0ffee0wl/files-to-prompt`
- **asciinema**: Installed from git source via cargo: `cargo install --locked --git https://github.com/asciinema/asciinema`
- **llm-tools-context**: Installed from local directory: `$SCRIPT_DIR/llm-tools-context`

