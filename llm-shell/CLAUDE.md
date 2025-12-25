# llm-shell - Shell-Native AI Assistant

## Overview

llm-shell provides a shell-native AI assistant that works in any terminal. Unlike llm-assistant (which requires Terminator), llm-shell works in tmux, SSH sessions, Kitty, Alacritty, and any other terminal.

## Key Features

- **`@ <query>` syntax**: Fast, natural way to interact with AI from command line
- **Daemon architecture**: <100ms response time after first call
- **Per-terminal conversations**: Each terminal maintains its own conversation
- **Asciinema context**: Automatically includes recent command history
- **Block-level hashing**: Avoids resending unchanged context
- **Streaming markdown**: Real-time Rich markdown rendering with `Live` context manager
- **System prompt**: Simplified llm-assistant-style prompt optimized for shell workflows
- **Tool support**: Auto-executes tools (execute_python, fetch_url, search_google)

## Architecture

```
┌─────────────────┐     Unix Socket      ┌──────────────────────┐
│ @ query         │ ──────────────────▶  │ llm-shell-daemon     │
│ (shell func)    │                      │ (warm Python process) │
│                 │ ◀──────────────────  │ - llm loaded         │
│                 │     Response stream  │ - conversations cached│
└─────────────────┘                      └──────────────────────┘
```

## Usage

```bash
# Simple query
@ What's new about space travel?

# Continue conversation
@ Tell me more about the Saturn news

# Start fresh conversation
@ /new

# Show status
@ /status

# Get help
@ /help
```

## File Structure

```
llm-shell/
├── llm_shell/
│   ├── __init__.py         # Package exports
│   ├── __main__.py         # Entry: python -m llm_shell
│   ├── cli.py              # CLI entry point
│   ├── utils.py            # Config dir, database, terminal ID
│   ├── context_capture.py  # Asciinema context with hashing
│   ├── daemon.py           # Unix socket server with system prompt
│   ├── client.py           # Shell-side socket client with streaming markdown
│   └── templates/
│       └── system_prompt.j2  # Jinja2 system prompt template
└── pyproject.toml          # Depends on llm-assistant, jinja2
```

## Database

llm-shell shares config directory with llm-assistant but uses separate files:
- Database: `~/.config/llm-assistant/logs-shell.db`
- Session tracking: `~/.config/llm-assistant/shell-sessions/`
- Respects `llm logs off` global setting

## Context Capture

Context comes from asciinema recordings via direct import from `scripts/context`:
1. Imports `get_command_blocks()` from context module (no subprocess)
2. Gets last 3 command blocks from asciinema recording
3. Applies SHA256 hashing for deduplication (shared with llm-assistant)
4. Uses `[Content unchanged]` placeholder when context hasn't changed

## Tools

llm-shell exposes these tools to the model:

**Built-in:**
- `suggest_command` - Place command on user's prompt (Ctrl+G to apply)

**Plugin tools (if installed):**
- `execute_python` - Run Python code in sandbox (llm-tools-sandboxed-shell)
- `fetch_url` - Fetch web page content (llm-tools-web-fetch)
- `search_google` - Google web search (llm-tools-google-search)
- `load_github` - Load GitHub repo content (llm-tools-fragment-bridge)
- `load_pdf` - Extract PDF text (llm-tools-fragment-bridge)
- `load_yt` - Get YouTube transcripts (llm-tools-fragment-bridge)
- `prompt_fabric` - Run AI patterns (llm-tools-fabric)

Tools are auto-executed when the model calls them. Results are sent back
to the model for multi-turn tool use (max 10 iterations).

During tool execution, a spinner is displayed showing the action (e.g., "Running Python...").
The spinner disappears automatically when execution completes.

## Keybindings

- `Ctrl+N` - AI command completion (via llm-cmd-comp)
- `Ctrl+G` - Apply suggested command from `suggest_command` tool

## Shared Components

Reuses from llm-assistant:
- `filter_new_blocks()` - Block-level hash filtering from `llm_assistant.context`
- Prompt detection patterns from `llm_tools.prompt_detection`

Has its own:
- Simplified system prompt template (`templates/system_prompt.j2`)
- Tool auto-execution loop (similar pattern to llm-assistant but simplified)

## Installation

Installed automatically by `install-llm-tools.sh`:
- Package installed into llm's uv environment
- Wrapper scripts: `~/.local/bin/llm-shell`, `~/.local/bin/llm-shell-daemon`
- Shell function `@()` defined in `integration/llm-common.sh`

## Daemon Management

- Auto-starts on first `@` command
- Auto-terminates after 30 minutes idle
- Socket: `/tmp/llm-shell-{UID}.sock`
- Manual shutdown: `@ /quit`

## Streaming Markdown

Responses are rendered as markdown in real-time using Rich's `Live` context manager:
- 10 FPS refresh rate for smooth updates
- Accumulated text approach (same as `/opt/llm` CLI)
- Handles code blocks, headers, lists, and other markdown syntax
- Falls back to plain text for errors
