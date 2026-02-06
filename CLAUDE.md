# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Information

**GitHub Repository**: https://github.com/c0ffee0wl/llm-linux-setup

## Repository Overview

Installation and configuration system for Simon Willison's `llm` CLI tool and related AI/LLM command-line utilities on Linux (Debian/Ubuntu/Kali). The system includes:

- **Self-updating installation script** with provider configuration (Azure/Gemini)
- **Session recording & context** for AI-accessible terminal history
- **Shell integration** with keybindings, wrapper function, tab completion
- **Terminal assistants** (Terminator, inline `@` command, GTK popup)
- **Workflow engine** for YAML-based automation
- **Desktop integration** (espanso, ulauncher, speech-to-text)

## Component Documentation

| Component | Documentation |
|-----------|---------------|
| Session Recording & Context | [`llm-tools-context/CLAUDE.md`](llm-tools-context/CLAUDE.md) |
| Shell Integration | [`integration/CLAUDE.md`](integration/CLAUDE.md) |
| Terminator Assistant | [`llm-assistant/CLAUDE.md`](llm-assistant/CLAUDE.md) |
| Inline Assistant (`@`) | [`llm-inlineassistant/CLAUDE.md`](llm-inlineassistant/CLAUDE.md) |
| GUI Assistant (GTK) | [`llm-guiassistant/CLAUDE.md`](llm-guiassistant/CLAUDE.md) |
| Workflow Engine | [`burr_workflow/CLAUDE.md`](burr_workflow/CLAUDE.md) |
| Desktop Integration | [`docs/DESKTOP_INTEGRATION.md`](docs/DESKTOP_INTEGRATION.md) |
| Microsoft MCP Setup | [`docs/MICROSOFT_MCP_SETUP.md`](docs/MICROSOFT_MCP_SETUP.md) |

## Architecture

### Self-Updating Installation Pattern

The core design uses a **self-updating script pattern**:

1. **Phase 0 (Self-Update)**: Checks if running in git repo, fetches updates
2. **Critical Check**: Uses `git rev-list HEAD..@{u}` to count commits behind remote
   - If behind > 0: pulls updates and re-execs with `exec "$0" "$@"`
   - If equal or ahead: continues normally
3. Prevents executing with partially-updated code mid-run

**When modifying `install-llm-tools.sh`**: Phase 0 must ALWAYS run before any other operations.

### Installation Phases

| Phase | Purpose |
|-------|---------|
| 0 | Self-update (git fetch/pull/exec) |
| 1 | Prerequisites (pipx, uv, Node.js, Rust, document processors) |
| 2 | LLM Core + Plugins (single `uv tool install --force --with ...`) |
| 3 | Provider configuration (Azure/Gemini prompts) |
| 4 | LLM Templates |
| 5 | Shell Integration (source statements in rc files) |
| 6 | Additional Tools (gitingest, files-to-prompt, argc, context script) |
| 7 | Agentic CLI tools (Claude Code, Codex CLI, Claude Code Router) |

### Consolidated Plugin Installation

All plugins are installed in a **single command** for performance:

```bash
uv tool install --force --reinstall-package <local> --with <all> llm
```

- `REMOTE_PLUGINS` array: External plugins (PyPI, git repositories)
- `LOCAL_PLUGINS` array: In-repo plugins (always rebuilt)
- Local plugins use `--reinstall-package` to pick up source changes

### Helper Functions

Helper functions are split between two files:

**`shared/common.sh`** (sourced by install script):
| Function | Purpose |
|----------|---------|
| `install_apt_package(pkg, [cmd])` | Install apt packages with checks |
| `version_at_least(v, min)` | Version comparison |
| `ask_yes_no(prompt, default)` | Interactive prompts |
| `install_or_upgrade_uv_tool(src)` | Unified uv tool management (auto-detects git) |
| `install_or_upgrade_cargo_git_tool(name, url)` | Cargo tools with commit tracking |
| `install_or_upgrade_github_release(name, repo, suffix)` | GitHub release binary with tag tracking |
| `install_or_upgrade_rust()` | Rust version management |
| `install_or_upgrade_nodejs()` | Node.js version management |

**`install-llm-tools.sh`**:
| Function | Purpose |
|----------|---------|
| `update_template_file(name)` | Smart template updates with checksum |
| `configure_azure_openai()` | Azure configuration prompts |
| `configure_gemini()` | Gemini configuration prompts |
| `get_llm_config_dir()` | Get llm config directory (cached) |

Use these functions for consistency rather than duplicating logic.

## Provider Configuration

### Azure OpenAI

- Model IDs: `azure/gpt-4.1-mini`, `azure/gpt-4.1`, `azure/o4-mini`, etc.
- Default: `azure/gpt-4.1-mini`
- Chat config: `~/.config/io.datasette.llm/extra-openai-models.yaml`
- Embedding config: `~/.config/io.datasette.llm/azure/config.yaml`
- Keys: `llm keys set azure`

### Google Gemini

- Uses `llm-gemini` plugin
- Models: `gemini-2.5-flash`, `gemini-2.5-pro`, etc.
- Keys: `llm keys set gemini`

### Switching Providers

```bash
./install-llm-tools.sh --azure   # Configure/reconfigure Azure
./install-llm-tools.sh --gemini  # Configure/reconfigure Gemini
```

Both flags cannot be used simultaneously.

### Claude Code Router

Supports flexible provider configurations for Claude Code:
- **Dual-Provider**: Azure primary, Gemini for web search
- **Gemini-Only**: All routing uses Gemini

Config: `~/.claude-code-router/config.json` (checksum-tracked for smart updates)

## Common Commands

### Installation

```bash
# Full installation
./install-llm-tools.sh

# Minimal (LLM core only, persists for future runs)
./install-llm-tools.sh --minimal

# Override minimal preference
./install-llm-tools.sh --full

# Clear package caches (npm, go, pip, cargo, uv)
./install-llm-tools.sh --clear-cache
```

### Testing

```bash
# Syntax check
bash -n install-llm-tools.sh

# Test idempotency (run twice)
./install-llm-tools.sh

# Test context system
context          # Last command
context 5        # Last 5 commands
llm --tool context "what did I just run?"
```

## Key File Locations

### Configuration

| Path | Purpose |
|------|---------|
| `~/.config/io.datasette.llm/extra-openai-models.yaml` | Azure chat models |
| `~/.config/io.datasette.llm/azure/config.yaml` | Azure embeddings |
| `~/.config/io.datasette.llm/templates/*.yaml` | LLM templates |
| `~/.claude-code-router/config.json` | Claude Code Router |
| `~/.profile` | Provider environment variables |
| `~/.config/llm-tools/` | Script tracking files |

### Repository Structure

| Path | Purpose |
|------|---------|
| `install-llm-tools.sh` | Main installation script |
| `shared/common.sh` | Shared helper functions |
| `integration/` | Shell integration files |
| `llm-templates/` | Template sources |
| `llm-assistant/` | Terminator assistant |
| `llm-inlineassistant/` | Inline `@` command |
| `llm-guiassistant/` | GTK popup assistant |
| `llm-tools-context/` | Context extraction |
| `llm-tools-core/` | Shared Python utilities |
| `burr_workflow/` | Workflow engine |
| `espanso-llm/` | espanso text expander package |
| `ulauncher-llm/` | Ulauncher extension |

## Key Constraints

1. **Debian/Ubuntu/Kali**: Uses `apt-get`; needs modification for other distros
2. **Git Repository Required**: Self-update only works when cloned from git
3. **Path Assumptions**: Writes to `~/.bashrc`, `~/.zshrc`, `~/.config/`
4. **Rust 1.85+**: Required for modern cargo tools
5. **Node.js 20+**: Required for Claude Code
6. **Per-Pane Recording**: Each tmux pane gets independent recording (intentional)

## Special Packages & Forks

Most plugins are installed from `git+https://github.com/c0ffee0wl/...` forks. Key packages:

| Package | Source | Purpose |
|---------|--------|---------|
| llm | c0ffee0wl/llm | Fork with markdown enhancements |
| llm-uv-tool | c0ffee0wl/llm-uv-tool | Plugin persistence across upgrades |
| files-to-prompt | c0ffee0wl/files-to-prompt | Dan Mackinlay's fork |
| llm-templates-fabric | c0ffee0wl/llm-templates-fabric | Damon McMinn's fork |
| asciinema | cargo git install | Session recording (Rust) |
| md2cb | letientai299/md2cb | Markdown to rich HTML clipboard |

Other git-based plugins include: llm-gemini, llm-vertex, llm-cmd, llm-tools-*, llm-fragments-*, llm-arxiv, etc. See `REMOTE_PLUGINS` array in `install-llm-tools.sh` for the complete list.

## Troubleshooting

### Installation Script

**Infinite loop**: Local commits ahead of origin. Solution: `git push` or `git reset --hard origin/main`

**Rust version too old**: Script auto-prompts to install rustup if < 1.85

### Provider Issues

**Azure not working**: Check `llm keys get azure` and `extra-openai-models.yaml`

**Gemini not working**: Check `llm keys get gemini`

### Component-Specific Issues

See the respective CLAUDE.md files linked in [Component Documentation](#component-documentation).
