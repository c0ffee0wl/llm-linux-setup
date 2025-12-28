"""llm-inlineassistant daemon - Unix socket server for fast startup.

Keeps a warm Python process with llm loaded, handling queries
from shell clients with <100ms response time after first call.

Architecture:
- Listens on Unix socket at /tmp/llm-inlineassistant-{UID}/daemon.sock
- Maintains per-terminal conversation state
- Per-terminal request queues for concurrent handling
- Auto-terminates after 30 minutes idle
- Streams responses as NDJSON events
"""

import asyncio
import json
import os
import platform
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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


# Error codes for structured error responses
class ErrorCode:
    EMPTY_QUERY = "EMPTY_QUERY"
    MODEL_ERROR = "MODEL_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    TIMEOUT = "TIMEOUT"
    PARSE_ERROR = "PARSE_ERROR"
    INTERNAL = "INTERNAL"


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
    """Get the tools available to llm-inlineassistant.

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


def build_simple_system_prompt() -> str:
    """Build the system prompt for simple mode with current date/time.

    This matches the prompt used by espanso-llm for consistency.
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


# Idle timeout before daemon auto-terminates
IDLE_TIMEOUT_MINUTES = 30
# Worker idle timeout before cleanup (separate from daemon)
WORKER_IDLE_MINUTES = 5


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
    """Unix socket server for llm-inlineassistant."""

    def __init__(self, model_id: Optional[str] = None):
        self.socket_path = get_socket_path()
        self.model = llm.get_model(model_id) if model_id else llm.get_model()
        self.system_prompt = get_system_prompt()
        self.conversations: Dict[str, ConversationState] = {}
        self.server: Optional[asyncio.AbstractServer] = None
        self.last_activity = datetime.now()
        self.running = True
        self.console = Console(stderr=True)

        # Per-terminal request queues for concurrent handling
        self.request_queues: Dict[str, asyncio.Queue] = {}
        self.workers: Dict[str, asyncio.Task] = {}
        self.worker_last_activity: Dict[str, datetime] = {}

    def get_conversation_state(self, terminal_id: str) -> ConversationState:
        """Get or create conversation state for terminal."""
        if terminal_id not in self.conversations:
            self.conversations[terminal_id] = ConversationState(
                terminal_id, self.model, self.system_prompt
            )
        return self.conversations[terminal_id]

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a client connection with JSON protocol."""
        try:
            # Read the JSON request
            data = await asyncio.wait_for(reader.read(65536), timeout=5.0)
            if not data:
                return

            self.last_activity = datetime.now()

            # Parse JSON request
            try:
                request = json.loads(data.decode('utf-8').strip())
            except json.JSONDecodeError as e:
                await self._emit_error(writer, ErrorCode.PARSE_ERROR, f"Invalid JSON: {e}")
                return

            cmd = request.get('cmd', '')
            tid = request.get('tid', 'unknown')

            # Handle commands
            if cmd == 'query':
                await self._queue_request(tid, request, writer)
            elif cmd == 'new':
                await self.handle_new(tid, writer)
            elif cmd == 'status':
                await self.handle_status(tid, writer)
            elif cmd == 'shutdown':
                await self.handle_shutdown(writer)
            else:
                await self._emit_error(writer, ErrorCode.PARSE_ERROR, f"Unknown command: {cmd}")

        except asyncio.TimeoutError:
            await self._emit_error(writer, ErrorCode.TIMEOUT, "Request timeout")
        except Exception as e:
            await self._emit_error(writer, ErrorCode.INTERNAL, str(e))
        finally:
            writer.close()
            await writer.wait_closed()

    async def _queue_request(self, tid: str, request: dict, writer: asyncio.StreamWriter):
        """Queue a request for the terminal's worker."""
        # Get or create queue for this terminal
        if tid not in self.request_queues:
            self.request_queues[tid] = asyncio.Queue()
            self.workers[tid] = asyncio.create_task(self._worker(tid))
            self.worker_last_activity[tid] = datetime.now()

        # Put request in queue and wait for completion
        response_future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self.request_queues[tid].put((request, writer, response_future))

        # Wait for the worker to complete processing
        try:
            await asyncio.wait_for(response_future, timeout=300)  # 5 min timeout
        except asyncio.TimeoutError:
            await self._emit_error(writer, ErrorCode.TIMEOUT, "Request processing timeout")

    async def _worker(self, tid: str):
        """Per-terminal worker that processes requests sequentially."""
        queue = self.request_queues[tid]

        try:
            while True:
                try:
                    # Wait for request with timeout
                    request, writer, future = await asyncio.wait_for(
                        queue.get(),
                        timeout=WORKER_IDLE_MINUTES * 60
                    )
                except asyncio.TimeoutError:
                    # Worker idle timeout - clean up
                    break

                self.worker_last_activity[tid] = datetime.now()

                try:
                    await self._process_query(tid, request, writer)
                    future.set_result(True)
                except Exception as e:
                    await self._emit_error(writer, ErrorCode.INTERNAL, str(e))
                    future.set_exception(e)

        finally:
            # Clean up worker
            del self.request_queues[tid]
            del self.workers[tid]
            del self.worker_last_activity[tid]

    async def _process_query(self, tid: str, request: dict, writer: asyncio.StreamWriter):
        """Process a query request with NDJSON streaming."""
        query = request.get('q', '').strip()
        session_log = request.get('log', '')
        mode = request.get('mode', 'assistant')
        custom_system_prompt = request.get('sys', '')

        if not query:
            await self._emit_error(writer, ErrorCode.EMPTY_QUERY, "Empty query")
            return

        state = self.get_conversation_state(tid)
        conversation = state.get_or_create_conversation()

        # Set SESSION_LOG_FILE for context capture
        if session_log:
            os.environ['SESSION_LOG_FILE'] = session_log

        # Capture context with deduplication
        context, state.context_hashes = capture_shell_context(state.context_hashes)

        # Build prompt with context
        # - New context: send full terminal_context block
        # - Unchanged: send marker reminding LLM to use previous context
        # - Empty: no context block
        if context and context != "[Content unchanged]":
            full_prompt = f"{format_context_for_prompt(context)}\n\n{query}"
        elif context == "[Content unchanged]":
            # Context unchanged - send marker so LLM knows to reference previous context
            full_prompt = f"<terminal_context>[Terminal context unchanged from previous message]</terminal_context>\n\n{query}"
        else:
            full_prompt = query

        # Determine tools and system prompt based on mode
        # - "assistant": Full tools, agent system prompt (default)
        # - "simple": No tools, custom or simple system prompt
        if mode == 'simple':
            tools = []  # No tools in simple mode
            system_prompt = custom_system_prompt or build_simple_system_prompt()
        else:  # 'assistant' mode (default)
            tools = get_shell_tools()
            system_prompt = state.system_prompt

        implementations = get_tool_implementations()

        # Maximum tool call iterations to prevent infinite loops
        MAX_TOOL_ITERATIONS = 10

        try:
            # Stream the response with system prompt and tools
            if len(conversation.responses) == 0:
                response = conversation.prompt(
                    full_prompt,
                    system=system_prompt,
                    tools=tools if tools else None
                )
            else:
                response = conversation.prompt(
                    full_prompt,
                    tools=tools if tools else None
                )

            # Stream the response text as NDJSON events
            for chunk in response:
                if chunk:
                    await self._emit(writer, {"type": "text", "content": chunk})

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
                        await self._emit(writer, {"type": "text", "content": chunk})

                # Check for more tool calls
                tool_calls = list(followup_response.tool_calls())

                # Log follow-up response
                if logs_on():
                    followup_response.log_to_db(db)

            # Signal completion
            await self._emit(writer, {"type": "done"})

        except Exception as e:
            await self._emit_error(writer, ErrorCode.MODEL_ERROR, str(e))

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

        # Emit tool start event
        await self._emit(writer, {
            "type": "tool_start",
            "tool": tool_name,
            "args": tool_args
        })

        # Check if we have an implementation
        if tool_name not in implementations:
            await self._emit(writer, {
                "type": "tool_done",
                "tool": tool_name,
                "result": f"Error: Tool '{tool_name}' not available"
            })
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

            await self._emit(writer, {
                "type": "tool_done",
                "tool": tool_name,
                "result": output[:500] if len(output) > 500 else output  # Truncate for event
            })

            return ToolResult(
                name=tool_call.name,
                output=output,
                tool_call_id=tool_call_id
            )

        except Exception as e:
            error_msg = f"Error executing {tool_name}: {e}"
            await self._emit(writer, {
                "type": "tool_done",
                "tool": tool_name,
                "result": error_msg
            })

            return ToolResult(
                name=tool_call.name,
                output=error_msg,
                tool_call_id=tool_call_id
            )

    async def _emit(self, writer: asyncio.StreamWriter, event: dict):
        """Emit a NDJSON event."""
        line = json.dumps(event) + '\n'
        writer.write(line.encode('utf-8'))
        await writer.drain()

    async def _emit_error(self, writer: asyncio.StreamWriter, code: str, message: str):
        """Emit an error event and done."""
        await self._emit(writer, {"type": "error", "code": code, "message": message})
        await self._emit(writer, {"type": "done"})

    async def handle_new(self, terminal_id: str, writer: asyncio.StreamWriter):
        """Handle /new command - start fresh conversation."""
        state = self.get_conversation_state(terminal_id)
        state.reset()
        await self._emit(writer, {"type": "text", "content": "New conversation started."})
        await self._emit(writer, {"type": "done"})

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
            "active_workers": len(self.workers),
        }

        await self._emit(writer, {"type": "text", "content": json.dumps(status, indent=2)})
        await self._emit(writer, {"type": "done"})

    async def handle_shutdown(self, writer: asyncio.StreamWriter):
        """Handle shutdown request."""
        await self._emit(writer, {"type": "text", "content": "Shutting down daemon..."})
        await self._emit(writer, {"type": "done"})
        self.running = False

    async def idle_checker(self):
        """Check for idle timeout and shutdown if exceeded."""
        while self.running:
            await asyncio.sleep(60)  # Check every minute

            idle_time = datetime.now() - self.last_activity
            if idle_time > timedelta(minutes=IDLE_TIMEOUT_MINUTES):
                self.console.print(
                    f"[dim]llm-inlineassistant daemon: idle timeout ({IDLE_TIMEOUT_MINUTES}m), shutting down[/]",
                    highlight=False
                )
                self.running = False
                break

    async def run(self):
        """Run the daemon server."""
        # Clean up stale socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Ensure socket directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Start server
        self.server = await asyncio.start_unix_server(
            self.handle_client,
            path=str(self.socket_path)
        )

        # Set socket permissions (user only)
        os.chmod(self.socket_path, 0o600)

        self.console.print(
            f"[dim]llm-inlineassistant daemon started (socket: {self.socket_path})[/]",
            highlight=False
        )

        # Start idle checker
        idle_task = asyncio.create_task(self.idle_checker())

        try:
            while self.running:
                await asyncio.sleep(0.1)
        finally:
            idle_task.cancel()

            # Cancel all workers
            for task in self.workers.values():
                task.cancel()

            self.server.close()
            await self.server.wait_closed()

            # Clean up socket
            if self.socket_path.exists():
                self.socket_path.unlink()

            self.console.print("[dim]llm-inlineassistant daemon stopped[/]", highlight=False)


def main():
    """Entry point for llm-inlineassistant-daemon."""
    import argparse

    parser = argparse.ArgumentParser(description="llm-inlineassistant daemon server")
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
