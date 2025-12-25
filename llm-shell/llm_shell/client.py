"""llm-shell client - Communicates with the daemon via Unix socket.

Handles:
- Daemon startup if not running
- Query sending with streaming response
- Slash commands (/new, /status, etc.)
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import re

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

# Protocol markers (STX...ETX)
TOOL_START_PATTERN = re.compile(r'\x02TOOL:([^\x03]+)\x03')
TOOL_DONE_MARKER = '\x02TOOL_DONE\x03'


# Maximum time to wait for daemon startup
DAEMON_STARTUP_TIMEOUT = 5.0


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
    cmd = ["llm-shell-daemon"]
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


def send_request(command: str, terminal_id: str, data: str = "") -> Optional[str]:
    """Send request to daemon and return response.

    For streaming responses, yields chunks instead.
    """
    socket_path = get_socket_path()

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(120)  # 2 minute timeout for long responses
        sock.connect(str(socket_path))

        # Send request
        request = f"{command}:{terminal_id}:{data}"
        sock.sendall(request.encode())
        sock.shutdown(socket.SHUT_WR)

        # Read response
        response = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response.append(chunk.decode('utf-8'))

        sock.close()
        return ''.join(response)

    except socket.timeout:
        return "ERROR: Request timed out"
    except (socket.error, OSError) as e:
        return f"ERROR: Socket error: {e}"


def stream_request(command: str, terminal_id: str, data: str = ""):
    """Send request and stream response chunks."""
    socket_path = get_socket_path()

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(120)
        sock.connect(str(socket_path))

        # Send request
        request = f"{command}:{terminal_id}:{data}"
        sock.sendall(request.encode())
        sock.shutdown(socket.SHUT_WR)

        # Stream response
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            yield chunk.decode('utf-8')

        sock.close()

    except socket.timeout:
        yield "\nERROR: Request timed out\n"
    except (socket.error, OSError) as e:
        yield f"\nERROR: Socket error: {e}\n"


def handle_slash_command(command: str, terminal_id: str) -> bool:
    """Handle slash commands.

    Returns True if command was handled (don't send as query).
    """
    console = Console()

    if command in ('/new', '/reset'):
        response = send_request("new", terminal_id)
        console.print(f"[green]{response.strip()}[/]")
        return True

    elif command in ('/status', '/info'):
        response = send_request("status", terminal_id)
        try:
            import json
            status = json.loads(response)
            console.print("[bold]llm-shell status:[/]")
            console.print(f"  Model: {status.get('model', 'unknown')}")
            console.print(f"  Conversation: {status.get('conversation_id', 'none')}")
            console.print(f"  Messages: {status.get('messages', 0)}")
        except json.JSONDecodeError:
            console.print(response)
        return True

    elif command == '/quit' or command == '/exit':
        response = send_request("shutdown", terminal_id)
        console.print(f"[dim]{response.strip()}[/]")
        return True

    elif command == '/help':
        console.print("[bold]llm-shell commands:[/]")
        console.print("  /new, /reset  - Start new conversation")
        console.print("  /status       - Show session info")
        console.print("  /quit         - Shutdown daemon")
        console.print("  /help         - Show this help")
        return True

    return False


def query(prompt: str, model: Optional[str] = None, stream: bool = True) -> str:
    """Send a query to llm-shell daemon.

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
        console.print("[red]ERROR: Could not start llm-shell daemon[/]")
        return ""

    # Check for slash commands
    if prompt.startswith('/'):
        if handle_slash_command(prompt.strip(), terminal_id):
            return ""

    # Send query and stream response with real-time markdown rendering
    if stream:
        accumulated_text = ""
        active_tool = None  # Currently executing tool name

        with Live(refresh_per_second=10) as live:
            for chunk in stream_request("query", terminal_id, prompt):
                # Check for tool start marker
                tool_match = TOOL_START_PATTERN.search(chunk)
                if tool_match:
                    active_tool = tool_match.group(1)
                    # Remove marker from chunk
                    chunk = TOOL_START_PATTERN.sub('', chunk)

                # Check for tool done marker
                if TOOL_DONE_MARKER in chunk:
                    active_tool = None
                    # Remove marker from chunk
                    chunk = chunk.replace(TOOL_DONE_MARKER, '')

                # Add remaining text to accumulated
                if chunk:
                    accumulated_text += chunk

                # Update display with optional spinner
                if active_tool:
                    action = TOOL_DISPLAY.get(active_tool, f'Executing {active_tool}')
                    spinner_text = Text(f"{action}...", style="cyan")
                    live.update(Group(
                        Markdown(accumulated_text),
                        Spinner("dots", text=spinner_text, style="cyan")
                    ))
                else:
                    live.update(Markdown(accumulated_text))

        # Check for error after streaming completes
        if accumulated_text.startswith("ERROR:"):
            console.print(accumulated_text.strip())

        return accumulated_text
    else:
        response_text = send_request("query", terminal_id, prompt)
        # Render as markdown if we have content
        if response_text and not response_text.startswith("ERROR:"):
            console.print(Markdown(response_text))
        else:
            console.print(response_text)
        return response_text


def main():
    """CLI entry point for llm-shell client."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Shell-native AI assistant",
        usage="@ <query>  or  llm-shell <query>"
    )
    parser.add_argument("query", nargs="*", help="Query to send")
    parser.add_argument("-m", "--model", help="Model to use")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming")

    args = parser.parse_args()

    if not args.query:
        # Interactive mode hint
        console = Console()
        console.print("[dim]Usage: @ <query>[/]")
        console.print("[dim]       llm-shell <query>[/]")
        console.print("[dim]       llm-shell /help[/]")
        return

    prompt = ' '.join(args.query)
    query(prompt, model=args.model, stream=not args.no_stream)


if __name__ == "__main__":
    main()
