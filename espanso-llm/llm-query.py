#!/usr/bin/env python3
"""Query llm-assistant daemon via Unix socket.

Used by espanso triggers to get AI responses for text expansion.
Uses shared stream_events() from llm-tools-core for daemon communication.
"""

import argparse
import os
import sys

# Import shared utilities from llm_tools_core
from llm_tools_core import (
    ensure_daemon,
    stream_events,
    build_simple_system_prompt,
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

    # Stream events and accumulate text
    text_parts = []
    for event in stream_events(request):
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

    return ''.join(text_parts).strip()


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
