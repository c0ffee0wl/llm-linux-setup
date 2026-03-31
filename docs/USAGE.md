# Usage Guide

Organized by agentic level. Each level builds on the previous.

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->

- [Level 0: LLM CLI](#level-0-llm-cli)
  - [Basic Prompts](#basic-prompts)
  - [Interactive Chat](#interactive-chat)
  - [Markdown Rendering](#markdown-rendering)
  - [Attachments & Multi-modal](#attachments--multi-modal)
  - [Fragments](#fragments)
  - [Templates](#templates)
  - [RAG (Document Querying)](#rag-document-querying)
  - [Code Generation](#code-generation)
  - [Git & Sorting](#git--sorting)
  - [Image Generation (imagemage)](#image-generation-imagemage)
  - [JSON Processing](#json-processing)
  - [Tools](#tools)
- [Level 1: Enhanced Shell](#level-1-enhanced-shell)
  - [Smart Wrapper](#smart-wrapper)
  - [Understanding Command Output (wut)](#understanding-command-output-wut)
  - [Command Completion (Ctrl+N)](#command-completion-ctrln)
  - [Session Recording & Context](#session-recording--context)
  - [Context Tool](#context-tool)
  - [Side-by-Side Workflow](#side-by-side-workflow)
- [Level 2: Inline Assistant (@)](#level-2-inline-assistant-)
  - [The @ Command](#the--command)
  - [How It Works](#how-it-works)
  - [Zsh Smart @ Widget](#zsh-smart--widget)
  - [Fragment Attachments](#fragment-attachments)
  - [Daemon Management](#daemon-management)
- [Level 3: Terminal AI (llm-assistant)](#level-3-terminal-ai-llm-assistant)
  - [Getting Started](#getting-started)
  - [Command Execution](#command-execution)
  - [Watch Mode](#watch-mode)
  - [Knowledge Bases](#knowledge-bases)
  - [Memory System (AGENTS.md)](#memory-system-agentsmd)
  - [Conversation Management](#conversation-management)
  - [MCP Servers](#mcp-servers)
  - [Pentest Findings](#pentest-findings)
  - [Voice Input & Speech Output](#voice-input--speech-output)
  - [Screenshot Capture](#screenshot-capture)
  - [Web Companion](#web-companion)
  - [Input Modes](#input-modes)
  - [All Slash Commands](#all-slash-commands)
  - [Micro Text Editor Integration](#micro-text-editor-integration)
- [Level 4: Desktop AI](#level-4-desktop-ai)
  - [GTK Popup Assistant](#gtk-popup-assistant)
  - [Espanso Text Expansion](#espanso-text-expansion)
  - [Ulauncher Extension](#ulauncher-extension)
  - [Speech-to-Text](#speech-to-text)
- [Level 5: Agentic Coding](#level-5-agentic-coding)
  - [Claude Code](#claude-code)
  - [Claude Code Router](#claude-code-router)
  - [Codex CLI](#codex-cli)
- [Additional Tools](#additional-tools)
  - [LLM Functions (Optional)](#llm-functions-optional)
  - [Repository Analysis](#repository-analysis)
  - [Piping Workflows](#piping-workflows)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Level 0: LLM CLI

Core `llm` command-line tool. Works immediately after installation.

### Basic Prompts

```bash
llm "Was ist das meistverbreite Betriebssystem für Pentester?"
llm -c "Und für Forensiker?"             # Continue last conversation

llm "Tell me about my OS: $(uname -a)"   # Include system info
llm "Was ist Docker?" | pbcopy           # Pipe to clipboard
```

### Interactive Chat

```bash
llm chat                  # New conversation
llm chat -c               # Continue last conversation

# Pipe help text, then chat about it
docker --help | llm
llm chat -c --md
# > What's the difference between 'run' and 'exec'?
```

### Markdown Rendering

```bash
llm "Explain Docker" --md                 # Rich terminal rendering
llm "Top 5 Linux commands" --markdown     # Full flag name
```

### Attachments & Multi-modal

```bash
llm "Beschreibe" -a https://example.com/poster.pdf
llm "Extrahiere den Text" -a image1.jpg -a image2.jpg
cat poster.pdf | llm 'describe image' -a -    # Stdin attachment
```

**Azure limitation**: Image attachments work, PDF attachments do not. Use `pdf:` fragments instead.

### Fragments

Load context from files, URLs, and repositories.

```bash
# Local files and URLs
llm -f /path/to/file.py "Explain this code"
llm -f https://example.com/article "Summarize"
llm -f site:https://example.com/blog "Extract key points"  # Smart extraction

# GitHub repositories and issues
llm -f github:user/repo "Analyze this codebase"
llm -f issue:simonw/llm/123 "Summarize this issue"
llm -f pr:simonw/llm/456 "Review this PR"

# PDFs, YouTube, arXiv
llm -f pdf:document.pdf "Summarize"
llm -f yt:https://youtube.com/watch?v=VIDEO_ID "Key points?"
llm -f arxiv:2310.06825 "Summarize the findings"

# Directories
llm -f dir:./src "Analyze the structure"

# Combine fragments
llm -f github:user/repo -f requirements.txt "Review dependencies"
```

**URL types**: `-f https://url` fetches raw content (APIs, JSON). `-f site:https://url` intelligently extracts article text.

### Templates

The assistant template is **auto-applied** by default. Use `-t` only for different templates.

```bash
# Default (no -t needed)
llm "Your question"

# Fabric patterns
llm -t fabric:summarize -f site:https://example.com/article
llm -t fabric:analyze_threat_report -a report.pdf
llm -t fabric:create_stride_threat_model -f github:user/app
llm -t fabric:explain_code -f github:user/repo

# Custom templates
cat > ~/.config/io.datasette.llm/templates/mytemplate.yaml <<'EOF'
system: You are a PostgreSQL expert. Always provide SQL examples.
EOF
llm -t mytemplate "How do I create a composite index?"

# List templates
llm templates
```

### RAG (Document Querying)

Hybrid semantic + keyword search (ChromaDB + BM25).

```bash
llm rag add mydocs /path/to/files          # Add documents
llm rag add mycode git:https://github.com/user/repo  # Add repo
llm rag search mydocs "how does auth work?" # Search
llm -T 'rag("mydocs")' "Explain the auth"  # Use as tool
llm rag list                                # List collections
llm rag rebuild mydocs                      # Rebuild index
```

Search modes: `hybrid` (default), `vector` (semantic only), `keyword` (BM25 only).

### Code Generation

```bash
llm code "Bash script to backup with timestamp" | tee backup.sh
llm code -c "add error handling" | tee backup.sh   # Iterate
llm code "SQL select users registered this month"   # Stdout

# Advanced: use fragments for context
llm code -f github:simonw/llm-hacker-news \
  "Write a new plugin called llm_video_frames.py..."

# Direct execution (use with caution)
python <(llm code "Fibonacci function, print first 10")
```

### Git & Sorting

```bash
# AI commit messages
llm git-commit                # From staged changes
llm git-commit --tracked      # From all tracked changes

# Semantic sorting
cat names.txt | llm sort --query "Most suitable for a pet monkey?"
llm sort --query "Most technical" --top-k 5 topics.txt

# Text classification
echo "Exciting news!" | llm classify -c positive -c negative -c neutral
```

### Image Generation (imagemage)

Requires Gemini provider and Go 1.22+.

```bash
imagemage generate "watercolor fox in snowy forest"
imagemage generate "banner image" --aspect=16:9 --count=3
imagemage edit photo.png "make it black and white"
imagemage icon "minimalist cloud logo"
```

### JSON Processing

```bash
echo '{"users": [{"name": "Alice"}, {"name": "Bob"}]}' | llm jq 'extract names'
curl -s https://api.github.com/repos/simonw/llm/issues | llm jq 'count by user.login, top 3'
```

### Tools

Tools are AI-callable capabilities. The assistant template includes `context` and `sandboxed_shell` by default.

```bash
# Sandboxed shell (auto-available in assistant template)
llm "Check if docker is installed and show version"
llm "List files in /root" --td        # Show tool details
llm "Check kernel version" --ta       # Require approval

# File manipulation
llm -T Patch "Read config.yaml" --ta
llm -T Patch "Create hello.py with hello world" --ta
llm -T Patch "In config.yaml, change debug to true" --ta

# SQLite
llm -T 'SQLite("chinook.db")' "Top 5 best-selling artists" --td

# Chain limits
llm --cl 30 "Analyze system, find large files, suggest cleanup"
llm --cl 0 "Complex multi-step task"   # Unlimited (use with caution)
```

Default chain limit: 15 (configurable via `--cl`).

---

## Level 1: Enhanced Shell

Shell integration adds automatic templates, keybindings, and session recording. Available after installation (level 2+).

### Smart Wrapper

The `llm` command is wrapped to auto-apply the assistant template and tools.

```bash
# These are equivalent:
llm "Your question"
command llm -t llm --tool context --tool sandboxed_shell "Your question"

# Wrapper skips template when you specify -t, -s, -c, or --cid
llm -t fabric:summarize < report.txt    # Uses fabric template
llm -c "Follow up"                       # Continues (no re-apply)
```

Bypass with `command llm` for raw access.

### Understanding Command Output (wut)

```bash
docker build -t myapp .     # Error occurs
wut                          # AI explains the error
wut "is this a security issue?"  # Specific question

# Follow up
llm chat -c
```

### Command Completion (Ctrl+N)

Type a description or partial command, press Ctrl+N:

```bash
# Type: find pdf files larger than 20MB
# Press: Ctrl+N
# Result: find . -type f -name "*.pdf" -size +20M

# Iterative: type follow-up, press Ctrl+N again
# Type: Aber nur auf demselben Dateisystem.
# Result: find . -type f -name "*.pdf" -size +20M -xdev
```

Also: `llm cmd "Find .sh files below /root"` for non-interactive use.

### Session Recording & Context

Every terminal session is automatically recorded via asciinema. Each tmux pane records independently.

```bash
context                  # Last command output
context 5                # Last 5 commands
context all              # Entire session
context -e               # Export SESSION_LOG_FILE path
```

Storage: `/tmp/session_logs/asciinema/` (default, cleared on reboot) or `~/session_logs/asciinema/` (permanent).

Suppress startup message: `export SESSION_LOG_SILENT=1`

### Context Tool

Built into the assistant template. Just ask:

```bash
llm "What was the error in my last command?"
llm chat
# > Summarize what I did in this session
# > How do I fix the compilation error?
```

### Side-by-Side Workflow

```bash
# Terminal 1: your work
context -e               # Copy the export command

# Terminal 2: AI assistant
export SESSION_LOG_FILE="/tmp/session_logs/asciinema/..."  # Paste
llm "What did the build script just do?"
llm chat                 # Continuous assistance
```

---

## Level 2: Inline Assistant (@)

AI assistant that works in any terminal. Each terminal keeps its own conversation. Backed by the shared daemon.

### The @ Command

```bash
@ What's new about space travel?       # Query
@ Tell me more about Saturn            # Continue conversation
@ /new                                  # Fresh conversation
@ /status                               # Session info
@ /help                                 # Available commands
```

### How It Works

- Each terminal gets its own conversation (tracked by terminal ID)
- Daemon starts automatically on first use
- Idle timeout: 30 minutes
- Ctrl+G applies suggested commands from the AI

### Zsh Smart @ Widget

In Zsh, `@` at line start enters LLM mode:
- Tab completion for `/slash-commands`
- Tab completion for `@fragments` (`@pdf:`, `@yt:`, `@arxiv:`, `@github:`, `@dir:`, `@file:`)
- `@` elsewhere in the line inserts a literal `@`

### Fragment Attachments

```bash
@ @pdf:document.pdf What does this say?
@ @yt:https://youtube.com/watch?v=ID Summarize this
@ @github:user/repo Explain the architecture
```

### Daemon Management

```bash
@ /quit                  # Stop daemon
@ /status                # Check status
# Daemon auto-starts on next @ command
```

---

## Level 3: Terminal AI (llm-assistant)

AI pair programming assistant for Terminator. This is where things get serious: knowledge bases, persistent memory, pentest reports, MCP servers, voice input/output, and a web UI.

### Getting Started

```bash
llm assistant                    # Launch in Terminator
llm assistant azure/gpt-4.1     # With specific model
```

Auto-creates a split Exec terminal for command execution.

### Command Execution

AI suggests commands via `execute_in_terminal` tool:
1. AI proposes a command
2. You approve: `[y/n/e]` (yes/no/edit)
3. Command runs in Exec terminal
4. Output captured for next AI iteration

### Watch Mode

Proactive terminal monitoring with user-defined goals.

```bash
/watch detect security issues            # Enable
/watch spot inefficient commands          # Different goal
/watch monitor logs for errors            # Log watching
/watch off                                # Disable
/watch                                    # Show status
```

Hash-based change detection prevents duplicate alerts. AI focuses on new content only.

### Knowledge Bases

Persistent context files loaded into the system prompt.

```bash
/kb                          # List available KBs
/kb load pentest-checklist   # Load KB
/kb load kb1,kb2,kb3         # Load multiple
/kb unload pentest-checklist # Unload
/kb reload                   # Reload all
```

Location: `~/.config/llm-assistant/kb/` (markdown files).

Auto-load via `~/.config/llm-assistant/assistant-config.yaml`:
```yaml
knowledge_base:
  auto_load:
    - pentest-checklist
    - company-standards
```

### Memory System (AGENTS.md)

Persistent notes across sessions.

```bash
/memory                      # Show loaded memory
/memory global               # Global memory only
/memory local                # Project memory only
/memory reload               # Reload from disk

# Quick notes
# The customer uses OAuth2 for auth
# local Remember: DB migrations need approval
```

Locations: `~/.config/llm-assistant/AGENTS.md` (global), `./AGENTS.md` (project).

### Conversation Management

```bash
/squash                      # Compress context (auto at 80%)
/rewind                      # Interactive picker
/rewind -3                   # Go back 3 turns
/rewind undo                 # Restore last rewind
/copy                        # Copy last response
/copy 5                      # Copy last 5
/copy all                    # Copy entire conversation
/copy raw                    # With markdown preserved
```

### MCP Servers

```bash
/mcp                         # List servers and status
/mcp load microsoft-learn    # Enable server
/mcp unload microsoft-learn  # Disable

# Available servers:
# microsoft-learn  - Microsoft docs search
# aws-knowledge    - AWS docs search
# arxiv            - Paper search/download
# chrome-devtools  - Browser DevTools (7 tools)
```

### Pentest Findings

Capture and manage security findings during assessments.

```bash
/report init acme-webapp en              # New project (English)
/report init client-test de              # German findings
/report "SQL injection in login form"    # Capture finding
/report list                             # List findings
/report severity F001 8                  # Override severity
/report export                           # Word document (pandoc)
/report projects                         # List projects
/report open acme-webapp                 # Switch project
```

AI auto-generates: title, OWASP severity (1-9), description, remediation. Storage: `~/.config/llm-assistant/findings/`.

### Voice Input & Speech Output

```bash
/voice                       # Enable voice (auto-submit)
/voice off                   # Disable
/voice clean                 # Enable transcript cleanup
/voice clean off             # Disable cleanup

/speech                      # Enable TTS (Vertex models only)
/speech off                  # Disable
```

Voice model: Parakeet TDT INT8 (~600MB). TTS: Google Cloud Chirp3-HD.

Keybindings: Ctrl+Space (voice), Esc (stop TTS).

### Screenshot Capture

```bash
/screenshot                  # Active window
/screenshot region           # Select region
/screenshot full             # Full screen
/screenshot rdp              # RDP window
/screenshot annotate         # Flameshot annotation
```

### Web Companion

```bash
/web                         # Open in browser (localhost:8741)
/web stop                    # Stop web server
```

Real-time streaming, same conversation as terminal.

### Input Modes

- `!multi` — Enter multi-line mode (finish with `!end`)
- `!fragment <name>` — Attach an llm fragment to conversation

### All Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show commands |
| `/clear` | Clear history |
| `/reset` | Full reset (history + squash) |
| `/model [name]` | Switch model / list models |
| `/info` | Session info |
| `/watch [goal]` | Watch mode |
| `/squash` | Compress context |
| `/rewind [n]` | Rewind conversation |
| `/kb [load\|unload\|reload]` | Knowledge bases |
| `/memory [reload\|global\|local]` | Memory system |
| `/mcp [load\|unload]` | MCP servers |
| `/report [...]` | Pentest findings |
| `/voice [on\|off\|clean]` | Voice input |
| `/speech [on\|off]` | TTS output |
| `/screenshot [mode]` | Screenshot capture |
| `/copy [n\|all\|raw]` | Copy to clipboard |
| `/web [stop]` | Web companion |
| `/quit` | Exit |

### Micro Text Editor Integration

AI-powered editing in the Micro terminal editor.

```bash
micro myfile.py

# Generate (no selection): position cursor, press Ctrl+E
llm write a fibonacci function

# Modify (with selection): select code, press Ctrl+E
llm add error handling to this function

# Use templates
llm -t llm-code implement quicksort in python
```

---

## Level 4: Desktop AI

System-wide AI access outside the terminal.

### GTK Popup Assistant

| Hotkey | Action |
|--------|--------|
| Super+^ | Open popup (German keyboards) |
| Super+Shift+^ | Open with current selection |
| Super+` | Open popup (US keyboards) |
| Super+Shift+` | Open with current selection |

Features: action panel (Ctrl+K), image drag & drop, desktop context capture, browser fallback at `localhost:8741`.

See [Desktop Integration](DESKTOP_INTEGRATION.md#gtk-popup-assistant) for details.

### Espanso Text Expansion

Type triggers in **any application**:

| Trigger | Mode | Clipboard |
|---------|------|-----------|
| `:llm:` | simple | no |
| `:llmc:` | simple | yes |
| `:@:` | assistant | no |
| `:@c:` | assistant | yes |

See [Desktop Integration](DESKTOP_INTEGRATION.md#text-expansion-with-espanso) for details.

### Ulauncher Extension

| Keyword | Mode |
|---------|------|
| `llm` | simple query |
| `@` | assistant with tools |

Launch via Ctrl+Space. Streaming responses, persistent conversations.

See [Desktop Integration](DESKTOP_INTEGRATION.md#ulauncher-extension) for details.

### Speech-to-Text

```bash
transcribe recording.mp3
transcribe video.mp4 -o transcript.txt
```

25 languages, multiple formats. See [Desktop Integration](DESKTOP_INTEGRATION.md#speech-to-text-transcription).

---

## Level 5: Agentic Coding

### Claude Code

Anthropic's agentic coding CLI. Installed at level 2+.

```bash
claude                       # Launch Claude Code
```

### Claude Code Router

Multi-provider routing proxy for Claude Code.

```bash
routed-claude                # Launch through router
```

| Mode | Primary | Web Search |
|------|---------|------------|
| Dual-Provider | Azure OpenAI | Gemini |
| Gemini-Only | Gemini | Gemini |

Route types: `default`, `background`, `think`, `longContext`, `webSearch`.

Config: `~/.claude-code-router/config.json`.

### Codex CLI

Alternative agentic coding tool (Azure-only).

---

## Additional Tools

### LLM Functions (Optional)

Custom tools in Bash, JavaScript, or Python via [llm-functions](https://github.com/sigoden/llm-functions/).

```bash
git clone https://github.com/sigoden/llm-functions.git
cd llm-functions
cat > tools.txt <<EOF
get_current_weather.sh
execute_command.sh
EOF
argc build && argc check

# Use with llm
llm -T get_current_weather "What's the weather in Berlin?"
```

### Repository Analysis

```bash
# gitingest (Python, feature-rich)
gitingest https://github.com/user/repo | llm "What does this do?"

# yek (Rust, 230x faster)
yek /path/to/repo | llm "Review architecture"

# files-to-prompt
files-to-prompt src/*.py | llm "Review for security issues"
files-to-prompt project/ -e py -e js -c > context.xml
```

### Piping Workflows

```bash
curl -s https://api.github.com/repos/simonw/llm | \
    llm jq 'extract stars, forks, open issues' | \
    llm "Analyze project popularity"

tail -n 100 /var/log/syslog | llm "Identify errors and explain"

git log --since="30 days ago" | \
    llm "Prepare a timeline of development" --md
```
