# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Information

**GitHub Repository**: https://github.com/c0ffee0wl/llm-linux-setup

## Repository Overview

Installation and configuration system for Simon Willison's `llm` CLI tool and related AI/LLM command-line utilities on Linux (Debian/Ubuntu/Kali). Consists of a self-updating installation script, shell integration files, and an automatic terminal session recording system that provides AI context.

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
   - Uses regex patterns to detect shell prompts (bash `$/#`, zsh `%/❯/→/➜`, etc.), handles Kali two-line prompts
   - Extracts **prompt blocks** (prompt + command + output from one prompt to the next)
   - Filters out previous `context` command outputs (lines starting with `#c#`) to avoid recursion
   - Excludes the last block if it's empty, prompt-only, or a self-referential `context` command with no output

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

**Suppressing Session Log Messages:**
- Set `SESSION_LOG_SILENT=true` before sourcing shell integration to suppress the session log notification
- Useful for automated environments or when you want cleaner shell startup
- Example: Add `export SESSION_LOG_SILENT=1` in your `.bashrc` before the integration source line

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
- `integration/llm-common.sh` - Shared configuration (PATH, env vars, aliases, llm wrapper function)
- `integration/llm-integration.bash` - Bash-specific integration (sources common, defines Bash widgets)
- `integration/llm-integration.zsh` - Zsh-specific integration (sources common, defines Zsh widgets)

**LLM Wrapper Function** (`integration/llm-common.sh:16-80`):
- Automatically applies templates to prompt commands (chat, code, default prompts)
- **`llm chat`** → adds `-t assistant` unless user specifies `-t`, `-s`, `-c`, or `--cid`
- **`llm code`** → always adds `-t code` (outputs clean executable code without markdown)
- **Default prompts** → adds `-t assistant` unless user specifies template/system prompt/continuation
- **Excluded subcommands** (no template): models, keys, plugins, templates, tools, schemas, fragments, collections, embed, etc.
- When modifying wrapper logic, update the `exclude_commands` array and `should_skip_template()` function

**Command Completion (Ctrl+N)**:
- Uses `llm cmdcomp` command from **llm-cmd-comp** plugin (git: `c0ffee0wl/llm-cmd-comp`)
- **llm-cmd** plugin provides command execution (git: `c0ffee0wl/llm-cmd`)

**When adding new shell features**:
- Shell-agnostic → `integration/llm-common.sh`
- Bash-specific (readline bindings) → `integration/llm-integration.bash`
- Zsh-specific (zle widgets) → `integration/llm-integration.zsh`

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
- **`update_template_file(template_name)`**: Smart template update with checksum tracking
  - Compares repository version vs installed version using SHA256 checksums
  - Stores checksums in `~/.config/llm-tools/template-checksums`
  - Auto-updates if user hasn't modified the file (installed checksum = stored checksum)
  - Prompts user if local modifications detected (installed checksum ≠ stored checksum)
  - Used in Phase 4 for assistant.yaml and code.yaml templates

**Helper Functions Philosophy:**
These functions follow the DRY (Don't Repeat Yourself) principle and ensure consistent behavior across the script. When adding new features:
- **Always check** if an existing helper function can be used or extended
- **Create new helpers** for operations repeated more than twice
- **Keep helpers focused** - single responsibility per function

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

### Node.js Installation Strategy

The script uses **intelligent version detection** similar to the Rust approach:

**Version Detection Pattern:**
1. Check repository Node.js version via `apt-cache policy nodejs`
2. Extract and compare version (minimum required: 20 for Claude Code/OpenCode)
3. Choose installation method based on availability and current state

**Installation Logic:**
- **If Node.js not installed:**
  - Installs from apt if repo version ≥ 20
  - Installs Node 22 via nvm if repo version < 20
- **If Node.js already installed and version < 20:**
  - Warns user to upgrade via nvm
  - Provides upgrade instructions
- **NPM installation:**
  - Automatically installs npm from repositories for apt-based installations
  - For nvm installations, npm is bundled with Node.js

**Coexistence Strategy:**
- nvm installs to `~/.nvm` (takes precedence in PATH via shell rc files)
- System packages remain in `/usr` (harmless when shadowed)
- No package removal needed (consistent with Rust handling)

**NPM Permissions Detection:**
- Script tests write permissions to npm global directory
- Automatically uses sudo for npm global installs if needed
- Provides `npm_install()` wrapper function that adapts based on permissions

**Why This Matters:** Claude Code and OpenCode require Node.js 20+ for modern JavaScript features and APIs.

### Azure OpenAI Configuration

The system is specifically configured for **Azure Foundry** (not standard OpenAI):
- Model IDs use `azure/` prefix (e.g., `azure/gpt-5-mini`)
- Configuration stored in `~/.config/io.datasette.llm/extra-openai-models.yaml`
- API keys managed via `llm keys set azure` (not `openai`)
- Each model entry requires: `model_id`, `model_name`, `api_base`, `api_key_name: azure`

**When adding new models**: Follow the Azure OpenAI format in the YAML file, not standard OpenAI format.

## Key Files & Components

- **`install-llm-tools.sh`**: Main installation/update script with self-update logic
- **`integration/llm-common.sh`**: Shared shell configuration, llm wrapper function, asciinema auto-recording, routed-claude alias, code subcommand handler
- **`integration/llm-integration.bash`** / **`integration/llm-integration.zsh`**: Shell-specific keybindings (Ctrl+N for command completion)
- **`context/context`**: Python script for extracting terminal history from asciinema recordings
- **`llm-tools-context/`**: LLM plugin package that exposes `context` tool to AI models
- **`llm-template/assistant.yaml`**: Custom assistant template with security/IT expertise configuration (German-language template)
- **`llm-template/code.yaml`**: Code-only generation template - outputs clean, executable code without explanations or markdown formatting

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

### Code Generation Commands

The `llm code` command generates clean, executable code without explanations or markdown formatting - perfect for piping to files or direct execution:

```bash
# Generate Python code
llm code "function to check if number is prime" > prime.py

# Generate bash script
llm code "script to backup directory with timestamp" > backup.sh

# Generate SQL query
llm code "select users who registered this month"

# Generate Dockerfile
llm code "dockerfile for nodejs app with nginx" > Dockerfile

# Direct execution (use with caution!)
llm code "one-liner to find files larger than 100MB" | bash

# Chain with other commands
llm code "regex to match email addresses" | grep -o '@'

# Generate configuration file
llm code "nginx config for reverse proxy on port 3000" > nginx.conf
```

**Key Features:**
- No markdown code blocks (no \`\`\`)
- No explanations or natural language
- Output is directly executable/pipeable
- Infers programming language from context
- Ideal for automation and scripting

## Common Development Tasks

### Updating the README Table of Contents

The README.md includes an automatically generated Table of Contents (TOC) using [doctoc](https://github.com/thlorenz/doctoc).

**Manual TOC update** (if needed):
```bash
# Update the TOC in README.md
doctoc README.md
```

**Automatic updates**: A git pre-commit hook automatically updates the TOC whenever README.md is committed. The hook:
- Detects when README.md is being committed
- Runs doctoc to regenerate the TOC
- Adds the updated file to the commit

**Pre-commit Hook Setup:**
The repository includes a pre-commit hook at `.git/hooks/pre-commit`. If you're working on a fresh clone:
- The hook should already be executable (committed to the repository)
- Verify it exists: `ls -la .git/hooks/pre-commit`
- If missing or not executable: `chmod +x .git/hooks/pre-commit`
- Requires doctoc to be installed: `npm install -g doctoc`

**TOC markers**: The TOC is placed between these special comments in README.md:
```markdown
<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- END doctoc generated TOC please keep comment here to allow auto update -->
```

**When editing README.md**: Just edit the content normally. The TOC will auto-update on commit, or you can manually run `doctoc README.md` to regenerate it.

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

**Infinite loop on script start**: Local commits ahead of origin. The script uses `git rev-list HEAD..@{u}` to only pull when BEHIND, not ahead. Solution: `git push` or `git reset --hard origin/main`

**Rust version too old**: Script auto-detects and prompts to install rustup if < 1.75. Manual fix: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`

**Wrong Rust version used**: Ensure `~/.cargo/bin` comes before `/usr/bin` in PATH. Add `export PATH="$HOME/.cargo/bin:$PATH"` to shell RC file if needed.

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

**New tmux panes don't record**: Check `env | grep IN_ASCIINEMA_SESSION` shows pane-specific markers (`IN_ASCIINEMA_SESSION_tmux_0=1`). Re-source shell RC file if needed.

**Recording not starting**: Verify integration is sourced: `grep -r "llm-integration" ~/.bashrc ~/.zshrc` and `which asciinema` shows path.

**Context shows wrong session**: Check `echo $SESSION_LOG_FILE` or manually set: `export SESSION_LOG_FILE="/path/to/session.cast"`

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
   - For template updates: `update_template_file template_name`

2. **Follow the phase structure**: Add new installations to the appropriate phase
3. **Maintain idempotency**: Check if tools/configs exist before installing/modifying
4. **Test with syntax check**: `bash -n install-llm-tools.sh`
5. **Preserve self-update logic**: Never move or remove Phase 0

**Adding new templates**: Simply add a new YAML file to `llm-template/` and add one line to Phase 4: `update_template_file "newtemplate"`

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
- `~/.config/io.datasette.llm/templates/{assistant,code}.yaml` - Custom LLM templates
- `~/.config/llm-tools/asciinema-commit` - Tracks asciinema version for update detection
- `~/.config/llm-tools/template-checksums` - Tracks template checksums for smart updates
- `$SESSION_LOG_DIR/*.cast` - Session recordings (default: `/tmp/session_logs/asciinema/`)

### Repository Structure
- `install-llm-tools.sh` - Main installation script with 7 phases
- `integration/llm-common.sh` - Shared shell config, llm wrapper, asciinema auto-recording
- `integration/llm-integration.{bash,zsh}` - Shell-specific keybindings (Ctrl+N)
- `context/context` - Python script for extracting terminal history from recordings
- `llm-tools-context/` - LLM plugin exposing context as tool
- `llm-template/{assistant,code}.yaml` - Template sources installed to user config
- `.git/hooks/pre-commit` - Automatic TOC updater for README.md

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
12. **Node.js Version Management**: Script automatically detects Node.js version and installs via nvm if repository version < 20
13. **Per-Pane Recording in tmux**: Each tmux pane gets its own independent recording session (intentional design for workflow isolation)

## Special Packages & Forks

Note that several packages use **forks** or specific sources:
- **llm-cmd**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-cmd`
- **llm-cmd-comp**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-cmd-comp`
- **llm-templates-fabric**: Uses Damon McMinn's fork: `git+https://github.com/c0ffee0wl/llm-templates-fabric`
- **files-to-prompt**: Uses Dan Mackinlay's fork: `git+https://github.com/c0ffee0wl/files-to-prompt`
- **asciinema**: Installed from git source via cargo: `cargo install --locked --git https://github.com/asciinema/asciinema`
- **llm-tools-context**: Installed from local directory: `$SCRIPT_DIR/llm-tools-context`
