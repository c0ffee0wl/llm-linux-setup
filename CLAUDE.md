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

2. **Context Extraction** (`llm-tools-context/`): Python package that parses asciinema recordings
   - Finds current session's `.cast` file via `$SESSION_LOG_FILE` or most recent file in log directory
   - Converts binary `.cast` format to text using `asciinema convert`
   - Uses regex patterns to detect shell prompts (bash `$/#`, zsh `%/❯/→/➜`, etc.), handles Kali two-line prompts
   - Extracts **prompt blocks** (prompt + command + output from one prompt to the next)
   - Filters out previous `context` command outputs (lines starting with `#c#`) to avoid recursion
   - Excludes the last block if it's empty, prompt-only, or a self-referential `context` command with no output
   - Provides both CLI (`context` command) and LLM tool (`llm --tool context`)

3. **Prompt Detection Module** (`llm-assistant/llm_assistant/prompt_detection.py`): Shared Python module
   - PromptDetector class used by both context tool and llm-assistant
   - **Hybrid detection** for llm-assistant:
     - **Priority 1**: Unicode markers (100% reliable in VTE terminals)
     - **Priority 2**: Regex fallback (SSH sessions, non-VTE terminals)
   - **Unicode markers** (`\u200B\u200D\u200B` and `\u200D\u200B\u200D`):
     - Invisible zero-width characters injected into PS1/PROMPT
     - Only in VTE terminals (Terminator, GNOME Terminal, Tilix)
     - Survives VTE's `get_text_range_format(Vte.Format.TEXT)`
   - **Regex patterns** for fallback:
     - Supports bash, zsh, and Kali two-line prompts (┌/╭ and └/╰ box-drawing characters)
     - Pattern matching for various prompt styles ($/#, %/❯/→/➜, user@host)
   - Installed to Python user site-packages (`python3 -m site --user-site`)/llm_tools/ in Phase 4

4. **LLM Integration** (`llm-tools-context/`): LLM plugin exposing context as a tool
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

**Resilience in Restricted Environments (chroot/rescue)**:
- **Test-before-exec pattern**: Before replacing the shell with asciinema, tests if pty creation actually works
- **Graceful degradation**: If asciinema cannot create a pty (common in chroot without proper mounts), shell initialization continues normally
- **Always warns on failure**: Displays "Warning: Session recording disabled (cannot create pty in this environment)" to stderr (ignores `SESSION_LOG_SILENT`)
- **Use case**: Enables shell usage in Hetzner rescue systems, minimal chroots, containers, and other restricted environments
- **Test command**: `asciinema rec -c "true" /dev/null --quiet` - lightweight silent test that exits immediately
- **Tradeoff**: ~100ms startup overhead in normal environments (acceptable for robustness)

### Shell Integration Architecture

The shell integration uses a **three-file pattern** located in the `integration/` subdirectory:
- `integration/llm-common.sh` - Shared configuration (PATH, env vars, aliases, llm wrapper function)
- `integration/llm-integration.bash` - Bash-specific integration (sources common, defines Bash widgets)
- `integration/llm-integration.zsh` - Zsh-specific integration (sources common, defines Zsh widgets)

**LLM Wrapper Function** (`integration/llm-common.sh`):
- Automatically applies templates to prompt commands (chat, code, default prompts)
- **`llm chat`** → adds `-t llm` unless user specifies `-t`, `-s`, `-c`, or `--cid`
- **`llm code`** → always adds `-t llm-code` (outputs clean executable code without markdown)
- **`llm rag`** → subcommand provided by llm-tools-rag plugin for RAG functionality
- **Default prompts** → adds `-t llm` unless user specifies template/system prompt/continuation
- **Excluded subcommands** (no template): models, keys, plugins, templates, tools, schemas, fragments, collections, embed, rag, etc.
- When modifying wrapper logic, update the `exclude_commands` array, `should_skip_template()`, and `has_google_search()` functions

**Default Tools Handling**:
- Default tools (`context`, `sandboxed_shell`) are added by the wrapper, NOT the template
- Tools are added via `--tool context --tool sandboxed_shell` flags when using the default assistant template
- **`has_google_search()`** function detects if `-o google_search` option is present
- **Tool skipping conditions**:
  - When user specifies custom template (`-t`), system prompt (`-s`), or continuation (`-c`) → no tools (user in control)
  - When `google_search` option is detected → no tools (incompatible with non-search tools on Vertex/Gemini)
- **Why**: Vertex/Gemini API requires that when multiple tools are present, they must ALL be search tools. Non-search tools (context, sandboxed_shell) conflict with Google Search.
- **search_google tool**: NOT included by default - use `--tool search_google` to enable. This tool uses Gemini/Vertex with google_search grounding internally, allowing any model to access Google Search.

| Command | Template | Tools |
|---------|----------|-------|
| `llm chat` | assistant | context, sandboxed_shell |
| `llm chat --tool search_google` | assistant | context, sandboxed_shell, search_google |
| `llm chat -o google_search 1` | assistant | (none) |
| `llm -o google_search 1 "prompt"` | assistant | (none) |
| `llm chat -t custom` | custom | (none) |
| `llm chat -s "system"` | (none) | (none) |

**VTE Unicode Marker Injection** (`integration/llm-common.sh`):
- Injects invisible Unicode markers into shell prompts for 100% reliable detection
- **VTE-only**: Only injected when `$VTE_VERSION` is set (Terminator, GNOME Terminal, Tilix)
- **Why VTE-only**: Kitty and Windows Terminal render zero-width chars as visible spaces
- **Markers**:
  - `PROMPT_START_MARKER` (`\u200B\u200D\u200B`): Before PS1 content
  - `INPUT_START_MARKER` (`\u200D\u200B\u200D`): After PS1, where user types
- **Injection mechanism**: Uses `PROMPT_COMMAND` (Bash) or `precmd_functions` (Zsh)
- **Prompt framework compatibility**: Appends to run LAST, after Starship/Powerlevel10k
- **Detection**: `PromptDetector.has_unicode_markers()` and `detect_prompt_at_end()` use hybrid detection

**Command Completion (Ctrl+N)**:
- Uses `llm cmdcomp` command from **llm-cmd-comp** plugin (git: `c0ffee0wl/llm-cmd-comp`)
- **llm-cmd** plugin provides command execution (git: `c0ffee0wl/llm-cmd`)

**Tab Completion (Zsh)**:
- Uses **llm-zsh-plugin** (forked from `eliyastein/llm-zsh-plugin`) with custom extensions
- Provides comprehensive tab completion for all llm commands, options, models, and templates
- **Custom Extensions**: Adds completion for `llm code` and `llm rag` subcommands (not in upstream)
- **Version-controlled in repository**: Fork is maintained directly in `integration/llm-zsh-plugin/`
- **Update Strategy**: Manual updates to the fork in this repository (not auto-pulled from upstream)
- **Coexistence**: Tab completion (plugin) and Ctrl+N (AI cmdcomp) serve different purposes - both work together
- Only active in Zsh; Bash users can use Ctrl+N for AI-powered command suggestions

**`wut` Function** (`integration/llm-common.sh`):
- Explains the output of the last command using the context tool
- Usage: `wut` (no arguments needed)
- Uses `-t llm-wut` template for structured explanations
- Captures terminal context and sends to LLM for analysis

**When adding new shell features**:
- Shell-agnostic → `integration/llm-common.sh`
- Bash-specific (readline bindings) → `integration/llm-integration.bash`
- Zsh-specific (zle widgets) → `integration/llm-integration.zsh`
- **If adding new llm subcommands**: Update the completion file `integration/llm-zsh-plugin/completions/_llm`

### Installation Phases

The script is organized into numbered phases:

0. **Self-Update**: Git fetch/pull/exec pattern
1. **Prerequisites**: Install pipx, uv, Node.js, Rust/Cargo, asciinema, document processors (poppler-utils, pandoc)
2. **LLM Core + Plugins**: Install llm with ALL plugins in a single `uv tool install --force --with ...` command; configure Azure OpenAI, create `extra-openai-models.yaml` (chat) and `azure/config.yaml` (embeddings)
3. **LLM Templates**: Install/update custom templates from `llm-templates/` directory to `~/.config/io.datasette.llm/templates/`
4. **Shell Integration**: Add source statements to `.bashrc`/`.zshrc` (idempotent checks), llm wrapper includes RAG routing
5. **Additional Tools**: Install/update gitingest (uv), files-to-prompt (uv), argc (cargo), context script, wrapper scripts
6. **Claude Code & Router**: Install Claude Code, claudo (Claude in Docker, if Docker installed), Claude Code Router (with dual-provider support: Azure primary, Gemini web search), and Codex CLI

### Consolidated Plugin Installation

The installation script uses a **consolidated installation approach** for maximum performance:

**The Pattern**: Instead of installing plugins one at a time (30+ separate `llm install` calls), all plugins are installed in a single `uv tool install --with ... --with ...` command.

**How It Works**:
1. **ALL_PLUGINS array**: Defines all plugins upfront in Phase 2
2. **Single command**: `uv tool install --force --with plugin1 --with plugin2 ... llm`
3. **Hash-based detection**: Tracks plugin list changes (for logging purposes)

**Why Always `--force`**:
- `uv tool upgrade` doesn't support `--with` flags
- Without `--with`, local plugins (llm-assistant, llm-tools-context, etc.) get removed during upgrade
- Always using `--force` with `--with` flags ensures local plugins are preserved

**Hash-Based Detection** (for logging only):
- Generates SHA256 hash of sorted ALL_PLUGINS array **plus git commit hash**
- Stores hash in `~/.config/llm-tools/plugins-hash`
- **Hash matches**: Logs "Plugin list unchanged, checking for updates..."
- **Hash differs**: Logs "Plugin list changed, reinstalling..."
- Both cases run the same `uv tool install --force` command

**Performance**:
| Scenario | Old Approach | New Approach |
|----------|-------------|--------------|
| First run | ~90-120s (30+ installs) | ~60-90s (single install) |
| Subsequent runs | ~60-90s (30+ installs) | ~20-40s (single install, git checks) |

**Tracking Files**:
- `~/.config/llm-tools/plugins-hash` - SHA256 hash of plugin list
- `~/.config/io.datasette.llm/uv-tool-packages.json` - Plugin list for llm-uv-tool

### Plugin Persistence with llm-uv-tool

The system uses **llm-uv-tool** (https://github.com/c0ffee0wl/llm-uv-tool) to make LLM plugins persist across upgrades:

**The Problem**: When LLM is installed via `uv tool install`, plugins installed via `llm install` are stored in that isolated environment. When you run `uv tool upgrade llm`, all plugins are wiped and must be reinstalled.

**The Solution**: llm-uv-tool intercepts `llm install` and `llm uninstall` commands and redirects them through uv's `--with` flag, making plugins persistent across upgrades.

**How It Works**:
- llm-uv-tool is bundled with llm during installation: `uv tool install --with "git+https://github.com/c0ffee0wl/llm-uv-tool" "git+..."`
- Maintains a tracking file: `~/.config/io.datasette.llm/uv-tool-packages.json`
- Intercepts `llm install <plugin>` and converts to `uv tool install --with <plugin> llm`
- User-facing commands remain unchanged: `llm install llm-gemini` works as before
- Plugins automatically persist when llm is upgraded

**Installation**:
- New installations: `uv tool install --with "git+https://github.com/c0ffee0wl/llm-uv-tool" "git+https://github.com/c0ffee0wl/llm"`
- Upgrades: `uv tool install --force --with ...` (llm-uv-tool persists automatically)
- Note: `uv tool upgrade` is NOT used because it doesn't support `--with` flags

**Benefits**:
- ✅ Plugins persist across LLM upgrades automatically
- ✅ Faster script execution (plugins don't need reinstallation every run)
- ✅ More robust upgrade process
- ✅ No changes to user-facing commands
- ✅ Compatible with Python 3.10+

**Plugin Dependencies**: All plugin dependencies (like yt-dlp for YouTube transcripts) are automatically installed by pip when the plugin is installed. No manual installation required.

### Helper Functions (Code Reusability)

The installation script uses **helper functions** to eliminate code duplication and follow KISS principles:

- **`install_apt_package(package_name, [command_name])`**: Installs apt packages with existence checks (used in Phase 1)
  - If `command_name` is provided, checks for that command instead of `package_name`
  - Examples: `install_apt_package git`, `install_apt_package bubblewrap bwrap`, `install_apt_package poppler-utils pdftotext`
- **`compare_versions(v1, v2)`**: Compare two semantic versions (used throughout)
  - Returns: 0 if equal, 1 if v1 > v2, 2 if v1 < v2
  - Example: `compare_versions "1.85.0" "1.80.0"` returns 1
- **`version_at_least(version, minimum)`**: Check if version >= minimum (convenience wrapper)
  - Returns: 0 (true) if v1 >= v2, 1 (false) otherwise
  - Example: `if version_at_least "$rust_ver" "1.85"; then`
- **`version_less_than(version, target)`**: Check if version < target (convenience wrapper)
  - Returns: 0 (true) if v1 < v2, 1 (false) otherwise
  - Example: `if version_less_than "$node_ver" "20"; then`
- **`ask_yes_no(prompt, default)`**: Interactive yes/no prompt with default (used throughout)
  - Returns: 0 for yes, 1 for no
  - Example: `if ask_yes_no "Install Rust?" Y; then` or `if ask_yes_no "Overwrite?" N; then`
- **`get_llm_config_dir()`**: Get llm config directory path, cached for performance (used in Phase 2-4)
  - Returns: Path to `~/.config/io.datasette.llm/` (or equivalent)
  - Example: `TEMPLATES_DIR="$(get_llm_config_dir)/templates"`
- **`install_or_upgrade_uv_tool(tool_source, [is_git_package])`**: Unified uv tool installation/upgrade with intelligent source detection (used in Phase 2, 6)
  - **Intelligent source detection**: Uses `uv tool list --show-version-specifiers` to check current installation source
  - **Git packages** (is_git_package=true):
    - If already from **same git URL** → uses `uv tool upgrade` (efficient, checks for new commits)
    - If from **PyPI or different git URL** → uses `uv tool install --force` (migration needed)
    - Provides clear logging showing migration path (PyPI→git or git1→git2)
  - **PyPI packages** (is_git_package=false): Uses `uv tool upgrade` for efficiency
  - Automatically extracts tool name from git URLs (e.g., `git+https://github.com/user/llm` → `llm`)
  - **Implementation**: Parses uv's output format: `llm v0.27.1 [required:  git+https://github.com/...]` to detect git sources
  - **Why this matters**: uv remembers the original installation source. Without source detection, `uv tool upgrade` checks the original source (PyPI→PyPI, git→git). The intelligent detection only forces reinstall when source switching is needed.
- **`update_shell_rc_file(rc_file, integration_file, shell_name)`**: Updates bash/zsh RC files with integration (used in Phase 4)
- **`install_or_upgrade_uv()`**: Install or upgrade uv via pipx and configure system Python preference (used in Phase 1)
- **`install_or_upgrade_rust()`**: Install or upgrade Rust with intelligent version detection (used in Phase 1)
  - Uses apt if repo version >= 1.85, otherwise falls back to rustup
  - Prompts user if upgrade needed from old apt version to rustup
- **`install_or_upgrade_nodejs()`**: Install or upgrade Node.js with intelligent version detection (used in Phase 1)
  - Uses apt if repo version >= 20, otherwise falls back to nvm (Node 22)
  - Also ensures npm is installed
- **`detect_npm_permissions()`**: Detect if npm needs sudo for global installs (used in Phase 1)
  - Sets `NPM_NEEDS_SUDO` and `NPM_PREFIX` environment variables
- **`configure_azure_openai()`**: Centralized Azure OpenAI configuration prompts (used in Phase 2)
- **`install_rust_via_rustup()`**: Low-level helper to install Rust via rustup (called by `install_or_upgrade_rust`)
- **`update_rust_via_rustup()`**: Low-level helper to update Rust via rustup (called by `install_or_upgrade_rust`)
- **`update_template_file(template_name)`**: Smart template update with checksum tracking
  - Compares repository version vs installed version using SHA256 checksums
  - Stores checksums in `~/.config/llm-tools/template-checksums`
  - Auto-updates if user hasn't modified the file (installed checksum = stored checksum)
  - Prompts user if local modifications detected (installed checksum ≠ stored checksum)
  - Used in Phase 3 for llm.yaml and llm-code.yaml templates
- **`install_or_upgrade_cargo_tool(tool_name)`**: Install/upgrade cargo tools from crates.io (used in Phase 5)
  - Checks if installed, provides feedback, runs `cargo install`
  - Used for argc (crates.io packages only)
- **`install_or_upgrade_cargo_git_tool(tool_name, git_url)`**: Install/upgrade cargo tools from git with commit-hash tracking (used in Phase 5)
  - Stores commit hash in `~/.config/llm-tools/{tool}-commit`
  - Only rebuilds when upstream has new commits (avoids unnecessary recompilation)
  - Used for asciinema, yek (git packages that change frequently)
- **`install_go()`**: Install Go if not present or version is insufficient (used in Phase 5)
  - Returns 0 if Go >= 1.22 is available, 1 otherwise
  - Only installs from apt - warns and skips if repo version insufficient
  - Called lazily only when Gemini is configured (for imagemage)
- **`extract_plugin_name(source)`**: Extract normalized plugin name from various source formats (used in Phase 2)
  - Git URLs: `git+https://github.com/user/llm-foo` → `llm-foo`
  - Local paths: `/path/to/llm-foo` → `llm-foo`
  - PyPI names: `llm-foo` → `llm-foo` (passthrough)
- **`install_or_upgrade_llm_plugin(plugin_source)`**: **DEPRECATED** - Smart LLM plugin installation (legacy, kept for backward compatibility)
  - **Deprecated**: Plugins are now installed via consolidated `uv tool install --with` in Phase 2
  - Logs deprecation warning and delegates to `llm install --upgrade`
- **`cleanup_stale_local_plugin_paths()`**: Remove stale local plugin paths from tracking files (used in Phase 2)
  - Handles migration from local plugins to git repositories
  - Cleans **two files**: `uv-tool-packages.json` (llm-uv-tool) and `uv-receipt.toml` (uv internal)
  - Scans for local paths (`/path/to/...`) that no longer exist on disk
  - Removes stale entries before llm upgrade to prevent failures
- **`remove_plugin_from_tracking(plugin_name)`**: Remove a specific plugin by name from both tracking files (used in Phase 2)
  - Cleans both `uv-tool-packages.json` and `uv-receipt.toml`
  - Must run BEFORE any llm operations - invalid local paths cause failures
- **`npm_install(package_name)`**: NPM global installation with retry logic (used in Phase 6)
  - 3 attempts with 2-second delay between retries
  - Handles ENOTEMPTY errors by detecting and removing conflicting directories
  - Auto-detects if sudo is required based on write permissions
- **`install_or_upgrade_npm_global(package_name)`**: Version-aware npm package management (used in Phase 6)
- **`upgrade_npm_global_if_installed(package_name)`**: Conditional upgrade only if package already installed
- **`prompt_for_session_log_dir()`**: Interactive first-run prompt for session log storage preference (used in Phase 4)
- **`prompt_for_session_log_silent()`**: Interactive first-run prompt for silent mode preference (used in Phase 4)

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
2. Extract and compare version (minimum required: 1.85 for edition2024 cargo tools)
3. Choose installation method based on availability and current state

**Installation Logic:**
- **If Rust not installed:**
  - Uses rustup if already available
  - Installs from apt if repo version ≥ 1.85
  - Falls back to rustup if repo version < 1.85
- **If Rust already installed via rustup:**
  - Automatically updates via `rustup update stable`
- **If Rust already installed via apt and version < 1.85:**
  - Prompts user: `"Install Rust 1.85+ via rustup? This will shadow the system installation. (Y/n)"`
  - Default: Yes (critical for some cargo tools)
  - If accepted: Installs rustup (shadows system packages via PATH)
  - If declined: Warns that some cargo tool builds may fail

**Coexistence Strategy:**
- rustup installs to `~/.cargo/bin` (already prioritized in PATH)
- System packages remain in `/usr/bin` (harmless when shadowed)
- No package removal needed (consistent with Node.js handling)
- Uses `-y` flag in rustup installer to prevent blocking prompts

**Why This Matters:** Prevents `cargo install` failures caused by outdated Rust versions. Some modern cargo tools require edition2024 support (Rust 1.85+).

### Node.js Installation Strategy

The script uses **intelligent version detection** similar to the Rust approach:

**Version Detection Pattern:**
1. Check repository Node.js version via `apt-cache policy nodejs`
2. Extract and compare version (minimum required: 20 for Claude Code)
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

**Why This Matters:** Claude Code requires Node.js 20+ for modern JavaScript features and APIs.

### Go Installation Strategy

The script uses a **simple apt-only approach** for Go installation:

**Version Detection Pattern:**
1. Check if Go is already installed and version >= 1.22
2. Check repository Go version via `apt-cache policy golang-go`
3. Install from apt if version sufficient, otherwise warn and skip

**Installation Logic:**
- **If Go already installed >= 1.22:** Use existing installation
- **If Go not installed and repo version >= 1.22:** Install from apt
- **If Go version insufficient:** Warn and skip imagemage installation

**No Manual Download:** Unlike Rust (rustup) or Node.js (nvm), Go is not downloaded from external sources. If the apt version is insufficient, imagemage is simply skipped with a warning.

**Why This Matters:** imagemage (Gemini image generation CLI) requires Go 1.22+. The tool is optional, so skipping it on older systems is acceptable.

### Provider Configuration: Azure OpenAI and Google Gemini

The system supports **Azure OpenAI** and **Google Gemini** providers:

**First-Run Behavior:**
- Prompts for Azure OpenAI configuration (Y/n) - default choice for enterprise
- **Only if Azure is declined**: Prompts for Google Gemini configuration (y/N)

**Switching Providers:**
```bash
# Switch to or reconfigure Azure
./install-llm-tools.sh --azure

# Switch to or reconfigure Gemini
./install-llm-tools.sh --gemini

# ERROR: Cannot use both flags simultaneously
./install-llm-tools.sh --azure --gemini  # Script will exit with error
```

**Provider Configuration:**
- **Flag validation**: Script errors out if both `--azure` and `--gemini` are specified (fail fast)
- When `--azure` flag is used: Sets `GEMINI_CONFIGURED=false`
- When `--gemini` flag is used: Sets `AZURE_CONFIGURED=false`
- Both providers can coexist for `llm` CLI via different model IDs

**Azure OpenAI Configuration:**
- Model IDs use `azure/` prefix (e.g., `azure/gpt-4.1-mini`, `azure/gpt-4.1-nano`)
- Default model: `azure/gpt-4.1-mini` (balanced, recommended for most tasks)
- Available models: `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`, `o4-mini`, plus legacy `gpt-5`, `gpt-5-mini`, `gpt-5-nano`
- Migration logic: Script automatically updates existing `azure/gpt-5*` defaults to `azure/gpt-4.1-mini`
- **Chat models**: `~/.config/io.datasette.llm/extra-openai-models.yaml` (OpenAI compatibility layer)
- **Embedding models**: `~/.config/io.datasette.llm/azure/config.yaml` (llm-azure plugin)
- API keys managed via `llm keys set azure` (not `openai`)
- Each chat model entry requires: `model_id`, `model_name`, `api_base`, `api_key_name: azure`

**Azure Embedding Models (llm-azure plugin):**
- Plugin: `llm-azure` (installed conditionally when Azure is configured)
- Config file: `~/.config/io.datasette.llm/azure/config.yaml`
- Default embedding model: `azure/text-embedding-3-small`
- Used by llm-tools-rag for RAG functionality
- **API base difference**: Chat models use full path (`https://RESOURCE.openai.azure.com/openai/v1/`), but embedding models need just the base endpoint (`https://RESOURCE.openai.azure.com`). The install script automatically strips the `/openai/v1/` suffix when generating the embedding config.
- **API version**: Uses `2024-10-21` (latest GA version; the `AzureOpenAI` client requires explicit api_version)
- **Why two config files?**: The built-in openai_models plugin doesn't support Azure embeddings. The llm-azure plugin provides native Azure SDK support for embeddings via a separate config file. Both plugins coexist - they read different files and register different model types.

**Google Gemini Configuration:**
- Uses `llm-gemini` plugin (installed in Phase 2 via ALL_PLUGINS)
- API key managed via `llm keys set gemini`
- Free tier available from Google AI Studio
- Models: `gemini-2.5-flash`, `gemini-2.5-pro`, etc.

**Helper Functions:**
- **`configure_azure_openai()`**: Prompts for Azure API key and resource URL
- **`configure_gemini()`**: Prompts for Gemini API key with link to get free key

**When adding new models**: Follow the appropriate provider's format (Azure or Gemini).

### Claudo: Claude Code in Docker

**Claudo** is a lightweight wrapper that runs Claude Code inside a Docker container for isolation:

**Purpose:**
- Protects your system from potentially dangerous AI operations
- Mounts your current directory for filesystem access
- Maintains separate authentication via `~/.claudo` (isolated from host `~/.claude`)

**Installation (Phase 6):**
- Only installed when Docker is detected (`command -v docker`)
- Downloaded from: `https://raw.githubusercontent.com/c0ffee0wl/claudo/main/claudo`
- Installed to: `~/.local/bin/claudo`

**Usage:**
```bash
# Run Claude Code in container with current directory mounted
claudo

# With additional options
claudo --no-sudo          # Run without sudo capabilities
claudo --docker-socket    # Mount Docker socket for sibling containers
```

**Key Features:**
- Uses pre-built Docker image: `ghcr.io/c0ffee0wl/claudo:latest`
- Mounts current directory to `/workspaces/<dirname>`
- Network restriction capability using httpjail
- Named persistent containers support

**Source:** https://github.com/c0ffee0wl/claudo

### Claude Code Router: Flexible Provider Support

**Claude Code Router supports flexible provider configurations**:

**Supported Configurations:**
1. **Dual-Provider (Azure + Gemini)**: Azure primary for all tasks, Gemini for web search
2. **Gemini-Only**: Single provider for all routing (default, background, think, longContext, webSearch)

**Architecture:**
- Configuration file: `~/.claude-code-router/config.json`
- Transformer plugin: `~/.claude-code-router/plugins/strip-reasoning.js` (Azure configs only)
- LOG_LEVEL: Always set to `"warn"` (not `"debug"`)

**Installation Logic (Phase 6):**
- **Independent of configuration flags**: Checks actual key store (`llm keys get azure/gemini`)
- If Azure key exists but Gemini doesn't → Prompts to configure Gemini for web search
- Exports provider keys to `~/.profile` (`AZURE_OPENAI_API_KEY`, `GEMINI_API_KEY`)
- Only installs CCR if at least one provider is configured
- Generates appropriate config based on available providers

**Configuration Management:**
- Uses **checksum tracking** (like templates) to preserve user modifications
- Auto-updates if config hasn't been modified by user
- Prompts before overwriting if local modifications detected
- Creates timestamped backups when overwriting: `config.json.backup-YYYYMMDD-HHMMSS`
- Checksum stored in: `~/.config/llm-tools/template-checksums` (entry: `ccr-config`)

**Dynamic Configuration:**
- Azure API base extracted from `extra-openai-models.yaml` (same source as llm CLI)
- Environment variables use placeholders: `$AZURE_OPENAI_API_KEY`, `$GEMINI_API_KEY`
- `$HOME` dynamically expanded to actual path (e.g., `/home/kali`)
- Config auto-adapts based on which providers have keys configured

**Key Features:**
- ✅ Flexible provider support (dual or single)
- ✅ Specialized routing when using Azure + Gemini
- ✅ Configuration survives provider flag changes (`--azure`/`--gemini`)
- ✅ User modifications preserved via checksum tracking
- ✅ Auto-disables Claude Code updater (`DISABLE_AUTOUPDATER=1` in `llm-common.sh`)

**Dual-Provider Auto-Configuration:**
- When Azure is configured but Gemini is not, Phase 6 prompts to configure Gemini for web search
- Enables the dual-provider routing (Azure primary + Gemini web search) automatically
- This allows Claude Code Router to use Azure for all tasks except web search, which uses Gemini

**Environment Variables:**
- Must run `source ~/.profile` to load in current session
- Automatically available in new login shells
- Provider keys exported based on availability

**Routing Configurations:**

*Dual-Provider (Azure + Gemini):*
```json
"Router": {
  "default": "azure-codex,gpt-5.1-codex",
  "background": "azure-gpt4,gpt-4.1-mini",
  "think": "azure-codex,gpt-5.1-codex",
  "longContext": "azure-codex,gpt-5.1-codex",
  "webSearch": "gemini,gemini-2.5-flash"
}
```

*Gemini-Only:*
```json
"Router": {
  "default": "gemini,gemini-2.5-pro",
  "background": "gemini,gemini-2.5-flash",
  "think": "gemini,gemini-2.5-pro",
  "longContext": "gemini,gemini-2.5-pro",
  "webSearch": "gemini,gemini-2.5-flash"
}
```

### Terminator Assistant Integration

See [`llm-assistant/CLAUDE.md`](llm-assistant/CLAUDE.md) for comprehensive documentation on the llm-assistant terminal assistant, including architecture, components, usage, and troubleshooting.

### Workflow Engine

See [`burr_workflow/CLAUDE.md`](burr_workflow/CLAUDE.md) for documentation on the YAML-based workflow engine built on Burr, including the compilation pipeline, protocol-based integration, action system, and security features.

### Speech-to-Text Transcription

The repository includes **speech-to-text transcription tools** using onnx-asr with NVIDIA's Parakeet TDT model:

**Components**:
- **Handy**: System-wide STT application (installed via .deb, x86_64 only)
- **onnx-asr**: Speech recognition library with ONNX Runtime backend
- **nemo-parakeet-tdt-0.6b-v3-int8**: INT8 quantized model (smaller, faster)
- **transcribe**: CLI wrapper script for file transcription
- **pydub**: Audio format conversion (requires ffmpeg)

**Handy Integration**:
- Handy provides OS-level voice input accessible from any application
- When Handy is running, llm-assistant's built-in voice input is automatically disabled
- Both use the same shared INT8 model to avoid duplication

**Features**:
- **25 European languages** with automatic language detection
- **High accuracy**: Average WER 6.34% on Hugging Face Open ASR Leaderboard
- **Auto-punctuation**: Automatic punctuation and capitalization
- **Format conversion**: mp3, mp4, m4a, flac, ogg, webm via pydub/ffmpeg

**Supported Languages**:
Bulgarian (bg), Croatian (hr), Czech (cs), Danish (da), Dutch (nl), English (en),
Estonian (et), Finnish (fi), French (fr), German (de), Greek (el), Hungarian (hu),
Italian (it), Latvian (lv), Lithuanian (lt), Maltese (mt), Polish (pl), Portuguese (pt),
Romanian (ro), Slovak (sk), Slovenian (sl), Spanish (es), Swedish (sv), Russian (ru),
Ukrainian (uk)

**Usage**:
```bash
# Basic transcription (outputs to stdout)
transcribe recording.mp3

# Save to file
transcribe video.mp4 -o transcript.txt

# Suppress progress messages
transcribe meeting.m4a 2>/dev/null | less
```

**Model Information**:
- Model: `nemo-parakeet-tdt-0.6b-v3` INT8 quantized (600M parameters)
- Size: ~670MB total (encoder: 652MB, decoder: 18MB, plus config files)
- **Shared location**: `~/.local/share/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8/`
- Downloaded from HuggingFace during installation (not on first use)
- Audio: Non-WAV formats converted via pydub/ffmpeg; resampling handled by onnx-asr

**Comparison with llm-assistant Voice Input**:
Both `transcribe` and llm-assistant's voice input use the same Parakeet TDT model via onnx-asr.
The difference is:
- **transcribe**: File-based transcription for pre-recorded audio
- **llm-assistant**: Real-time microphone input with streaming

**Note**: This tool supports 25 European languages. For 99+ language support (including Asian
languages), consider using the standalone whisper-ctranslate2 tool: `uv tool install whisper-ctranslate2`

### Text Expansion with espanso

The repository includes **espanso** text expander with LLM integration:

**Components**:
- **espanso**: Cross-platform text expander (installed via .deb, x86_64 only)
- **espanso-llm**: LLM integration package using llm-assistant daemon

**Installation**:
- Only installed when a desktop environment is detected (`has_desktop_environment`)
- Automatically selects X11 or Wayland variant based on `$XDG_SESSION_TYPE`
- Service registered and started automatically after installation

**Triggers**:
| Trigger | Mode | Clipboard | Description |
|---------|------|-----------|-------------|
| `:llm:` | simple | no | Quick query without tools |
| `:llmc:` | simple | yes | Simple mode with clipboard context |
| `:@:` | assistant | no | Full inline-assistant with tools |
| `:@c:` | assistant | yes | Inline-assistant with clipboard context |

**Usage**:
- Type a trigger (e.g., `:@:`) in any text field
- Enter your question in the popup dialog
- The AI response replaces the trigger text

**Requirements**:
- llm-assistant daemon running (auto-started on first use)
- Uses Unix socket communication (no HTTP server needed)

**Troubleshooting**:
- If espanso doesn't start: `espanso service register && espanso start`
- Check status: `espanso status`
- View logs: `espanso log`
- Reload config: `espanso restart`

### Ulauncher LLM Extension

The repository includes **ulauncher-llm**, a Ulauncher extension for accessing the llm-daemon:

**Components**:
- **Ulauncher**: Application launcher (installed via .deb)
- **ulauncher-llm**: Extension connecting to llm-assistant daemon

**Keywords**:
| Keyword | Mode | Clipboard | Description |
|---------|------|-----------|-------------|
| `llm` | simple | no | Quick AI query without tools |
| `llmc` | simple | yes | Simple mode with clipboard context |
| `@` | assistant | no | Full assistant with tools (execute code, search, etc.) |
| `@c` | assistant | yes | Assistant with clipboard context |

**Features**:
- Streaming responses with live UI updates
- Tool execution feedback (shows "[Executing Python...]", "[Searching Google...]")
- Slash commands: `/new` (fresh conversation), `/status` (session info), `/help`
- Persistent conversations within Ulauncher session
- Copy options: Enter copies plain text, Alt+Enter copies markdown

**Usage**:
1. Launch Ulauncher (default: Ctrl+Space or Meta key)
2. Type `llm what is 2+2?` for simple query
3. Type `@ explain this error` for assistant mode with tools
4. Press Enter to copy response to clipboard

**Architecture**:
- Connects to llm-assistant daemon via Unix socket
- Uses GLib.idle_add for thread-safe UI updates
- Session ID persists across queries for conversation continuity

**Installation**:
- Ulauncher deb package installed automatically when desktop environment detected
- Extension symlinked to `~/.local/share/ulauncher/extensions/ulauncher-llm`

**Troubleshooting**:
- If extension doesn't appear: Restart Ulauncher
- If daemon not running: Extension auto-starts it, or run `llm-assistant --daemon`
- Check daemon status: `@ /status`

## Common Commands

### Installation and Updates

```bash
# First-time installation (full mode - all tools)
./install-llm-tools.sh

# Minimal installation (LLM core only - skips Claude Code, shell integration, etc.)
./install-llm-tools.sh --minimal

# Full installation (override saved --minimal preference)
./install-llm-tools.sh --full

# Update all tools (pulls git updates, upgrades packages, preserves config)
./install-llm-tools.sh
```

**Installation Modes:**

| Flag | Description |
|------|-------------|
| `--minimal` | Install only LLM core tools. Persists for future runs. |
| `--full` | Install all tools. Overrides saved `--minimal` preference. |
| (no flag) | Uses saved preference, or defaults to full mode on first run. |

**What `--minimal` mode includes:**
- LLM CLI with all plugins (providers, tools, fragments, utilities)
- Provider configuration (Azure/Gemini setup prompts)
- Templates (llm.yaml, llm-code.yaml, etc.)
- gitingest, llm-observability, llm-server, toko

**What `--minimal` mode excludes:**
- Rust installation (only needed for asciinema, argc, yek)
- Node.js installation (only needed for Claude Code, CCR, npm packages)
- asciinema (session recording)
- Shell integration (llm wrapper function, session logging, Ctrl+N keybinding)
- Context wrapper script
- MCP servers (microsoft-learn, aws-knowledge, arxiv, chrome-devtools)
- llm-assistant, llm-inlineassistant, terminator plugin
- Additional CLI tools: tldr, transcribe, files-to-prompt, argc, yek, micro, xclip
- Claude Code, Claudo, Claude Code Router, Codex CLI
- Desktop tools: espanso, ulauncher, Handy, llm-guiassistant

**Mode persistence:** The preference is saved to `~/.config/llm-tools/install-mode` and automatically used on subsequent runs unless overridden with `--full`.

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

**Rust version too old**: Script auto-detects and prompts to install rustup if < 1.85. Manual fix: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`

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

### Troubleshooting Tab Completion

**Tab completion not working**: Verify zsh completion system is loaded: `which compinit` should show a path. Re-source your `.zshrc` or start a new shell.

**Completions show old commands**: Clear the zsh completion cache: `rm -f ~/.zcompdump*` then restart your shell.

**Code/rag subcommands not completing**: Check if custom modifications are applied: `grep -q "'code:Generate code" "$SCRIPT_DIR/integration/llm-zsh-plugin/completions/_llm"`. Re-run the installation script to apply modifications.

**Model/template completions not appearing**: Ensure llm is accessible: `which llm` and that you have configured at least one model. The plugin dynamically fetches available models via `llm models list`.

**Completion conflicts or errors**: Delete the plugin and let the script reinstall: `rm -rf "$SCRIPT_DIR/integration/llm-zsh-plugin"` then run `./install-llm-tools.sh`.

### Adding New LLM Plugins

Add to the `ALL_PLUGINS` array in Phase 2 of `install-llm-tools.sh`:

```bash
ALL_PLUGINS=(
    # Plugin management (must be first)
    "git+https://github.com/c0ffee0wl/llm-uv-tool"
    # Provider plugins
    "git+https://github.com/c0ffee0wl/llm-gemini"
    "llm-anthropic"
    # Add new plugin here
    "git+https://github.com/user/repo"  # For git-based plugins
    # Local plugins (at the end, after dependencies)
    "$SCRIPT_DIR/llm-tools-core"
    "$SCRIPT_DIR/llm-tools-context"
)
```

All plugins are installed in a single `uv tool install --force --with ...` command for better performance (single dependency resolution pass instead of 30+ separate operations).

### Modifying Installation Script

When adding new functionality to `install-llm-tools.sh`:

1. **Use existing helper functions** where possible:
   - For apt packages: `install_apt_package package_name [command_name]`
   - For version comparison: `version_at_least "$ver" "1.85"` or `version_less_than "$ver" "20"`
   - For Y/n prompts: `if ask_yes_no "Question?" Y; then`
   - For llm config dir: `$(get_llm_config_dir)/templates`
   - For uv installation: `install_or_upgrade_uv`
   - For uv tools from PyPI: `install_or_upgrade_uv_tool tool_name`
   - For uv tools from git: `install_or_upgrade_uv_tool "git+https://github.com/user/repo" true`
   - For Rust installation: `install_or_upgrade_rust`
   - For cargo tools: `install_or_upgrade_cargo_tool tool_name [git_url]`
   - For Node.js installation: `install_or_upgrade_nodejs`
   - For npm permission detection: `detect_npm_permissions`
   - For shell RC updates: `update_shell_rc_file rc_file integration_file shell_name`
   - For Azure config: `configure_azure_openai`
   - For template updates: `update_template_file template_name`

2. **Follow the phase structure**: Add new installations to the appropriate phase
3. **Maintain idempotency**: Check if tools/configs exist before installing/modifying
4. **Test with syntax check**: `bash -n install-llm-tools.sh`
5. **Preserve self-update logic**: Never move or remove Phase 0

**Adding new templates**: Simply add a new YAML file to `llm-templates/` and add one line to Phase 3: `update_template_file "newtemplate"`

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
- `llm`: Uses `install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/llm" true` with force reinstall for git packages
- Plugins: `llm install <plugin> --upgrade`
- `gitingest`: `install_or_upgrade_uv_tool gitingest` (PyPI package, uses upgrade)
- `files-to-prompt`: `install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/files-to-prompt" true` (force reinstall)
- `asciinema`: `install_or_upgrade_cargo_git_tool asciinema https://github.com/asciinema/asciinema` (with commit-hash tracking)
- Claude Code: `npm install -g @anthropic-ai/claude-code`
- Claude Code Router: `npm install -g @musistudio/claude-code-router`
- `imagemage`: Clone and build from source (only if Gemini configured, Go 1.22+ available)

**Important Note on llm Upgrades**: Since llm is installed from a git repository fork (`git+https://github.com/c0ffee0wl/llm`), the script uses `install_or_upgrade_uv_tool` with `is_git_package=true` which intelligently detects the current installation source:
- **If already from the fork**: Uses `uv tool upgrade` (efficient, checks for new commits)
- **If from PyPI**: Forces reinstall to migrate to the fork (one-time migration)
- **If from different git URL**: Forces reinstall to switch sources

This is critical because `uv tool upgrade` respects the original installation source—if llm was previously installed from PyPI, upgrade would check PyPI instead of the fork. The intelligent source detection ensures efficient updates while handling migrations automatically.

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
- `~/.config/io.datasette.llm/extra-openai-models.yaml` - Azure OpenAI chat model definitions
- `~/.config/io.datasette.llm/azure/config.yaml` - Azure OpenAI embedding model definitions (llm-azure plugin)
- `~/.config/io.datasette.llm/templates/{llm,llm-code,llm-wut,llm-assistant,llm-assistant-report}.yaml` - Custom LLM templates
- `~/.codex/config.toml` - Codex CLI configuration with Azure OpenAI credentials (auto-generated)
- `~/.claude-code-router/config.json` - Claude Code Router dual-provider configuration (auto-generated with checksum tracking)
- `~/.claude-code-router/plugins/strip-reasoning.js` - CCR transformer plugin for reasoning token handling
- `~/.profile` - Environment variables for providers (AZURE_OPENAI_API_KEY, AZURE_RESOURCE_NAME, GEMINI_API_KEY)
- `~/.config/llm-tools/asciinema-commit` - Tracks asciinema version for update detection
- `~/.config/llm-tools/template-checksums` - Tracks template and CCR config checksums for smart updates
- `~/.config/llm-tools/plugins-hash` - SHA256 hash of ALL_PLUGINS array + git commit for smart reinstall detection
- `~/.config/llm-tools/install-mode` - Persisted installation mode preference (`full` or `minimal`)
- `~/.config/terminator/plugins/terminator_assistant.py` - Terminator assistant plugin (see [`llm-assistant/CLAUDE.md`](llm-assistant/CLAUDE.md))
- `~/.config/micro/plug/llm/` - Micro editor llm-micro plugin
- `~/.config/micro/settings.json` - Micro editor configuration (optional)
- `$SESSION_LOG_DIR/*.cast` - Session recordings (default: `/tmp/session_logs/asciinema/`)
- `~/.local/share/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8/` - Shared INT8 Parakeet model (used by Handy and llm-assistant)
- `~/.config/espanso/` - espanso text expander configuration directory
- `~/.config/espanso/match/packages/espanso-llm/` - espanso LLM integration package
- `~/.local/share/ulauncher/extensions/ulauncher-llm/` - Ulauncher LLM extension (symlinked to repository)
- `~/.config/wireplumber/wireplumber.conf.d/50-alsa-config.conf` - PipeWire VM audio fix (auto-generated in VMs)

### Repository Structure
- `install-llm-tools.sh` - Main installation/update script with self-update 
- `integration/llm-common.sh` - Shared shell config, llm wrapper, asciinema auto-recording
- `integration/llm-integration.{bash,zsh}` - Shell-specific keybindings (Ctrl+N) and tab completion setup
- `integration/llm-zsh-plugin/` - Cloned llm-zsh-plugin with custom extensions
- `integration/llm-zsh-plugin/completions/_llm` - Tab completion definitions (includes custom code/rag)
- `llm-assistant/` - Assistant components: llm_assistant/ package, terminator-assistant-plugin/, llm-tools-assistant/ (see [`llm-assistant/CLAUDE.md`](llm-assistant/CLAUDE.md))
- `burr_workflow/` - YAML-based workflow engine built on Burr (see [`burr_workflow/CLAUDE.md`](burr_workflow/CLAUDE.md))
- `llm-tools-context/` - Context extraction package (CLI + LLM tool) for terminal history
- `ulauncher-llm/` - Ulauncher extension for llm-daemon access (simple + assistant modes)
- `llm-templates/{llm,llm-code,llm-wut,llm-assistant,llm-assistant-report}.yaml` - Template sources installed to user config
- `docs/MICROSOFT_MCP_SETUP.md` - Comprehensive guide for Codex CLI, Azure MCP, Lokka, and Microsoft Learn MCP
- `.git/hooks/pre-commit` - Automatic TOC updater for README.md

## Key Constraints & Design Decisions

1. **Provider Configuration Patterns**:
   - **Claude Code Router: Flexible** - Supports dual-provider (Azure + Gemini) OR Gemini-only configurations. When both keys exist: Azure serves as primary, Gemini handles web search. When only Gemini exists: all routing uses Gemini
   - **llm CLI: BOTH providers supported** - Can use both providers via different model IDs
   - CCR checks actual key store (`llm keys get`) independent of configuration flags, auto-adapting config based on available providers
2. **Azure Foundry Format**: When using Azure, all model configs use Azure format with `azure/` prefix (NOT standard OpenAI API)
3. **Debian/Ubuntu/Kali**: Uses `apt-get` for system packages; would need modification for RHEL/Arch
4. **Interactive Prompts on First Run**: The script prompts for provider choice (Azure or Gemini) on first run only; subsequent runs preserve existing configuration automatically
5. **Simplified Configuration**: No manual YAML editing required - provider configs are automatically generated via helper functions
6. **Git Repository Required**: Self-update only works when cloned from git (not if downloaded as ZIP)
7. **Path Assumptions**: The script assumes it can write to `~/.bashrc`, `~/.zshrc`, `~/.config/io.datasette.llm/`, and `~/.claude-code-router/`
8. **Asciinema Dependency**: Context system requires `asciinema` to be installed for session recording
9. **Context Script Location**: The `context` script must be in `$PATH` for the `llm-tools-context` plugin to work
10. **NPM Permissions**: The script detects if npm requires sudo for global installs and adapts accordingly
11. **Rust Required**: asciinema is installed via cargo (Rust's package manager); minimum Rust 1.85 required
12. **Rust Version Management**: Script automatically detects outdated Rust and offers to upgrade via rustup with user approval (default: Yes)
13. **rustup vs apt Coexistence**: rustup and apt-installed Rust can coexist safely; rustup takes precedence via PATH
14. **Node.js Version Management**: Script automatically detects Node.js version and installs via nvm if repository version < 20
15. **Go Optional**: Go 1.22+ is required for imagemage but installation is apt-only; if repo version is insufficient, imagemage is skipped with a warning
16. **Per-Pane Recording in tmux**: Each tmux pane gets its own independent recording session (intentional design for workflow isolation)

## Special Packages & Forks

Note that several packages use **forks** or specific sources:
- **llm**: Installed from git repository fork: `git+https://github.com/c0ffee0wl/llm` (forked from simonw/llm with markdown markup enhancements)
- **llm-uv-tool**: Installed from git repository fork: `git+https://github.com/c0ffee0wl/llm-uv-tool` (bundled with llm installation via `--with` flag, makes plugins persist across LLM upgrades)
- **llm-cmd**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-cmd`
- **llm-cmd-comp**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-cmd-comp`
- **llm-tools-llm-functions**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-tools-llm-functions` (bridge for optional llm-functions integration)
- **llm-tools-sandboxed-shell**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-tools-sandboxed-shell` (sandboxed shell command execution)
- **llm-tools-patch**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-tools-patch` (file manipulation tools: read, write, edit, multi_edit, info)
- **llm-vertex**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-vertex` (Google Vertex AI Gemini models integration)
- **llm-fragments-youtube-transcript**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-fragments-youtube-transcript` (YouTube video transcript extraction with metadata)
- **llm-templates-fabric**: Uses Damon McMinn's fork: `git+https://github.com/c0ffee0wl/llm-templates-fabric`
- **files-to-prompt**: Uses Dan Mackinlay's fork: `git+https://github.com/c0ffee0wl/files-to-prompt`
- **llm-zsh-plugin**: Forked in-repository from eliyastein/llm-zsh-plugin with custom modifications for `code` and `rag` subcommands
- **asciinema**: Installed from git source via cargo: `cargo install --locked --git https://github.com/asciinema/asciinema`
- **argc**: Installed via cargo from crates.io: `cargo install argc` (prerequisite for llm-functions, also useful standalone for Bash CLI development)
- **llm-tools-context**: Installed from local directory: `$SCRIPT_DIR/llm-tools-context`
- **llm-tools-google-search**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-tools-google-search` (Google Search tool using Vertex/Gemini as backend)
- **llm-tools-web-fetch**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-tools-web-fetch` (Web fetch tool for retrieving URL content)
- **llm-tools-fabric**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-tools-fabric` (Fabric pattern integration - run 230+ AI patterns as isolated subagents)
- **llm-tools-fragment-bridge**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-tools-fragment-bridge` (bridge converting fragment loaders to callable tools: load_yt, load_github, load_pdf)
- **llm-functions**: NOT automatically installed; users must install manually from https://github.com/sigoden/llm-functions/ if needed
- **imagemage**: Installed from Go package: `github.com/quinnypig/imagemage@latest` (only when Gemini is configured and Go 1.22+ available)
- **llm-anthropic**: Anthropic API plugin (PyPI)
- **llm-openrouter**: OpenRouter API plugin (PyPI)
- **llm-tools-quickjs**: QuickJS runtime for llm (PyPI)
- **llm-tools-sqlite**: SQLite query tool (PyPI)
- **llm-tools-mcp**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-tools-mcp` (MCP client for connecting to Model Context Protocol servers)
- **llm-azure**: Installed from git repository: `git+https://github.com/c0ffee0wl/llm-azure` (Azure OpenAI embedding models; installed conditionally when Azure is configured)
- llm is installed in the uv environment llm, calling python3 -c "import llm" wont work.

## MCP Client Integration (llm-tools-mcp)

The system includes **llm-tools-mcp** for connecting to MCP (Model Context Protocol) servers:

**Configuration**: `~/.llm-tools-mcp/mcp.json`

**Default Server**: Microsoft Learn MCP (`https://learn.microsoft.com/api/mcp`)

**Exposed Tools** (Microsoft Learn):
- `microsoft_docs_search` - Semantic search of Microsoft documentation
- `microsoft_docs_fetch` - Fetch documentation page as markdown
- `microsoft_code_sample_search` - Search official code samples

**Usage**:
```bash
# Use MCP tools in llm
llm --ta -T MCP "search for Azure authentication documentation"

# With custom config
llm --ta -T 'MCP("/path/to/custom/mcp.json")' "your prompt"
```

**Adding MCP Servers**: Edit `~/.llm-tools-mcp/mcp.json`:
```json
{
  "mcpServers": {
    "microsoft-learn": {
      "type": "http",
      "url": "https://learn.microsoft.com/api/mcp"
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/projects"]
    }
  }
}
```

**Transport Types**:
- `http` - Streamable HTTP (for remote servers like Microsoft Learn)
- `sse` - Server-Sent Events
- `stdio` - Local process (command + args)

**Pre-installed MCP Servers**:

The install script pre-configures these MCP servers in `~/.llm-tools-mcp/mcp.json`:

- **arxiv**: arXiv paper search and retrieval
  - Command: `arxiv-mcp-server` (installed via uv)
  - Usage: Search academic papers, get abstracts, download PDFs

- **chrome-devtools** (if Chrome/Chromium detected): Browser automation
  - Command: `npx chrome-devtools-mcp@latest --browser-url=http://127.0.0.1:9222`
  - Requires: Chrome launched with `--remote-debugging-port=9222`
  - Tools: navigate, screenshot, click, type, evaluate JavaScript

**llm-assistant Integration**: MCP tools are whitelisted via `EXTERNAL_TOOL_PLUGINS` and display nicely with custom action verbs.
