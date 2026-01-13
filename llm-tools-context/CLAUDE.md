# llm-tools-context - Session Recording & Context System

This file provides guidance to Claude Code when working with the session recording and context extraction system.

## Overview

The system provides **automatic terminal session recording** and **AI-accessible context extraction**. It enables LLMs to query recent terminal history including command outputs.

## Architecture

```
Shell starts → asciinema records → $SESSION_LOG_FILE points to recording
    → `context` script parses it → `llm-tools-context` exposes it to AI
```

## Components

### 1. Automatic Recording (`integration/llm-common.sh`)

Interactive shells automatically start asciinema recording:
- Only triggers in interactive shells (not scripts or nested sessions)
- Prevents recursion by checking session-specific environment markers
- Stores recordings in configurable directory via `$SESSION_LOG_DIR`
- Creates timestamp-based filenames
- Exports `$SESSION_LOG_FILE` for the context tool

### 2. Context Extraction (`llm-tools-context/`)

Python package that parses asciinema recordings:
- Finds current session's `.cast` file via `$SESSION_LOG_FILE` or most recent file
- Converts binary `.cast` format to text using `asciinema convert`
- Uses regex patterns to detect shell prompts (bash `$/#`, zsh `%/❯/→/➜`, etc.)
- Handles Kali two-line prompts (┌/╭ and └/╰ box-drawing characters)
- Extracts **prompt blocks** (prompt + command + output from one prompt to the next)
- Filters out previous `context` command outputs (lines starting with `#c#`)

### 3. LLM Tool Integration

Registered as a tool that LLMs can call during conversations:
- Usage: `llm --tool context "what did I just run?"`
- Allows AI to query recent terminal history including command outputs

### 4. Prompt Detection Module (`llm-assistant/llm_assistant/prompt_detection.py`)

Shared Python module for shell prompt detection:
- **Hybrid detection**: Unicode markers (priority 1) + regex fallback (priority 2)
- **Unicode markers** (`\u200B\u200D\u200B` and `\u200D\u200B\u200D`):
  - Invisible zero-width characters injected into PS1/PROMPT
  - Only in VTE terminals (Terminator, GNOME Terminal, Tilix)
- **Regex patterns** for fallback: bash, zsh, and Kali two-line prompts

## Configuration

### First-Run Setup

On first installation, the script prompts for session history storage:
- **Permanent**: `~/session_logs/asciinema` (survives reboots)
- **Temporary**: `/tmp/session_logs/asciinema` (cleared on reboot, default)

Saved as `export SESSION_LOG_DIR="..."` in `.bashrc`/`.zshrc`.

### Suppressing Startup Messages

```bash
# Add to .bashrc before integration source line
export SESSION_LOG_SILENT=1
```

## Terminal Multiplexer Behavior

### tmux/screen

- **Each pane/window gets independent recording** (intentional design)
- Different panes = different workflows = separate contexts
- Uses pane-specific markers (e.g., `IN_ASCIINEMA_SESSION_tmux_0`)
- Session filenames include pane identifiers: `2025-10-05_14-30-45-123_12345_tmux0.cast`

### Unified Recording Alternative

Start asciinema manually before launching tmux:
```bash
asciinema rec --command "tmux attach"
```

## Restricted Environments (chroot/rescue)

- **Test-before-exec pattern**: Tests if pty creation works before replacing shell
- **Graceful degradation**: Shell continues normally if asciinema fails
- **Always warns**: "Warning: Session recording disabled (cannot create pty...)"
- Works in Hetzner rescue systems, minimal chroots, containers

## Usage

### CLI Commands

```bash
# Show last command and output
context

# Show last 5 commands
context 5

# Show entire session
context all

# Output SESSION_LOG_FILE export command
context -e
```

### With LLM

```bash
llm --tool context "what was the output of my last command?"
```

## Context Output Format

All output lines are prefixed with `#c#` for identification and filtering.

## File Locations

| Path | Purpose |
|------|---------|
| `$SESSION_LOG_DIR/*.cast` | Session recordings (default: `/tmp/session_logs/asciinema/`) |
| `llm-tools-context/llm_tools_context/` | Python package source |
| `integration/llm-common.sh` | Auto-recording logic |
| `llm-assistant/llm_assistant/prompt_detection.py` | Shared prompt detector |

## Testing

```bash
# Install in editable mode
cd llm-tools-context
llm install -e '.[test]'

# Run tests
python -m pytest tests/

# Test with debug output
llm --tool context "test" --tools-debug

# Verify plugin loaded
llm plugins | grep context
```

## Troubleshooting

**Recording not starting**: Verify integration is sourced (`grep -r "llm-integration" ~/.bashrc ~/.zshrc`) and `which asciinema` shows path.

**Context shows wrong session**: Check `echo $SESSION_LOG_FILE` or manually set it.

**New tmux panes don't record**: Check `env | grep IN_ASCIINEMA_SESSION` shows pane-specific markers.
