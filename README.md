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

## What Gets Installed

### Core Tools
- **llm** - Simon Willison's LLM CLI tool
- **uv** - Modern Python package installer
- **Node.js v22+** - Via nvm (if needed)

### LLM Plugins
- llm-gemini
- llm-openrouter
- llm-anthropic
- llm-tools-quickjs
- llm-tools-sqlite
- llm-tools-context (terminal history integration)
- llm-fragments-site-text
- llm-fragments-pdf
- llm-fragments-github
- llm-jq
- llm-templates-fabric (Damon McMinn's fork)

### LLM Templates
- **assistant.yaml** - Custom assistant template with security/IT expertise configuration

### Additional Tools
- **repomix** - Repository packager for AI consumption
- **gitingest** - Convert Git repositories to LLM-friendly text
- **files-to-prompt** - File content formatter for LLM prompts

### Shell Integration
- AI-powered command completion (Ctrl+N)
- Custom llm wrapper with default assistant template
- Common aliases and PATH configuration

## Installation

### Quick Start

```bash
git clone https://github.com/c0ffee0wl/llm-linux-setup.git
cd llm-linux-setup
./install-llm-tools.sh
```

During installation, you'll be prompted for:
1. **Azure OpenAI API Key** - Your Azure API key
2. **Azure Foundry Resource URL** - e.g., `https://YOUR-RESOURCE.openai.azure.com/openai/v1/`

### Updating

Simply re-run the installation script:

```bash
cd llm-linux-setup
./install-llm-tools.sh
```

The script will:
1. Pull the latest version from git
2. Update llm and all plugins
3. Update custom templates (assistant.yaml)
4. Update repomix, gitingest, and files-to-prompt
5. Refresh shell integration files

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

### Azure OpenAI Models

The following models are configured:
- `azure/gpt-5` - GPT-5
- `azure/gpt-5-mini` - GPT-5 Mini (default)
- `azure/gpt-5-nano` - GPT-5 Nano
- `azure/gpt-4.1` - GPT-4.1

### Additional Tools

```bash
# Package repository for AI analysis
repomix

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
  - `templates/assistant.yaml` - Custom assistant template (auto-installed from `llm-template/` directory)
  - API keys stored securely via llm's key management

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

## System Requirements

- **OS**: Debian, Ubuntu, Kali Linux (or derivatives)
- **Python**: 3.8+ (usually pre-installed)
- **Internet**: Required for installation and API access
- **Disk Space**: ~500MB for all tools and dependencies

## Supported Shells

- Bash (3.0+)
- Zsh (5.0+)

## Documentation

- [LLM Documentation](https://llm.datasette.io/)
- [LLM Plugins Directory](https://llm.datasette.io/en/stable/plugins/directory.html)
- [Pedantic Journal - LLM Guide](https://pedanticjournal.com/llm/)
- [Repomix Documentation](https://github.com/yamadashy/repomix)
- [Gitingest Documentation](https://github.com/coderamp-labs/gitingest)
- [Files-to-Prompt](https://github.com/danmackinlay/files-to-prompt)

## License

This installation script is provided as-is. Individual tools have their own licenses:
- llm: Apache 2.0
- Repomix: MIT
- See individual tool repositories for details

## Contributing

To modify or extend this installation:

1. Edit the scripts in the repository
2. Test your changes
3. Commit and push to git
4. The changes will be pulled automatically on next run

## Credits

- [Simon Willison](https://github.com/simonw) - llm CLI tool
- [Repomix Team](https://github.com/yamadashy/repomix) - Repository packaging
- [Dan Mackinlay](https://github.com/danmackinlay) - files-to-prompt fork
- [Damon McMinn](https://github.com/damonmcminn) - llm-templates-fabric fork
