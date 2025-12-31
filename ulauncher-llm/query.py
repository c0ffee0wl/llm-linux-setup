"""Synchronous query functions for ulauncher-llm.

Provides blocking query functions that communicate with the llm-assistant daemon.
Uses blocking calls like ulauncher-gemini-direct for simplicity and reliability.
No threading - all operations block until complete, then return the full response.
"""

import json
import socket
from typing import Iterator, Tuple, Optional

from daemon_client import (
    ensure_daemon,
    get_socket_path,
    RECV_BUFFER_SIZE,
    REQUEST_TIMEOUT,
)


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


def query_daemon_sync(query: str, mode: str, session_id: str) -> Tuple[str, Optional[str]]:
    """Query daemon synchronously, blocking until response is complete.

    Like ulauncher-gemini-direct, this blocks the main thread.
    This is simpler and more reliable than threading with GLib.idle_add.

    Args:
        query: User's query text
        mode: "simple" or "assistant"
        session_id: Session ID for conversation continuity

    Returns:
        Tuple of (response_text, error_message).
        If successful, error_message is None.
        If failed, response_text is empty.
    """
    # Ensure daemon is running
    if not ensure_daemon():
        return "", "Could not start llm-assistant daemon"

    request = {
        "cmd": "query",
        "tid": session_id,
        "q": query,
        "mode": mode,
    }

    accumulated_text = ""
    error_msg = None

    for event in stream_events(request):
        event_type = event.get("type", "")
        if event_type == "text":
            accumulated_text += event.get("content", "")
        elif event_type == "error":
            error_msg = event.get("message", "Unknown error")

    if error_msg:
        return "", error_msg

    return accumulated_text, None


def stream_events(request: dict) -> Iterator[dict]:
    """Send JSON request to daemon and yield NDJSON events.

    Generator pattern ensures clean exit when "done" is received.

    Args:
        request: JSON request dict

    Yields:
        Event dicts from NDJSON response
    """
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(REQUEST_TIMEOUT)
        sock.connect(str(get_socket_path()))

        # Send JSON request
        sock.sendall(json.dumps(request).encode('utf-8'))
        sock.shutdown(socket.SHUT_WR)

        # Parse NDJSON response
        buffer = ""
        while True:
            try:
                chunk = sock.recv(RECV_BUFFER_SIZE)
                if not chunk:
                    break
                buffer += chunk.decode('utf-8')

                # Process complete lines
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        yield event
                        if event.get('type') == 'done':
                            sock.close()
                            return
                    except json.JSONDecodeError:
                        continue

            except socket.timeout:
                yield {"type": "error", "message": "Request timed out"}
                yield {"type": "done"}
                break

        sock.close()

    except socket.timeout:
        yield {"type": "error", "message": "Connection timed out"}
        yield {"type": "done"}
    except ConnectionRefusedError:
        yield {"type": "error", "message": "Daemon not running. Start with: llm-assistant --daemon"}
        yield {"type": "done"}
    except Exception as e:
        yield {"type": "error", "message": str(e)}
        yield {"type": "done"}
