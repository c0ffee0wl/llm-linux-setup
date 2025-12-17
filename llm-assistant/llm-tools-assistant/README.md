# llm-tools-assistant

> Note: This plugin was previously called `llm-tools-sidechat`.

LLM tools for terminal control in the assistant application.

## Overview

This plugin provides structured tool interfaces for terminal operations in assistant:

- `execute_in_terminal` - Execute shell commands in the Exec terminal
- `send_keypress` - Send keypresses/sequences to TUI applications
- `capture_terminal` - Capture terminal content or screenshots
- `refresh_context` - Refresh terminal context

## Design

These are "stub" tools - they return structured JSON to indicate intent, but the actual execution is handled by the assistant application. This approach provides:

1. **Schema validation** - The model generates structured tool calls that can't be malformed
2. **Separation of concerns** - Tool definitions are separate from execution logic
3. **Approval flow** - Assistant handles user approval before executing actions

## Installation

```bash
llm install /path/to/llm-tools-assistant
```

Or install in development mode:

```bash
llm install -e /path/to/llm-tools-assistant
```

## Usage

These tools are designed to be used by the assistant application, not standalone. The assistant main loop:

1. Passes these tools to the model via `conversation.prompt(tools=[...])`
2. Parses `response.tool_calls()` to extract structured commands
3. Executes actions using existing assistant logic with user approval
4. Returns results via `ToolResult` objects

## Tool Descriptions

### execute_in_terminal

Execute a shell command in the Exec terminal.

```python
execute_in_terminal(command="ls -la")
# Returns: {"action": "execute", "command": "ls -la", "status": "queued"}
```

### send_keypress

Send a keypress to interactive applications.

```python
send_keypress(keypress="Ctrl+C")
# Returns: {"action": "keypress", "key": "Ctrl+C", "status": "queued"}
```

### capture_terminal

Capture terminal content.

```python
capture_terminal(scope="all")
# Returns: {"action": "capture", "scope": "all", "status": "queued"}
```

### refresh_context

Refresh the terminal context.

```python
refresh_context()
# Returns: {"action": "refresh", "status": "queued"}
```

## License

Apache-2.0
