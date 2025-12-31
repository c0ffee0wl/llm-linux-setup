"""Synchronous query functions for ulauncher-llm.

Provides blocking query functions that communicate with the llm-assistant daemon.
Uses blocking calls like ulauncher-gemini-direct for simplicity and reliability.
No threading - all operations block until complete, then return the full response.
"""

from typing import Tuple, Optional, List

from llm_tools_core import (
    ensure_daemon,
    stream_events,
)


# Friendly names for tools (used in UI feedback)
TOOL_DISPLAY_NAMES = {
    "execute_python": "Python",
    "suggest_command": "Command",
    "sandboxed_shell": "Shell",
    "search_google": "Google Search",
    "context": "Context",
    "read_file": "Read File",
    "write_file": "Write File",
    "edit_file": "Edit File",
    "multi_edit_file": "Multi Edit",
    "web_fetch": "Web Fetch",
}


def execute_slash_command_sync(
    command: str,
    session_id: str,
    model_arg: Optional[str] = None
) -> Tuple[str, str]:
    """Execute a slash command synchronously.

    Args:
        command: The command ("new", "status", "model")
        session_id: Session ID for the request
        model_arg: Optional model name for /model command

    Returns:
        Tuple of (title, message) for display
    """
    if not ensure_daemon():
        return "Error", "Could not connect to daemon"

    # Model command is sent as a query with /model prefix
    if command == "model":
        if model_arg:
            query = f"/model {model_arg}"
        else:
            query = "/model"
        request = {
            "cmd": "query",
            "tid": session_id,
            "q": query,
            "mode": "assistant"
        }
    else:
        request = {
            "cmd": command,
            "tid": session_id,
        }

    # Collect response
    response_text = ""
    error_text = ""
    for event in stream_events(request):
        event_type = event.get("type", "")
        if event_type == "text":
            response_text += event.get("content", "")
        elif event_type == "error":
            error_text = event.get("message", "Unknown error")

    # Format result
    if error_text:
        return "Error", error_text
    elif command == "new":
        return "Conversation Reset", response_text or "Started fresh conversation"
    elif command == "status":
        return "Session Status", response_text or "No status available"
    elif command == "model":
        if model_arg:
            return f"Model: {model_arg}", response_text or "Model switched"
        else:
            return "Available Models", response_text or "No models available"

    return "Done", response_text or ""


def query_daemon_sync(
    query: str, mode: str, session_id: str
) -> Tuple[str, Optional[str], List[str]]:
    """Query daemon synchronously, blocking until response is complete.

    Like ulauncher-gemini-direct, this blocks the main thread.
    This is simpler and more reliable than threading with GLib.idle_add.

    Args:
        query: User's query text
        mode: "simple" or "assistant"
        session_id: Session ID for conversation continuity

    Returns:
        Tuple of (response_text, error_message, tools_used).
        If successful, error_message is None.
        If failed, response_text is empty.
        tools_used contains friendly names of tools that were executed.
    """
    # Ensure daemon is running
    if not ensure_daemon():
        return "", "Could not start llm-assistant daemon", []

    request = {
        "cmd": "query",
        "tid": session_id,
        "q": query,
        "mode": mode,
    }

    accumulated_text = ""
    error_msg = None
    tools_used = []

    for event in stream_events(request):
        event_type = event.get("type", "")
        if event_type == "text":
            accumulated_text += event.get("content", "")
        elif event_type == "tool_start":
            # Track tool execution
            tool_name = event.get("tool", "")
            friendly_name = TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
            if friendly_name and friendly_name not in tools_used:
                tools_used.append(friendly_name)
        elif event_type == "error":
            error_msg = event.get("message", "Unknown error")

    if error_msg:
        return "", error_msg, tools_used

    return accumulated_text, None, tools_used
