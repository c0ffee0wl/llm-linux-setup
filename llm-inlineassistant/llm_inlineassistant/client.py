"""llm-inlineassistant client - Communicates with the daemon via Unix socket.

Handles:
- Daemon startup if not running
- Query sending with NDJSON streaming response
- Slash commands (/new, /status, etc.)
"""

import json
import os
import socket
import sys
from typing import Iterator, Optional, Tuple

import click
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

# Import shared utilities from llm_tools_core
from llm_tools_core import (
    get_socket_path,
    get_terminal_session_id,
    is_daemon_running,
    start_daemon,
    ensure_daemon,
    DAEMON_STARTUP_TIMEOUT,
    RECV_BUFFER_SIZE,
    REQUEST_TIMEOUT,
)


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

    if command in ('/new', '/reset', '/clear'):
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
                    console.print("[bold]llm-assistant daemon status:[/]")
                    console.print(f"  Model: {status.get('model', 'unknown')}")
                    console.print(f"  Conversation: {status.get('conversation_id', 'none')}")
                    console.print(f"  Messages: {status.get('messages', 0)}")
                    console.print(f"  Tools: {len(status.get('tools', []))}")
                    console.print(f"  Active workers: {status.get('active_workers', 0)}")
                    console.print(f"  Active sessions: {status.get('active_sessions', 0)}")
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
        request = {**request_base, "cmd": "help"}
        for event in send_json_request(request):
            if event.get("type") == "text":
                console.print(event.get('content', ''))
        return True

    elif command.startswith('/copy'):
        # Parse /copy [raw] [all] [N]
        parts = command.split()
        raw_mode = 'raw' in command.lower()
        copy_all = 'all' in command.lower()
        count = 1
        for p in parts[1:]:
            if p.isdigit():
                count = int(p)
                break

        request = {
            **request_base,
            "cmd": "get_responses",
            "count": count,
            "all": copy_all,
        }

        items = []
        for event in send_json_request(request):
            if event.get("type") == "responses":
                items = event.get("items", [])

        if not items:
            console.print("[yellow]No responses to copy[/]")
            return True

        # Format text
        if copy_all:
            texts = []
            for item in items:
                prompt = item.get("prompt", "")
                response = item.get("response", "")
                if prompt:
                    texts.append(f"User: {prompt}\n\nAssistant: {response}")
                else:
                    texts.append(response)
            combined = "\n\n---\n\n".join(texts)
        else:
            combined = "\n\n---\n\n".join(item.get("response", "") for item in items)

        # Strip markdown unless raw mode
        if not raw_mode:
            try:
                from llm_tools_core import strip_markdown
                combined = strip_markdown(combined)
            except ImportError:
                pass  # Keep markdown if library unavailable

        # Copy to clipboard
        try:
            import pyperclip
            pyperclip.copy(combined)
            what = "conversation" if copy_all else f"{len(items)} response(s)"
            mode = "raw markdown" if raw_mode else "plain text"
            console.print(f"[green]Copied {what} to clipboard ({mode})[/]")
        except ImportError:
            console.print("[red]pyperclip not installed. Install with: pip install pyperclip[/]")
        except Exception as e:
            console.print(f"[red]Clipboard error: {e}[/]")

        return True

    return False


def send_query(prompt: str, model: Optional[str] = None, stream: bool = True) -> str:
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
        console.print("[red]ERROR: Could not start llm-assistant daemon[/]")
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
                    live.refresh()  # Force immediate display for fast tools
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


def get_completions(prefix: str, model: Optional[str] = None) -> list:
    """Get completions for a prefix from the daemon.

    Args:
        prefix: Prefix to complete (e.g., '/', '@', 'model:')
        model: Model to use (starts daemon if needed)

    Returns:
        List of completion dicts with 'text' and 'description' keys
    """
    # Ensure daemon is running
    if not ensure_daemon(model):
        return []

    request = {
        "cmd": "complete",
        "prefix": prefix,
    }

    completions = []
    for event in send_json_request(request):
        if event.get("type") == "completions":
            completions = event.get("items", [])
        elif event.get("type") == "done":
            break

    return completions


@click.command()
@click.argument('query_args', nargs=-1, metavar='QUERY')
@click.option('-m', '--model', help='Model to use')
@click.option('--no-stream', is_flag=True, help='Disable streaming')
@click.option('--complete', is_flag=True,
              help='Get completions for prefix (outputs tab-separated list)')
@click.option('--complete-json', is_flag=True,
              help='Get completions as JSON')
def main(
    query_args: Tuple[str, ...],
    model: Optional[str],
    no_stream: bool,
    complete: bool,
    complete_json: bool,
):
    """Inline LLM assistant.

    \b
    Usage: @ <query>  or  llm-inlineassistant <query>
    """
    # Handle completion mode
    if complete or complete_json:
        prefix = ' '.join(query_args) if query_args else ''
        completions = get_completions(prefix, model=model)

        if complete_json:
            print(json.dumps(completions))
        else:
            # Tab-separated output for shell completion
            for item in completions:
                text = item.get('text', '')
                desc = item.get('description', '')
                if desc:
                    print(f"{text}\t{desc}")
                else:
                    print(text)
        return

    if not query_args:
        # Interactive mode hint
        console = Console()
        console.print("[dim]Usage: @ <query>[/]")
        console.print("[dim]       llm-inlineassistant <query>[/]")
        console.print("[dim]       llm-inlineassistant /help[/]")
        return

    prompt = ' '.join(query_args)
    send_query(prompt, model=model, stream=not no_stream)


if __name__ == "__main__":
    main()
