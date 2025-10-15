# LLM Tools Installation Script for Linux

**GitHub Repository**: https://github.com/c0ffee0wl/llm-linux-setup

Automated installation script for [Simon Willison's llm CLI tool](https://github.com/simonw/llm) and related AI/LLM command-line utilities.

## Features

- ✅ **One-command installation** - Run once to install everything
- ✅ **Self-updating** - Re-run to update all tools automatically
- ✅ **Safe git updates** - Pulls latest script version before execution
- ✅ **Multi-shell support** - Works with both Bash and Zsh
- ✅ **Azure OpenAI integration** - Configured for Azure Foundry
- ✅ **AI command completion** - Press Ctrl+N for intelligent command suggestions
- ✅ **Automatic session recording** - Terminal history captured for AI context
- ✅ **AI-powered context retrieval** - Query your command history with `context` or `llm --tool context`

## What Gets Installed

### Core Tools
- **llm** - Simon Willison's LLM CLI tool
- **uv** - Modern Python package installer
- **Node.js** - JavaScript runtime (v20+, from repositories or nvm)
- **Rust/Cargo** - Rust toolchain (v1.75+, from repositories or rustup)

### LLM Plugins
- **llm-gemini** - Google Gemini models integration
- **llm-openrouter** - OpenRouter API integration
- **llm-anthropic** - Anthropic Claude models integration
- **llm-cmd** - Command execution and management
- **llm-cmd-comp** - AI-powered command completion (powers Ctrl+N)
- **llm-tools-quickjs** - JavaScript execution tool
- **llm-tools-sqlite** - SQLite database tool
- **llm-tools-context** - Terminal history integration (exposes `context` tool to AI)
- **llm-fragments-site-text** - Web page content extraction
- **llm-fragments-pdf** - PDF content extraction
- **llm-fragments-github** - GitHub repository integration
- **llm-jq** - JSON processing tool
- **llm-templates-fabric** - Fabric prompt templates (Damon McMinn's fork)

### LLM Templates
- **assistant.yaml** - Custom assistant template with security/IT expertise configuration
- **code.yaml** - Code-only generation template (outputs clean, executable code without markdown)

### Additional Tools
- **gitingest** - Convert Git repositories to LLM-friendly text
- **files-to-prompt** - File content formatter for LLM prompts
- **asciinema** - Terminal session recorder (built from source for latest features)
- **context** - Python script for extracting terminal history from asciinema recordings
- **Claude Code** - Anthropic's official agentic coding CLI
- **OpenCode** - AI coding agent for terminal

### Shell Integration
- AI-powered command completion (Ctrl+N)
- Custom llm wrapper with automatic template application (`llm chat`, `llm code`)
- Automatic session recording with asciinema (configurable storage location)
- Common aliases and PATH configuration

## Session Recording & Context System

This setup includes an **automatic terminal session recording system** that captures your command history and allows AI models to query it for context-aware assistance.

### How It Works

1. **Automatic Recording**: Every interactive shell session is automatically recorded using asciinema
   - Recording starts transparently when you open a terminal
   - Each tmux/screen pane gets its own independent recording
   - No manual intervention required

2. **Context Extraction**: The `context` command parses asciinema recordings to show command history
   - Shows commands with their complete output
   - Filters out noise and focuses on actual commands
   - Supports showing last N commands or entire session

3. **AI Integration**: The `llm-tools-context` plugin exposes terminal history as a tool
   - AI models can query your recent command history
   - Provides context-aware assistance based on what you've been doing
   - Example: `llm --tool context "what was the error in my last command?"`

### Storage Configuration

On first installation, you'll be prompted to choose where session recordings are stored:

- **Temporary** (default): `/tmp/session_logs/asciinema/` - Cleared on reboot, saves disk space
- **Permanent**: `~/session_logs/asciinema/` - Survives reboots, useful for long-term history

You can change this later by editing the `SESSION_LOG_DIR` export in your `.bashrc` or `.zshrc`.

### tmux/screen Behavior

Each tmux pane or screen window gets its own independent recording session. This is intentional - different panes typically represent different workflows, so having separate contexts makes sense. If you want unified recording across all panes, start asciinema manually before launching tmux: `asciinema rec --command "tmux attach"`

## Installation

### Quick Start

```bash
git clone https://github.com/c0ffee0wl/llm-linux-setup.git
cd llm-linux-setup
./install-llm-tools.sh
```

During first-time installation, you'll be prompted for:
1. **Azure OpenAI Configuration** (optional) - API key and resource URL
2. **Session Log Storage** - Choose between temporary (`/tmp`, cleared on reboot) or permanent (`~/session_logs`, survives reboots)

To reconfigure Azure OpenAI settings later:
```bash
./install-llm-tools.sh --azure
```

### Updating

Simply re-run the installation script:

```bash
cd llm-linux-setup
./install-llm-tools.sh
```

The script will:
1. Pull the latest version from git
2. Update llm and all plugins
3. Update custom templates (assistant.yaml, code.yaml)
4. Update gitingest, files-to-prompt, asciinema, Claude Code, and OpenCode
5. Refresh shell integration files
6. Preserve existing Azure OpenAI and session log configurations

## Usage

### Basic LLM Usage

```bash
# Ask a question (uses assistant template by default)
llm "What is the capital of France?"

# Start an interactive chat session
llm chat "Let's discuss Python"

# Use a specific model
llm -m azure/gpt-5 "Explain quantum computing"

# List available models
llm models list

# View installed plugins
llm plugins
```

### AI Command Completion

Type a partial command or describe what you want in natural language, then press **Ctrl+N**:

```bash
# Type: list all pdf files
# Press Ctrl+N
# Result: find . -type f -name "*.pdf"
```

The AI will suggest and execute the command automatically.

### Context System Usage

Query your terminal history to get context-aware AI assistance:

```bash
# Show last command and output
context

# Show last 5 commands with outputs
context 5

# Show entire session history
context all

# Get export command for current session file (useful for debugging)
context -e

# Ask AI about your command history
llm --tool context "what was the error in my last command?"
llm --tool context "summarize what I did in this session"
llm --tool context "how do I fix the compilation error I just got?"
```

The context system automatically captures:
- Commands you run
- Complete command output
- Error messages and stack traces
- Multi-line commands and their results

This allows AI models to provide context-aware debugging and assistance based on your actual terminal activity.

### Code Generation with llm code

Generate clean, executable code without markdown formatting:

```bash
# Generate Python function
llm code "function to check if number is prime" > prime.py

# Generate bash script
llm code "script to backup directory with timestamp" > backup.sh

# Generate SQL query
llm code "select users who registered this month"

# Generate configuration file
llm code "nginx config for reverse proxy on port 3000" > nginx.conf

# Direct execution (use with caution!)
llm code "one-liner to find files larger than 100MB" | bash

# Generate Dockerfile
llm code "dockerfile for nodejs app with nginx" > Dockerfile
```

The `llm code` command automatically uses the `code` template which outputs pure code without explanations or markdown code blocks, making it perfect for piping to files or direct execution.

### Azure OpenAI Models

The following models are configured:
- `azure/gpt-5` - GPT-5
- `azure/gpt-5-mini` - GPT-5 Mini (default)
- `azure/gpt-5-nano` - GPT-5 Nano
- `azure/o4-mini` - O4 Mini
- `azure/gpt-4.1` - GPT-4.1

### Additional Tools

```bash
# Convert Git repositories to LLM-friendly text
gitingest https://github.com/user/repo
gitingest /path/to/local/repo

# Convert files to LLM-friendly format
files-to-prompt src/*.py
```

## Configuration

### Configuration Files

- `~/.config/io.datasette.llm/` - LLM configuration directory
  - `extra-openai-models.yaml` - Azure OpenAI model definitions
  - `templates/assistant.yaml` - Custom assistant template with security/IT expertise
  - `templates/code.yaml` - Code-only generation template (no markdown, no explanations)
  - `default_model.txt` - Currently selected default model
  - API keys stored securely via llm's key management

- `~/.config/llm-tools/` - Additional tool configuration
  - `asciinema-commit` - Tracks asciinema version for update detection

- `$SESSION_LOG_DIR/` - Session recording storage
  - Default: `/tmp/session_logs/asciinema/` (temporary) or `~/session_logs/asciinema/` (permanent)
  - Contains `.cast` files with terminal session recordings
  - Configured via `SESSION_LOG_DIR` environment variable in your shell RC file

### Shell Integration Files

Located in the `integration/` subdirectory:
- `integration/llm-integration.bash` - Bash integration
- `integration/llm-integration.zsh` - Zsh integration
- `integration/llm-common.sh` - Shared configuration

These are automatically sourced from your `.bashrc` or `.zshrc`.

### Changing Default Model

```bash
llm models default azure/gpt-5
```

### Managing API Keys

```bash
# Set Azure key
llm keys set azure

# View key storage path
llm keys path

# List all configured keys
llm keys
```

## Troubleshooting

### Command completion not working

1. Restart your shell or source your profile:
   ```bash
   source ~/.bashrc  # or ~/.zshrc
   ```

2. Verify llm is in PATH:
   ```bash
   which llm
   ```

3. Test llm command completion:
   ```bash
   llm cmdcomp "list files"
   ```

### Azure API errors

1. Verify API key is set:
   ```bash
   llm keys get azure
   ```

2. Check model configuration:
   ```bash
   cat ~/.config/io.datasette.llm/extra-openai-models.yaml
   ```

3. Update the API base URL in the YAML file if needed

### Update fails

If the script update fails:

```bash
cd llm-linux-setup
git reset --hard origin/main
./install-llm-tools.sh
```

### Rust version issues

**Problem**: `cargo install` fails with errors about minimum Rust version

**Solution**: The script automatically detects and offers to upgrade Rust to 1.75+ via rustup. If you declined during installation:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

**Check Rust version**:
```bash
rustc --version
```

### Node.js version issues

**Problem**: npm or node commands fail, or Claude Code/OpenCode won't install

**Solution**: The script requires Node.js 20+. If you have an older version:

```bash
# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc  # or ~/.zshrc

# Install Node 22
nvm install 22
nvm use 22
nvm alias default 22
```

### Session recording not working

**Problem**: `context` command shows "No asciinema session recording found"

**Solutions**:

1. Verify asciinema is installed and in PATH:
   ```bash
   which asciinema
   ```

2. Check shell integration is loaded:
   ```bash
   grep -r "llm-integration" ~/.bashrc ~/.zshrc
   ```

3. Restart your shell or re-source your RC file:
   ```bash
   source ~/.bashrc  # or ~/.zshrc
   ```

4. Check if recording is active (should see asciinema process):
   ```bash
   ps aux | grep asciinema
   ```

### Context shows wrong session

**Problem**: `context` command shows old or wrong session history

**Solutions**:

1. Check current session file:
   ```bash
   echo $SESSION_LOG_FILE
   ```

2. Manually set session file if needed:
   ```bash
   export SESSION_LOG_FILE="/path/to/your/session.cast"
   ```

3. Get correct export command:
   ```bash
   context -e
   ```

### tmux panes not recording independently

**Problem**: New tmux panes don't get their own recordings

**Solution**: Check for pane-specific environment markers:

```bash
env | grep IN_ASCIINEMA_SESSION
```

You should see markers like `IN_ASCIINEMA_SESSION_tmux_0=1` for each pane. If not, re-source your shell RC file in the new pane:

```bash
source ~/.bashrc  # or ~/.zshrc
```

**Note**: Independent per-pane recording is intentional. If you want unified recording across all tmux panes, start asciinema before tmux:

```bash
asciinema rec --command "tmux attach"
```

## System Requirements

- **OS**: Debian, Ubuntu, Kali Linux (or derivatives)
- **Python**: 3.8+ (usually pre-installed)
- **Rust**: 1.75+ (automatically installed via rustup if not available)
- **Node.js**: 20+ (automatically installed via nvm if repository version is older)
- **Internet**: Required for installation and API access
- **Disk Space**: ~500MB for all tools and dependencies

**Note**: The installation script automatically handles Rust and Node.js version requirements. If your system has older versions, it will offer to install newer versions via rustup and nvm respectively.

## Supported Shells

- Bash (3.0+)
- Zsh (5.0+)

## Documentation

- [LLM Documentation](https://llm.datasette.io/)
- [LLM Plugins Directory](https://llm.datasette.io/en/stable/plugins/directory.html)
- [Pedantic Journal - LLM Guide](https://pedanticjournal.com/llm/)
- [Gitingest Documentation](https://github.com/coderamp-labs/gitingest)
- [Files-to-Prompt](https://github.com/danmackinlay/files-to-prompt)

## License

This installation script is provided as-is. Individual tools have their own licenses:
- llm: Apache 2.0
- See individual tool repositories for details

## Contributing

To modify or extend this installation:

1. Edit the scripts in the repository
2. Test your changes
3. Commit and push to git
4. The changes will be pulled automatically on next run

## Credits

- [Simon Willison](https://github.com/simonw) - llm CLI tool
- [Dan Mackinlay](https://github.com/danmackinlay) - files-to-prompt fork
- [Damon McMinn](https://github.com/damonmcminn) - llm-templates-fabric fork
