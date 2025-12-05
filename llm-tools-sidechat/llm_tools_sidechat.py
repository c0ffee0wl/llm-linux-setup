"""
LLM tools for terminal control in sidechat.

These tools provide structured interfaces for terminal operations. They are
"stub" tools - they return structured JSON to indicate intent, but the actual
execution is handled by the sidechat application which processes tool_calls.

This approach provides schema validation at the model level while keeping
the actual terminal control logic in sidechat.
"""
import json
from typing import Optional

import llm


def execute_in_terminal(command: str) -> str:
    """
    Execute a shell command in the Exec terminal.

    Use this tool to run commands in the designated execution terminal.
    The command will be sent to the terminal and its output captured.

    Always explain your reasoning before using this tool - describe what
    you're about to do and why.

    Args:
        command: The shell command to execute (e.g., "ls -la", "git status")

    Returns:
        JSON indicating the command has been queued for execution.
        Actual execution is handled by sidechat with user approval.
    """
    return json.dumps({
        "action": "execute",
        "command": command,
        "status": "queued"
    })


def send_keypress(keypress: str) -> str:
    """
    Send a keypress or key sequence to the Exec terminal.

    Use this for interactive applications (TUIs) that need keyboard input,
    such as vim, less, htop, or any application expecting keypresses.

    Supported special keys:
    - Enter, Escape, Tab, Space, Backspace, Delete
    - Ctrl+<key> (e.g., "Ctrl+C", "Ctrl+D", "Ctrl+Z")
    - Alt+<key> (e.g., "Alt+F", "Alt+B")
    - Arrow keys: Up, Down, Left, Right
    - Function keys: F1-F12
    - Page keys: PageUp, PageDown, Home, End

    For regular text input, just use the characters directly (e.g., ":wq" for vim).

    Always explain your reasoning before using this tool.

    Args:
        keypress: The key or key sequence to send (e.g., "Enter", "Ctrl+C", ":wq", "q")

    Returns:
        JSON indicating the keypress has been queued.
        Actual execution is handled by sidechat with user approval.
    """
    return json.dumps({
        "action": "keypress",
        "key": keypress,
        "status": "queued"
    })


def capture_terminal(scope: str = "exec") -> str:
    """
    Capture terminal content or screenshot.

    Use this to see the current state of the terminal(s). For TUI applications
    (vim, htop, etc.), this captures a screenshot. For regular command output,
    it captures the text content.

    Args:
        scope: Which terminals to capture:
               - "exec": Only the Exec terminal (default)
               - "all": All visible terminals

    Returns:
        JSON indicating capture has been queued.
        The captured content will be provided in the next context.
    """
    valid_scopes = ["exec", "all"]
    if scope not in valid_scopes:
        scope = "exec"

    return json.dumps({
        "action": "capture",
        "scope": scope,
        "status": "queued"
    })


def refresh_context() -> str:
    """
    Refresh the terminal context before continuing.

    Use this when you need updated terminal content before deciding
    what to do next. This is useful when:
    - You're waiting for a long-running command to complete
    - You want to see the current state without executing anything
    - The terminal content may have changed since your last observation

    Returns:
        JSON indicating refresh has been queued.
        Updated context will be provided in the next message.
    """
    return json.dumps({
        "action": "refresh",
        "status": "queued"
    })


@llm.hookimpl
def register_tools(register):
    """Register sidechat terminal control tools."""
    register(execute_in_terminal)
    register(send_keypress)
    register(capture_terminal)
    register(refresh_context)
