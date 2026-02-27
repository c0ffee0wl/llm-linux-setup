"""llm-inlineassistant client - Communicates with the daemon via Unix socket.

Handles:
- Daemon startup if not running
- Query sending with NDJSON streaming response
- Slash commands (/new, /status, etc.)
"""

import json
import os
import sys
import unicodedata
from typing import Iterator, Optional, Tuple

import click
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

# Import shared utilities from llm_tools_core
from llm_tools_core import (
    get_terminal_session_id,
    ensure_daemon,
    stream_events,
    get_action_verb,
    RAGHandler,
    strip_markdown,
)


def _strip_trailing_artifacts(text: str) -> str:
    """Remove isolated non-Latin characters that models sometimes emit at end.

    Some LLMs (especially multilingual ones) emit stray CJK or other Unicode
    characters as trailing artifacts. This strips them when they appear as
    isolated characters on the final line.
    """
    stripped = text.rstrip()
    if not stripped:
        return stripped
    # Check if the last line is a single isolated character from a different script
    lines = stripped.rsplit('\n', 1)
    if len(lines) == 2 and len(lines[1].strip()) <= 2:
        last_chars = lines[1].strip()
        if last_chars and all(
            unicodedata.category(c).startswith('Lo') for c in last_chars
        ):
            # Single "Other Letter" (CJK, etc.) on its own line - likely artifact
            return lines[0].rstrip()
    return stripped


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

    for event in stream_events(request):
        event_type = event.get("type", "")
        if event_type == "text":
            yield ("text", event.get("content", ""))
        elif event_type == "tool_start":
            yield ("tool_start", event.get("tool", ""))
        elif event_type == "tool_done":
            yield ("tool_done", event.get("tool", ""))
        elif event_type == "error":
            code = event.get("code", "UNKNOWN")
            message = event.get("message", "Unknown error")
            yield ("error", f"{message} ({code})")
        elif event_type == "done":
            return


def handle_rag_command(command: str, terminal_id: str, console: Console) -> bool:
    """Handle /rag commands.

    Syntax:
        /rag                 - List collections
        /rag list            - List collections
        /rag search <coll> <query>  - Search collection
        /rag <collection>    - Activate collection for session
    """
    parts = command.split(maxsplit=2)

    # /rag or /rag list - list collections
    if len(parts) == 1 or (len(parts) == 2 and parts[1] == 'list'):
        handler = RAGHandler()
        if not handler.available():
            console.print("[yellow]RAG not available (llm-tools-rag not installed)[/]")
            return True

        collections = handler.list_collections()
        if not collections:
            console.print("[dim]No RAG collections found[/]")
            console.print("[dim]Add documents with: /rag add <collection> <path>[/]")
            return True

        console.print("[bold]RAG Collections:[/]")
        for coll in collections:
            name = coll.get('name', 'unknown')
            count = coll.get('count', 0)
            console.print(f"  {name}: {count} documents")
        return True

    subcommand = parts[1]

    # /rag search <collection> <query>
    if subcommand == 'search':
        if len(parts) < 3:
            console.print("[yellow]Usage: /rag search <collection> <query>[/]")
            return True

        # Parse collection and query from remaining args
        rest = parts[2]
        search_parts = rest.split(maxsplit=1)
        if len(search_parts) < 2:
            console.print("[yellow]Usage: /rag search <collection> <query>[/]")
            return True

        collection = search_parts[0]
        query = search_parts[1]

        handler = RAGHandler()
        if not handler.available():
            console.print("[yellow]RAG not available[/]")
            return True

        results = handler.search(collection, query, top_k=5)
        if not results:
            console.print(f"[dim]No results found in '{collection}'[/]")
            return True

        console.print(f"[bold]Search results from '{collection}':[/]")
        for i, result in enumerate(results, 1):
            score = result.score
            content = result.content
            source = result.source or 'unknown'
            # Truncate content for display
            preview = content[:200] + '...' if len(content) > 200 else content
            console.print(f"\n[cyan]{i}. Score: {score:.3f} | Source: {source}[/]")
            console.print(preview)
        return True

    # /rag add <collection> <path>
    if subcommand == 'add':
        if len(parts) < 3:
            console.print("[yellow]Usage: /rag add <collection> <path>[/]")
            return True

        rest = parts[2]
        add_parts = rest.split(maxsplit=1)
        if len(add_parts) < 2:
            console.print("[yellow]Usage: /rag add <collection> <path>[/]")
            return True

        collection = add_parts[0]
        path = add_parts[1]

        handler = RAGHandler()
        if not handler.available():
            console.print("[yellow]RAG not available[/]")
            return True

        result = handler.add_documents(collection, path)
        if result.status == "success":
            console.print(f"[green]Added {result.chunks} chunks to '{collection}'[/]")
        else:
            console.print(f"[red]Failed to add documents: {result.error}[/]")
        return True

    # /rag <collection> - activate for session
    # Send to daemon to activate RAG for this terminal's session
    collection = subcommand
    request = {
        "cmd": "rag_activate",
        "tid": terminal_id,
        "collection": collection,
    }

    activated = False
    for event in stream_events(request):
        if event.get("type") == "text":
            console.print(event.get('content', ''))
            activated = True
        elif event.get("type") == "error":
            console.print(f"[red]{event.get('message', 'Unknown error')}[/]")

    if not activated:
        console.print(f"[green]RAG collection '{collection}' activated for this session[/]")

    return True


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
        for event in stream_events(request):
            if event.get("type") == "text":
                console.print(f"[green]{event.get('content', '')}[/]")
        return True

    elif command in ('/status', '/info'):
        request = {**request_base, "cmd": "status"}
        for event in stream_events(request):
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
        for event in stream_events(request):
            if event.get("type") == "text":
                console.print(f"[dim]{event.get('content', '')}[/]")
        return True

    elif command == '/help':
        request = {**request_base, "cmd": "help"}
        for event in stream_events(request):
            if event.get("type") == "text":
                console.print(event.get('content', ''))
        return True

    elif command.startswith('/rag'):
        return handle_rag_command(command, terminal_id, console)

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
        for event in stream_events(request):
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
            combined = strip_markdown(combined)

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

        # Stream with Live display
        # Start transient=True (clear on exit if no content), switch to False once content arrives
        with Live(Markdown(""), console=console, refresh_per_second=10, transient=True) as live:
            for event_type, data in stream_request(prompt):
                if event_type == "text":
                    accumulated_text += data
                    # Once we have content, make it persistent (don't clear on exit)
                    if accumulated_text.strip():
                        live.transient = False
                elif event_type == "tool_start":
                    active_tool = data
                elif event_type == "tool_done":
                    active_tool = None
                elif event_type == "error":
                    error_message = data

                # Update display
                if active_tool:
                    action = get_action_verb(active_tool)
                    spinner_text = Text(f"{action}...", style="cyan")
                    live.update(Group(
                        Markdown(accumulated_text) if accumulated_text else Text(""),
                        Spinner("dots", text=spinner_text, style="cyan")
                    ))
                    live.refresh()  # Force immediate display for fast tools
                elif accumulated_text:
                    live.update(Markdown(accumulated_text))

            # Strip trailing model artifacts (stray CJK/Unicode tokens)
            accumulated_text = _strip_trailing_artifacts(accumulated_text)

            # Final update to ensure the last chunk is fully rendered before Live exits
            if accumulated_text:
                live.update(Markdown(accumulated_text))

        # Eat the extra trailing blank line that Rich Markdown rendering adds
        if accumulated_text:
            sys.stdout.write("\033[A")
            sys.stdout.flush()

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
            console.print()  # Add empty line after response

        return accumulated_text


def get_completions(prefix: str, model: Optional[str] = None, cwd: Optional[str] = None) -> list:
    """Get completions for a prefix from the daemon.

    Args:
        prefix: Prefix to complete (e.g., '/', '@', 'model:')
        model: Model to use (starts daemon if needed)
        cwd: Working directory for file completions

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

    # Include cwd for @ completions that need file paths
    if cwd:
        request["cwd"] = cwd
    elif prefix.startswith('@'):
        # Default to current directory for @ completions
        request["cwd"] = os.getcwd()

    completions = []
    for event in stream_events(request):
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
@click.option('--stdin', 'from_stdin', is_flag=True,
              help='Read query from stdin (avoids shell quoting issues)')
def main(
    query_args: Tuple[str, ...],
    model: Optional[str],
    no_stream: bool,
    complete: bool,
    complete_json: bool,
    from_stdin: bool,
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

    # Read query from stdin if --stdin flag is set
    if from_stdin:
        import sys
        prompt = sys.stdin.read()
        if not prompt.strip():
            return
        send_query(prompt, model=model, stream=not no_stream)
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
