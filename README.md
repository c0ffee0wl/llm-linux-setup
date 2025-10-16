# LLM Tools Installation Script for Linux

**GitHub Repository**: https://github.com/c0ffee0wl/llm-linux-setup

Automated installation script for [Simon Willison's llm CLI tool](https://github.com/simonw/llm) and related AI/LLM command-line utilities for Debian-based Linux environments.

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

- [Features](#features)
- [System Requirements](#system-requirements)
- [Installation](#installation)
  - [Prerequisites (Recommended)](#prerequisites-recommended)
  - [Quick Start](#quick-start)
  - [Updating](#updating)
- [Documentation](#documentation)
  - [Original Tools](#original-tools)
  - [This Project](#this-project)
- [What Gets Installed](#what-gets-installed)
  - [Core Tools](#core-tools)
  - [LLM Plugins](#llm-plugins)
  - [LLM Templates](#llm-templates)
  - [Additional Tools](#additional-tools)
  - [Shell Integration](#shell-integration)
- [Quick Reference](#quick-reference)
- [Usage](#usage)
  - [Getting Started](#getting-started)
  - [Basic Prompts](#basic-prompts)
  - [AI Command Completion](#ai-command-completion)
  - [Attachments & Multi-modal](#attachments--multi-modal)
  - [Fragments](#fragments)
  - [Templates](#templates)
  - [Code Generation](#code-generation)
  - [Tools](#tools)
  - [Context System Usage](#context-system-usage)
  - [Integration with Other Tools](#integration-with-other-tools)
  - [Managing Models](#managing-models)
- [Understanding the Shell Integration](#understanding-the-shell-integration)
  - [How Automatic Templates Work](#how-automatic-templates-work)
  - [Bypassing the Shell Wrapper](#bypassing-the-shell-wrapper)
  - [Command Shortcuts](#command-shortcuts)
  - [When to Use `-t`](#when-to-use--t)
  - [Key Benefits](#key-benefits)
  - [Context Tool Integration](#context-tool-integration)
- [Configuration](#configuration)
  - [Configuration Files](#configuration-files)
  - [Shell Integration Files](#shell-integration-files)
  - [Changing Default Model](#changing-default-model)
  - [Managing API Keys](#managing-api-keys)
- [Session Recording & Context System](#session-recording--context-system)
  - [How It Works](#how-it-works)
  - [Storage Configuration](#storage-configuration)
- [Troubleshooting](#troubleshooting)
  - [Command completion not working](#command-completion-not-working)
  - [Azure API errors](#azure-api-errors)
  - [Update fails](#update-fails)
  - [Rust version issues](#rust-version-issues)
  - [Node.js version issues](#nodejs-version-issues)
  - [Session recording not working](#session-recording-not-working)
  - [Context shows wrong session](#context-shows-wrong-session)
  - [tmux panes not recording independently](#tmux-panes-not-recording-independently)
- [Support](#support)
- [Related Projects](#related-projects)
- [Credits](#credits)
  - [Core Tools & Frameworks](#core-tools--frameworks)
  - [LLM Plugins & Extensions](#llm-plugins--extensions)
  - [Additional Tools](#additional-tools-1)
- [License](#license)
- [Contributing](#contributing)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Features

- ✅ **One-command installation** - Run once to install everything
- ✅ **Self-updating** - Re-run to update all tools automatically
- ✅ **Safe git updates** - Pulls latest script version before execution
- ✅ **Multi-shell support** - Works with both Bash and Zsh
- ✅ **Azure OpenAI integration** - Configured for Azure Foundry
- ✅ **AI command completion** - Press Ctrl+N for intelligent command suggestions
- ✅ **Automatic session recording** - Terminal history captured for AI context
- ✅ **AI-powered context retrieval** - Query your command history with `context` or `llm --tool context`

## System Requirements

- **OS**: Debian, Ubuntu, Kali Linux (or derivatives)
- **Python**: 3.8+ (usually pre-installed)
- **Rust**: 1.75+ (automatically installed via rustup if not available)
- **Node.js**: 20+ (automatically installed via nvm if repository version is older)
- **Internet**: Required for installation and API access
- **Disk Space**: ~500MB for all tools and dependencies

**Supported Shells**:
- Bash (3.0+)
- Zsh (5.0+)

**Note**: The installation script automatically handles Rust and Node.js version requirements. If your system has older versions, it will offer to install newer versions via rustup and nvm respectively.

**Recommended**: For a fully configured Linux environment with optimized shell settings, security tools, and system utilities, consider installing [linux-setup](https://github.com/c0ffee0wl/linux-setup) first. This provides the base configuration layer that complements the LLM tools.

## Installation

### Prerequisites (Recommended)

For the best experience, install [linux-setup](https://github.com/c0ffee0wl/linux-setup) first to get a fully configured Linux environment:

```bash
git clone https://github.com/c0ffee0wl/linux-setup.git
cd linux-setup
./install.sh
```

The linux-setup repository provides:
- Optimized Bash/Zsh configurations and aliases
- Essential security and development tools
- System utilities and PATH configuration
- Base environment that complements LLM tools

This step is **optional** but recommended for the most complete setup.

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
3. Update custom templates ([assistant.yaml](llm-template/assistant.yaml), [code.yaml](llm-template/code.yaml))
4. Update gitingest, files-to-prompt, asciinema, Claude Code, and OpenCode
5. Refresh shell integration files
6. Preserve existing Azure OpenAI and session log configurations

## Documentation

### Original Tools
- [LLM Documentation](https://llm.datasette.io/)
- [LLM Plugins Directory](https://llm.datasette.io/en/stable/plugins/directory.html)
- [Gitingest Documentation](https://github.com/coderamp-labs/gitingest)
- [Files-to-Prompt](https://github.com/simonw/files-to-prompt) (forked)

### This Project
- [README.md](README.md) - Readme
- [CLAUDE.md](CLAUDE.md) - Developer documentation and architecture guide (for Claude Code and contributors)

## What Gets Installed

### Core Tools
- **[llm](https://llm.datasette.io/)** - Simon Willison's LLM CLI tool
- **[uv](https://docs.astral.sh/uv/)** - Modern Python package installer
- **[Python 3](https://python.org/)** - Required for llm
- **[Node.js](https://nodejs.org/)** - JavaScript runtime (v20+, from repositories or nvm)
- **[Rust/Cargo](https://www.rust-lang.org/)** - Rust toolchain (v1.75+, from repositories or rustup)
- **[Claude Code](https://docs.claude.com/en/docs/claude-code)** - Anthropic's official agentic coding CLI
- **[OpenCode](https://github.com/sst/opencode)** - AI coding agent for terminal

### LLM Plugins
- **[llm-gemini](https://github.com/simonw/llm-gemini)** - Google Gemini models integration
- **[llm-openrouter](https://github.com/simonw/llm-openrouter)** - OpenRouter API integration
- **[llm-anthropic](https://github.com/simonw/llm-anthropic)** - Anthropic Claude models integration
- **[llm-cmd](https://github.com/c0ffee0wl/llm-cmd)** - Command execution and management
- **[llm-cmd-comp](https://github.com/c0ffee0wl/llm-cmd-comp)** - AI-powered command completion (powers Ctrl+N)
- **[llm-tools-quickjs](https://github.com/simonw/llm-tools-quickjs)** - JavaScript execution tool
- **[llm-tools-sqlite](https://github.com/simonw/llm-tools-sqlite)** - SQLite database tool
- **[llm-tools-context](llm-tools-context/)** - Terminal history integration (exposes `context` tool to AI)
- **[llm-fragments-site-text](https://github.com/daturkel/llm-fragments-site-text)** - Web page content extraction
- **[llm-fragments-pdf](https://github.com/daturkel/llm-fragments-pdf)** - PDF content extraction
- **[llm-fragments-github](https://github.com/simonw/llm-fragments-github)** - GitHub repository integration
- **[llm-jq](https://github.com/simonw/llm-jq)** - JSON processing tool
- **[llm-templates-fabric](https://github.com/c0ffee0wl/llm-templates-fabric)** - Fabric prompt templates

### LLM Templates
- **[assistant.yaml](llm-template/assistant.yaml)** - Custom assistant template with security/IT expertise configuration (German language, optimized for cybersecurity and Linux tasks, includes `context` tool by default)
- **[code.yaml](llm-template/code.yaml)** - Code-only generation template (outputs clean, executable code without markdown)

### Additional Tools
- **[gitingest](https://github.com/coderamp-labs/gitingest)** - Convert Git repositories to LLM-friendly text
- **[files-to-prompt](https://github.com/c0ffee0wl/files-to-prompt)** - File content formatter for LLM prompts
- **[asciinema](https://asciinema.org/)** - Terminal session recorder (built from source for latest features)
- **[context](context/context)** - Python script for extracting terminal history from asciinema recordings

### Shell Integration
- AI-powered command completion (Ctrl+N) - see [`llm-integration.bash`](integration/llm-integration.bash) / [`.zsh`](integration/llm-integration.zsh)
- Custom llm wrapper with automatic template application - see [`llm-common.sh`](integration/llm-common.sh)
- Automatic session recording with asciinema - see [`llm-common.sh`](integration/llm-common.sh)
- macOS-style clipboard aliases (`pbcopy`/`pbpaste` via `xsel` on Linux)
- Common aliases and PATH configuration

## Quick Reference

**⚡ Most Common Commands** (no need to specify `-t assistant` - it's the default!)

```bash
# Ask questions (assistant template auto-applied)
llm "Your question here"
llm -c "Your follow up"     # Continue last conversation on CLI
llm chat                    # Start interactive conversation
llm chat -c                 # Continue last conversation interactively

# Include local context via shell expansion or piping
llm "explain this error: $(python zero_division.py 2>&1)"
docker logs -n 20 my_app | llm "check logs, find errors, provide possible solutions"

cat setup.py | llm 'extract the metadata'
llm -f setup.py 'extract the metadata'    # Alternatively use local fragments

ls -1aG | llm "Describe each of the files"

llm "What does 'ls -1aG' do?"

# Use command completion
# Type: find pdf files larger than 20MB
# Press: Ctrl+N

# Generate clean code (shorthand for -t code)
llm code "python function to..." | tee output.py

# Use fragments for context
llm -f github:user/repo "analyze this"
llm -f pdf:document.pdf "summarize"
llm -f https://example.com "extract key points"

# Use -t when you want a DIFFERENT template that the default assistant template
llm -t fabric:summarize "..."        # Not the default
llm -t fabric:analyze_threat_report  # Not the default

# Query terminal history (context tool is built into assistant template!)
context                                        # Show last command
context 5                                      # Show last 5 commands
llm "what was the error in my last command?"   # Uses context tool automatically in default template
llm --tool context "..."   # Explicit tool call (for non-assistant templates)
```

## Usage

### Getting Started

Discover your installation and available commands:

```bash
# Verify llm is installed and in PATH
which llm
command llm

# View general help
llm --help

# Get help for specific commands
llm prompt --help
llm chat --help

# View your assistant template configuration
xdg-open ~/.config/io.datasette.llm/templates/assistant.yaml  # Linux
open ~/.config/io.datasette.llm/templates/assistant.yaml  # macOS

# View installed plugins
llm plugins
```

### Basic Prompts

**💡 Important**: All `llm` and `llm chat` commands automatically use the **[assistant template](llm-template/assistant.yaml)** by default. You don't need to specify `-t assistant`!

The assistant template is configured for security/IT expertise with German language responses - perfect for cybersecurity and Linux tasks.

Simple question-and-answer prompts:

```bash
# Ask a question (per your assistant template, will answer in German)
# The assistant template is automatically applied
llm "Was ist das meistverbreite Betriebssystem für Pentester?"

# Continue the most recent conversation in CLI mode (--continue / -c)
llm -c "Und für Forensiker?"

# Include system information in your prompt
llm "Tell me about my operating system: $(uname -a)"

# Pipe output to clipboard
llm "Was ist das meistverbreite Betriebssystem für Pentester?" | pbcopy
```

**Note**: The shell integration provides macOS-style clipboard commands (`pbcopy`/`pbpaste`) on Linux via aliases to `xsel`.

**Interactive Chat Mode**

Continue a conversation with context (assistant template is auto-applied to new chats):

```bash
# Start a new chat conversation (assistant template auto-applied)
llm chat

# Continue the most recent conversation (template not re-applied)
llm chat -c

# Ask a follow-up question (template not re-applied for continuations)
llm "Wenn ich nur eines mache, was ist dann das wichtigste?" -c
```

### AI Command Completion

Type a partial command or describe what you want in natural language, then press **Ctrl+N**:

```bash
# Type: list all pdf files
# Press Ctrl+N
# Result: find . -type f -name "*.pdf"

# Type: Wie finde ich alle .sh-Dateien unter /root?
# Press Ctrl+N
# Result: find /root -name "*.sh"
# Don't execute it yet.
# Type: Aber nur auf demselben Dateisystem.
# Result: find /root -name "*.sh" -xdev
```

The AI will suggest and execute the command automatically. You can also use `llm cmd` directly:

```bash
# Generate a command
llm cmd "undo last git commit"

# More complex examples
llm cmd "Wie finde ich alle .sh-Dateien unter /root?"
```

### Attachments & Multi-modal

Some models support images, PDFs, audio, and video as input:

```bash
# Attach an image from URL
llm "Beschreibe" -a https://www.corporate-trust.de/wp-content/uploads/2023/12/poster_ransomware.pdf
llm "Wenn ich nur eines mache, was ist dann das wichtigste?" -c

# Attach local images
llm "Extrahiere den Text" -a image1.jpg -a image2.jpg

# Pipe content and attach it
wget https://www.corporate-trust.de/wp-content/uploads/2023/12/poster_ransomware.pdf -O poster.pdf
cat poster.pdf | llm 'describe image' -a -
```

**Note**: The `-a -` flag reads attachment data from stdin.

**⚠️ Azure OpenAI Limitation**

Azure OpenAI models configured in this setup **do not support the `-a`/`--attachment` parameter** for images, PDFs, or other files. Even with `vision: true` in the configuration, you will receive this error:

```
Error: Error code: 400 - {'error': {'message': "Invalid Value: 'file'.
This model does not support file content types.", 'type': 'invalid_request_error'}}
```

**Workarounds**:

1. **For text extraction only**: Use fragments to extract text content (not visual analysis):
   ```bash
   # Extract text from PDF (not visual analysis!)
   llm -f pdf:poster.pdf "summarize the text"
   ```
   
2. **Use non-Azure models for vision tasks**: Switch to OpenAI, Gemini, or Anthropic models that support attachments:
   ```bash
   # Use with attachments
   llm -m gpt-4o "describe this image" -a image.jpg
   llm -m gemini-2.5-flash "describe" -a poster.pdf
   llm -m claude-4-5-sonnet "analyze" -a document.pdf
   ```

See [Azure OpenAI vision documentation](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/gpt-with-vision) for current limitations.

### Fragments

Fragments are reusable pieces of context that can be loaded into your prompts. They can be files, URLs, GitHub repositories, or PDFs.

For more details, see the [official fragments documentation](https://llm.datasette.io/en/stable/fragments.html).

**File and URL Fragments**

Load local files or fetch content from URLs:

```bash
# Load a local file as context
llm -f /path/to/file.py "Explain this code"

# Load content directly from a URL (raw fetch)
llm -f https://example.com/article "Summarize this article"

# Combine multiple fragments (great for comparing files)
llm chat -f https://raw.githubusercontent.com/offsh/terminator_logger/refs/heads/master/auditor.py \
         -f https://raw.githubusercontent.com/gnome-terminator/terminator/refs/heads/master/terminatorlib/plugins/logger.py

# Then ask in the chat
# > Was ist der Unterschied zwischen den beiden Implementierungen?
```

**💡 URL Fragment Types: `-f https://url` vs `-f site:https://url`**

There are two ways to fetch web content:

1. **Direct URL fetch** (`-f https://url`):
   - Fetches the URL content directly (raw)
   - Best for: APIs, JSON endpoints, raw files, GitHub raw content, RSS feeds
   - Gets exactly what the URL returns (HTML, JSON, plain text, etc.)

2. **Smart web extraction** (`-f site:https://url`):
   - Uses the `llm-fragments-site-text` plugin
   - Intelligently extracts main content from HTML pages
   - Converts to clean markdown
   - Removes navigation, ads, sidebars, and boilerplate
   - Best for: Blog posts, articles, documentation pages, news sites

**When to use which:**
```bash
# ✅ Direct fetch for raw content and APIs
llm -f https://api.github.com/repos/user/repo "What's the star count?"
llm -f https://raw.githubusercontent.com/user/repo/main/script.py "Explain this code"

# ✅ Smart extraction for web pages with lots of HTML
llm -f site:https://example.com/blog/article "Summarize the main points"
llm -f site:https://docs.example.com/guide "Extract the installation steps"

# ❌ Don't use site: for raw content (it's already clean)
# llm -f site:https://api.github.com/repos/user/repo  # Unnecessary
# llm -f site:https://raw.githubusercontent.com/...   # Unnecessary
```

**GitHub Repository Fragments**

Load entire GitHub repositories or specific issues/PRs (powered by [llm-fragments-github](https://github.com/simonw/llm-fragments-github)):

```bash
# Load an entire repository
llm -f github:Softeria/ms-365-mcp-server \
    "Wo wird da die App / der Dienstprinzipal konfiguriert in welcher Quelltextdatei?"

# Continue the conversation
llm chat -c
# > Was muss ich machen, wenn ich selbst so eine App in meinem Tenant anlegen möchte?

# Compare two similar projects
llm -f github:space-cadet/yt-mcp \
    "Kann dieser MCP-Server Playlisten beliebiger länge abfragen, oder gibt es ein hartkodiertes Limit?"

llm -c -f github:emit-ia/youtube-transcript-mcp \
    "Und jetzt im Vergleich dieser. Was sind da die Unterschiede? Der letzte erfordert keinen API-Key, oder wie macht er das?"
    
# Load a specific GitHub issue
llm -f issue:simonw/llm/123 "Summarize this issue"

# Load a pull request with diff
llm -f pr:simonw/llm/456 "Review this PR"
```

**Website Content Fragments**

Extract and convert web pages to markdown using the `site:` prefix:

```bash
# Smart extraction with site: prefix (removes ads, navigation, etc.)
llm -f site:https://www.heise.de "summarize this"

# You can also use direct URL fetch (but may include extra HTML)
llm -f https://www.volexity.com/blog/2025/04/22/phishing-for-codes-russian-threat-actors-target-microsoft-365-oauth-workflows/ \
    "What are the key findings?"

# Compare: site: prefix gives cleaner content for complex web pages
llm -f site:https://www.volexity.com/blog/2025/04/22/phishing-for-codes-russian-threat-actors-target-microsoft-365-oauth-workflows/ \
    "Extract the key findings"  # Cleaner, focuses on article content
```

**Tip**: For web pages with lots of navigation, ads, or HTML structure, use `-f site:https://url` for cleaner results. See the [URL Fragment Types](#-url-fragment-types--f-httpsurl-vs--f-sitehttpsurl) section above for detailed comparison.

**PDF Fragments**

For models that don't support native PDF attachments, use the `pdf:` fragment type:

```bash
# Load a PDF as context
llm -f pdf:poster.pdf "Beschreibe"

# Alternative to using -a for PDFs (converts to markdown first)
wget https://www.corporate-trust.de/wp-content/uploads/2023/12/poster_ransomware.pdf -O poster.pdf
llm -f pdf:poster.pdf "Was ist das wichtigste auf diesem Poster?"
```

### Templates

Templates are pre-configured prompt patterns that you can reuse. This setup includes custom templates and Fabric patterns.

**💡 Reminder**: The `assistant` template is **already the default** - you only need `-t` for **different** templates!

**Using Templates**

```bash
# ❌ Don't do this (assistant is already default)
# llm -t assistant "Your question here"

# ✅ Just use llm directly for the default assistant template
llm "Your question here"

# ✅ Use -t when you want a DIFFERENT template
llm -t fabric:summarize -f https://github.com/c0ffee0wl/llm-linux-setup

# ✅ The code template outputs clean code without markdown
llm -t code "Python function to calculate fibonacci"

# ✅ Or use the convenient shorthand
llm code "Python function to calculate fibonacci"
```

**Fabric Templates**

The llm-templates-fabric plugin provides access to [Fabric patterns](https://github.com/danielmiessler/Fabric/tree/main/data/patterns):

```bash
# Explain code using Fabric's explain_code pattern
llm -f github:TheR1D/shell_gpt -t fabric:explain_code

# Analyze a threat report
llm -f https://www.volexity.com/blog/2025/04/22/phishing-for-codes-russian-threat-actors-target-microsoft-365-oauth-workflows/ \
    -t fabric:analyze_threat_report

# Summarize content
llm -f site:https://example.com/article -t fabric:summarize

# Review code architecture
llm -f github:user/repo -t fabric:review_code

# Create STRIDE threat model
llm -f github:user/secure-app -t fabric:create_stride_threat_model

# Write Semgrep rules
llm "Create a Semgrep rule for SQL injection" -t fabric:write_semgrep_rule
```

**Popular Fabric Patterns**:
- `fabric:explain_code` - Explains code, security tool output, configuration
- `fabric:analyze_threat_report` - Extracts insights from cybersecurity reports
- `fabric:summarize` - Creates concise summaries
- `fabric:review_code` - Analyzes code architecture and design
- `fabric:create_stride_threat_model` - STRIDE threat modeling
- `fabric:write_semgrep_rule` - Generates Semgrep security rules

For a complete list of available patterns, see the [Fabric Pattern Explanations](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/pattern_explanations.md).

### Code Generation

Generate clean, executable code without markdown formatting using the `llm code` command.

**💡 Note**: `llm code` is a **convenient shorthand** for `llm -t code` provided by the shell integration. Both forms work identically:
```bash
# These are equivalent:
llm code "python function to check prime"
llm -t code "python function to check prime"
```

The `code` template outputs **pure code without explanations or markdown blocks**, making it perfect for piping to files or direct execution.

**💡 Tip**: Examples below use `| tee filename` instead of `> filename` to provide visual feedback - you'll see the generated code in your terminal while it's also being saved to the file. This helps verify the output before using it.

**Examples:**

```bash
# Generate Python function
llm code "function to check if number is prime in Python" | tee prime.py

# Generate bash script
llm code "Bash script to backup directory with timestamp" | tee backup.sh

# Generate SQL query (prints to stdout)
llm code "SQL select users who registered this month"

# Generate configuration file
llm code "nginx config for reverse proxy on port 3000" | tee nginx.conf

# Direct execution (use with caution!)
llm code "one-liner to find files larger than 100MB in Bash" | bash

# Generate Dockerfile
llm code "dockerfile for nodejs app with nginx" | tee Dockerfile

# Generate regex pattern
llm code "regex to match email addresses"
```

**Process Substitution (Execute Without Saving)**

Use Bash/Zsh process substitution `<(command)` to execute generated Python code as a temporary file:

```bash
# Execute Python code directly without creating files
python <(llm code "Python script to display fizzbuzz")

# Test code instantly
python <(llm code "Python function to calculate fibonacci, then print first 10 numbers")
```

**How it works**: The `<(command)` syntax creates a temporary file descriptor that Python reads as if it were a regular file, then automatically cleans up when done.

**Benefits**:
- No temporary files to manage or delete
- Instant testing and iteration
- Clean workspace

**⚠️ Security Warning**: Only use with trusted prompts. Generated code executes immediately with your user permissions. Review output first for sensitive operations:
```bash
# Safer: Review before executing
llm code "Python script to delete old files" | tee cleanup.py
# Review cleanup.py, then: python cleanup.py
```

**Key Features:**
- No markdown code blocks (no \`\`\`)
- No explanations or commentary
- Output is directly executable/pipeable
- Infers language from context
- Ideal for automation and scripting

### Tools

LLM supports tools that AI models can call during conversations.

**SQLite Database Queries**

Query SQLite databases using natural language:

```bash
# Download a sample database
wget https://www.timestored.com/data/sample/chinook.db

# Query with natural language
llm -T 'SQLite("chinook.db")' "Count rows in the most interesting looking table" --td

# Interactive chat mode with database access
llm chat -T 'SQLite("chinook.db")'
# > Show me the three most interesting looking tables
# > What are the top 5 best-selling artists?
```

The `--td` flag shows tool descriptions.

**JSON Processing with llm-jq**

Generate and execute jq programs using natural language:

```bash
# Process JSON from an API
curl -s https://api.github.com/repos/simonw/datasette/issues | \
    llm jq 'count by user.login, top 3'

# Parse complex JSON structures
echo '{"users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]}' | \
    llm jq 'extract names and ages'

# Options:
# -s/--silent: Hide the generated jq program
# -o/--output: Show only the jq program (don't execute)
# -v/--verbose: Display the AI prompt and response
```

### Context System Usage

**💡 Important**: The `context` tool is **built into the assistant template** by default! You can ask about your terminal history naturally without typing `--tool context` every time.

Query your terminal history to get context-aware AI assistance:

```bash
# ✅ Ask AI naturally - context tool is automatically available
llm "what was the error in my last command?"
llm chat
# > Read the context. How do I fix the compilation error?
# > Summarize what I did in this session

# ⚠️ Explicit tool invocation (useful for one-shot queries or non-assistant templates)
llm --tool context "what was the error in my last command?"
llm --tool context "summarize what I did in this session"

# Show last command and output directly
context

# Show last 5 commands with outputs
context 5

# Show entire session history
context all
```

**Side-by-Side Terminal Workflow**

The `context -e` feature enables a powerful workflow where you can query an ongoing session from a separate terminal window. This is ideal for side-by-side setups in Terminator, tmux, or any terminal multiplexer:

```bash
# Terminal 1 (left pane): Your work session
cd /opt/myproject
./build.sh
# ... working on something ...

# Get the export command
context -e

# Terminal 2 (right pane): AI assistant watching your session
# Paste the export command from Terminal 1
export SESSION_LOG_FILE="/tmp/session_logs/asciinema/2025-10-15_14-30-45-123_12345.cast"

# Now you can query what's happening in Terminal 1 in real-time
# Context tool is available automatically - just ask naturally!
llm "what did the build script just do?"
llm "were there any errors in the last command?"

# Or use chat mode for continuous assistance
llm chat
# > Read the context. What compilation errors occurred?
# > Suggest a fix for the last error shown

# Terminal 1: Keep working
make test
git commit -m "fix compilation error"

# Terminal 2: Query the updates
llm "did the tests pass?"
```

**Use Cases:**
- **Real-time debugging**: Watch compilation/test output in one terminal while querying AI for solutions in another
- **Code review assistance**: Run commands in one terminal while AI analyzes outputs in another
- **Learning workflows**: Execute tutorials/commands while asking AI to explain what's happening

**Note**: Both terminals read from the same `.cast` file, so the side terminal sees all commands and outputs from the work terminal as they happen.

**Example Workflow**

```bash
# Run a command that might fail
pong heise.de

# Check what happened
context

# Ask AI to analyze the error
context | llm "Was hab ich die letzten beiden Befehle gemacht?"

# Continue in chat mode
llm chat -c
# > Lies den context nochmal. Und der ping-Befehl, den ich gerade ausgeführt habe, hat der besser funktioniert?

# Try the corrected command
ping heise.de -c 5

# Verify success with AI
llm chat -c
# > Lies den context nochmal. Hat der ping jetzt funktioniert?
```

The context system automatically captures:
- Commands you run
- Complete command output
- Error messages and stack traces
- Multi-line commands and their results

This allows AI models to provide context-aware debugging and assistance based on your actual terminal activity.


### Integration with Other Tools

**Repository Analysis with gitingest**

Convert Git repositories to LLM-friendly text:

```bash
# Analyze a remote repository
gitingest https://github.com/user/repo

# Analyze a local repository
gitingest /path/to/local/repo

# Combine with LLM for analysis
cat digest.txt | \
    llm "What is the main purpose of this codebase?"

# Show more parameters, e.g. for exclusion and inclusion
gitingest --help
```

**File Bundling with files-to-prompt**

Concatenate multiple files for LLM context:

```bash
# Bundle all Python files in a directory
files-to-prompt src/*.py | llm "Review this codebase for security issues"

# Include only specific file types
files-to-prompt project/ -e py -e js | llm "What frameworks are being used?"

# Output in Claude XML format
files-to-prompt src/ -c > context.xml

# Show more parameters, e.g. for exclusion and inclusion
files-to-prompt --help
```

**Git Log Analysis**

Analyze your development history:

```bash
# Summarize recent development (last 30 days)
cd /opt/llm-linux-setup
git log --since="30 days ago" | \
    llm "In visual markdown, prepare a timeline of development during this period, including stages of work and milestones."

# Analyze commit patterns
git log --author="Your Name" --since="1 month ago" --pretty=format:"%s" | \
    llm "Categorize these commits by type (feature, fix, docs, etc.)"
```

**Piping Workflows**

Chain multiple tools together:

```bash
# Fetch, extract, and analyze
curl -s https://api.github.com/repos/simonw/llm | \
    llm jq 'extract stars, forks, and open issues' | \
    llm "Analyze the project popularity"

# Process logs
tail -n 100 /var/log/syslog | \
    llm "Identify any errors or warnings and explain them"

# Analyze command output
docker ps --format json | \
    llm jq 'count by image' | \
    llm "Which containers are running most frequently?"
```

### Managing Models

**List Available Models**

```bash
# List all models
llm models

# Find default model
llm models | grep -i default

# List Azure models
llm models | grep azure
```

**Set Default Model**

```bash
# Set default model for all commands
llm models default azure/gpt-5-mini

# Alternative: using environment variable
export LLM_MODEL=azure/o4-mini
llm "Your prompt"  # Uses o4-mini
```

**Use Specific Models**

```bash
# Override default with -m flag
llm "Ten names for cheesecakes" -m azure/o4-mini

# Use different models for different tasks
llm -m azure/gpt-5 "Complex reasoning task"
llm -m azure/gpt-5-mini "Simple question"
```

**Azure OpenAI Models**

The following models are configured:
- `azure/gpt-5` - GPT-5 (most capable)
- `azure/gpt-5-mini` - GPT-5 Mini (balanced, default)
- `azure/gpt-5-nano` - GPT-5 Nano (fast, cost-effective)
- `azure/o4-mini` - O4 Mini (advanced reasoning)
- `azure/gpt-4.1` - GPT-4.1 (previous generation)

## Understanding the Shell Integration

The installation adds a **smart wrapper function** around the `llm` command that automatically applies templates based on context. This means you rarely need to specify `-t assistant` explicitly!

### How Automatic Templates Work

**✅ When the assistant template is AUTO-APPLIED:**
- `llm "Your question"` → Uses assistant template automatically
- `llm chat` → Uses assistant template automatically
- Any prompt command without explicit template specification

**⚠️ When templates are NOT auto-applied:**
- When continuing a conversation: `llm chat -c`, `llm -c "follow-up"`
- When specifying a custom template: `llm -t fabric:summarize`
- When using a custom system prompt: `llm -s "You are..."`
- For management commands: `llm models`, `llm keys`, `llm plugins`, etc.
- For specialized subcommands: `llm cmd`, `llm jq`, `llm cmdcomp`, etc.

### Bypassing the Shell Wrapper

If you need to execute the original `llm` command without the shell wrapper function (for debugging, testing, or to avoid automatic template application), use the `command` builtin:

```bash
# Uses the shell wrapper (assistant template auto-applied)
llm "Your question"

# Bypasses the wrapper (no automatic template)
command llm "Your question"

# Useful for debugging or when you want exact control
command llm -t assistant "Your question"  # You must specify -t explicitly
```

**When to use `command llm`:**
- Debugging shell integration issues
- Testing without automatic template modifications
- Scripts that need exact `llm` behavior without wrapper modifications
- When you want complete manual control over all parameters

### Command Shortcuts

**`llm code` is a convenient shorthand:**
```bash
# These are equivalent:
llm code "python function to check prime numbers"
llm -t code "python function to check prime numbers"
```

The `code` template outputs clean, executable code without markdown blocks or explanations - perfect for piping to files or direct execution.

### When to Use `-t`

**You ONLY need `-t` when you want a DIFFERENT template:**
```bash
# ❌ Unnecessary (assistant is already default)
llm -t assistant "What is Docker?"

# ✅ Correct (just omit -t)
llm "What is Docker?"

# ✅ Use -t for non-default templates
llm -t fabric:summarize "Explain Kubernetes"
llm -t fabric:analyze_threat_report -f report.pdf
```

### Key Benefits

- **Less typing**: No need to specify `-t assistant` every time
- **Consistent behavior**: Your custom [assistant template](llm-template/assistant.yaml) (with security/IT expertise, German language) is used by default
- **Clear shortcuts**: `llm code` is easier to type than `llm -t code`
- **Smart detection**: Conversation continuations automatically skip re-applying templates

**What does the assistant template do?** See the [assistant.yaml source](llm-template/assistant.yaml) - it configures the AI with:
- Security/IT/Linux expertise (20 years experience)
- German language responses (code/commands in English)
- Cybersecurity focus (ethical hacking, forensics, incident response)
- Kali Linux/Ubuntu/Debian environment awareness
- **Integrated `context` tool** for reading terminal history (automatically available!)

### Context Tool Integration

The assistant template includes the **`context` tool by default**, which means AI models can automatically read your terminal history without you needing to explicitly specify `--tool context` every time!

**✅ When the context tool is automatically available:**
```bash
# Just ask naturally - the AI can use the context tool
llm "what was the error in my last command?"
llm chat
# > Read the context. What did I just run?
# > Can you explain the output from my last command?
```

**⚠️ When you need to use `--tool context` explicitly:**
```bash
# When continuing a conversation (template not re-applied)
llm chat -c
llm --tool context "what was the error?" -c  # Need explicit --tool

# When using a different template
llm -t fabric:summarize --tool context "what happened?"

# When bypassing the shell wrapper
command llm --tool context "what went wrong?"
```

**Key Insight**: The assistant template makes the context tool available during conversations, so you can simply ask "what was the error?" or "what did I just do?" and the AI will automatically check your terminal history. The explicit `--tool context` syntax is mainly useful for:
- One-shot queries outside of conversations
- Using context with non-assistant templates
- Continuation commands where the template isn't re-applied

## Configuration

### Configuration Files

- `~/.config/io.datasette.llm/` - LLM configuration directory
  - `extra-openai-models.yaml` - Azure OpenAI model definitions
  - `templates/assistant.yaml` - Custom [assistant template](llm-template/assistant.yaml) with security/IT expertise (German language, cybersecurity focus)
  - `templates/code.yaml` - [Code-only generation template](llm-template/code.yaml) (no markdown, no explanations)
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
- [`integration/llm-integration.bash`](integration/llm-integration.bash) - Bash integration (Ctrl+N keybinding)
- [`integration/llm-integration.zsh`](integration/llm-integration.zsh) - Zsh integration (Ctrl+N keybinding)
- [`integration/llm-common.sh`](integration/llm-common.sh) - Shared configuration (llm wrapper function, auto-recording)

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
   - The `context` tool is **built into the assistant template by default**
   - AI models can automatically query your recent command history during conversations
   - Just ask naturally: `llm "what was the error?"` - no need for `--tool context`!
   - Explicit `--tool context` is only needed for non-assistant templates or continuations
   - Example: `llm "what was the error in my last command?"` (context tool used automatically)

### Storage Configuration

On first installation, you'll be prompted to choose where session recordings are stored:

- **Temporary** (default): `/tmp/session_logs/asciinema/` - Cleared on reboot, saves disk space
- **Permanent**: `~/session_logs/asciinema/` - Survives reboots, useful for long-term history

You can change this later by editing the `SESSION_LOG_DIR` export in your `.bashrc` or `.zshrc`.

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

## Support

For issues, questions, or suggestions:
- Open an issue: https://github.com/c0ffee0wl/llm-linux-setup/issues

## Related Projects

- [llm-windows-setup](https://github.com/c0ffee0wl/llm-windows-setup) - Windows version

## Credits

### Core Tools & Frameworks
- [Simon Willison](https://github.com/simonw) - llm CLI tool and plugins (llm-gemini, llm-anthropic, llm-openrouter, llm-jq, llm-tools-sqlite, llm-tools-quickjs, llm-fragments-github, llm-cmd)
- [Astral](https://astral.sh/) - uv Python package manager
- [Rust Foundation](https://foundation.rust-lang.org/) - Rust programming language and Cargo
- [Node.js Foundation](https://nodejs.org/) - Node.js JavaScript runtime
- [Anthropic](https://www.anthropic.com/) - Claude Code agentic coding CLI
- [SST](https://sst.dev/) - OpenCode agentic coding CLI

### LLM Plugins & Extensions
- [Daniel Turkel](https://github.com/daturkel) - llm-fragments-pdf, llm-fragments-site-text
- [ Ryan Patterson ](https://github.com/CGamesPlay) - llm-cmd-comp plugin
- [Dan Mackinlay](https://github.com/danmackinlay) - files-to-prompt (fork)
- [Damon McMinn](https://github.com/damonmcminn) - llm-templates-fabric (fork)
- [Daniel Miessler](https://github.com/danielmiessler) - Original Fabric prompt patterns

### Additional Tools
- [Asciinema](https://github.com/asciinema/asciinema) - Terminal session recorder
- [Coderamp Labs](https://github.com/coderamp-labs/gitingest) - gitingest repository analyzer

## License

This installation script is provided as-is. 
Individual tools have their own licenses:
- llm: Apache 2.0
- See individual tool repositories for details

## Contributing

To modify or extend this installation, see [CLAUDE.md](CLAUDE.md) for detailed architecture documentation.

**Key files to understand:**
- [`install-llm-tools.sh`](install-llm-tools.sh) - Main installation script (7 phases, self-updating)
- [`integration/llm-common.sh`](integration/llm-common.sh) - Shell wrapper function, auto-recording
- [`context/context`](context/context) - Terminal history extraction script
- [`llm-tools-context/`](llm-tools-context/) - LLM plugin for context tool
- [`llm-template/`](llm-template/) - Custom template sources

**Development workflow:**
1. Read [CLAUDE.md](CLAUDE.md) to understand architecture
2. Edit the scripts in the repository
3. Test your changes
4. Commit and push to git
5. Changes pulled automatically on next run
