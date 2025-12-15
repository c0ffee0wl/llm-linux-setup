# LLM Assistant - Terminator AI Assistant

This file provides guidance to Claude Code when working with the llm-assistant component.

## Overview

The repository includes **llm-assistant**, a TmuxAI-inspired terminal assistant for Terminator terminal emulator that provides an interactive AI pair programming experience.

## Architecture Overview

- **Python-based**: Uses `llm` library directly (no external AI binaries)
- **Automatic Exec terminal**: Auto-creates split terminal for command execution
- **Smart context**: Captures all terminals except Chat terminal (self-awareness)
- **Watch mode**: Proactive monitoring with user-defined goals
- **D-Bus integration**: Uses Terminator's existing D-Bus API for terminal management

## Core Components

1. **Terminator Plugin** (`terminator-assistant-plugin/terminator_assistant.py`):
   - Provides VTE content capture via Plugin API
   - Version-aware VTE text extraction (supports VTE 72+ and older)
   - Terminal enumeration and metadata
   - Command injection via `vte.feed_child()`
   - Accessed by standalone app via **D-Bus service** (not PluginRegistry)
   - Inherits from both `plugin.Plugin` and `dbus.service.Object`
   - `is_likely_tui_active()` - Hybrid TUI detection (command name + terminal state via vadjustment)

2. **Standalone Application** (`llm-assistant`):
   - Python script that imports llm library directly
   - Rich terminal UI with streaming markdown
   - Conversation management with auto-squashing
   - Tool-based command execution with structured output
   - Asyncio-based watch mode

3. **Assistant Template** (`llm-template/terminator-assistant.yaml`):
   - System prompt optimized for Terminator environment
   - Instructs AI on tool usage for terminal interaction
   - Explains context awareness and watch mode

4. **Assistant Tools Plugin** (`llm-tools-assistant/`):
   - Provides structured tool definitions for terminal control
   - Tools: execute_in_terminal, send_keypress, capture_terminal, refresh_context, view_attachment, view_pdf, view_youtube_native
   - Schema validation at model level prevents malformed commands

## User Workflow

```
1. Run llm-assistant in any Terminator terminal
2. Script auto-creates Exec terminal via D-Bus hsplit
3. Type messages in Chat terminal (where script runs)
4. AI responds with streaming markdown
5. If AI uses tools (execute_in_terminal, etc.), prompted: "Execute? [y/n/e]"
6. Approved commands run in Exec terminal
7. Command output captured for next AI iteration
```

## Context Capture

- **Visible content only**: Captures up to 5000 lines of scrollback per terminal
- **Self-aware**: Excludes Chat terminal (where assistant runs)
- **All terminals**: Monitors all terminals in current window
- **Intelligent filtering**: Optionally excludes Exec terminal output

## Context Management

- **Auto-squashing**: Compresses conversation at 80% of 800k token limit (640k threshold)
- **Manual squashing**: `/squash` command
- **Token estimation**: Uses API's actual token counts from last response (accurate); falls back to char-based estimation (4 chars = 1 token) if unavailable
- **Preserves recent context**: Keeps system prompt + last 5 messages + summary

## Command Completion Detection

Commands executed in the Exec terminal use **prompt-based completion detection** (inspired by TmuxAI):

- **PromptDetector**: Uses shared `prompt_detection.py` module to detect shell prompts
- **Polls for prompt**: Instead of fixed timeout, polls until prompt appears
- **Visual feedback**: Shows spinner animation while waiting
- **Timeout fallback**: Falls back to 60-second timeout for long-running commands
- **TUI detection**: Switches to screenshot capture for TUI apps (htop, vim, etc.)

**Detection patterns**: Supports bash (`$`/`#`), zsh (`%`/`❯`/`→`/`➜`), and Kali two-line prompts.

## Watch Mode

- **All-terminal monitoring**: Watches all terminals except Chat
- **User-defined goals**: `/watch detect security issues`
- **Proactive suggestions**: AI only responds when actionable
- **Background asyncio**: Non-blocking monitoring loop
- **Fixed 5-second interval**: Polls every 5 seconds
- **Exec terminal state**: Reports if Exec terminal is idle or running a command

**Intelligent Change Detection** (TmuxAI-inspired):
1. **Hash-based skip**: SHA256 hash of terminal context - skips AI calls when unchanged
2. **History-aware prompting**: AI compares against conversation history, focuses on NEW content
3. **Robust dismissive filter**: Recognizes "OK", "no issues", "nothing to report", etc.

## Slash Commands

- `/help` - Show available commands
- `/clear` - Clear conversation history
- `/reset` - Full reset: clear history and remove squash summaries
- `/model [name]` - Switch AI model or list available models
- `/info` - Show session info (conversation ID, context size, model)
- `/watch` - Show watch mode status (with usage hint if disabled)
- `/watch <goal>` - Enable watch mode with goal
- `/watch off` - Disable watch mode
- `/watch status` - Show watch mode status (same as `/watch`)
- `/squash` - Manually compress conversation context
- `/kb` - List available and loaded knowledge bases
- `/kb load <name>` - Load a knowledge base into session (comma-separated for multiple)
- `/kb unload <name>` - Remove a knowledge base from session (comma-separated for multiple)
- `/kb reload` - Reload all loaded knowledge bases
- `/speech` - Enable TTS output (Vertex models only)
- `/speech off` - Disable TTS output
- `/speech status` - Show TTS status
- `/assistant` - Switch to assistant mode (conservative, 10 tool iterations)
- `/agent` - Switch to agent mode (agentic, 100 tool iterations)
- `/copy` - Copy last response to clipboard (markdown stripped)
- `/copy [n]` - Copy last n responses
- `/copy raw [n]` - Copy with markdown preserved
- `/copy all` - Copy entire conversation
- `/quit` or `/exit` - Exit assistant

## Knowledge Base System

The assistant supports TmuxAI-style knowledge bases for persistent context:

**Location**: `~/.config/io.datasette.llm/kb/` (in llm's config directory)

**Usage**:
```bash
# Create a KB file
mkdir -p ~/.config/io.datasette.llm/kb
echo "## Project Conventions
- Use Python 3.10+
- Follow PEP8" > ~/.config/io.datasette.llm/kb/project.md

# In assistant:
/kb load project              # Load single KB
/kb load project,docker,git   # Load multiple KBs
/kb                           # List all KBs
/kb unload project            # Remove from session
```

**Auto-load config** (optional):
```yaml
# ~/.config/io.datasette.llm/assistant-config.yaml
knowledge_base:
  auto_load:
    - project
    - docker-conventions
```

Loaded KBs are injected after the system prompt, providing persistent context without consuming conversation history.

## Input Modes

- **Single-line mode** (default): Press Enter to submit
- **Multi-line mode**: Type `!multi` to enter, `!end` to finish and submit

**Keybindings:**
- `Ctrl+Space` - Toggle voice input (start/stop recording)
- `Esc` - Stop TTS playback
- `Ctrl+D` - Exit assistant
- `Ctrl+C` - Double-press within 2 seconds to exit

**Voice Input:**
- Press `Ctrl+Space` to start recording (shows 🎤 Recording...)
- Press `Ctrl+Space` again to stop and transcribe
- Uses onnx-asr with Parakeet TDT model (lazy-loaded on first use)
- Transcribed text is inserted at cursor position
- `/voice` or `/voice auto` - auto-submit transcribed text
- `/voice off` - disable auto-submit
- `/voice status` - show voice input status

**Speech Output (TTS):**
- Available only when using Vertex models (`vertex/*`)
- Uses Google Cloud Text-to-Speech with Chirp3-HD voices
- Uses EU endpoint (`eu-texttospeech.googleapis.com`) for data residency compliance
- **Streaming synthesis**: Uses `streaming_synthesize()` API for lower latency
- **Markdown stripping**: Removes formatting before synthesis (fenced code blocks skipped entirely)
- **Credential resolution order:**
  1. `GOOGLE_APPLICATION_CREDENTIALS` env var (service account JSON path)
  2. `llm vertex set-credentials /path/to/sa.json` (stored in llm config)
  3. Application Default Credentials (run `gcloud auth application-default login`)
- `/speech` or `/speech on` - enable TTS output
- `/speech off` - disable TTS output
- `/speech status` - show TTS status (including credential method)
- `Esc` - stop current TTS playback
- Default voice: `de-DE-Chirp3-HD-Laomedeia` (German)
- Requires `google-cloud-texttospeech`, `strip-markdown` packages (auto-installed)

**Other input:**
- `!fragment <name>` - Attach an llm fragment to the conversation

## AI Tool Interface

The AI uses structured tool calling to interact with terminals. See the `terminator-assistant.yaml` template for full documentation:
- `execute_in_terminal(command: str)` - Execute shell command in Exec terminal
- `send_keypress(keypress: str)` - Send keypresses (for TUI apps like vim, htop)
- `capture_terminal(scope: str)` - Screenshot capture ("exec" or "all")
- `refresh_context()` - Request fresh terminal content capture

These tools provide schema validation at the model level, ensuring the AI's requests are properly structured.

## Advantages Over Context Tool

- **Directed capture**: Any terminal by UUID, not just current session
- **Live content**: Current scrollback buffer, not past recordings
- **TUI support**: Can capture ANSI/TUI content
- **Selective capture**: Specific terminals, specific line counts
- **Self-aware**: Excludes own terminal to avoid recursion

## Architecture Decisions

- **No Terminator modification**: Uses only Plugin API + existing D-Bus
- **No ipc.py patching**: Plugin exports its own D-Bus service for external access
- **Clean separation**: Content (plugin D-Bus) vs Management (Terminator D-Bus)
- **Self-contained**: All code in this repository

## Installation

- Template: `terminator-assistant.yaml` installed (Phase 4)
- Plugin: Copied to `~/.config/terminator/plugins/` (Phase 5)
- Application: `llm-assistant` installed to `~/.local/bin/` (Phase 5)
- Dependencies: PyGObject, dbus-python conditionally installed (Phase 1, only if Terminator detected)
- Enable plugin: Terminator Preferences → Plugins → Check "TerminatorAssistant"

## Dependencies

- Terminator terminal emulator
- llm library (already installed)
- Python 3.8+
- PyGObject (for GTK/VTE bindings)
- D-Bus (for terminal management)
- prompt_toolkit (for keybindings)
- sounddevice, numpy, onnx-asr (optional, for voice input)
- google-cloud-texttospeech, google-auth, strip-markdown (optional, for TTS output with Vertex models)

## Usage Examples

**Preferred invocation** (via llm subcommand):
```bash
# Launch assistant in any Terminator terminal
llm assistant

# Launch with specific model
llm assistant azure/gpt-4.1

# Direct invocation also works
llm-assistant

# Inside assistant:
you> why did my docker build fail?
llm> [streams analysis based on terminal content]
llm> <EXECUTE>docker build --no-cache -t myapp .</EXECUTE>
Execute in Exec terminal? [y/n/e] y
[command runs in Exec terminal, output captured for next iteration]

you> /watch detect inefficient commands
Watch mode enabled: monitoring all terminals
[AI provides proactive suggestions when observing relevant activity]
```

## Operating Modes

The assistant supports two mutually exclusive operating modes:

- **Assistant Mode (default)**: Conservative behavior with 10 tool iterations per turn
  - Asks for clarification when uncertain
  - Suggests commands before executing state-changing operations
  - One step at a time approach

- **Agent Mode**: Agentic behavior with 100 tool iterations per turn
  - Bias for action, keeps working until task resolved
  - Doesn't ask unnecessary confirmation questions
  - Breaks down complex tasks and executes step by step

Switch modes at runtime with `/assistant` or `/agent`, or start in agent mode with `--agent` flag.

Mode persists across `/reset` - only explicit `/assistant` or `/agent` commands change it.

## CLI Arguments

Model selection (llm-compatible):
- `-m, --model MODEL` - LLM model to use (e.g., `llm-assistant -m azure/gpt-4.1-mini`)
- `-q, --query QUERY` - Select model by fuzzy matching (can be used multiple times, e.g., `-q haiku -q claude`)

Conversation persistence (llm-compatible):
- `-c, --continue` - Continue the most recent conversation
- `--cid ID` - Continue conversation with given ID
- `--no-log` - Disable conversation logging to database

Other options:
- `--debug` - Enable debug output for troubleshooting
- `--max-context TOKENS` - Max context tokens before auto-squash (default: 800000)
- `--agent` - Start in agent mode (agentic, 100 tool iterations)

Examples:
```bash
llm-assistant -m azure/gpt-4.1-mini    # explicit model
llm-assistant -q opus                   # fuzzy match for "opus"
llm-assistant -q haiku -q claude        # first model matching both "haiku" AND "claude"
llm assistant -m gemini-2.5-flash       # via llm wrapper
llm assistant -c                        # continue most recent conversation
llm assistant --cid 01abc123def...      # continue specific conversation
```

## Conversation Persistence

Conversations are automatically logged to the llm database (`~/.config/io.datasette.llm/logs.db`), enabling:

- **Resume across sessions**: Continue where you left off with `-c` or `--cid`
- **Search conversation history**: Use `llm logs` to view past assistant interactions
- **Consistent UX**: Same conversation model as `llm chat`

**What's stored:**
- User prompts (with terminal context stripped for privacy)
- AI responses
- Tool calls and results
- Token usage and timing

**What's NOT stored:**
- Terminal context (captured live on each prompt)
- Watch mode state
- Loaded knowledge bases

**Context Squashing and Conversation Links:**
When context is squashed, a new conversation is created and linked to the previous one. Links are stored in `~/.config/io.datasette.llm/squash-links.json`. The assistant displays the link when resuming a conversation that was created from a squash.

**Disabling logging:**
Use `--no-log` to run without database persistence (conversation won't be resumable).

## Technical Implementation

- **Content flow**: VTE terminals → Plugin (get_text_range_format) → llm.Conversation → streaming response
- **Terminal management**: D-Bus `hsplit`, `get_terminals`, `get_focused_terminal` (existing API)
- **Conversation continuity**: Uses `llm.Conversation()` objects with auto-squashing
- **Database logging**: Uses `response.log_to_db()` to store conversations in llm's `logs.db`
- **Context stripping**: Terminal context removed from prompts before database storage (privacy)
- **Self-awareness**: Filters out `self.chat_terminal_uuid` when capturing context
- **Tool calling**: Uses llm's tool_calls() for structured command extraction with schema validation
- **Execution**: `vte.feed_child()` via plugin
- **Completion detection**: `PromptDetector.detect_prompt_at_end()` for smart wait-for-prompt

## File Locations

### Repository Files
- `integration/llm-assistant` - Standalone application
- `integration/terminator-assistant-plugin/terminator_assistant.py` - Terminator plugin
- `llm-template/terminator-assistant.yaml` - System prompt template
- `llm-tools-assistant/` - LLM plugin for structured tool definitions

### Installed Locations
- `~/.local/bin/llm-assistant` - Application binary
- `~/.config/terminator/plugins/terminator_assistant.py` - Plugin
- `~/.config/io.datasette.llm/templates/terminator-assistant.yaml` - Template

### Data Locations
- `~/.config/io.datasette.llm/logs.db` - Conversation database (shared with llm CLI)
- `~/.config/io.datasette.llm/squash-links.json` - Conversation squash chain links

## Troubleshooting

- **Plugin not appearing**: Check `~/.config/terminator/plugins/terminator_assistant.py` exists, restart Terminator
- **Import errors**: Ensure running inside Terminator terminal: `echo $TERMINATOR_UUID`
- **D-Bus errors**: Ensure D-Bus enabled in Terminator config
- **No terminals captured**: Enable plugin in Terminator Preferences → Plugins
- **Watch mode not working**: Check asyncio compatibility, ensure Python 3.8+
