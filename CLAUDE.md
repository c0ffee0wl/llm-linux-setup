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
- **`llm chat`** → adds `-t assistant` unless user specifies `-t`, `-s`, `-c`, or `--cid`
- **`llm code`** → always adds `-t code` (outputs clean executable code without markdown)
- **`llm rag`** → routes to `aichat --rag` for RAG functionality (special handling)
- **Default prompts** → adds `-t assistant` unless user specifies template/system prompt/continuation
- **Excluded subcommands** (no template): models, keys, plugins, templates, tools, schemas, fragments, collections, embed, rag, etc.
- When modifying wrapper logic, update the `exclude_commands` array and `should_skip_template()` function

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

**When adding new shell features**:
- Shell-agnostic → `integration/llm-common.sh`
- Bash-specific (readline bindings) → `integration/llm-integration.bash`
- Zsh-specific (zle widgets) → `integration/llm-integration.zsh`
- **If adding new llm subcommands**: Update the completion file `integration/llm-zsh-plugin/completions/_llm`

### Installation Phases

The script is organized into numbered phases:

0. **Self-Update**: Git fetch/pull/exec pattern
1. **Prerequisites**: Install pipx, uv, Node.js, Rust/Cargo, asciinema, document processors (poppler-utils, pandoc)
2. **LLM Core**: Install/upgrade llm (with llm-uv-tool for persistent plugins), configure Azure OpenAI, create `extra-openai-models.yaml`, configure aichat
3. **LLM Plugins**: Install/upgrade all plugins using `llm install --upgrade` (llm-uv-tool intercepts for persistence; includes llm-tools-llm-functions bridge plugin, llm-tools-sandboxed-shell, llm-tools-patch)
4. **LLM Templates**: Install/update custom templates from `llm-template/` directory to `~/.config/io.datasette.llm/templates/`
5. **Shell Integration**: Add source statements to `.bashrc`/`.zshrc` (idempotent checks), llm wrapper includes RAG routing
6. **Additional Tools**: Install/update gitingest (uv), files-to-prompt (uv), aichat (cargo), argc (cargo), context script
7. **Claude Code & Router**: Install Claude Code, Claude Code Router (with dual-provider support: Azure primary, Gemini web search), and Codex CLI

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
- Upgrades: `uv tool upgrade llm` (llm-uv-tool persists automatically)

**Benefits**:
- ✅ Plugins persist across LLM upgrades automatically
- ✅ Faster script execution (plugins don't need reinstallation every run)
- ✅ More robust upgrade process
- ✅ No changes to user-facing commands
- ✅ Compatible with Python 3.10+

**Plugin Dependencies**: All plugin dependencies (like yt-dlp for YouTube transcripts) are automatically installed by pip when the plugin is installed. No manual installation required.

### Helper Functions (Code Reusability)

The installation script uses **helper functions** to eliminate code duplication and follow KISS principles:

- **`install_apt_package(package_name)`**: Installs apt packages with existence checks (used in Phase 1)
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
- **`install_or_upgrade_cargo_tool(tool_name)`**: Install/upgrade cargo tools from crates.io (used in Phase 6)
  - Checks if installed, provides feedback, runs `cargo install`
  - Used for aichat, argc (crates.io packages only)
- **`install_or_upgrade_cargo_git_tool(tool_name, git_url)`**: Install/upgrade cargo tools from git with commit-hash tracking (used in Phase 6)
  - Stores commit hash in `~/.config/llm-tools/{tool}-commit`
  - Only rebuilds when upstream has new commits (avoids unnecessary recompilation)
  - Used for asciinema, yek (git packages that change frequently)
- **`install_go()`**: Install Go if not present or version is insufficient (used in Phase 6)
  - Returns 0 if Go >= 1.22 is available, 1 otherwise
  - Only installs from apt - warns and skips if repo version insufficient
  - Called lazily only when Gemini is configured (for imagemage)

**Helper Functions Philosophy:**
These functions follow the DRY (Don't Repeat Yourself) principle and ensure consistent behavior across the script. When adding new features:
- **Always check** if an existing helper function can be used or extended
- **Create new helpers** for operations repeated more than twice
- **Keep helpers focused** - single responsibility per function

**When modifying the installation script**: Use these helper functions for consistency rather than duplicating installation logic.

**Note on AIChat Configuration**: AIChat configuration is now created inline in Phase 2 using heredocs directly at the point of use, rather than through separate helper functions. This follows YAGNI (You Aren't Gonna Need It) since each configuration is only created once per provider.

### Rust/Cargo Installation Strategy

The script uses **intelligent version detection** similar to the Node.js approach:

**Version Detection Pattern:**
1. Check repository Rust version via `apt-cache policy rustc`
2. Extract and compare version (minimum required: 1.85 for aichat edition2024)
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
  - Default: Yes (critical for aichat build)
  - If accepted: Installs rustup (shadows system packages via PATH)
  - If declined: Warns that aichat build will fail

**Coexistence Strategy:**
- rustup installs to `~/.cargo/bin` (already prioritized in PATH)
- System packages remain in `/usr/bin` (harmless when shadowed)
- No package removal needed (consistent with Node.js handling)
- Uses `-y` flag in rustup installer to prevent blocking prompts

**Why This Matters:** Prevents `cargo install` failures caused by outdated Rust versions. Modern tools like aichat v0.30.0+ require edition2024 support (Rust 1.85+).

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

### Provider Configuration: Azure OpenAI OR Google Gemini

The system supports **EITHER** Azure OpenAI **OR** Google Gemini (mutually exclusive for AIChat):

**First-Run Behavior:**
- Prompts for Azure OpenAI configuration (Y/n) - default choice for enterprise
- **Only if Azure is declined**: Prompts for Google Gemini configuration (y/N)
- Users can only configure one provider at a time for AIChat

**Switching Providers:**
```bash
# Switch to or reconfigure Azure
./install-llm-tools.sh --azure

# Switch to or reconfigure Gemini
./install-llm-tools.sh --gemini

# ERROR: Cannot use both flags simultaneously
./install-llm-tools.sh --azure --gemini  # Script will exit with error
```

**Mutual Exclusivity Implementation:**
- **Flag validation**: Script errors out if both `--azure` and `--gemini` are specified (fail fast)
- When `--azure` flag is used: Sets `GEMINI_CONFIGURED=false`
- When `--gemini` flag is used: Sets `AZURE_CONFIGURED=false`
- AIChat config is overwritten (with backup) when switching providers
- Both providers can coexist for `llm` CLI, but AIChat uses only one

**Azure OpenAI Configuration:**
- Model IDs use `azure/` prefix (e.g., `azure/gpt-4.1-mini`, `azure/gpt-4.1-nano`)
- Default model: `azure/gpt-4.1-mini` (balanced, recommended for most tasks)
- Available models: `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`, `o4-mini`, plus legacy `gpt-5`, `gpt-5-mini`, `gpt-5-nano`
- Migration logic: Script automatically updates existing `azure/gpt-5*` defaults to `azure/gpt-4.1-mini`
- Configuration stored in `~/.config/io.datasette.llm/extra-openai-models.yaml`
- API keys managed via `llm keys set azure` (not `openai`)
- Each model entry requires: `model_id`, `model_name`, `api_base`, `api_key_name: azure`

**Google Gemini Configuration:**
- Uses `llm-gemini` plugin (installed in Phase 3)
- API key managed via `llm keys set gemini`
- Free tier available from Google AI Studio
- Models: `gemini-2.5-flash`, `gemini-2.5-pro`, etc.

**Helper Functions:**
- **`configure_azure_openai()`**: Prompts for Azure API key and resource URL
- **`configure_gemini()`**: Prompts for Gemini API key with link to get free key

**When adding new models**: Follow the appropriate provider's format (Azure or Gemini).

### Claude Code Router: Flexible Provider Support

Unlike AIChat's mutual exclusivity, **Claude Code Router supports flexible provider configurations**:

**Supported Configurations:**
1. **Dual-Provider (Azure + Gemini)**: Azure primary for all tasks, Gemini for web search
2. **Gemini-Only**: Single provider for all routing (default, background, think, longContext, webSearch)

**Architecture:**
- Configuration file: `~/.claude-code-router/config.json`
- Transformer plugin: `~/.claude-code-router/plugins/strip-reasoning.js` (Azure configs only)
- LOG_LEVEL: Always set to `"warn"` (not `"debug"`)

**Installation Logic (Phase 7):**
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

**Environment Variables:**
- Must run `source ~/.profile` to load in current session
- Automatically available in new login shells
- Provider keys exported based on availability (not tied to AIChat configuration)

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

### RAG Integration with aichat

The system integrates **aichat** (https://github.com/sigoden/aichat) for Retrieval-Augmented Generation (RAG) capabilities:

**Architecture**:
- `aichat` is installed via cargo in Phase 6
- Automatically configured with the selected provider (Azure OR Gemini)
- Accessible via both `aichat` command and `llm rag` wrapper
- Built-in vector database (no external dependencies like ChromaDB)

**Embedding Models by Provider:**
- **Azure OpenAI**: Uses `azure-openai:text-embedding-3-small`
- **Google Gemini**: Uses `gemini:text-embedding-004`

**Configuration** (`~/.config/aichat/config.yaml`):
- Auto-generated inline in Phase 2 during provider configuration (no separate helper functions)
- Uses heredocs with variable expansion for direct configuration file creation
- Includes document loaders for Git repos (gitingest)
- **Config Preservation**:
  - Normal setup: If config exists, keeps it without prompting (preserves user customizations)
  - Forced switch (`--azure` or `--gemini`): Automatically overwrites with backup (user explicitly requested provider change)
- **Provider-specific**: Contains only the selected provider's configuration (mutually exclusive)

**llm Wrapper Integration** (`integration/llm-common.sh`):
- `llm rag` command routes to `aichat --rag` internally
- `llm rag` requires a RAG name parameter (consistent with `aichat --rag` behavior)
- `llm rag <name>` opens/creates RAG collection named `<name>`
- Arguments passed through: `llm rag mydata --rebuild` → `aichat --rag mydata --rebuild-rag`
- For interactive mode without RAG, use `aichat` directly

**Document Loaders** (configured in Phase 2):
- **PDF**: `pdftotext` (from poppler-utils package)
- **DOCX**: `pandoc` (from pandoc package)
- **Git Repos**: `gitingest` (already installed in Phase 6)

**Git Repository Loader Configuration**:
The git document loader is configured in `~/.config/aichat/config.yaml`:
```yaml
document_loaders:
  git: 'gitingest $1 -o -'
```

**How It Works**:
- Document loaders are triggered by **explicit prefix syntax** (not file extensions)
- The `git:` prefix must be used to invoke the gitingest loader
- Without the prefix, GitHub URLs are treated as regular web pages (HTML fetch)
- Works with both remote URLs and local paths

**Usage in `.edit rag-docs`**:
```
# Correct - uses gitingest loader
git:https://github.com/user/repo
git:/path/to/local/repo
git:https://github.com/user/repo/tree/main/src

# Incorrect - treated as web page, not repository
https://github.com/user/repo  # This fetches HTML, not source code!
```

**Gitingest Features**:
- Automatically clones remote repositories (temporary)
- Respects `.gitignore` files
- Extracts source code in LLM-friendly format
- Supports subdirectories via GitHub tree URLs
- Outputs to stdout (required for aichat integration)

**RAG Workflow**:
1. Create RAG collection: `llm rag mydocs` or `aichat --rag mydocs`
2. Add documents interactively via `.edit rag-docs` command in REPL
3. Query documents: Ask questions in the interactive session
4. Manage documents: Use `.rag` commands in REPL (`.help` for details)
5. Rebuild index: `llm rag mydocs --rebuild` after document changes

**Key Design Decisions**:
- **Unified credentials**: Single Azure configuration serves both llm and aichat
- **Dual access**: `llm rag` wrapper for simplicity, `aichat` command for full features
- **Automatic sync**: Installation script updates aichat config when Azure settings change
- **No external deps**: aichat includes built-in vector database and full-text search

### Terminator Sidechat Integration

The repository includes **llm-sidechat**, a TmuxAI-inspired terminal assistant for Terminator terminal emulator that provides an interactive AI pair programming experience.

**Architecture Overview**:
- **Python-based**: Uses `llm` library directly (no external AI binaries)
- **Automatic Exec terminal**: Auto-creates split terminal for command execution
- **Smart context**: Captures all terminals except Chat terminal (self-awareness)
- **Watch mode**: Proactive monitoring with user-defined goals
- **D-Bus integration**: Uses Terminator's existing D-Bus API for terminal management

**Core Components**:

1. **Terminator Plugin** (`integration/terminator-sidechat-plugin/terminator_sidechat.py`):
   - Provides VTE content capture via Plugin API
   - Version-aware VTE text extraction (supports VTE 72+ and older)
   - Terminal enumeration and metadata
   - Command injection via `vte.feed_child()`
   - Accessed by standalone app via `PluginRegistry`

2. **Standalone Application** (`integration/llm-sidechat`):
   - Python script that imports llm library directly
   - Rich terminal UI with streaming markdown
   - Conversation management with auto-squashing
   - Command extraction from ```bash blocks
   - Asyncio-based watch mode

3. **LLM Plugin** (`llm-tools-terminator-fragments/`):
   - Exposes Terminator terminal content as LLM tool
   - Fragment syntax: `focused`, `all`, specific UUID
   - XML-wrapped output for LLM comprehension

4. **Sidechat Template** (`llm-template/terminator-sidechat.yaml`):
   - System prompt optimized for Terminator environment
   - Instructs AI on command formatting
   - Explains context awareness and watch mode

**User Workflow**:
```
1. Run llm-sidechat in any Terminator terminal
2. Script auto-creates Exec terminal via D-Bus hsplit
3. Type messages in Chat terminal (where script runs)
4. AI responds with streaming markdown
5. If AI suggests commands (```bash blocks), prompted: "Execute? [y/n/e]"
6. Approved commands run in Exec terminal
7. Command output captured for next AI iteration
```

**Context Capture** (like TmuxAI):
- **Visible content only**: Captures 100 lines of scrollback per terminal
- **Self-aware**: Excludes Chat terminal (where sidechat runs)
- **All terminals**: Monitors all terminals in current window
- **Intelligent filtering**: Optionally excludes Exec terminal output

**Context Management** (like TmuxAI):
- **Auto-squashing**: Compresses conversation at 80% of token limit
- **Manual squashing**: `/squash` command
- **Token estimation**: Rough estimation (4 chars = 1 token)
- **Preserves recent context**: Keeps system prompt + last 5 messages + summary

**Watch Mode** (like TmuxAI):
- **All-terminal monitoring**: Watches all terminals except Chat
- **User-defined goals**: `/watch detect security issues`
- **Proactive suggestions**: AI only responds when actionable
- **Background asyncio**: Non-blocking monitoring loop
- **Configurable interval**: Default 5 seconds

**Slash Commands**:
- `/help` - Show available commands
- `/clear` - Clear conversation history
- `/model [name]` - Switch AI model or list available models
- `/info` - Show session info (conversation ID, context size, model)
- `/watch <goal>` - Enable watch mode with goal
- `/watch off` - Disable watch mode
- `/watch status` - Show watch mode status
- `/squash` - Manually compress conversation context
- `/quit` or `/exit` - Exit sidechat

**Advantages Over Context Tool**:
- ✅ **Directed capture**: Any terminal by UUID, not just current session
- ✅ **Live content**: Current scrollback buffer, not past recordings
- ✅ **TUI support**: Can capture ANSI/TUI content
- ✅ **Selective capture**: Specific terminals, specific line counts
- ✅ **Self-aware**: Excludes own terminal to avoid recursion

**Architecture Decisions**:
- ✅ **No Terminator modification**: Uses only Plugin API + existing D-Bus
- ✅ **No ipc.py patching**: Plugin accessed via `PluginRegistry`
- ✅ **Clean separation**: Content (plugin) vs Management (D-Bus)
- ✅ **Self-contained**: All code in this repository

**Installation**:
- Plugin: Copied to `~/.config/terminator/plugins/` (Phase 5)
- Enable plugin: Terminator Preferences → Plugins → Check "TerminatorSidechatPlugin"
- Application: `llm-sidechat` installed to `~/.local/bin/` (Phase 5)
- LLM Plugin: `llm-tools-terminator-fragments` installed (Phase 3)
- Template: `terminator-sidechat.yaml` installed (Phase 4)

**Dependencies**:
- Terminator terminal emulator
- llm library (already installed)
- Python 3.8+
- PyGObject (for GTK/VTE bindings)
- D-Bus (for terminal management)

**Usage Examples**:
```bash
# Launch sidechat in any Terminator terminal
llm-sidechat

# Launch with specific model
llm-sidechat azure/gpt-4.1

# Inside sidechat:
you> why did my docker build fail?
ai> [streams analysis based on terminal content]
ai> ```bash
ai> docker build --no-cache -t myapp .
ai> ```
Execute in Exec terminal? [y/n/e] y
[command runs in Exec terminal, output captured for next iteration]

you> /watch detect inefficient commands
Watch mode enabled: monitoring all terminals
[AI provides proactive suggestions when observing relevant activity]
```

**Technical Implementation**:
- **Content flow**: VTE terminals → Plugin (get_text_range_format) → llm.Conversation → streaming response
- **Terminal management**: D-Bus `hsplit`, `get_terminals`, `get_focused_terminal` (existing API)
- **Conversation continuity**: Uses `llm.Conversation()` objects with auto-squashing
- **Self-awareness**: Filters out `self.chat_terminal_uuid` when capturing context
- **Command extraction**: Regex parsing of ```bash code blocks from markdown
- **Execution**: `vte.feed_child()` via plugin

**File Locations**:
- Plugin: `integration/terminator-sidechat-plugin/terminator_sidechat.py`
- Application: `integration/llm-sidechat`
- LLM Plugin: `llm-tools-terminator-fragments/`
- Template: `llm-template/terminator-sidechat.yaml`

**Troubleshooting**:
- **Plugin not appearing**: Check `~/.config/terminator/plugins/terminator_sidechat.py` exists, restart Terminator
- **Import errors**: Ensure running inside Terminator terminal: `echo $TERMINATOR_UUID`
- **D-Bus errors**: Ensure D-Bus enabled in Terminator config
- **No terminals captured**: Enable plugin in Terminator Preferences → Plugins
- **Watch mode not working**: Check asyncio compatibility, ensure Python 3.8+

### Speech-to-Text Transcription

The repository includes a **speech-to-text transcription tool** using faster-whisper:

**Components**:
- **whisper-ctranslate2**: CLI tool installed via `uv tool install` (faster-whisper with CTranslate2 backend)
- **transcribe**: Wrapper script with sensible defaults for CPU transcription

**Features**:
- **99+ languages** with automatic language detection
- **Direct format support**: mp3, mp4, wav, m4a, flac, ogg, webm (via bundled PyAV/FFmpeg)
- **CPU optimized**: Uses INT8 quantization and batched inference
- **Multiple output formats**: txt, srt, vtt, json, tsv

**Default Settings** (in `transcribe` wrapper):
- Model: `medium` (~1.5GB, ~3-4GB RAM, good balance of quality and resource usage)
- Compute type: `int8` (best CPU performance)
- Batched inference: enabled (2-4x speed increase)

**Usage**:
```bash
# Basic transcription (outputs to <filename>.txt)
transcribe recording.mp3

# Generate SRT subtitles
transcribe video.mp4 -f srt

# Force German language, custom output
transcribe podcast.wav -l de -o german.txt

# Use smaller/faster model
transcribe meeting.m4a --model medium
```

**Direct whisper-ctranslate2 Usage** (without wrapper):
```bash
whisper-ctranslate2 file.mp3 --model large-v3-turbo --compute_type int8 --batched True
```

**Available Models**:
| Model | Size | Speed | Quality |
|-------|------|-------|---------|
| tiny | ~75MB | Fastest | Basic |
| base | ~150MB | Very fast | Good |
| small | ~500MB | Fast | Better |
| medium | ~1.5GB | Moderate | High (default) |
| large-v3 | ~3GB | Slow | Highest |
| large-v3-turbo | ~800MB | Fast | High |

**Note**: Models are automatically downloaded on first use and cached in `~/.cache/huggingface/`.

**Limitations**:
- `large-v3-turbo` does NOT support translation (only transcription)
- For speech translation (non-English → English), use `large-v3` model

## Key Files & Components

- **`install-llm-tools.sh`**: Main installation/update script with self-update logic
- **`integration/llm-common.sh`**: Shared shell configuration, llm wrapper function, asciinema auto-recording, routed-claude alias, code subcommand handler
- **`integration/llm-integration.bash`** / **`integration/llm-integration.zsh`**: Shell-specific keybindings (Ctrl+N for command completion) and tab completion setup
- **`integration/llm-zsh-plugin/`**: Fork of llm-zsh-plugin with custom extensions for `code` and `rag` subcommands
- **`context/context`**: Python script for extracting terminal history from asciinema recordings
- **`llm-tools-context/`**: LLM plugin package that exposes `context` tool to AI models
- **`llm-template/assistant.yaml`**: Custom assistant template with security/IT expertise configuration (German-language template)
- **`llm-template/code.yaml`**: Code-only generation template - outputs clean, executable code without explanations or markdown formatting
- **`scripts/transcribe`**: Speech-to-text wrapper script using faster-whisper (whisper-ctranslate2)
- **`docs/MICROSOFT_MCP_SETUP.md`**: Comprehensive guide for Codex CLI, Azure MCP Server, Lokka (Microsoft 365 MCP), and Microsoft Learn MCP setup and configuration

## Common Commands

### Installation and Updates

```bash
# First-time installation
./install-llm-tools.sh

# Update all tools (pulls git updates, upgrades packages, preserves config)
./install-llm-tools.sh
```

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
   - For uv tools from PyPI: `install_or_upgrade_uv_tool tool_name`
   - For uv tools from git: `install_or_upgrade_uv_tool "git+https://github.com/user/repo" true`
   - For cargo tools: `install_or_upgrade_cargo_tool tool_name [git_url]`
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
- `llm`: Uses `install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/llm" true` with force reinstall for git packages
- Plugins: `llm install <plugin> --upgrade`
- `gitingest`: `install_or_upgrade_uv_tool gitingest` (PyPI package, uses upgrade)
- `files-to-prompt`: `install_or_upgrade_uv_tool "git+https://github.com/c0ffee0wl/files-to-prompt" true` (force reinstall)
- `aichat`: `install_or_upgrade_cargo_tool aichat` (uses helper function)
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

## Azure MCP and Codex CLI

The repository includes comprehensive support for **Codex CLI** (OpenAI's coding agent) and **Model Context Protocol (MCP)** servers for enhanced AI-powered development workflows.

### Automatic Codex CLI Installation

When Azure OpenAI is configured, the installation script automatically:
- Installs Codex CLI via npm (Phase 7)
- Creates `~/.codex/config.toml` with Azure OpenAI credentials
- Exports environment variables to `~/.profile`:
  - `AZURE_OPENAI_API_KEY`
  - `AZURE_RESOURCE_NAME`

**To use Codex CLI:**
```bash
# Load environment variables (if not already loaded)
source ~/.profile

# Start Codex
codex
```

### VS Code Extension

Codex is also available as a **Visual Studio Code extension**, providing IDE-integrated AI coding assistance:
- Search for "Codex" in VS Code Extensions marketplace
- Supports VS Code, VS Code Insiders, Cursor, and Windsurf
- Features: inline suggestions, delegate tasks to cloud agent, review diffs, create PRs

### MCP Server Integration

The system supports integration with three official Microsoft MCP servers:

1. **Azure MCP Server**: Connect to 40+ Azure services (Storage, Compute, AI Services, Communication, etc.)
2. **Lokka**: Microsoft 365 and Microsoft Graph integration for tenant management
3. **Microsoft Learn MCP**: Access to trusted Microsoft documentation and code samples

**Comprehensive Setup Guide**: See [`docs/MICROSOFT_MCP_SETUP.md`](docs/MICROSOFT_MCP_SETUP.md) for:
- Codex quickstart guide and VS Code extension installation
- MCP server configuration in Codex (`codex mcp add`)
- Azure authentication with `az login`
- Service principal and certificate-based authentication
- App registration for Microsoft 365 access
- Testing and troubleshooting steps

**Quick Start:**
```bash
# Install Azure MCP (optional - not automatic)
npm install -g @azure/mcp

# Authenticate to Azure
az login

# Configure MCP server in Codex
codex mcp add azure -- npx -y @azure/mcp@latest server start

# Configure Lokka (Microsoft 365)
codex mcp add lokka -- npx -y @merill/lokka

# Configure Microsoft Learn MCP
npm install -g mcp-remote
codex mcp add microsoft-learn -- npx -y mcp-remote https://learn.microsoft.com/api/mcp
```

**References:**
- Codex Quickstart: https://developers.openai.com/codex/quickstart
- Codex MCP Guide: https://developers.openai.com/codex/mcp/
- Detailed Setup: [`docs/MICROSOFT_MCP_SETUP.md`](docs/MICROSOFT_MCP_SETUP.md)

## Important File Locations

### Configuration Files
- `~/.config/io.datasette.llm/extra-openai-models.yaml` - Azure OpenAI model definitions for llm
- `~/.config/io.datasette.llm/templates/{assistant,code,terminator-sidechat}.yaml` - Custom LLM templates
- `~/.config/aichat/config.yaml` - aichat configuration with Azure OpenAI and RAG settings
- `~/.codex/config.toml` - Codex CLI configuration with Azure OpenAI credentials (auto-generated)
- `~/.claude-code-router/config.json` - Claude Code Router dual-provider configuration (auto-generated with checksum tracking)
- `~/.claude-code-router/plugins/strip-reasoning.js` - CCR transformer plugin for reasoning token handling
- `~/.profile` - Environment variables for providers (AZURE_OPENAI_API_KEY, AZURE_RESOURCE_NAME, GEMINI_API_KEY)
- `~/.config/llm-tools/asciinema-commit` - Tracks asciinema version for update detection
- `~/.config/llm-tools/template-checksums` - Tracks template and CCR config checksums for smart updates
- `~/.config/terminator/plugins/terminator_sidechat.py` - Terminator sidechat plugin
- `~/.config/micro/plug/llm/` - Micro editor llm-micro plugin
- `~/.config/micro/settings.json` - Micro editor configuration (optional)
- `$SESSION_LOG_DIR/*.cast` - Session recordings (default: `/tmp/session_logs/asciinema/`)
- `~/.local/share/aichat/rags/` - RAG collections and vector databases

### Repository Structure
- `install-llm-tools.sh` - Main installation script with 7 phases
- `integration/llm-common.sh` - Shared shell config, llm wrapper, asciinema auto-recording
- `integration/llm-integration.{bash,zsh}` - Shell-specific keybindings (Ctrl+N) and tab completion setup
- `integration/llm-zsh-plugin/` - Cloned llm-zsh-plugin with custom extensions
- `integration/llm-zsh-plugin/completions/_llm` - Tab completion definitions (includes custom code/rag)
- `integration/llm-sidechat` - Standalone Terminator AI assistant application
- `integration/terminator-sidechat-plugin/terminator_sidechat.py` - Terminator plugin for VTE content capture
- `context/context` - Python script for extracting terminal history from recordings
- `llm-tools-context/` - LLM plugin exposing context as tool
- `llm-tools-terminator-fragments/` - LLM plugin exposing Terminator terminal content as tool
- `llm-template/{assistant,code,terminator-sidechat}.yaml` - Template sources installed to user config
- `docs/MICROSOFT_MCP_SETUP.md` - Comprehensive guide for Codex CLI, Azure MCP, Lokka, and Microsoft Learn MCP
- `.git/hooks/pre-commit` - Automatic TOC updater for README.md

## Key Constraints & Design Decisions

1. **Provider Configuration Patterns**:
   - **AIChat: EITHER Azure OR Gemini** (mutually exclusive) - The script validates flags and exits with error if both `--azure` and `--gemini` are specified
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
11. **Rust Required**: asciinema and aichat are installed via cargo (Rust's package manager); minimum Rust 1.85 required
12. **Rust Version Management**: Script automatically detects outdated Rust and offers to upgrade via rustup with user approval (default: Yes)
13. **rustup vs apt Coexistence**: rustup and apt-installed Rust can coexist safely; rustup takes precedence via PATH
14. **Node.js Version Management**: Script automatically detects Node.js version and installs via nvm if repository version < 20
15. **Go Optional**: Go 1.22+ is required for imagemage but installation is apt-only; if repo version is insufficient, imagemage is skipped with a warning
16. **Per-Pane Recording in tmux**: Each tmux pane gets its own independent recording session (intentional design for workflow isolation)
17. **Optional llm-functions Integration**: The script installs argc (prerequisite) and llm-tools-llm-functions (bridge plugin) to prepare the environment for optional llm-functions usage, but llm-functions itself is NOT automatically installed - users must install it separately if they want to build custom tools

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
- **aichat**: Installed via cargo from crates.io: `cargo install aichat`
- **argc**: Installed via cargo from crates.io: `cargo install argc` (prerequisite for llm-functions, also useful standalone for Bash CLI development)
- **llm-tools-context**: Installed from local directory: `$SCRIPT_DIR/llm-tools-context`
- **llm-functions**: NOT automatically installed; users must install manually from https://github.com/sigoden/llm-functions/ if needed
- **imagemage**: Installed from Go package: `github.com/quinnypig/imagemage@latest` (only when Gemini is configured and Go 1.22+ available)
