# llm-inlineassistant - Inline AI Assistant (Thin Client)

## Overview

llm-inlineassistant is a thin client that connects to llm-assistant's unified daemon. It provides an inline AI assistant that works in any terminal and espanso text expander. Unlike the full llm-assistant (which requires Terminator), llm-inlineassistant works in tmux, SSH sessions, Kitty, Alacritty, and espanso triggers in any application.

## Key Features

- **`@ <query>` syntax**: Fast, natural way to interact with AI from command line
- **Smart @ widget** (Zsh): @ at line start enters LLM mode with Tab completions
- **`:llm:`/`:llma:` espanso triggers**: AI-powered text expansion in any application
- **Unified daemon**: Uses `llm-assistant --daemon` as backend (shared with all clients)
- **Per-terminal conversations**: Each terminal maintains its own conversation
- **Asciinema context**: Automatically includes recent command history (shell mode)
- **Streaming markdown**: Real-time Rich markdown rendering with `Live` context manager
- **Tab completions** (Zsh): `/slash-commands` and `@fragments` via daemon

## Architecture

```
┌─────────────────────┐      Unix Socket       ┌────────────────────────┐
│ @ query             │ ──────────────────────▶│ llm-assistant --daemon │
│ (function or widget)│      JSON request      │                        │
│                     │◀──────────────────────│ - HeadlessSession      │
│                     │      NDJSON stream     │ - RAG, KB, Skills      │
└─────────────────────┘                        │ - Tool execution       │
                                               │ - Completions          │
┌─────────────────────┐                        │                        │
│ llm-inlineassistant │ ──────────────────────▶│                        │
│ (thin client)       │                        └────────────────────────┘
└─────────────────────┘

┌─────────────────────┐
│ :llm: query         │ ──────────────────────▶  Same daemon
│ (espanso)           │
└─────────────────────┘
```

The thin client handles:
- Socket communication with daemon
- Rich markdown rendering
- Daemon auto-start when not running
- Tab completion requests

The daemon (llm-assistant --daemon) handles:
- LLM API calls and conversation management
- Tool execution (suggest_command, execute_python, etc.)
- Context capture from asciinema
- Slash command and fragment completions

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

### Zsh Smart @ Widget

In Zsh, pressing @ at the start of a line enters LLM mode:
- Tab completion for `/slash-commands`
- Tab completion for `@fragments` (@pdf:, @yt:, @arxiv:, @dir:, @file:, @github:)
- @ elsewhere in line inserts literal @

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
│   ├── cli.py                # CLI entry point (--complete flag)
│   ├── client.py             # Socket client + Rich rendering
│   └── utils.py              # Config paths, terminal ID utilities
└── pyproject.toml            # Package metadata
```

## Protocol

### Request (JSON)

```json
{"cmd": "query", "tid": "tmux:%1", "log": "/tmp/.../session.cast", "q": "what's 2+2?", "mode": "assistant", "sys": ""}
```

| Field | Required | Description |
|-------|----------|-------------|
| `cmd` | yes | Command: `query`, `complete`, `new`, `status`, `shutdown` |
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

### Completion Response (for --complete)

Tab-separated: `text\tdescription`

```
/new	Start fresh conversation
/status	Show session info
@github:	Load GitHub repository
@pdf:	Load PDF document
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

## Keybindings

- `Ctrl+N` - AI command completion (via llm-cmd-comp)
- `Ctrl+G` - Apply suggested command from `suggest_command` tool

## Installation

Installed automatically by `install-llm-tools.sh`:
- Package installed into llm's uv environment
- CLI: `llm-inlineassistant`
- Shell function `@()` defined in `integration/llm-common.sh`
- Smart @ widget in `integration/llm-integration.zsh`

## Daemon Management

- **Daemon**: `llm-assistant --daemon` (unified backend)
- **Socket**: `/tmp/llm-assistant-{UID}/daemon.sock`
- **Auto-start**: Daemon starts automatically on first `@` command
- **Idle timeout**: 30 minutes (daemon terminates when idle)
- **Manual shutdown**: `@ /quit`

## Streaming Markdown

Responses are rendered as markdown in real-time using Rich's `Live` context manager:
- 10 FPS refresh rate for smooth updates
- Accumulated text approach (same as `/opt/llm` CLI)
- Handles code blocks, headers, lists, and other markdown syntax
- Falls back to plain text for errors
