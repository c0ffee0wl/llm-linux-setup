"""llm-inlineassistant client - Communicates with the daemon via Unix socket.

Handles:
- Daemon startup if not running
- Query sending with NDJSON streaming response
- Slash commands (/new, /status, etc.)
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from .utils import get_socket_path, get_terminal_session_id


# Tool display configuration: tool_name -> action_verb (same as llm-assistant)
TOOL_DISPLAY = {
    'execute_python': 'Executing Python',
    'fetch_url': 'Fetching',
    'search_google': 'Searching',
    'load_github': 'Loading repo',
    'load_pdf': 'Extracting PDF',
    'load_yt': 'Loading transcript',
    'prompt_fabric': 'Executing Fabric pattern',
    'suggest_command': 'Preparing command',
}


# Maximum time to wait for daemon startup
DAEMON_STARTUP_TIMEOUT = 5.0
# Socket recv buffer size
RECV_BUFFER_SIZE = 8192
# Request timeout in seconds
REQUEST_TIMEOUT = 120


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


def start_daemon(model: Optional[str] = None) -> bool:
    """Start the daemon in background.

    Returns True if daemon started successfully.
    """
    # Try absolute path first (for espanso and GUI apps)
    daemon_path = Path.home() / ".local" / "bin" / "llm-inlineassistant-daemon"
    if daemon_path.exists():
        cmd = [str(daemon_path)]
    else:
        cmd = ["llm-inlineassistant-daemon"]

    if model:
        cmd.extend(["-m", model])

    try:
        # Start daemon in background
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # Wait for socket to appear
        socket_path = get_socket_path()
        start_time = time.time()

        while time.time() - start_time < DAEMON_STARTUP_TIMEOUT:
            if socket_path.exists() and is_daemon_running():
                return True
            time.sleep(0.1)

        return False

    except FileNotFoundError:
        return False
    except Exception:
        return False


def ensure_daemon(model: Optional[str] = None) -> bool:
    """Ensure daemon is running, starting it if needed.

    Returns True if daemon is available.
    """
    if is_daemon_running():
        return True

    return start_daemon(model)


def send_json_request(request: dict) -> Iterator[dict]:
    """Send JSON request to daemon and yield NDJSON events.

    Args:
        request: JSON request dict

    Yields:
        Event dicts from NDJSON response
    """
    socket_path = get_socket_path()

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(REQUEST_TIMEOUT)
        sock.connect(str(socket_path))

        # Send JSON request
        request_bytes = json.dumps(request).encode('utf-8')
        sock.sendall(request_bytes)
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
                yield {"type": "error", "code": "TIMEOUT", "message": "Request timed out"}
                yield {"type": "done"}
                break

        sock.close()

    except socket.timeout:
        yield {"type": "error", "code": "TIMEOUT", "message": "Connection timed out"}
        yield {"type": "done"}
    except (socket.error, OSError) as e:
        yield {"type": "error", "code": "SOCKET_ERROR", "message": str(e)}
        yield {"type": "done"}


def stream_request(query: str, mode: str = "assistant", system_prompt: str = "") -> Iterator[Tuple[str, str]]:
    """Send query and yield parsed events.

    Args:
        query: Query text
        mode: "assistant" (with tools) or "simple" (no tools)
        system_prompt: Custom system prompt (for simple mode)

    Yields:
        Tuple of (event_type, data)
    """
    terminal_id = get_terminal_session_id()
    session_log = os.environ.get('SESSION_LOG_FILE', '')

    request = {
        "cmd": "query",
        "tid": terminal_id,
        "log": session_log,
        "q": query,
        "mode": mode,
        "sys": system_prompt,
    }

    for event in send_json_request(request):
        event_type = event.get("type", "")
        if event_type == "text":
            yield ("text", event.get("content", ""))
        elif event_type == "tool_start":
            yield ("tool_start", event.get("tool", ""))
        elif event_type == "tool_done":
            yield ("tool_done", event.get("tool", ""))
        elif event_type == "error":
            yield ("error", event.get("message", "Unknown error"))
        elif event_type == "done":
            return


def handle_slash_command(command: str, terminal_id: str) -> bool:
    """Handle slash commands.

    Returns True if command was handled (don't send as query).
    """
    console = Console()

    request_base = {
        "tid": terminal_id,
    }

    if command in ('/new', '/reset'):
        request = {**request_base, "cmd": "new"}
        for event in send_json_request(request):
            if event.get("type") == "text":
                console.print(f"[green]{event.get('content', '')}[/]")
        return True

    elif command in ('/status', '/info'):
        request = {**request_base, "cmd": "status"}
        for event in send_json_request(request):
            if event.get("type") == "text":
                content = event.get('content', '')
                try:
                    status = json.loads(content)
                    console.print("[bold]llm-inlineassistant status:[/]")
                    console.print(f"  Model: {status.get('model', 'unknown')}")
                    console.print(f"  Conversation: {status.get('conversation_id', 'none')}")
                    console.print(f"  Messages: {status.get('messages', 0)}")
                    console.print(f"  Tools: {len(status.get('tools', []))}")
                    console.print(f"  Active workers: {status.get('active_workers', 0)}")
                except json.JSONDecodeError:
                    console.print(content)
        return True

    elif command == '/quit' or command == '/exit':
        request = {**request_base, "cmd": "shutdown"}
        for event in send_json_request(request):
            if event.get("type") == "text":
                console.print(f"[dim]{event.get('content', '')}[/]")
        return True

    elif command == '/help':
        console.print("[bold]llm-inlineassistant commands:[/]")
        console.print("  /new, /reset  - Start new conversation")
        console.print("  /status       - Show session info")
        console.print("  /quit         - Shutdown daemon")
        console.print("  /help         - Show this help")
        return True

    return False


def query(prompt: str, model: Optional[str] = None, stream: bool = True) -> str:
    """Send a query to llm-inlineassistant daemon.

    Args:
        prompt: The user's query
        model: Model to use (uses default if not specified)
        stream: Whether to stream output (default True)

    Returns:
        The response text
    """
    console = Console()
    terminal_id = get_terminal_session_id()

    # Ensure daemon is running
    if not ensure_daemon(model):
        console.print("[red]ERROR: Could not start llm-inlineassistant daemon[/]")
        return ""

    # Check for slash commands
    if prompt.startswith('/'):
        if handle_slash_command(prompt.strip(), terminal_id):
            return ""

    # Send query and stream response with real-time markdown rendering
    if stream:
        accumulated_text = ""
        active_tool = None
        error_message = None

        with Live(refresh_per_second=10) as live:
            for event_type, data in stream_request(prompt):
                if event_type == "text":
                    accumulated_text += data
                elif event_type == "tool_start":
                    active_tool = data
                elif event_type == "tool_done":
                    active_tool = None
                elif event_type == "error":
                    error_message = data

                # Update display
                if active_tool:
                    action = TOOL_DISPLAY.get(active_tool, f'Executing {active_tool}')
                    spinner_text = Text(f"{action}...", style="cyan")
                    live.update(Group(
                        Markdown(accumulated_text),
                        Spinner("dots", text=spinner_text, style="cyan")
                    ))
                else:
                    live.update(Markdown(accumulated_text))

        # Show error if any
        if error_message:
            console.print(f"[red]Error: {error_message}[/]")

        return accumulated_text

    else:
        # Non-streaming mode: collect all text
        accumulated_text = ""
        for event_type, data in stream_request(prompt):
            if event_type == "text":
                accumulated_text += data
            elif event_type == "error":
                console.print(f"[red]Error: {data}[/]")

        if accumulated_text:
            console.print(Markdown(accumulated_text))

        return accumulated_text


def main():
    """CLI entry point for llm-inlineassistant client."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Inline AI assistant",
        usage="@ <query>  or  llm-inlineassistant <query>"
    )
    parser.add_argument("query", nargs="*", help="Query to send")
    parser.add_argument("-m", "--model", help="Model to use")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming")

    args = parser.parse_args()

    if not args.query:
        # Interactive mode hint
        console = Console()
        console.print("[dim]Usage: @ <query>[/]")
        console.print("[dim]       llm-inlineassistant <query>[/]")
        console.print("[dim]       llm-inlineassistant /help[/]")
        return

    prompt = ' '.join(args.query)
    query(prompt, model=args.model, stream=not args.no_stream)


if __name__ == "__main__":
    main()
