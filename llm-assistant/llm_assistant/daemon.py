"""llm-assistant daemon - Unix socket server for headless mode.

Keeps a warm Python process with llm loaded, handling queries
from shell clients with <100ms response time after first call.

Architecture:
- Listens on Unix socket at /tmp/llm-assistant-{UID}/daemon.sock
- Maintains per-terminal HeadlessSession instances
- Per-terminal request queues for concurrent handling
- Auto-terminates after 30 minutes idle
- Streams responses as NDJSON events
- Supports completion endpoint for slash commands and fragments
"""

import asyncio
import json
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import llm
import sqlite_utils
from llm import ToolResult
from llm.migrations import migrate
from rich.console import Console

# Import shared utilities from llm_tools_core
from llm_tools_core import (
    get_socket_path,
    ensure_socket_dir,
    build_simple_system_prompt,
    ErrorCode,
    IDLE_TIMEOUT_MINUTES,
    WORKER_IDLE_MINUTES,
    MAX_TOOL_ITERATIONS,
)

from .headless_session import (
    HeadlessSession,
    get_headless_tools,
    get_tool_implementations,
    capture_shell_context,
    format_context_for_prompt,
)
from .utils import get_config_dir, logs_on


def get_logs_db_path() -> Path:
    """Get the database path for conversation logs."""
    return get_config_dir() / "logs-daemon.db"


class SessionState:
    """State wrapper for a HeadlessSession tied to a terminal."""

    def __init__(self, terminal_id: str, session: HeadlessSession):
        self.terminal_id = terminal_id
        self.session = session
        self.last_activity = datetime.now()

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.now()


class AssistantDaemon:
    """Unix socket server for llm-assistant daemon mode."""

    def __init__(self, model_id: Optional[str] = None, debug: bool = False):
        self.socket_path = get_socket_path()
        self.model_id = model_id
        self.debug = debug
        self.sessions: Dict[str, SessionState] = {}
        self.server: Optional[asyncio.AbstractServer] = None
        self.last_activity = datetime.now()
        self.running = True
        self.console = Console(stderr=True)

        # Per-terminal request queues for concurrent handling
        self.request_queues: Dict[str, asyncio.Queue] = {}
        self.workers: Dict[str, asyncio.Task] = {}
        self.worker_last_activity: Dict[str, datetime] = {}

        # Logging enabled check
        self.logging_enabled = logs_on()

    def get_session_state(self, terminal_id: str, session_log: Optional[str] = None) -> SessionState:
        """Get or create session state for terminal."""
        if terminal_id not in self.sessions:
            session = HeadlessSession(
                model_name=self.model_id,
                debug=self.debug,
                session_log=session_log,
                terminal_id=terminal_id,
            )
            self.sessions[terminal_id] = SessionState(terminal_id, session)
        return self.sessions[terminal_id]

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
            elif cmd == 'complete':
                await self.handle_complete(request, writer)
            elif cmd == 'new':
                await self.handle_new(tid, writer)
            elif cmd == 'status':
                await self.handle_status(tid, writer)
            elif cmd == 'help':
                await self.handle_help(writer)
            elif cmd == 'shutdown':
                await self.handle_shutdown(writer)
            elif cmd == 'get_responses':
                await self.handle_get_responses(tid, request, writer)
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
            if tid in self.request_queues:
                del self.request_queues[tid]
            if tid in self.workers:
                del self.workers[tid]
            if tid in self.worker_last_activity:
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

        # Handle slash commands (queries starting with /)
        if query.startswith('/'):
            handled = await self._handle_slash_command(tid, query, writer)
            if handled:
                return

        # Reject agent mode in headless
        if mode == 'agent':
            await self._emit_error(
                writer,
                ErrorCode.INVALID_MODE,
                "Agent mode is not available in headless mode (requires Terminator)"
            )
            return

        # Get session state
        state = self.get_session_state(tid, session_log)
        state.touch()
        session = state.session

        # Update session log if provided
        if session_log:
            session.session_log = session_log

        # Capture context with deduplication
        context = session.capture_context(session_log)

        # Build prompt with context
        if context and context != "<terminal_context>[Content unchanged]</terminal_context>":
            full_prompt = f"{context}\n\n{query}"
        elif "[Content unchanged]" in context:
            # Context unchanged - send marker so LLM knows to reference previous context
            full_prompt = f"<terminal_context>[Terminal context unchanged from previous message]</terminal_context>\n\n{query}"
        else:
            full_prompt = query

        # Determine tools and system prompt based on mode
        if mode == 'simple':
            tools = []  # No tools in simple mode
            system_prompt = custom_system_prompt or build_simple_system_prompt()
        else:  # 'assistant' mode (default)
            tools = session.get_tools()
            system_prompt = session.get_system_prompt()

        implementations = get_tool_implementations()

        # Add MCP tool implementations if available
        if hasattr(session, '_get_mcp_tool_implementations'):
            mcp_impls = session._get_mcp_tool_implementations()
            implementations.update(mcp_impls)

        conversation = session.get_or_create_conversation()

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
            if self.logging_enabled:
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
                if self.logging_enabled:
                    followup_response.log_to_db(db)

            # Signal completion
            await self._emit(writer, {"type": "done"})

        except Exception as e:
            await self._emit_error(writer, ErrorCode.MODEL_ERROR, str(e))

    async def _handle_slash_command(
        self,
        tid: str,
        query: str,
        writer: asyncio.StreamWriter
    ) -> bool:
        """Handle slash commands that come through as queries.

        Returns True if command was handled, False to pass to LLM.
        """
        from .config import SLASH_COMMANDS, HEADLESS_AVAILABLE_COMMANDS

        parts = query.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Check if this is a known slash command that's not available in headless mode
        if cmd in SLASH_COMMANDS and cmd not in HEADLESS_AVAILABLE_COMMANDS:
            await self._emit(writer, {"type": "text", "content": f"[yellow]{cmd} is not available in headless mode[/]"})
            await self._emit(writer, {"type": "done"})
            return True

        # Get session for commands that need it
        # For most commands, we create the session if it doesn't exist
        session_log = None  # Will be passed in future if needed
        state = self.get_session_state(tid, session_log) if tid != 'unknown' else None
        session = state.session if state else None

        # Commands that need a session
        if cmd == "/model":
            if not args:
                # List available models
                lines = ["[bold]Available models:[/]"]
                current = self.model_id or llm.get_model().model_id
                for model in llm.get_models():
                    marker = " [green](current)[/]" if model.model_id == current else ""
                    lines.append(f"  - {model.model_id}{marker}")
                await self._emit(writer, {"type": "text", "content": "\n".join(lines)})
            else:
                # Switch model
                try:
                    new_model = llm.get_model(args)
                    self.model_id = args
                    if session:
                        session.model = new_model
                        session.model_name = args
                        if session.conversation:
                            session.conversation.model = new_model
                    await self._emit(writer, {"type": "text", "content": f"[green]Switched to model: {args}[/]"})
                except Exception as e:
                    await self._emit(writer, {"type": "text", "content": f"[red]Error switching model: {e}[/]"})
            await self._emit(writer, {"type": "done"})
            return True

        elif cmd == "/squash":
            if session and hasattr(session, 'squash_context'):
                keep = args if args else None
                try:
                    session.squash_context(keep=keep)
                    await self._emit(writer, {"type": "text", "content": "[green]Context squashed[/]"})
                except Exception as e:
                    await self._emit(writer, {"type": "text", "content": f"[red]Error squashing: {e}[/]"})
            else:
                await self._emit(writer, {"type": "text", "content": "[yellow]No active session to squash[/]"})
            await self._emit(writer, {"type": "done"})
            return True

        # Mixin commands - delegate to session handlers with console capture
        elif cmd in ("/kb", "/memory", "/rag", "/skill", "/report"):
            handler_map = {
                "/kb": ("_handle_kb_command", "Knowledge base"),
                "/memory": ("_handle_memory_command", "Memory"),
                "/rag": ("_handle_rag_command", "RAG"),
                "/skill": ("_handle_skill_command", "Skills"),
                "/report": ("_handle_report_command", "Report management"),
            }
            handler_name, feature_name = handler_map[cmd]
            output = self._call_session_handler(session, handler_name, args)
            if output is None:
                await self._emit(writer, {"type": "text", "content": f"[yellow]{feature_name} not available[/]"})
            elif output:
                await self._emit(writer, {"type": "text", "content": output})
            await self._emit(writer, {"type": "done"})
            return True

        elif cmd == "/mcp":
            if session and hasattr(session, '_handle_mcp_status'):
                def mcp_handler():
                    if not args or args.lower() == "status":
                        session._handle_mcp_status()
                    elif args.lower().startswith("load "):
                        session._handle_mcp_load(args[5:].strip())
                    elif args.lower().startswith("unload "):
                        session._handle_mcp_unload(args[7:].strip())
                    else:
                        session.console.print("[yellow]Usage: /mcp, /mcp load <server>, /mcp unload <server>[/]")
                output = self._capture_console_output(session, mcp_handler)
                if output:
                    await self._emit(writer, {"type": "text", "content": output})
            else:
                await self._emit(writer, {"type": "text", "content": "[yellow]MCP not available[/]"})
            await self._emit(writer, {"type": "done"})
            return True

        elif cmd == "/assistant":
            if session:
                if session.mode == "assistant":
                    await self._emit(writer, {"type": "text", "content": "[dim]Already in assistant mode[/]"})
                else:
                    session.mode = "assistant"
                    await self._emit(writer, {"type": "text", "content": "[green]Switched to assistant mode[/]"})
            else:
                await self._emit(writer, {"type": "text", "content": "[yellow]No active session[/]"})
            await self._emit(writer, {"type": "done"})
            return True

        elif cmd == "/imagemage":
            # Check imagemage availability
            import shutil
            if not shutil.which('imagemage'):
                await self._emit(writer, {"type": "text", "content": "[yellow]imagemage not installed (requires Go 1.22+ and Gemini)[/]"})
            elif not args or args.lower() == "status":
                loaded = session and hasattr(session, 'loaded_optional_tools') and 'imagemage' in getattr(session, 'loaded_optional_tools', set())
                status = "[green]loaded[/]" if loaded else "[yellow]not loaded[/]"
                await self._emit(writer, {"type": "text", "content": f"Imagemage: {status}"})
            elif args.lower() == "off":
                if session and hasattr(session, 'loaded_optional_tools'):
                    session.loaded_optional_tools.discard('imagemage')
                    await self._emit(writer, {"type": "text", "content": "[yellow]imagemage unloaded[/]"})
                else:
                    await self._emit(writer, {"type": "text", "content": "[dim]imagemage was not loaded[/]"})
            else:
                # Load imagemage
                if session:
                    if not hasattr(session, 'loaded_optional_tools'):
                        session.loaded_optional_tools = set()
                    session.loaded_optional_tools.add('imagemage')
                    await self._emit(writer, {"type": "text", "content": "[green]imagemage loaded[/]"})
                else:
                    await self._emit(writer, {"type": "text", "content": "[yellow]No active session[/]"})
            await self._emit(writer, {"type": "done"})
            return True

        elif cmd == "/copy":
            # /copy is handled by thin client via get_responses command
            # If it reaches here, pass through (shouldn't normally happen)
            return False

        # Commands normally handled by thin client - handle here for direct daemon usage
        elif cmd in ("/clear", "/reset", "/new"):
            if session:
                session.reset_conversation()
                session.context_hashes = set()
            await self._emit(writer, {"type": "text", "content": "[green]New conversation started.[/]"})
            await self._emit(writer, {"type": "done"})
            return True

        elif cmd in ("/info", "/status"):
            # Delegate to handle_status
            await self.handle_status(tid, writer)
            return True

        elif cmd == "/help":
            await self.handle_help(writer)
            return True

        elif cmd in ("/quit", "/exit"):
            await self.handle_shutdown(writer)
            return True

        # Not a recognized slash command - let it pass to LLM
        # This handles user queries that happen to start with /
        return False

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

    async def handle_complete(self, request: dict, writer: asyncio.StreamWriter):
        """Handle completion request for slash commands and fragments."""
        prefix = request.get('prefix', '')
        completions = []

        if prefix.startswith('/'):
            # Slash command completion
            completions = self._complete_slash_commands(prefix)
        elif prefix.startswith('@'):
            # Fragment completion (@github:, @pdf:, @yt:)
            completions = self._complete_fragments(prefix)
        elif prefix.startswith('model:'):
            # Model name completion
            completions = self._complete_models(prefix[6:])

        # Emit completions
        await self._emit(writer, {
            "type": "completions",
            "items": completions
        })
        await self._emit(writer, {"type": "done"})

    def _complete_slash_commands(self, prefix: str) -> List[Dict[str, str]]:
        """Complete slash commands (filtered for headless mode)."""
        from .config import SLASH_COMMANDS, HEADLESS_AVAILABLE_COMMANDS

        prefix_lower = prefix.lower()
        completions = []

        for cmd, info in SLASH_COMMANDS.items():
            # Only show commands available in headless mode
            if cmd not in HEADLESS_AVAILABLE_COMMANDS:
                continue
            if cmd.lower().startswith(prefix_lower):
                completions.append({
                    "text": cmd,
                    "description": info.get("description", "")
                })

        return completions

    def _complete_fragments(self, prefix: str) -> List[Dict[str, str]]:
        """Complete fragment prefixes."""
        # Common fragment types
        fragments = [
            ("@github:", "Load GitHub repository"),
            ("@pdf:", "Load PDF document"),
            ("@yt:", "Load YouTube transcript"),
            ("@url:", "Load web page"),
            ("@file:", "Load local file"),
        ]

        prefix_lower = prefix.lower()
        return [
            {"text": frag, "description": desc}
            for frag, desc in fragments
            if frag.lower().startswith(prefix_lower)
        ]

    def _call_session_handler(
        self,
        session: Optional["HeadlessSession"],
        handler_name: str,
        args: str
    ) -> Optional[str]:
        """Call a session handler method and capture its console output.

        Args:
            session: The HeadlessSession instance (may be None)
            handler_name: Name of the method to call (e.g., '_handle_kb_command')
            args: Arguments to pass to the handler

        Returns:
            Captured console output, or None if handler not available
        """
        if not session or not hasattr(session, handler_name):
            return None

        handler = getattr(session, handler_name)
        return self._capture_console_output(session, lambda: handler(args))

    def _capture_console_output(
        self,
        session: "HeadlessSession",
        func: callable
    ) -> str:
        """Capture Rich console output from a function call.

        Args:
            session: The HeadlessSession with console to capture
            func: Zero-argument callable to execute

        Returns:
            Captured console output as string
        """
        from io import StringIO

        # Capture console output
        string_io = StringIO()
        original_file = session.console.file
        session.console.file = string_io

        try:
            func()
        finally:
            session.console.file = original_file

        return string_io.getvalue()

    def _complete_models(self, prefix: str) -> List[Dict[str, str]]:
        """Complete model names."""
        prefix_lower = prefix.lower()
        completions = []

        try:
            for model in llm.get_models():
                model_id = model.model_id
                if model_id.lower().startswith(prefix_lower):
                    completions.append({
                        "text": model_id,
                        "description": ""
                    })
        except Exception:
            pass

        return completions

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
        if terminal_id in self.sessions:
            state = self.sessions[terminal_id]
            state.session.reset_conversation()
            state.touch()
        await self._emit(writer, {"type": "text", "content": "New conversation started."})
        await self._emit(writer, {"type": "done"})

    async def handle_status(self, terminal_id: str, writer: asyncio.StreamWriter):
        """Handle status request."""
        state = self.sessions.get(terminal_id)

        # Get available tools
        if state:
            tools = state.session.get_tools()
            tool_names = [t.name for t in tools]
            conv = state.session.conversation
            model_id = state.session.model_name
        else:
            tools = get_headless_tools()
            tool_names = [t.name for t in tools]
            conv = None
            model_id = self.model_id or llm.get_model().model_id

        status = {
            "terminal_id": terminal_id,
            "model": model_id,
            "conversation_id": conv.id if conv else None,
            "messages": len(conv.responses) if conv else 0,
            "uptime_minutes": (datetime.now() - self.last_activity).seconds // 60,
            "tools": tool_names,
            "active_workers": len(self.workers),
            "active_sessions": len(self.sessions),
        }

        await self._emit(writer, {"type": "text", "content": json.dumps(status, indent=2)})
        await self._emit(writer, {"type": "done"})

    async def handle_help(self, writer: asyncio.StreamWriter):
        """Handle help request - show available commands in headless mode."""
        from .config import SLASH_COMMANDS, HEADLESS_AVAILABLE_COMMANDS

        lines = []
        for cmd, info in sorted(SLASH_COMMANDS.items()):
            if cmd not in HEADLESS_AVAILABLE_COMMANDS:
                continue
            desc = info.get("description", "")
            lines.append(f"  {cmd:<15} {desc}")

        help_text = "\n".join([
            "[bold]Available commands:[/]",
            *lines,
        ])

        await self._emit(writer, {"type": "text", "content": help_text})
        await self._emit(writer, {"type": "done"})

    async def handle_shutdown(self, writer: asyncio.StreamWriter):
        """Handle shutdown request."""
        await self._emit(writer, {"type": "text", "content": "Shutting down daemon..."})
        await self._emit(writer, {"type": "done"})
        self.running = False

    async def handle_get_responses(self, terminal_id: str, request: dict, writer: asyncio.StreamWriter):
        """Handle get_responses request for /copy command.

        Request: {"cmd": "get_responses", "tid": "...", "count": N, "all": false, "raw": false}
        Response: {"type": "responses", "items": [{"prompt": "...", "response": "..."}]}
        """
        state = self.sessions.get(terminal_id)
        if not state or not state.session.conversation:
            await self._emit(writer, {"type": "responses", "items": []})
            await self._emit(writer, {"type": "done"})
            return

        count = request.get('count', 1)
        get_all = request.get('all', False)
        responses = state.session.conversation.responses

        if not responses:
            await self._emit(writer, {"type": "responses", "items": []})
            await self._emit(writer, {"type": "done"})
            return

        items = []
        if get_all:
            for r in responses:
                prompt_text = r.prompt.prompt if hasattr(r, 'prompt') and r.prompt else ""
                items.append({
                    "prompt": prompt_text,
                    "response": r.text()
                })
        else:
            for r in responses[-count:]:
                items.append({
                    "prompt": "",
                    "response": r.text()
                })

        await self._emit(writer, {"type": "responses", "items": items})
        await self._emit(writer, {"type": "done"})

    async def idle_checker(self):
        """Check for idle timeout and shutdown if exceeded."""
        while self.running:
            await asyncio.sleep(60)  # Check every minute

            idle_time = datetime.now() - self.last_activity
            if idle_time > timedelta(minutes=IDLE_TIMEOUT_MINUTES):
                self.console.print(
                    f"[dim]llm-assistant daemon: idle timeout ({IDLE_TIMEOUT_MINUTES}m), shutting down[/]",
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
            f"[dim]llm-assistant daemon started (socket: {self.socket_path})[/]",
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

            self.console.print("[dim]llm-assistant daemon stopped[/]", highlight=False)


def main(model_id: Optional[str] = None, debug: bool = False):
    """Entry point for llm-assistant --daemon."""
    daemon = AssistantDaemon(model_id=model_id, debug=debug)

    # Handle signals
    def signal_handler(sig, frame):
        daemon.running = False

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass


@click.command()
@click.option('-m', '--model', 'model_id', help='Model to use')
@click.option('--debug', is_flag=True, help='Enable debug output')
def daemon_cli(model_id: Optional[str], debug: bool):
    """llm-assistant daemon server."""
    main(model_id=model_id, debug=debug)


if __name__ == "__main__":
    daemon_cli()
