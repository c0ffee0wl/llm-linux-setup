# llm-inlineassistant - Inline AI Assistant

## Overview

llm-inlineassistant provides an inline AI assistant with daemon architecture that works in any terminal and espanso text expander. Unlike llm-assistant (which requires Terminator), llm-inlineassistant works in tmux, SSH sessions, Kitty, Alacritty, and espanso triggers in any application.

## Key Features

- **`@ <query>` syntax**: Fast, natural way to interact with AI from command line
- **`:llm:`/`:llma:` espanso triggers**: AI-powered text expansion in any application
- **Daemon architecture**: <100ms response time after first call
- **Per-terminal conversations**: Each terminal maintains its own conversation
- **Asciinema context**: Automatically includes recent command history (shell mode)
- **Block-level hashing**: Avoids resending unchanged context
- **Streaming markdown**: Real-time Rich markdown rendering with `Live` context manager
- **JSON/NDJSON protocol**: Clean, debuggable, extensible
- **Concurrent handling**: Multiple terminals don't block each other

## Architecture

```
┌─────────────────┐     Unix Socket      ┌────────────────────────┐
│ @ query         │ ──────────────────▶  │ llm-inlineassistant    │
│ (shell func)    │     JSON request     │ daemon                 │
│                 │ ◀──────────────────  │ - llm loaded           │
│                 │     NDJSON stream    │ - conversations cached │
└─────────────────┘                      │ - per-terminal queues  │
                                         └────────────────────────┘
┌─────────────────┐
│ :llm: query     │ ──────────────────▶  Same daemon
│ (espanso)       │
└─────────────────┘
```

## Usage

### Shell (via @ function)

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

### Espanso (text expansion)

- `:llm:` - Quick query without tools (simple mode)
- `:llmc:` - Query with clipboard as context (simple mode)
- `:llma:` - Full assistant with tools enabled

## File Structure

```
llm-inlineassistant/
├── llm_inlineassistant/
│   ├── __init__.py           # Package exports
│   ├── __main__.py           # Entry: python -m llm_inlineassistant
│   ├── cli.py                # CLI entry point
│   ├── utils.py              # Config dir, database, terminal ID
│   ├── context_capture.py    # Asciinema context with hashing
│   ├── daemon.py             # Unix socket server with NDJSON streaming
│   ├── client.py             # Shell-side socket client
│   └── templates/
│       └── system_prompt.j2  # Jinja2 system prompt template
└── pyproject.toml            # Depends on llm-assistant, jinja2
```

## Protocol

### Request (JSON)

```json
{"cmd": "query", "tid": "tmux:%1", "log": "/tmp/.../session.cast", "q": "what's 2+2?", "mode": "assistant", "sys": ""}
```

| Field | Required | Description |
|-------|----------|-------------|
| `cmd` | yes | Command: `query`, `new`, `status`, `shutdown` |
| `tid` | yes | Terminal ID for conversation tracking |
| `log` | no | Session log file for context capture |
| `q` | for query | Query text |
| `mode` | no | `"assistant"` (default, tools enabled) or `"simple"` (no tools) |
| `sys` | no | Custom system prompt (for simple mode) |

### Response (NDJSON)

```json
{"type": "text", "content": "Here's how to..."}
{"type": "tool_start", "tool": "execute_python", "args": {"code": "..."}}
{"type": "tool_done", "tool": "execute_python", "result": "42"}
{"type": "text", "content": " The answer is 42."}
{"type": "done"}
```

### Error Codes

| Code | Description |
|------|-------------|
| `EMPTY_QUERY` | Query text is empty |
| `MODEL_ERROR` | LLM API error |
| `TOOL_ERROR` | Tool execution failed |
| `TIMEOUT` | Request timed out |
| `PARSE_ERROR` | Invalid JSON request |
| `INTERNAL` | Unexpected server error |

## Database

llm-inlineassistant shares config directory with llm-assistant but uses separate files:
- Database: `~/.config/llm-assistant/logs-inlineassistant.db`
- Session tracking: `~/.config/llm-assistant/inlineassistant-sessions/`
- Respects `llm logs off` global setting

## Context Capture

Context comes from asciinema recordings via direct import from `scripts/context`:
1. Imports `get_command_blocks()` from context module (no subprocess)
2. Gets last 3 command blocks from asciinema recording
3. Applies SHA256 hashing for deduplication (shared with llm-assistant)
4. Uses `[Content unchanged]` placeholder when context hasn't changed

## Tools

llm-inlineassistant exposes these tools to the model (in assistant mode):

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
- Wrapper scripts: `~/.local/bin/llm-inlineassistant`, `~/.local/bin/llm-inlineassistant-daemon`
- Shell function `@()` defined in `integration/llm-common.sh`

## Daemon Management

- Auto-starts on first `@` command or espanso trigger
- Auto-terminates after 30 minutes idle
- Socket: `/tmp/llm-inlineassistant-{UID}/daemon.sock`
- Manual shutdown: `@ /quit`

## Streaming Markdown

Responses are rendered as markdown in real-time using Rich's `Live` context manager:
- 10 FPS refresh rate for smooth updates
- Accumulated text approach (same as `/opt/llm` CLI)
- Handles code blocks, headers, lists, and other markdown syntax
- Falls back to plain text for errors
