#!/usr/bin/env python3
"""Query llm-assistant daemon via Unix socket.

Used by espanso triggers to get AI responses for text expansion.
Communicates directly with the daemon - no dependency on llm-server.
"""

import argparse
import json
import os
import socket
import sys

# Import shared utilities from llm_tools_core
from llm_tools_core import (
    get_socket_path,
    is_daemon_running,
    ensure_daemon,
    build_simple_system_prompt,
    REQUEST_TIMEOUT,
    RECV_BUFFER_SIZE,
)


def query_daemon(prompt: str, mode: str = "assistant") -> str:
    """Send query to daemon and return response text.

    Args:
        prompt: The query text
        mode: "assistant" (with tools) or "simple" (no tools)

    Returns:
        Response text from the LLM
    """
    if not ensure_daemon():
        return "Error: Could not start llm-assistant daemon"

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
    parser = argparse.ArgumentParser(description="Query llm-assistant daemon")
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
