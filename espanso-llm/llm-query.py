#!/usr/bin/env python3
"""Query llm-inlineassistant daemon via Unix socket.

Used by espanso triggers to get AI responses for text expansion.
Communicates directly with the daemon - no dependency on llm-server.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def build_simple_system_prompt() -> str:
    """Build the system prompt for simple mode with current date/time.

    Returns:
        str: The system prompt with current date/time context.
    """
    now = datetime.now()
    context = (
        f"Current date: {now.strftime('%Y-%m-%d')}\n"
        f"Current time: {now.strftime('%H:%M')}"
    )

    return (
        "You are operating in a non-interactive mode.\n"
        "Do NOT use introductory phrases, greetings, or opening messages.\n"
        "You CANNOT ask the user for clarification, additional details, or preferences.\n"
        "When given a request, make reasonable assumptions based on the context and provide a complete, helpful response immediately.\n"
        "If a request is ambiguous, choose the most common or logical interpretation and proceed accordingly.\n"
        "Always deliver a substantive response rather than asking questions.\n"
        "NEVER ask the user for follow-up questions or clarifications.\n\n"
        f"Context:\n{context}"
    )


# Socket path
def get_socket_path() -> Path:
    uid = os.getuid()
    return Path(f"/tmp/llm-inlineassistant-{uid}/daemon.sock")


# Daemon startup timeout
STARTUP_TIMEOUT = 5.0
# Request timeout
REQUEST_TIMEOUT = 120
# Buffer size
RECV_BUFFER_SIZE = 8192


def is_daemon_running() -> bool:
    """Check if daemon is running by testing socket."""
    socket_path = get_socket_path()
    if not socket_path.exists():
        return False

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(str(socket_path))
        sock.close()
        return True
    except (socket.error, OSError):
        return False


def ensure_daemon_running() -> bool:
    """Ensure daemon is running, starting it if needed."""
    if is_daemon_running():
        return True

    # Use absolute path (espanso's PATH may not include ~/.local/bin)
    daemon_path = Path.home() / ".local" / "bin" / "llm-inlineassistant-daemon"
    if not daemon_path.exists():
        # Fallback to PATH lookup
        daemon_path = "llm-inlineassistant-daemon"
    else:
        daemon_path = str(daemon_path)

    try:
        subprocess.Popen(
            [daemon_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # Wait for socket with timeout
        start_time = time.time()
        while time.time() - start_time < STARTUP_TIMEOUT:
            if is_daemon_running():
                return True
            time.sleep(0.1)

        return False
    except Exception:
        return False


def query_daemon(prompt: str, mode: str = "assistant") -> str:
    """Send query to daemon and return response text.

    Args:
        prompt: The query text
        mode: "assistant" (with tools) or "simple" (no tools)

    Returns:
        Response text from the LLM
    """
    if not ensure_daemon_running():
        return "Error: Could not start llm-inlineassistant daemon"

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(REQUEST_TIMEOUT)
        sock.connect(str(get_socket_path()))

        # Build request
        request = {
            "cmd": "query",
            "tid": f"espanso:{os.getpid()}",
            "q": prompt,
            "mode": mode,
        }

        # Add system prompt for simple mode (with current date/time)
        if mode == "simple":
            request["sys"] = build_simple_system_prompt()

        # Send request
        sock.sendall(json.dumps(request).encode('utf-8'))
        sock.shutdown(socket.SHUT_WR)

        # Parse NDJSON response, extract only text content
        text_parts = []
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
                        event_type = event.get("type", "")

                        if event_type == "text":
                            text_parts.append(event.get("content", ""))
                        elif event_type == "error":
                            code = event.get("code", "UNKNOWN")
                            message = event.get("message", "Unknown error")
                            return f"Error ({code}): {message}"
                        elif event_type == "done":
                            break
                        # Ignore tool_start, tool_done for espanso output

                    except json.JSONDecodeError:
                        continue

            except socket.timeout:
                return "Error: Request timed out"

        sock.close()
        return ''.join(text_parts).strip()

    except socket.error as e:
        return f"Error: Socket error - {e}"
    except Exception as e:
        return f"Error: {e}"


def main():
    parser = argparse.ArgumentParser(description="Query llm-inlineassistant daemon")
    parser.add_argument("query", nargs="+", help="Query text")
    parser.add_argument(
        "--mode",
        choices=["simple", "assistant"],
        default="simple",
        help="Mode: simple (no tools) or assistant (with tools)"
    )

    args = parser.parse_args()
    prompt = ' '.join(args.query)

    if not prompt.strip():
        print("Error: Empty query")
        sys.exit(1)

    result = query_daemon(prompt, args.mode)
    print(result)


if __name__ == "__main__":
    main()
