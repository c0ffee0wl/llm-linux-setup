# LLM Tools Installation Script for Linux

Automated installation script for [Simon Willison's llm CLI tool](https://github.com/simonw/llm) and related AI/LLM command-line utilities for Debian-based Linux environments.

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

- [Features](#features)
- [System Requirements](#system-requirements)
- [Installation](#installation)
  - [Prerequisites (Recommended)](#prerequisites-recommended)
  - [Quick Start](#quick-start)
  - [Updating](#updating)
- [Quick Reference](#quick-reference)
- [Documentation](#documentation)
  - [This Project](#this-project)
  - [Original Tools](#original-tools)
- [What Gets Installed](#what-gets-installed)
  - [Core Tools](#core-tools)
  - [Necessary Prerequisites](#necessary-prerequisites)
  - [LLM Plugins](#llm-plugins)
  - [LLM Templates](#llm-templates)
  - [Additional Tools](#additional-tools)
  - [Shell Integration](#shell-integration)
- [Usage](#usage)
  - [Getting Started](#getting-started)
  - [Basic Prompts](#basic-prompts)
  - [Command Completion](#command-completion)
  - [Attachments & Multi-modal](#attachments--multi-modal)
  - [Fragments](#fragments)
  - [Templates](#templates)
  - [Code Generation](#code-generation)
  - [Tools](#tools)
  - [Context System Usage](#context-system-usage)
  - [RAG (Document Querying)](#rag-document-querying)
  - [Integration with Other Tools](#integration-with-other-tools)
  - [LLM Functions (Optional)](#llm-functions-optional)
  - [Managing Models](#managing-models)
  - [Managing API Keys](#managing-api-keys)
- [Understanding the Shell Integration](#understanding-the-shell-integration)
  - [How Automatic Templates Work](#how-automatic-templates-work)
  - [Bypassing the Shell Wrapper](#bypassing-the-shell-wrapper)
  - [When to Use `-t`](#when-to-use--t)
  - [Key Benefits](#key-benefits)
  - [Context Tool Integration](#context-tool-integration)
- [Session Recording & Context System](#session-recording--context-system)
  - [How It Works](#how-it-works)
  - [Storage Configuration](#storage-configuration)
- [Understanding Azure OpenAI Setup](#understanding-azure-openai-setup)
  - [Architecture Overview](#architecture-overview)
  - [Configuration Files](#configuration-files)
  - [Azure-Specific Limitations](#azure-specific-limitations)
  - [Why Azure OpenAI?](#why-azure-openai)
- [Alternative: Gemini for Private Use](#alternative-gemini-for-private-use)
  - [Switching Providers](#switching-providers)
- [Configuration](#configuration)
  - [Configuration Files](#configuration-files-1)
  - [Shell Integration Files](#shell-integration-files)
- [Troubleshooting](#troubleshooting)
  - [Update fails](#update-fails)
  - [Command completion not working](#command-completion-not-working)
  - [Azure API errors](#azure-api-errors)
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
- ✅ **Provider choice** - Configure either Azure OpenAI (enterprise) or Google Gemini (free tier)
- ✅ **Easy provider switching** - Use `--azure` or `--gemini` flags to switch anytime
- ✅ **Command completion** - Press Ctrl+N for AI command suggestions, Tab for llm autocompletion (Zsh)
- ✅ **Automatic session recording** - Terminal history captured for AI context
- ✅ **AI-powered context retrieval** - Query your command history with `context` or `llm -T context`
- ✅ **RAG document querying** - Query your documents with `llm rag` using AIChat's built-in vector database

## System Requirements

- **OS**: Debian, Ubuntu, Kali Linux (or derivatives)
- **Python**: 3.8+ (usually pre-installed)
- **Rust**: 1.85+ (automatically installed via rustup if not available)
- **Node.js**: 20+ (automatically installed via nvm if repository version is older)
- **Internet**: Required for installation and API access
- **Disk Space**: ~500MB for all tools and dependencies

**Supported Shells**:

- Zsh (5.0+) - Recommended
- Bash (3.0+)

**Note**: The installation script automatically handles Rust and Node.js version requirements. If your system has older versions, it will offer to install newer versions via rustup and nvm respectively.

**Recommended**: For a fully configured Linux environment with optimized shell settings, security tools, and system utilities, consider installing [linux-setup](https://github.com/c0ffee0wl/linux-setup) first. This provides the base configuration layer that complements the LLM tools.

## Installation

### Prerequisites (Recommended)

For the best experience, install [linux-setup](https://github.com/c0ffee0wl/linux-setup) first to get a fully configured Linux environment:

```bash
git clone https://github.com/c0ffee0wl/linux-setup.git
cd linux-setup
./linux-setup.sh
```

The linux-setup repository provides:

- Optimized Zsh configurations and aliases
- Essential security and development tools
- Base environment that complements LLM tools

This step is **optional** but recommended for the most complete setup.

### Quick Start

```bash
git clone https://github.com/c0ffee0wl/llm-linux-setup.git
cd llm-linux-setup
./install-llm-tools.sh
```

During first-time installation, you'll be prompted for:

1. **Provider Choice** - Choose **either** Azure OpenAI (enterprise) **or** Google Gemini (free tier)
   - **Azure**: Prompts for API key and resource URL
   - **Gemini**: Prompts for API key (free from Google AI Studio)
2. **Session Log Storage** - Choose between temporary (`/tmp`, cleared on reboot) or permanent (`~/session_logs`, survives reboots)

**Note:** You can only use one provider at a time for AIChat, especially for its RAG feature. To switch providers later, use the appropriate flag.

### Updating

Simply re-run the installation script:

```bash
cd llm-linux-setup
./install-llm-tools.sh
```

To switch or reconfigure providers:

```bash
# Switch to or reconfigure Azure OpenAI
./install-llm-tools.sh --azure

# Switch to or reconfigure Google Gemini
./install-llm-tools.sh --gemini
```

The script will:

1. Pull the latest version of the repository (and itself) from git.
2. Update llm, its plugins, and all applications installed by the llm-setup.
3. Update custom templates ([assistant.yaml](llm-template/assistant.yaml), [code.yaml](llm-template/code.yaml)).
4. Refresh shell integration files.
5. Preserve existing provider and session log configurations (unless switching providers).

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
llm 'extract the metadata' < setup.py     # Avoid useless use of cat
llm -f setup.py 'extract the metadata'    # Alternatively use local fragments

ls -1aG | llm "Describe each of the files"
llm "What does 'ls -1aG' do?"

# Use command completion (works best with Zsh)
# Type: find pdf files larger than 20MB
# Press: Ctrl+N

# Generate clean code ('llm code' is an alias for 'llm -t code')
llm code "python function to..." | tee output.py

# You can iterate on generated code by continuing the last conversation
llm code "find files larger than 100MB as a bash script" | tee script.sh
llm code -c "and smaller than 500MB; add comments" | tee script.sh

# Use fragments for context
llm -f github:user/repo "analyze this"
llm -f pdf:document.pdf "summarize"
llm -f yt:https://youtube.com/watch?v=VIDEO_ID "summarize video"
llm -f https://example.com "extract key points"

# Use -t when you want a DIFFERENT template that the default assistant template
llm -t fabric:summarize "..."        # Not the default
llm -t fabric:analyze_threat_report  # Not the default

# Query your documents with RAG ('llm rag' is an alias for 'aichat --rag')
llm rag mydocs                    # Open/create RAG collection for documents
llm rag mydocs --rebuild-rag      # Rebuild index after changes
aichat --rag projectdocs          # Direct aichat usage

# Query terminal history (context tool is built into assistant template!)
llm "what was the error in my last command?"   # Uses context tool automatically in default template
command llm -T context "..."                   # Explicit tool call (for non-assistant templates)
context                                        # Show last command
context 5                                      # Show last 5 commands

# Run shell commands safely (sandboxed_shell tool is built into assistant template!)
llm "Check if docker is installed and show version"   # AI runs commands safely, automatically
llm chat   # Then ask: "Can you list the files in /root?"
llm "Check kernel version" --td         # Show tool execution details
llm "Check kernel version" --ta         # Require manual approval before execution
command llm -T sandboxed_shell "..."    # Explicit tool call (for non-assistant templates)

# File manipulation (use -T Patch with --ta for safety)
llm -T Patch "Read config.yaml" --ta                           # Read files
llm -T Patch "Create hello.py with a hello world program" --ta # Create files
llm -T Patch "In config.yaml, change debug to true" --ta       # Edit files
```

## Documentation

### This Project

- [README.md](README.md) - Readme
- [CLAUDE.md](CLAUDE.md) - Developer documentation and architecture guide (for Claude Code and contributors)

### Original Tools

- [LLM Documentation](https://llm.datasette.io/)
- [LLM Plugins Directory](https://llm.datasette.io/en/stable/plugins/directory.html)
- [AIChat Documentation](https://github.com/sigoden/aichat/blob/main/README.md)
- [AIChat Wiki](https://github.com/sigoden/aichat/wiki)
- [Gitingest Documentation](https://github.com/coderamp-labs/gitingest)
- [Files-to-Prompt Documentation](https://github.com/simonw/files-to-prompt)
- [Claude Code Documentation](https://docs.claude.com/en/docs/claude-code/overview)
- [OpenCode Documentation](https://opencode.ai/docs)

## What Gets Installed

### Core Tools

- **[llm](https://github.com/c0ffee0wl/llm)** - LLM CLI tool (fork with markdown markup enhancements, originally by Simon Willison - [Documentation](https://llm.datasette.io/))
- **[AIChat](https://github.com/sigoden/aichat)** - All-in-one LLM CLI with RAG functionality (built-in vector database for document querying)
- **[Claude Code](https://docs.claude.com/en/docs/claude-code)** - Anthropic's official agentic coding CLI
- **[OpenCode](https://github.com/sst/opencode)** - AI coding agent for terminal

### Necessary Prerequisites

- **[Python 3](https://python.org/)** - Required for llm
- **[uv](https://docs.astral.sh/uv/)** - Modern Python package installer
- **[Node.js](https://nodejs.org/)** - JavaScript runtime (v20+, from repositories or nvm)
- **[Rust/Cargo](https://www.rust-lang.org/)** - Rust toolchain (v1.85+, from repositories or rustup)
- **[argc](https://github.com/sigoden/argc)** - Bash CLI framework and command runner (enables optional llm-functions integration)
- **[bubblewrap](https://github.com/containers/bubblewrap)** - Sandboxing tool for llm-tools-sandboxed-shell
- **[poppler-utils](https://poppler.freedesktop.org/)** - PDF utilities (pdftotext for RAG)
- **[pandoc](https://pandoc.org/)** - Document converter (DOCX support for RAG)
- **[xsel](https://github.com/kfish/xsel)** - X11 clipboard tool (enables pbcopy/pbpaste on Linux)
- **[jq](https://stedolan.github.io/jq/)** - Command-line JSON processor

### LLM Plugins

- **[llm-cmd](https://github.com/c0ffee0wl/llm-cmd)** - Command execution and management
- **[llm-cmd-comp](https://github.com/c0ffee0wl/llm-cmd-comp)** - AI-powered command completion (powers Ctrl+N)
- **[llm-tools-quickjs](https://github.com/simonw/llm-tools-quickjs)** - JavaScript execution tool
- **[llm-tools-sqlite](https://github.com/simonw/llm-tools-sqlite)** - SQLite database tool
- **[llm-jq](https://github.com/simonw/llm-jq)** - JSON processing tool
- **[llm-tools-sandboxed-shell](https://github.com/c0ffee0wl/llm-tools-sandboxed-shell)** - Sandboxed shell command execution
- **[llm-tools-patch](https://github.com/c0ffee0wl/llm-tools-patch)** - File manipulation tools (read, write, edit, multi_edit, info)
- **[llm-tools-context](llm-tools-context/)** - Terminal history integration (exposes `context` tool to AI)
- **[llm-fragments-site-text](https://github.com/daturkel/llm-fragments-site-text)** - Web page content extraction
- **[llm-fragments-pdf](https://github.com/daturkel/llm-fragments-pdf)** - PDF content extraction
- **[llm-fragments-github](https://github.com/simonw/llm-fragments-github)** - GitHub repository integration
- **[llm-fragments-youtube-transcript](https://github.com/c0ffee0wl/llm-fragments-youtube-transcript)** - YouTube video transcript extraction with metadata
- **[llm-templates-fabric](https://github.com/c0ffee0wl/llm-templates-fabric)** - Fabric prompt templates
- **[llm-tools-llm-functions](https://github.com/c0ffee0wl/llm-tools-llm-functions)** - Bridge for optional [llm-functions](https://github.com/sigoden/llm-functions) integration (enables custom tools in Bash/JS/Python)
- **[llm-gemini](https://github.com/simonw/llm-gemini)** - Google Gemini models integration
- **[llm-vertex](https://github.com/c0ffee0wl/llm-vertex)** - Google Vertex AI Gemini models integration
- **[llm-openrouter](https://github.com/simonw/llm-openrouter)** - OpenRouter API integration
- **[llm-anthropic](https://github.com/simonw/llm-anthropic)** - Anthropic Claude models integration

### LLM Templates

- **[assistant.yaml](llm-template/assistant.yaml)** - Custom assistant template with security/IT expertise configuration (Optimized for cybersecurity and Linux tasks, includes `context` and `sandboxed_shell` tools by default)
- **[code.yaml](llm-template/code.yaml)** - Code-only generation template (outputs clean, executable code without markdown)

### Additional Tools

- **[gitingest](https://github.com/coderamp-labs/gitingest)** - Convert Git repositories to LLM-friendly text
- **[yek](https://github.com/bodo-run/yek)** - Fast repository to LLM-friendly text converter (230x faster than alternatives, written in Rust)
- **[files-to-prompt](https://github.com/c0ffee0wl/files-to-prompt)** - File content formatter for LLM prompts
- **[asciinema](https://asciinema.org/)** - Terminal session recorder (built from source for latest features)
- **[context](context/context)** - Python script for extracting terminal history from asciinema recordings

### Shell Integration

- AI-powered command completion (Ctrl+N) - see [`llm-integration.bash`](integration/llm-integration.bash) / [`.zsh`](integration/llm-integration.zsh)
- Tab completion for llm commands (Zsh only) - see [`llm-zsh-plugin`](integration/llm-zsh-plugin/)
- Custom llm wrapper with automatic template application - see [`llm-common.sh`](integration/llm-common.sh)
- Automatic session recording with asciinema - see [`llm-common.sh`](integration/llm-common.sh)
- macOS-style clipboard aliases (`pbcopy`/`pbpaste` via `xsel` on Linux)
- Common aliases and PATH configuration

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

The assistant template is configured for security/IT expertise - perfect for cybersecurity and Linux tasks.

Simple question-and-answer prompts:

```bash
# Ask a question (per your assistant template)
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

**Markdown Rendering (Fork Feature)**

This llm fork includes markdown rendering capabilities using the `rich` library:

```bash
# Render output as beautifully formatted markdown
llm "Explain Docker in markdown format" --markdown

# Use the shorthand flag
llm "Create a bullet list of top 5 Linux commands" --md

# Works with piping too
cat article.txt | llm "Summarize in markdown" --md
```

The `--markdown`/`--md` flags provide:
- Syntax-highlighted code blocks
- Formatted tables, lists, and headers
- Better readability in the terminal

**Interactive Chat Mode**

Continue a conversation with context (assistant template is auto-applied to new chats):

```bash
# Start a new chat conversation (assistant template auto-applied)
llm chat

# Continue the most recent conversation (template not re-applied)
llm chat -c
```

### Command Completion

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

The AI will suggest a command and execute it after your approval.

You can also use `llm cmd` to suggest a command and leave you to modify and execute it.

```bash
# Generate a shell command
llm cmd "Find all .sh files below /root"
```

**Tab Completion (Zsh only)**:

In addition to AI-powered Ctrl+N, Zsh users also get traditional tab completion for `llm` commands:

```bash
llm <TAB>          # Shows: chat, code, rag, models, templates, etc.
llm chat -<TAB>    # Shows all available options
llm -m <TAB>       # Lists available models dynamically
llm -t <TAB>       # Lists available templates
```

This uses a forked version of [llm-zsh-plugin](https://github.com/eliyastein/llm-zsh-plugin) maintained in this repository with custom extensions for `llm code` and `llm rag` subcommands.

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
# Simple alternative without local fragment
llm "Explain this code" < /path/to/file.py

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

**PDF Fragments**

For models that don't support native PDF attachments, use the `pdf:` fragment type:

```bash
wget https://www.corporate-trust.de/wp-content/uploads/2023/12/poster_ransomware.pdf -O poster.pdf

# Load a PDF as context
# Alternative to using -a for PDFs (converts to markdown first)
llm -f pdf:poster.pdf "Was ist das wichtigste auf diesem Poster?"
```

**YouTube Video Fragments**

Extract transcripts and metadata from YouTube videos using the `yt:` fragment type:

```bash
# Summarize a YouTube video
llm -f yt:https://www.youtube.com/watch?v=VIDEO_ID "Summarize this video"

# Extract key points from a video
llm -f yt:https://youtu.be/VIDEO_ID "What are the main topics discussed?"

# Analyze video content
llm -f yt:https://www.youtube.com/watch?v=VIDEO_ID "Create a detailed outline of the topics covered"

# Compare multiple videos
llm -f yt:https://www.youtube.com/watch?v=VIDEO_ID_1 \
    -f yt:https://www.youtube.com/watch?v=VIDEO_ID_2 \
    "Compare these two videos and highlight the differences"
```

**What's included in YouTube fragments:**
- Video title
- Channel/uploader information
- Publication date
- Video description
- Full transcript text (converted to plaintext)

**Note**: The plugin uses yt-dlp for metadata extraction and youtube-transcript-api for transcript retrieval. Auto-generated captions are used if manual transcripts aren't available.

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
```

**Fabric Templates**

The llm-templates-fabric plugin provides access to [Fabric patterns](https://github.com/danielmiessler/Fabric/tree/main/data/patterns):

```bash
# Summarize content
llm -f site:https://example.com/article -t fabric:summarize

# Explain code using Fabric's explain_code pattern
llm -f github:TheR1D/shell_gpt -t fabric:explain_code

# Analyze email headers for phishing detection
llm -f email_headers.txt -t fabric:analyze_email_headers

# Analyze a threat report
llm -f https://www.volexity.com/blog/2025/04/22/phishing-for-codes-russian-threat-actors-target-microsoft-365-oauth-workflows/ \
    -t fabric:analyze_threat_report

# Review code architecture
llm -f github:user/repo -t fabric:review_code

# Create STRIDE threat model
llm -f github:user/secure-app -t fabric:create_stride_threat_model

# Analyze malware samples and extract IOCs
llm -f malware_report.txt -t fabric:analyze_malware

# Structure incident/breach analysis
llm -f breach_article.md -t fabric:analyze_incident

# Generate network threat landscape from port scan
nmap -sV scanme.nmap.org | llm -t fabric:create_network_threat_landscape
```

**Suggested Fabric Patterns**:

- [`fabric:explain_code`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/explain_code/system.md) - Explains code, security tool output, configuration
- [`fabric:analyze_email_headers`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/analyze_email_headers/system.md) - Analyze phishing/spam emails (SPF, DKIM, DMARC analysis)
- [`fabric:analyze_threat_report`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/analyze_threat_report/system.md) - Extracts insights from cybersecurity reports
- [`fabric:create_network_threat_landscape`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/create_network_threat_landscape/system.md) - Generate threat assessment from port scans and network services
- [`fabric:review_code`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/review_code/system.md) - Analyzes code architecture and design
- [`fabric:review_design`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/review_design/system.md) - Analyzes system and software design architecture
- [`fabric:create_stride_threat_model`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/create_stride_threat_model/system.md) - STRIDE threat modeling
- [`fabric:write_nuclei_template_rule`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/write_nuclei_template_rule/system.md) - Create Nuclei vulnerability detection templates
- [`fabric:write_semgrep_rule`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/write_semgrep_rule/system.md) - Generates Semgrep security rules
- [`fabric:create_sigma_rules`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/create_sigma_rules/system.md) - Generate Sigma SIEM detection rules from TTPs
- [`fabric:analyze_malware`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/analyze_malware/system.md) - Extract IOCs and MITRE ATT&CK techniques from malware analysis
- [`fabric:analyze_incident`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/analyze_incident/system.md) - Structure breach/incident analysis with attack types and remediation
- [`fabric:analyze_threat_report_cmds`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/analyze_threat_report_cmds/system.md) - Extract penetration testing commands from security materials
- [`fabric:create_report_finding`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/create_report_finding/system.md) - Structure security findings for professional reports
- [`fabric:analyze_risk`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/analyze_risk/system.md) - Conduct vendor risk assessments and recommend security controls
- [`fabric:analyze_terraform_plan`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/analyze_terraform_plan/system.md) - Evaluate infrastructure as code for security risks and compliance
- [`fabric:summarize`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/summarize/system.md) - Creates concise summaries
- [`fabric:create_threat_scenarios`](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/create_threat_scenarios/system.md) - Identifies likely attack methods for any system by providing a narrative-based threat model, balancing risk and opportunity.

For a complete list of available patterns, see the [Fabric Pattern Explanations](https://github.com/danielmiessler/Fabric/blob/main/data/patterns/pattern_explanations.md).

### Code Generation

Generate clean, executable code without markdown formatting using the `llm code` command (a convenient shorthand for `llm -t code`).

The `code` template outputs **pure code without explanations or markdown blocks**, making it perfect for piping to files or direct execution. It infers the scripting/programming language from context, but it is better explicitly state it.

**Examples:**

```bash
# Generate bash script
llm code "Bash script to backup directory with timestamp" | tee backup.sh

# Pass existing code and modify it
cat fizz_buzz.py | llm code "Generate comments for each line of my code" | sponge fizz_buzz.py

# You can iterate on generated code by continuing the last conversation
llm code "find files larger than 100MB as a bash script" | tee script.sh
llm code -c "and smaller than 500MB; add comments" | tee script.sh

# Direct execution (use with caution!)
llm code "one-liner to find files larger than 100MB in Bash" | bash

# Generate Python function
llm code "solve classic fizz buzz problem using Python" | tee fizz_buzz.py

# Generate SQL query (prints to stdout)
llm code "SQL select users who registered this month"

# Generate configuration file
llm code "nginx config for reverse proxy on port 3000" | tee nginx.conf

# Generate Dockerfile
llm code "dockerfile for nodejs app with nginx" | tee Dockerfile

# Generate regex pattern
llm code "regex to match email addresses"
```

**💡 Tip**: Examples use `| tee filename` instead of `> filename` to provide visual feedback - you'll see the generated code in your terminal while it's also being saved to the file. This helps verify the output before using it.

**Process Substitution (Execute Without Saving)**

Use Bash/Zsh process substitution `<(command)` to execute generated code as a temporary file:

```bash
# Execute Python code directly without creating files
python <(llm code "Python function to calculate fibonacci, then print first 10 numbers")
```

**How it works**: The `<(command)` syntax creates a temporary file descriptor that Python or any other program reads as if it were a regular file, then automatically cleans up when done.

**⚠️ Security Warning**: Only use with trusted prompts. Generated code executes immediately with your user permissions. Review output first for sensitive operations:

```bash
# Safer: Review before executing
llm code "Python script to delete old files" | tee cleanup.py
# Review cleanup.py, then: python cleanup.py
```

### Tools

LLM supports tools that AI models can call during conversations.

**Sandboxed Shell Execution**

**💡 Important**: The `sandboxed_shell` tool is **built into the assistant template** by default! You can ask AI to run commands naturally without specifying `-T sandboxed_shell` every time.

Execute shell commands safely in an isolated environment using bubblewrap (bwrap):

```bash
# ✅ Ask AI naturally - sandboxed_shell tool is automatically available
llm "Check if docker is installed and show version"
llm chat
# > Can you list the files in /root?
# > Which kernel am I using?

# ⚠️ Explicit tool invocation (useful for non-assistant templates)
command llm -T sandboxed_shell "Run this command safely: cat /etc/passwd"

# Show tool execution details with --td flag
llm "Check if docker is installed" --td

# Manually approve each tool execution with --ta flag
llm "Check if docker is installed" --ta

# Combine both to see details AND approve
llm "Check if docker is installed" --td --ta
```

The `--td` flag shows full details of tool executions.
The `--ta` flag requires manual approval before each tool execution.

**Security Benefits:**

- **Isolation**: Commands run in a restricted environment using Linux namespaces
- **Read-only root**: System directories are mounted read-only
- **No network access**: Sandboxed commands cannot access the network by default

**Note**: Requires bubblewrap (already installed by setup script).

**File Manipulation with llm-tools-patch**

The llm-tools-patch plugin provides AI agents with direct file system access for reading, writing, and editing files. This enables AI to autonomously manage files during conversations.

**⚠️ IMPORTANT SECURITY CONSIDERATIONS:**

- This plugin grants **direct file system access** to AI agents
- Always use `--ta` flag to manually approve each file operation
- Review operations carefully before approving
- Works within the current working directory by default
- Use `--chain-limit 0` to allow unlimited consecutive tool invocations when needed

**Available Operations:**

1. **patch_read** - Read complete file contents
2. **patch_write** - Create or overwrite files
3. **patch_edit** - Perform single string replacement
4. **patch_multi_edit** - Execute multiple replacements sequentially
5. **patch_info** - Access file metadata (size, permissions, modification time)

**Usage Examples:**

```bash
# Read a file (with manual approval)
llm -T Patch "Read the contents of config.yaml" --ta

# Create a new file
llm -T Patch "Create a file hello.py with a hello world program" --ta

# Edit existing file (single replacement)
llm -T Patch "In config.yaml, replace 'debug: false' with 'debug: true'" --ta

# Multiple edits in one operation
llm -T Patch "In script.sh, replace all instances of 'echo' with 'printf' and add error handling" --ta --chain-limit 0

# Get file information
llm -T Patch "Show me the file info for README.md" --ta

# Interactive mode with file access
llm chat -T Patch --ta
# > Read the package.json file
# > Update the version to 2.0.0
# > Create a new test file with basic scaffolding
```

**Best Practices:**

- **Always use `--ta`** (tool approval) for file operations to review changes before they're applied
- **Backup important files** before running multi-step edit operations
- **Use specific file paths** to avoid ambiguity
- **Test with `--td`** flag first to see what operations will be performed without executing
- **Be explicit** in your prompts about what changes you want

**Safety Example - Dry Run:**

```bash
# See what the AI plans to do without executing
llm -T Patch "Refactor index.js to use ES6 imports" --td --ta

# The --td flag shows tool execution details
# The --ta flag requires your approval before any changes
```

**Integration with Other Workflows:**

```bash
# Combine with context tool - fix errors automatically
llm -T Patch "Read the context. The last command failed. Fix the error in the script." --ta

# Chain with code generation
llm code "python script for file backup" | tee backup.py
llm -T Patch "Add error handling to backup.py" --ta

# Use with sandboxed_shell for complete automation
llm -T Patch "Check if config.yaml exists, if not create it with default values" --ta
```

**SQLite Database Queries**

Query SQLite databases using natural language:

```bash
# Download a sample database
wget https://www.timestored.com/data/sample/chinook.db

# Query with natural language (--td shows tool calls, in this case DB queries)
llm -T 'SQLite("chinook.db")' "Count rows in the most interesting looking table" --td

# Approve database queries before execution (--ta for manual approval)
llm -T 'SQLite("chinook.db")' "Count rows in the most interesting looking table" --ta

# Interactive chat mode with database access (add --td to show DB queries)
llm chat -T 'SQLite("chinook.db")'
# > Show me the three most interesting looking tables
# > What are the top 5 best-selling artists?
```

The `--td` flag shows full details of tool executions.
The `--ta` flag requires manual approval before each tool execution.

**JSON Processing with llm-jq**

Generate and execute jq programs using natural language:

```bash
# Parse JSON structures
echo '{"users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]}' | \
    llm jq 'extract names and ages'
    
# Process JSON from an API
curl -s https://api.github.com/repos/simonw/datasette/issues | \
    llm jq 'count by user.login, top 3'

# Options:
# -s/--silent: Hide the generated jq program
# -o/--output: Show only the jq program (don't execute)
# -v/--verbose: Display the AI prompt and response
```

**Controlling Tool Execution with Chain Limits**

When AI models use tools, they can call multiple tools in sequence to accomplish complex tasks. The `--chain-limit` (or `--cl`) parameter controls how many consecutive tool calls are allowed in a single prompt, preventing infinite loops while enabling multi-step reasoning.

**Default Behavior:**

- Standard llm default: 5 tool calls
- This setup's default: **15 tool calls** (configured in shell wrapper)
- Set to 0 for unlimited tool calls

```bash
# Use the default chain limit (15 in this setup)
llm "Check if docker is installed, then show all running containers"

# Allow more tool calls for complex multi-step tasks
llm --cl 30 "Analyze system, check disk space, find large files, and suggest cleanup"

# Limit tool calls to prevent excessive API usage
llm --cl 3 "Simple task with minimal tool usage"

# Unlimited tool calls (use with caution!)
llm --cl 0 "Complex task requiring many sequential operations"

# Works in chat mode too
llm chat --cl 20
```

**When to adjust:**

- **Increase** (`--cl 20` or higher): Complex multi-step tasks, extensive data analysis, or when you see "Chain limit reached" errors
- **Decrease** (`--cl 3-5`): Simple tasks where you want to minimize API calls and costs
- **Unlimited** (`--cl 0`): Only when necessary, as it can lead to excessive API usage if the model gets stuck in loops

### Context System Usage

**💡 Important**: The `context` tool is **built into the assistant template** by default! You can ask about your terminal history naturally without typing `-T context` or `--tool context` every time.

Query your terminal history to get context-aware AI assistance.
Make sure to include wording that lets the LLM know that you want it to query the history as context.

```bash
# ✅ Ask AI naturally - context tool is automatically available
llm "what was the error in my last command?"
llm chat
# > Summarize what I did in this session
# > Read the context. How do I fix the compilation error?

# ⚠️ Explicit tool invocation (useful for one-shot queries or non-assistant templates)
command llm -T context "summarize what I did in this session"

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
- **Learning workflows**: Execute tutorials/commands while asking AI to explain what's happening

**Note**: Both terminals read from the same `.cast` file, so the side terminal sees all commands and outputs from the work terminal as they happen.

The context system automatically captures:

- Commands you run
- Complete command output
- Error messages and stack traces

This allows AI models to provide context-aware debugging and assistance based on your actual terminal activity.

### RAG (Document Querying)

Query your documents, codebases, and knowledge bases using AI with AIChat's RAG (Retrieval-Augmented Generation) functionality. The system uses a built-in vector database and automatically syncs with your Azure OpenAI configuration.

`llm rag` is a wrapper that executes `aichat --rag`.

**For detailed documentation**, see the [AIChat RAG Guide](https://github.com/sigoden/aichat/wiki/RAG-Guide).

**Quick Start:**

```bash
# Create/open a RAG collection
llm rag mydocs

# 2. Add documents interactively
> Set chunk size: 2000
> Set chunk overlay: 200
> Add documents: https://github.com/sigoden/aichat/wiki/**

# Ask questions about your documents
How do I configure the RAG?
What are the configuration files?

# Exit (Ctrl+D) and rebuild if source changed or you add more docs
llm rag mydocs --rebuild-rag

# List all RAG collections
aichat --list-rags

# View RAG collection info
aichat --rag projectdocs --info

# Launch web playground and query a RAG in browser
aichat --serve
xdg-open http://127.0.0.1:8000/playground
```

**Interactive RAG Commands** (in REPL):

Within the aichat interactive session:

```bash
aichat
.rag mydocs              # Create/switch to RAG collection
.edit rag-docs           # Add/edit documents in current RAG
.rebuild rag             # Rebuild RAG index after document changes
.sources rag             # Show citation sources from last query
.info rag                # Show RAG configuration details
.exit rag                # Exit RAG mode
.help                    # Show all available REPL commands
.set                     # View/change RAG settings
```

**Document Source Types:**

AIChat can build RAG knowledge bases from a variety of document sources:

| Source | Example |
|--------|---------|
| Files | `/tmp/dir1/file1;/tmp/dir1/file2` |
| Directory | `/tmp/dir1/` |
| Directory (extensions) | `/tmp/dir2/**/*.{md,txt}` |
| Url | `https://sigoden.github.io/mynotes/tools/linux.html` |
| RecursiveUrl (websites) | `https://sigoden.github.io/mynotes/tools/**` |
| Git Repository (remote) | `git:https://github.com/user/repo` |
| Git Repository (local) | `git:/path/to/local/repo` |
| Git Subdirectory | `git:https://github.com/user/repo/tree/main/src` |

**Supported Document Types:**

The system automatically processes various file types:

- **Text files**: .txt, .md, .rst, .json, .yaml, .py, .js, etc.
- **PDF files**: Automatically extracted with pdftotext (requires poppler-utils)
- **DOCX files**: Automatically converted with pandoc
- **Git repositories**: Full repository context via gitingest
- **Web URLs**: HTTP/HTTPS URLs are fetched and indexed
- **Directories**: Recursively index all supported files

**Adding Git Repositories to an existing RAG:**

```bash
# Enter the name of the RAG collection for your project
llm rag myproject

# In the REPL, add documents with .edit rag-docs
.edit rag-docs
```

Your editor opens - add sources (one per line):

```
# Remote GitHub repositories (use git: prefix!)
git:https://github.com/sigoden/aichat

# Local repositories
git:/home/user/projects/myapp
git:./relative-path-to-repo
```

Save and exit - aichat will process all sources:

```bash
# Now query your repositories
Explain the authentication system in this codebase
What are the main components?
How does the RAG implementation work?
```

**💡 Why the `git:` prefix is required:**

- **Without prefix**: `https://github.com/user/repo` → Fetched as a web page (HTML)
- **With prefix**: `git:https://github.com/user/repo` → Processed as a git repository (source code)

The `git:` prefix explicitly triggers the gitingest document loader, which:

- Extracts source code from the repository
- Respects .gitignore files
- Provides clean, formatted code for the RAG index

**Tips:**

- Use descriptive names for RAG collections (e.g., `aws-docs`, `company-policies`, `project-api`)
- Rebuild the index with `--rebuild-rag` after significant document changes
- RAG collections are stored in `~/.local/share/aichat/rags/`
- Use `.set` in REPL to adjust settings like `rag_top_k` (number of results)

### Integration with Other Tools

**Repository Analysis with gitingest and yek**

Convert Git repositories to LLM-friendly text. Both tools serve the same purpose but with different performance characteristics:

- **gitingest**: Python-based, feature-rich, good compatibility
- **yek**: Rust-based, extremely fast (230x faster), parallel processing

```bash
# Using gitingest (Python-based)
gitingest https://github.com/user/repo
gitingest /path/to/local/repo

# Using yek (Rust-based, much faster)
yek https://github.com/user/repo
yek /path/to/local/repo

# Both tools output to stdout by default
yek https://github.com/user/repo > repo-context.txt

# Combine with LLM for analysis
yek https://github.com/user/repo | \
    llm "What is the main purpose of this codebase?"

# Direct analysis without saving
yek /path/to/local/repo | llm "Review architecture and suggest improvements"

# Show more parameters
gitingest --help
yek --help
```

**Performance comparison**: For large repositories with many files, yek is significantly faster due to Rust's performance and parallel processing. Choose based on your needs:
- Use **yek** for large repos or when speed matters
- Use **gitingest** for maximum compatibility or specific features

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

For complete documentation of parameters, see the [files-to-prompt repository](https://github.com/c0ffee0wl/files-to-prompt/blob/main/README.md).

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

### LLM Functions (Optional)

**Note**: This is an **optional** feature. The installation script prepares your environment for llm-functions by installing `argc` and the `llm-tools-llm-functions` bridge plugin, but you must install llm-functions separately if you want to use it.

[llm-functions](https://github.com/sigoden/llm-functions/) is a framework that allows you to build custom LLM tools and agents using Bash, JavaScript, and Python. When installed, these tools become available to the `llm` command through the bridge plugin.

**Installing llm-functions:**

```bash
# Clone the llm-functions repository
git clone https://github.com/sigoden/llm-functions.git
cd llm-functions

# Create tools.txt to specify which tools to enable (one per line)
cat > tools.txt <<EOF
get_current_weather.sh
execute_command.sh
# execute_py_code.py  # Lines starting with # are disabled
EOF

# Build function declarations and binaries
argc build

# Verify environment is ready (checks dependencies, env vars, etc.)
argc check

# Link to AIChat 
# Option 1: Symlink to AIChat's functions directory
ln -s "$(pwd)" "$(aichat --info | sed -n 's/^functions_dir\s\+//p')"

# Option 2: Use environment variable
export AICHAT_FUNCTIONS_DIR="$(pwd)"
```

**Creating Custom Tools:**

llm-functions uses a simple comment-based syntax to define tools:

**Bash Example** (`tools/get_current_weather.sh`):

```bash
#!/usr/bin/env bash
set -e

# @describe Get the current weather in a given location.
# @option --location! The city and optionally the state or country, e.g., "London", "San Francisco, CA".

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    curl -fsSL "https://wttr.in/$(echo "$argc_location" | sed 's/ /+/g')?format=4&M" \
    >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
```

**Python Example:**

See the [`execute_py_code.py` example](https://github.com/sigoden/llm-functions/blob/main/tools/execute_py_code.py) in the llm-functions repository. This tool uses Python's `ast` module to execute code and capture output, demonstrating how to define function parameters via docstrings.

**JavaScript Example:**

See the [`execute_js_code.js` example](https://github.com/sigoden/llm-functions/blob/main/tools/execute_js_code.js) in the llm-functions repository. This tool demonstrates JavaScript/Node.js code execution with output capture, using JSDoc comments to define parameters.

**Tool Discovery in llm:**

The llm-tools-llm-functions plugin automatically discovers tools by reading the `functions.json` file generated by `argc build`. Tools are registered with llm's function-calling system and become available to AI models.

```bash
# Use tools with llm
llm -T get_current_weather "What's the weather in Berlin?"

# In interactive mode
llm chat -T get_current_weather
# > What's the weather in Berlin?
```

**Integration with AIChat:**

llm-functions was originally designed for [AIChat](https://github.com/sigoden/aichat) and is also fully supported there:

```bash
# Use tools with AIChat
aichat --role %functions% what is the weather in Paris?
```

**Why llm-functions is Optional:**

- Requires manual setup and tool development
- Best for users who need custom tool integration
- Not everyone needs to build custom function-calling tools

**Use Cases:**

- **System Integration**: Call system commands, APIs, or services from AI conversations
- **Custom Workflows**: Build domain-specific tools for your projects
- **Automation**: Create tools that interact with databases, cloud services, or local applications

For complete documentation, see the [llm-functions repository](https://github.com/sigoden/llm-functions/).

### Managing Models

**List Available Models**

```bash
# List all models (shows Azure, Gemini, and other configured models)
llm models

# Find default model
llm models | grep -i default

# List Azure models
llm models | grep azure

# List Gemini models
llm models | grep gemini

# Get detailed Gemini model info
llm gemini models
```

**Set Default Model**

```bash
# Set default model for all commands
llm models default azure/gpt-4.1-mini

# Alternative: using environment variable
export LLM_MODEL=azure/gpt-4.1
llm "Your prompt"  # Uses gpt-4.1
```

**Use Specific Models**

```bash
# Override default with -m flag
llm "Ten names for cheesecakes" -m azure/gpt-4.1-mini

# Use different models for different tasks
llm -m azure/gpt-4.1 "Enterprise compliance analysis"
llm -m gemini-2.5-flash "Personal coding question"
llm -m gemini-2.5-flash "Describe this image" -a photo.jpg
```

**Azure OpenAI Models**

The following Azure models are configured (examples):

- `azure/gpt-4.1` - GPT-4.1 (most capable)
- `azure/gpt-4.1-mini` - GPT-4.1 Mini (balanced, **default**)
- `azure/gpt-4.1-nano` - GPT-4.1 Nano (fast, cost-effective)
- `azure/o4-mini` - O4 Mini (advanced reasoning)
- `azure/gpt-5`, `azure/gpt-5-mini`, `azure/gpt-5-nano` - GPT-5 models

**Default Model Recommendation:**

This setup uses `azure/gpt-4.1-mini` as the default for its balance of performance and cost-effectiveness. For more complex tasks requiring deeper reasoning (such as extensive code analysis, multi-step problem solving, or nuanced decision making), switch to `azure/gpt-4.1`:

```bash
# Switch to gpt-4.1 for complex tasks
llm models default azure/gpt-4.1

# Or use it for a single query with -m flag
llm -m azure/gpt-4.1 "Complex analysis task..."
```

**Note**: Model IDs shown above are examples from a specific Azure deployment. Your available models depend on your Azure Foundry configuration. Use `llm models` to see your configured models.

### Managing API Keys

**Configure Azure OpenAI Key:**

```bash
# Set Azure key interactively
llm keys set azure

# View configured keys
llm keys

# View key storage path
llm keys path
```

**Configure Gemini Key:**

```bash
# Set Gemini key interactively
llm keys set gemini

# Verify Gemini key is working
llm -m gemini-2.5-flash "test prompt"
```

**Get API Keys:**

- **Azure OpenAI**: Obtained from your Azure Foundry portal/deployment
- **Gemini**: Free from [Google AI Studio](https://ai.google.dev/gemini-api/docs/api-key) (no credit card required)
- **OpenAI**: From [OpenAI platform](https://platform.openai.com/api-keys) (requires payment)
- **Anthropic**: From [Anthropic console](https://console.anthropic.com/) (requires payment)

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
# Bypasses the wrapper (no automatic template)
command llm "Your question"

# Useful for debugging or when you want exact control
command llm -t assistant "Your question"  # You must specify -t explicitly
```

**When to use `command llm`:**

- Testing without automatic template modifications
- Scripts that need exact `llm` behavior without wrapper modifications
- When you want complete manual control over all parameters

### When to Use `-t`

**You ONLY need `-t` when you want a DIFFERENT template:**

```bash
# ❌ Unnecessary (assistant is already default)
llm -t assistant "What is Docker?"

# ✅ Correct (just omit -t)
llm "What is Docker?"

# ✅ Use -t for non-default templates
llm -t fabric:summarize < report.txt
llm -t fabric:analyze_threat_report -a report.pdf
```

### Key Benefits

**What does the assistant template do?** See the [assistant.yaml source](llm-template/assistant.yaml) - it configures the AI with:

- Security/IT/Linux expertise (20 years experience)
- Cybersecurity focus (ethical hacking, forensics, incident response)
- Kali Linux/Ubuntu/Debian environment awareness
- **Integrated `context` tool** for reading terminal history (automatically available!)
- **Integrated `sandboxed_shell` tool** for safe command execution (automatically available!)

### Context Tool Integration

The assistant template includes the **`context` and `sandboxed_shell` tools by default**, which means AI models can automatically read your terminal history and execute shell commands safely without you needing to explicitly specify `--tool context` or `-T sandboxed_shell` every time!

**✅ When these tools are automatically available:**

```bash
# Just ask naturally - the AI can use the context tool
llm "what was the error in my last command?"

# Or ask the AI to run commands via sandboxed_shell
llm "check if docker is installed and show version"

llm chat
# > Read the context. What did I just run?
# > Can you explain the output from my last command?
# > List the files in /root
```

**⚠️ When you need to use tools explicitly:**

```bash
# When using a different template
llm -t fabric:summarize --tool context "what happened?"
llm -t fabric:summarize -T sandboxed_shell "check docker"

# When bypassing the shell wrapper
command llm --tool context "what went wrong?"
command llm -T sandboxed_shell "run this safely"
```

## Session Recording & Context System

This setup includes an **automatic terminal session recording system** that captures your command history and allows AI models to query it for context-aware assistance.

### How It Works

1. **Automatic Recording**: Every interactive shell session is automatically recorded using asciinema
   - Recording starts transparently when you open a terminal
   - Each tmux/screen pane gets its own independent recording

2. **Context Extraction**: The `context` command parses asciinema recordings to show command history
   - Shows commands with their complete output
   - Supports showing last N commands or entire session

3. **AI Integration**: The `llm-tools-context` plugin exposes terminal history as a tool
   - The `context` tool is **built into the assistant template by default**
   - AI models can automatically query your recent command history during conversations
   - Just ask naturally: `llm "what was the error?"` - no need for `--tool context`!
   - Explicit `--tool context` is only needed for non-assistant templates or continuations

### Storage Configuration

On first installation, you'll be prompted to choose where session recordings are stored:

- **Temporary** (default): `/tmp/session_logs/asciinema/` - Cleared on reboot, saves disk space
- **Permanent**: `~/session_logs/asciinema/` - Survives reboots, useful for long-term history

You can change this later by editing the `SESSION_LOG_DIR` export in your `.bashrc` or `.zshrc`.

**Suppressing Session Start Messages:**

To hide the "Session is logged for 'context'..." message on shell startup:

```bash
# Add to your .bashrc or .zshrc before the integration source line
export SESSION_LOG_SILENT=1
```

Useful for cleaner shell startup or automated environments.

## Understanding Azure OpenAI Setup

This installation can configure **either** Azure OpenAI (Azure Foundry) **or** Google Gemini. On first run, you'll be asked which provider you want to use. **You can only use one provider at a time** for AIChat.

If you choose **Azure OpenAI** (default choice for enterprise/workplace use), the setup differs from standard OpenAI API integration.

### Architecture Overview

**Key Differences from Standard OpenAI:**

- Uses Azure-hosted OpenAI models (not direct OpenAI API)
- Model IDs require `azure/` prefix (e.g., `azure/gpt-4.1-mini`, `azure/o4-mini`)
- Requires separate API key (`azure` not `openai`)
- API base URL points to your Azure resource (e.g., `https://your-resource.openai.azure.com`)

### Configuration Files

**LLM Configuration:**

- **Location:** `~/.config/io.datasette.llm/extra-openai-models.yaml`
- **Purpose:** Defines Azure-hosted models for llm CLI tool
- **Format:** YAML file with model definitions

**Example structure:**

```yaml
- model_id: azure/gpt-4.1-mini
  model_name: gpt-4.1-mini
  api_base: https://your-resource.openai.azure.com
  api_key_name: azure
```

**AIChat Configuration:**

- **Location:** `~/.config/aichat/config.yaml`
- **Purpose:** Automatically synced with Azure credentials from llm config by the setup script

### Azure-Specific Limitations

**⚠️ Attachments Not Supported:**
Azure OpenAI models in this setup **do not support** the `-a`/`--attachment` parameter for images, PDFs, or other files, even with `vision: true` configured.

**Error you'll see:**

```
Error code: 400 - {'error': {'message': "Invalid Value: 'file'.
This model does not support file content types.", 'type': 'invalid_request_error'}}
```

**Workarounds:**

1. **For text extraction:** Use fragments instead

   ```bash
   llm -f pdf:document.pdf "summarize the text"
   ```

2. **For visual analysis:** Use non-Azure models

   ```bash
   llm -m gemini-2.5-flash "describe this image" -a image.jpg
   llm -m claude-3-5-sonnet "analyze" -a document.pdf
   ```

### Why Azure OpenAI?

**When Azure is the right choice:**

- **Enterprise/workplace requirements** - Compliance, SLAs, data residency
- **Organizational policies** - Centralized billing, governance
- **Private deployments** - Models hosted in your Azure subscription

**When to consider alternatives:**

- **Personal/hobbyist use** - Free tiers available elsewhere (see Gemini section below)
- **Attachment support needed** - Standard APIs support multimodal better
- **No Azure subscription** - Direct API access simpler

## Alternative: Gemini for Private Use

For **personal projects, learning, and hobbyist use**, Google's **Gemini 2.5 Flash** offers exceptional value with a generous free tier and competitive performance.

**⚠️ Important:** This script configures **either** Azure OpenAI **or** Gemini - you cannot use both simultaneously for AIChat. Choose the provider that best fits your needs:

- **Azure OpenAI**: Enterprise/workplace environments, compliance requirements
- **Gemini**: Personal projects, free tier, hobbyist use

**Get your API key:**

- Visit [Google AI Studio](https://ai.google.dev/gemini-api/docs/api-key)
- Sign up (free, no credit card required)
- Generate an API key from the dashboard

### Switching Providers

To switch from Azure to Gemini or vice versa:

```bash
# Switch to Gemini
./install-llm-tools.sh --gemini

# Switch to Azure
./install-llm-tools.sh --azure
```

The script will backup your existing AIChat configuration and reconfigure for the selected provider.

**Temperature Note:** Gemini supports temperature values in the range `[0, 2)`, unlike most models that use `[0, 1]`. Be mindful when setting temperature values.

## Configuration

### Configuration Files

- `~/.config/io.datasette.llm/` - LLM configuration directory
  - `extra-openai-models.yaml` - Azure OpenAI model definitions
  - `templates/assistant.yaml` - Custom [assistant template](llm-template/assistant.yaml) with security/IT expertise (cybersecurity focus)
  - `templates/code.yaml` - [Code-only generation template](llm-template/code.yaml) (no markdown, no explanations)
  - `default_model.txt` - Currently selected default model
  - API keys stored securely via llm's key management

- `~/.config/llm-tools/` - Additional tool configuration
  - `asciinema-commit` - Tracks asciinema version for update detection

- `$SESSION_LOG_DIR/` - Session recording storage
  - Default: `/tmp/session_logs/asciinema/` (temporary) or `~/session_logs/asciinema/` (permanent)
  - Contains `.cast` files with terminal session recordings
  - Configured via `SESSION_LOG_DIR` environment variable in your shell RC file

- `~/.config/aichat/` - AIChat configuration
  - `config.yaml` - Auto-configured with Azure OpenAI settings, RAG configuration, and document loaders
  
- `~/.local/share/aichat/rags/` - RAG collections and vector databases
  - Each subdirectory is a named RAG collection
  - Contains vector embeddings and indexed documents

### Shell Integration Files

Located in the `integration/` subdirectory:

- [`integration/llm-integration.bash`](integration/llm-integration.bash) - Bash integration (Ctrl+N keybinding)
- [`integration/llm-integration.zsh`](integration/llm-integration.zsh) - Zsh integration (Ctrl+N keybinding)
- [`integration/llm-common.sh`](integration/llm-common.sh) - Shared configuration (llm wrapper function, auto-recording)

These are automatically sourced from your `.bashrc` or `.zshrc`.

## Troubleshooting

### Update fails

**"fatal: Not possible to fast-forward, aborting"**

This error occurs when your local git branch has diverged from the remote (both have conflicting changes). This typically happens if you made local edits to files that were also updated remotely.

**Solutions:**

```bash
# Option 1: Discard local changes and match remote (recommended)
cd llm-linux-setup
git reset --hard origin/main
./install-llm-tools.sh

# Option 2: Try to reapply your local commits on top of remote changes
git pull --rebase

# Option 3: Nuclear option - delete and re-clone
rm -rf llm-linux-setup
git clone https://github.com/c0ffee0wl/llm-linux-setup
cd llm-linux-setup
./install-llm-tools.sh
```

### Command completion not working

**For Ctrl+N (AI completion)**:

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

**For Tab completion (Zsh only)**:

1. Verify you're using Zsh: `echo $SHELL` (should show `/bin/zsh` or similar)

2. Clear completion cache and restart shell:

   ```bash
   rm -f ~/.zcompdump*
   exec zsh
   ```

3. Verify the plugin is in fpath:

   ```bash
   echo $fpath | grep llm-zsh-plugin
   ```

4. Test tab completion:

   ```bash
   llm <TAB>  # Should show: chat, code, rag, models, etc.
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

### Rust version issues

**Problem**: `cargo install` fails with errors about minimum Rust version

**Solution**: The script automatically detects and offers to upgrade Rust to 1.85+ via rustup. If you declined during installation:

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

## Support

For issues, questions, or suggestions:

- Open an issue: https://github.com/c0ffee0wl/llm-linux-setup/issues

## Related Projects

- [llm-windows-setup](https://github.com/c0ffee0wl/llm-windows-setup) - Windows version

## Credits

### Core Tools & Frameworks

- [Simon Willison](https://github.com/simonw) - Original llm CLI tool and plugins (llm-gemini, llm-anthropic, llm-openrouter, llm-jq, llm-tools-sqlite, llm-tools-quickjs, llm-fragments-github, llm-cmd)
- [c0ffee0wl](https://github.com/c0ffee0wl) - llm fork with markdown markup enhancements
- [sigoden](https://github.com/sigoden) - AIChat all-in-one LLM CLI with RAG, argc Bash CLI framework, and llm-functions framework
- [Anthropic](https://www.anthropic.com/) - Claude Code agentic coding CLI
- [SST](https://sst.dev/) - OpenCode agentic coding CLI
- [Astral](https://astral.sh/) - uv Python package manager
- [Rust Foundation](https://foundation.rust-lang.org/) - Rust programming language and Cargo
- [Node.js Foundation](https://nodejs.org/) - Node.js JavaScript runtime

### LLM Plugins & Extensions

- [Daniel Turkel](https://github.com/daturkel) - llm-fragments-pdf, llm-fragments-site-text
- [ Ryan Patterson ](https://github.com/CGamesPlay) - llm-cmd-comp plugin
- [Dan Mackinlay](https://github.com/danmackinlay) - files-to-prompt (fork)
- [Damon McMinn](https://github.com/damonmcminn) - llm-templates-fabric (fork)
- [Daniel Miessler](https://github.com/danielmiessler) - Original Fabric prompt patterns

### Additional Tools

- [Bubblewrap Project](https://github.com/containers/bubblewrap) - Sandboxing tool for unprivileged containers
- [stedolan/jq](https://github.com/stedolan/jq) - Command-line JSON processor
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
