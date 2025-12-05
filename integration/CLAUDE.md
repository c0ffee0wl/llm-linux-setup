# LLM Sidechat - Terminator AI Assistant

This file provides guidance to Claude Code when working with the llm-sidechat component.

## Overview

The repository includes **llm-sidechat**, a TmuxAI-inspired terminal assistant for Terminator terminal emulator that provides an interactive AI pair programming experience.

## Architecture Overview

- **Python-based**: Uses `llm` library directly (no external AI binaries)
- **Automatic Exec terminal**: Auto-creates split terminal for command execution
- **Smart context**: Captures all terminals except Chat terminal (self-awareness)
- **Watch mode**: Proactive monitoring with user-defined goals
- **D-Bus integration**: Uses Terminator's existing D-Bus API for terminal management

## Core Components

1. **Terminator Plugin** (`terminator-sidechat-plugin/terminator_sidechat.py`):
   - Provides VTE content capture via Plugin API
   - Version-aware VTE text extraction (supports VTE 72+ and older)
   - Terminal enumeration and metadata
   - Command injection via `vte.feed_child()`
   - Accessed by standalone app via **D-Bus service** (not PluginRegistry)
   - Inherits from both `plugin.Plugin` and `dbus.service.Object`
   - `is_likely_tui_active()` - Hybrid TUI detection (command name + terminal state via vadjustment)

2. **Standalone Application** (`llm-sidechat`):
   - Python script that imports llm library directly
   - Rich terminal UI with streaming markdown
   - Conversation management with auto-squashing
   - Command extraction from `<EXECUTE>` XML tags
   - Asyncio-based watch mode

3. **Sidechat Template** (`llm-template/terminator-sidechat.yaml`):
   - System prompt optimized for Terminator environment
   - Instructs AI on command formatting
   - Explains context awareness and watch mode

## User Workflow

```
1. Run llm-sidechat in any Terminator terminal
2. Script auto-creates Exec terminal via D-Bus hsplit
3. Type messages in Chat terminal (where script runs)
4. AI responds with streaming markdown
5. If AI suggests commands (<EXECUTE> tags), prompted: "Execute? [y/n/e]"
6. Approved commands run in Exec terminal
7. Command output captured for next AI iteration
```

## Context Capture

- **Visible content only**: Captures 100 lines of scrollback per terminal
- **Self-aware**: Excludes Chat terminal (where sidechat runs)
- **All terminals**: Monitors all terminals in current window
- **Intelligent filtering**: Optionally excludes Exec terminal output

## Context Management

- **Auto-squashing**: Compresses conversation at 80% of token limit
- **Manual squashing**: `/squash` command
- **Token estimation**: Rough estimation (4 chars = 1 token)
- **Preserves recent context**: Keeps system prompt + last 5 messages + summary

## Command Completion Detection

Commands executed in the Exec terminal use **prompt-based completion detection** (inspired by TmuxAI):

- **PromptDetector**: Uses shared `prompt_detection.py` module to detect shell prompts
- **Polls for prompt**: Instead of fixed timeout, polls until prompt appears
- **Visual feedback**: Shows spinner animation while waiting
- **Timeout fallback**: Falls back to 30-second timeout for long-running commands
- **TUI detection**: Switches to screenshot capture for TUI apps (htop, vim, etc.)

**Detection patterns**: Supports bash (`$`/`#`), zsh (`%`/`❯`/`→`/`➜`), and Kali two-line prompts.

## Watch Mode

- **All-terminal monitoring**: Watches all terminals except Chat
- **User-defined goals**: `/watch detect security issues`
- **Proactive suggestions**: AI only responds when actionable
- **Background asyncio**: Non-blocking monitoring loop
- **Configurable interval**: Default 5 seconds
- **Exec terminal state**: Reports if Exec terminal is idle or running a command

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
- `/quit` or `/exit` - Exit sidechat

## Input Modes

- `!multi` - Enter multi-line input mode (finish with `!end`)
- `!fragment <name>` - Attach an llm fragment to the conversation

## AI Command Syntax

The AI uses XML action tags to interact with terminals. See the `terminator-sidechat.yaml` template for full documentation:
- `<EXECUTE>command</EXECUTE>` - Execute shell command in Exec terminal
- `<PRESSKEY>keypress</PRESSKEY>` - Send keypresses (for TUI apps like vim, htop)
- `<CAPTURE/>` / `<CAPTURE>all</CAPTURE>` - Screenshot capture for TUI analysis
- `<REFRESH/>` - Request fresh terminal content capture

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

- Template: `terminator-sidechat.yaml` installed (Phase 4)
- Plugin: Copied to `~/.config/terminator/plugins/` (Phase 6)
- Application: `llm-sidechat` installed to `~/.local/bin/` (Phase 6)
- Dependencies: PyGObject, dbus-python conditionally installed (Phase 1, only if Terminator detected)
- Enable plugin: Terminator Preferences → Plugins → Check "TerminatorSidechat"

## Dependencies

- Terminator terminal emulator
- llm library (already installed)
- Python 3.8+
- PyGObject (for GTK/VTE bindings)
- D-Bus (for terminal management)

## Usage Examples

**Preferred invocation** (via llm subcommand):
```bash
# Launch sidechat in any Terminator terminal
llm sidechat

# Launch with specific model
llm sidechat azure/gpt-4.1

# Direct invocation also works
llm-sidechat

# Inside sidechat:
you> why did my docker build fail?
llm> [streams analysis based on terminal content]
llm> <EXECUTE>docker build --no-cache -t myapp .</EXECUTE>
Execute in Exec terminal? [y/n/e] y
[command runs in Exec terminal, output captured for next iteration]

you> /watch detect inefficient commands
Watch mode enabled: monitoring all terminals
[AI provides proactive suggestions when observing relevant activity]
```

## CLI Arguments

- `llm-sidechat [model]` - Launch with optional model name (e.g., `llm-sidechat azure/gpt-4.1`)
- `--capture-timeout SECS` - Configure capture timeout (default: 3.0 seconds)

## Technical Implementation

- **Content flow**: VTE terminals → Plugin (get_text_range_format) → llm.Conversation → streaming response
- **Terminal management**: D-Bus `hsplit`, `get_terminals`, `get_focused_terminal` (existing API)
- **Conversation continuity**: Uses `llm.Conversation()` objects with auto-squashing
- **Self-awareness**: Filters out `self.chat_terminal_uuid` when capturing context
- **Command extraction**: Regex parsing of `<EXECUTE>` XML tags from AI responses
- **Execution**: `vte.feed_child()` via plugin
- **Completion detection**: `PromptDetector.detect_prompt_at_end()` for smart wait-for-prompt

## File Locations

### Repository Files
- `integration/llm-sidechat` - Standalone application
- `integration/terminator-sidechat-plugin/terminator_sidechat.py` - Terminator plugin
- `llm-template/terminator-sidechat.yaml` - System prompt template

### Installed Locations
- `~/.local/bin/llm-sidechat` - Application binary
- `~/.config/terminator/plugins/terminator_sidechat.py` - Plugin
- `~/.config/io.datasette.llm/templates/terminator-sidechat.yaml` - Template

## Troubleshooting

- **Plugin not appearing**: Check `~/.config/terminator/plugins/terminator_sidechat.py` exists, restart Terminator
- **Import errors**: Ensure running inside Terminator terminal: `echo $TERMINATOR_UUID`
- **D-Bus errors**: Ensure D-Bus enabled in Terminator config
- **No terminals captured**: Enable plugin in Terminator Preferences → Plugins
- **Watch mode not working**: Check asyncio compatibility, ensure Python 3.8+
