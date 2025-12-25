"""llm-shell daemon - Unix socket server for fast startup.

Keeps a warm Python process with llm loaded, handling queries
from shell clients with <100ms response time after first call.

Architecture:
- Listens on Unix socket at /tmp/llm-shell-{UID}.sock
- Maintains per-terminal conversation state
- Auto-terminates after 30 minutes idle
- Streams responses back to client
- Supports tools: execute_python, fetch_url, search_google
"""

import asyncio
import json
import os
import platform
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

import jinja2
import llm
import sqlite_utils
from llm import Tool, ToolResult
from llm.migrations import migrate
from rich.console import Console

from .utils import (
    get_socket_path,
    get_logs_db_path,
    get_active_conversation,
    save_active_conversation,
    logs_on,
    write_suggested_command,
)
from .context_capture import capture_shell_context, format_context_for_prompt


# External plugin tools to expose to the model
SHELL_TOOL_NAMES = (
    'execute_python',    # Python in sandbox
    'fetch_url',         # Web content
    'search_google',     # Google search
    'load_github',       # GitHub content
    'load_pdf',          # PDF extraction
    'load_yt',           # YouTube transcripts
    'prompt_fabric',     # AI patterns
)


def _suggest_command_impl(command: str) -> str:
    """Implementation for suggest_command tool."""
    write_suggested_command(command)
    return f"Command ready. User presses Ctrl+G to apply: {command}"


# Built-in suggest_command tool
SUGGEST_COMMAND_TOOL = Tool(
    name="suggest_command",
    description="""Suggest a shell command by placing it on the user's command line.

The command is saved for the user to apply. After your response, the user
presses Ctrl+G to place the command on their prompt, where they can review,
edit, and execute it by pressing Enter.

IMPORTANT: This does NOT execute the command. The user must press Ctrl+G
to apply, then Enter to execute. Use this when the user asks for a command
or when you want to propose a command for them to run.

Args:
    command: The shell command to suggest

Returns:
    Confirmation that command is ready (user presses Ctrl+G to apply)
""",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to place on the user's prompt"
            }
        },
        "required": ["command"]
    },
    implementation=_suggest_command_impl,
)


def get_shell_tools() -> List[Tool]:
    """Get the tools available to llm-shell.

    Returns list of Tool objects including built-in and plugin tools.
    Plugin tools that aren't installed are silently skipped.
    """
    tools = [SUGGEST_COMMAND_TOOL]  # Built-in tool always available

    all_tools = llm.get_tools()
    for name in SHELL_TOOL_NAMES:
        if name in all_tools:
            tool = all_tools[name]
            if isinstance(tool, Tool):
                tools.append(tool)
    return tools


def get_tool_implementations() -> Dict[str, callable]:
    """Get tool implementations for auto-dispatch.

    Returns dict of tool_name -> implementation function.
    """
    # Built-in tools
    implementations = {
        'suggest_command': _suggest_command_impl,
    }

    # Plugin tools
    all_tools = llm.get_tools()
    for name in SHELL_TOOL_NAMES:
        if name in all_tools:
            tool = all_tools[name]
            if isinstance(tool, Tool) and hasattr(tool, 'implementation') and tool.implementation:
                implementations[name] = tool.implementation
    return implementations


def get_system_prompt() -> str:
    """Load and render the system prompt template."""
    template_dir = Path(__file__).parent / "templates"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_dir),
        undefined=jinja2.StrictUndefined,
    )
    template = env.get_template("system_prompt.j2")

    return template.render(
        date=datetime.now().strftime("%Y-%m-%d"),
        platform=platform.system(),
        shell=os.environ.get("SHELL", "/bin/bash"),
    )


# Idle timeout before daemon auto-terminates
IDLE_TIMEOUT_MINUTES = 30


class ConversationState:
    """State for a single terminal's conversation."""

    def __init__(self, terminal_id: str, model: llm.Model, system_prompt: str):
        self.terminal_id = terminal_id
        self.model = model
        self.system_prompt = system_prompt
        self.conversation: Optional[llm.Conversation] = None
        self.context_hashes: Set[str] = set()
        self.last_activity = datetime.now()

    def get_or_create_conversation(self) -> llm.Conversation:
        """Get existing conversation or create new one."""
        if self.conversation is None:
            # Check for saved conversation ID
            saved_cid = get_active_conversation(self.terminal_id)
            if saved_cid:
                try:
                    from llm.cli import load_conversation
                    loaded = load_conversation(saved_cid, database=str(get_logs_db_path()))
                    if loaded:
                        self.conversation = loaded
                        # Update model reference if conversation was loaded
                        self.model = loaded.model
                except Exception:
                    # Conversation not found or load failed, create new
                    pass

            if self.conversation is None:
                self.conversation = llm.Conversation(model=self.model)

            # Save conversation ID for resume
            if self.conversation.id:
                save_active_conversation(self.terminal_id, self.conversation.id)

        self.last_activity = datetime.now()
        return self.conversation

    def reset(self) -> None:
        """Start a new conversation."""
        self.conversation = llm.Conversation(model=self.model)
        self.context_hashes.clear()
        if self.conversation.id:
            save_active_conversation(self.terminal_id, self.conversation.id)
        self.last_activity = datetime.now()


class ShellDaemon:
    """Unix socket server for llm-shell."""

    def __init__(self, model_id: Optional[str] = None):
        self.socket_path = get_socket_path()
        self.model = llm.get_model(model_id) if model_id else llm.get_model()
        self.system_prompt = get_system_prompt()
        self.conversations: Dict[str, ConversationState] = {}
        self.server: Optional[asyncio.AbstractServer] = None
        self.last_activity = datetime.now()
        self.running = True
        self.console = Console(stderr=True)

    def get_conversation_state(self, terminal_id: str) -> ConversationState:
        """Get or create conversation state for terminal."""
        if terminal_id not in self.conversations:
            self.conversations[terminal_id] = ConversationState(
                terminal_id, self.model, self.system_prompt
            )
        return self.conversations[terminal_id]

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a client connection."""
        try:
            # Read the request
            data = await asyncio.wait_for(reader.read(65536), timeout=5.0)
            if not data:
                return

            request = data.decode('utf-8').strip()
            self.last_activity = datetime.now()

            # Parse request: "command:terminal_id:query"
            parts = request.split(':', 2)
            if len(parts) < 2:
                writer.write(b"ERROR: Invalid request format\n")
                await writer.drain()
                return

            command = parts[0]
            terminal_id = parts[1]
            query = parts[2] if len(parts) > 2 else ""

            # Handle commands
            if command == "query":
                await self.handle_query(terminal_id, query, writer)
            elif command == "new":
                await self.handle_new(terminal_id, writer)
            elif command == "status":
                await self.handle_status(terminal_id, writer)
            elif command == "shutdown":
                await self.handle_shutdown(writer)
            else:
                writer.write(f"ERROR: Unknown command: {command}\n".encode())
                await writer.drain()

        except asyncio.TimeoutError:
            writer.write(b"ERROR: Request timeout\n")
            await writer.drain()
        except Exception as e:
            writer.write(f"ERROR: {e}\n".encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def handle_query(self, terminal_id: str, query: str, writer: asyncio.StreamWriter):
        """Handle a query request with tool support."""
        if not query.strip():
            writer.write(b"ERROR: Empty query\n")
            await writer.drain()
            return

        state = self.get_conversation_state(terminal_id)
        conversation = state.get_or_create_conversation()

        # Capture context with deduplication
        context, state.context_hashes = capture_shell_context(state.context_hashes)

        # Build prompt with context
        if context and context != "[Content unchanged]":
            full_prompt = f"{format_context_for_prompt(context)}\n\n{query}"
        else:
            full_prompt = query

        # Get available tools
        tools = get_shell_tools()
        implementations = get_tool_implementations()

        # Maximum tool call iterations to prevent infinite loops
        MAX_TOOL_ITERATIONS = 10

        try:
            # Stream the response with system prompt and tools
            # Only pass system prompt on first message of conversation
            if len(conversation.responses) == 0:
                response = conversation.prompt(
                    full_prompt,
                    system=state.system_prompt,
                    tools=tools if tools else None
                )
            else:
                response = conversation.prompt(
                    full_prompt,
                    tools=tools if tools else None
                )

            # Stream the response text
            for chunk in response:
                if chunk:
                    writer.write(chunk.encode())
                    await writer.drain()

            # Check for tool calls
            tool_calls = list(response.tool_calls())

            # Log initial response to database
            if logs_on():
                db_path = get_logs_db_path()
                db_path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite_utils.Database(db_path)
                migrate(db)
                response.log_to_db(db)

            # Process tool calls if any
            iteration = 0
            while tool_calls and iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                # Execute each tool call and collect results
                tool_results = []
                for tool_call in tool_calls:
                    result = await self._execute_tool_call(tool_call, implementations, writer)
                    tool_results.append(result)

                # Continue conversation with tool results
                followup_response = conversation.prompt(
                    "",  # Empty prompt - tool results drive the continuation
                    tools=tools if tools else None,
                    tool_results=tool_results
                )

                # Stream follow-up response
                for chunk in followup_response:
                    if chunk:
                        writer.write(chunk.encode())
                        await writer.drain()

                # Check for more tool calls
                tool_calls = list(followup_response.tool_calls())

                # Log follow-up response
                if logs_on():
                    followup_response.log_to_db(db)

            writer.write(b"\n")
            await writer.drain()

        except Exception as e:
            writer.write(f"\nERROR: {e}\n".encode())
            await writer.drain()

    async def _execute_tool_call(
        self,
        tool_call,
        implementations: Dict[str, callable],
        writer: asyncio.StreamWriter
    ) -> ToolResult:
        """Execute a single tool call and return the result."""
        tool_name = (tool_call.name or "").lower().strip()
        tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        tool_call_id = tool_call.tool_call_id

        # Send tool start marker (client shows spinner)
        writer.write(f"\x02TOOL:{tool_name}\x03".encode())
        await writer.drain()

        # Check if we have an implementation
        if tool_name not in implementations:
            # Send tool done marker before returning
            writer.write(b"\x02TOOL_DONE\x03")
            await writer.drain()
            return ToolResult(
                name=tool_call.name,
                output=f"Error: Tool '{tool_name}' not available",
                tool_call_id=tool_call_id
            )

        try:
            impl = implementations[tool_name]
            result = impl(**tool_args)

            # Handle different result types
            if result is None:
                output = "(no output)"
            elif not isinstance(result, str):
                output = json.dumps(result, default=repr)
            else:
                output = result

            # Send tool done marker
            writer.write(b"\x02TOOL_DONE\x03")
            await writer.drain()

            return ToolResult(
                name=tool_call.name,
                output=output,
                tool_call_id=tool_call_id
            )

        except Exception as e:
            # Send tool done marker even on error
            writer.write(b"\x02TOOL_DONE\x03")
            await writer.drain()

            return ToolResult(
                name=tool_call.name,
                output=f"Error executing {tool_name}: {e}",
                tool_call_id=tool_call_id
            )

    async def handle_new(self, terminal_id: str, writer: asyncio.StreamWriter):
        """Handle /new command - start fresh conversation."""
        state = self.get_conversation_state(terminal_id)
        state.reset()
        writer.write(b"New conversation started.\n")
        await writer.drain()

    async def handle_status(self, terminal_id: str, writer: asyncio.StreamWriter):
        """Handle status request."""
        state = self.get_conversation_state(terminal_id)
        conv = state.conversation

        # Get available tools
        tools = get_shell_tools()
        tool_names = [t.name for t in tools]

        status = {
            "terminal_id": terminal_id,
            "model": self.model.model_id,
            "conversation_id": conv.id if conv else None,
            "messages": len(conv.responses) if conv else 0,
            "uptime_minutes": (datetime.now() - self.last_activity).seconds // 60,
            "tools": tool_names,
        }

        writer.write(json.dumps(status).encode() + b"\n")
        await writer.drain()

    async def handle_shutdown(self, writer: asyncio.StreamWriter):
        """Handle shutdown request."""
        writer.write(b"Shutting down daemon...\n")
        await writer.drain()
        self.running = False

    async def idle_checker(self):
        """Check for idle timeout and shutdown if exceeded."""
        while self.running:
            await asyncio.sleep(60)  # Check every minute

            idle_time = datetime.now() - self.last_activity
            if idle_time > timedelta(minutes=IDLE_TIMEOUT_MINUTES):
                self.console.print(
                    f"[dim]llm-shell daemon: idle timeout ({IDLE_TIMEOUT_MINUTES}m), shutting down[/]",
                    highlight=False
                )
                self.running = False
                break

    async def run(self):
        """Run the daemon server."""
        # Clean up stale socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Start server
        self.server = await asyncio.start_unix_server(
            self.handle_client,
            path=str(self.socket_path)
        )

        # Set socket permissions (user only)
        os.chmod(self.socket_path, 0o600)

        self.console.print(
            f"[dim]llm-shell daemon started (socket: {self.socket_path})[/]",
            highlight=False
        )

        # Start idle checker
        idle_task = asyncio.create_task(self.idle_checker())

        try:
            while self.running:
                await asyncio.sleep(0.1)
        finally:
            idle_task.cancel()
            self.server.close()
            await self.server.wait_closed()

            # Clean up socket
            if self.socket_path.exists():
                self.socket_path.unlink()

            self.console.print("[dim]llm-shell daemon stopped[/]", highlight=False)


def main():
    """Entry point for llm-shell-daemon."""
    import argparse

    parser = argparse.ArgumentParser(description="llm-shell daemon server")
    parser.add_argument("-m", "--model", help="Model to use")
    parser.add_argument("--foreground", "-f", action="store_true",
                        help="Run in foreground (don't daemonize)")
    args = parser.parse_args()

    daemon = ShellDaemon(model_id=args.model)

    # Handle signals
    def signal_handler(sig, frame):
        daemon.running = False

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
