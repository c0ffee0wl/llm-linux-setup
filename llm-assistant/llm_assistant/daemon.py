"""llm-assistant daemon - Unix socket server for headless mode.

Keeps a warm Python process with llm loaded, handling queries
from shell clients with <100ms response time after first call.

Architecture:
- Listens on Unix socket at /tmp/llm-assistant-{UID}/daemon.sock
- Maintains per-terminal HeadlessSession instances
- Per-terminal request queues for concurrent handling
- Runs indefinitely until explicitly stopped (no idle timeout)
- Streams responses as NDJSON events
- Supports completion endpoint for slash commands and fragments
"""

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import daemon
from daemon import pidfile as daemon_pidfile
from lockfile import AlreadyLocked, LockTimeout

import click
import llm
import sqlite_utils
from llm import ToolResult
from llm.migrations import migrate
from rich.console import Console

# Import shared utilities from llm_tools_core
from llm_tools_core import (
    get_socket_path,
    build_simple_system_prompt,
    ErrorCode,
    WORKER_IDLE_MINUTES,
    MAX_TOOL_ITERATIONS,
    is_daemon_process_alive,
    write_pid_file,
    remove_pid_file,
    cleanup_stale_daemon,
    get_assistant_default_model,
)
from llm_tools_core.tool_execution import execute_tool_call

from .headless_session import (
    HeadlessSession,
    get_headless_tools,
    get_tool_implementations,
)
from .utils import get_config_dir, get_logs_db_path, logs_on

# GUI server (aiohttp - always available)
from .web_ui_server import WebUIServer


def _get_default_pidfile() -> str:
    """Get default PID file path for daemon mode."""
    return str(get_config_dir() / "daemon.pid")


def _get_default_logfile() -> str:
    """Get default log file path for daemon mode."""
    return str(get_config_dir() / "daemon.log")


def _cleanup_stale_pidfile(pidfile_path: str) -> bool:
    """Clean up stale PID file if the daemon process is no longer running.

    python-daemon's TimeoutPIDLockFile creates both a PID file and a .lock file.
    If the daemon crashes or is killed with SIGKILL, these files may be left behind.
    This function checks if the PID in the file is still running and cleans up if not.

    Args:
        pidfile_path: Path to the PID file (e.g., ~/.config/llm-assistant/daemon.pid)

    Returns:
        True if stale files were cleaned up, False if daemon is still running or no files exist
    """
    lock_path = pidfile_path + ".lock"

    # Check if PID file exists
    if not os.path.exists(pidfile_path):
        # No PID file, but lock file might exist from interrupted startup
        if os.path.exists(lock_path):
            try:
                os.unlink(lock_path)
            except OSError:
                pass
        return False

    # Read PID from file
    try:
        with open(pidfile_path, 'r') as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        # Corrupt PID file - clean it up
        pid = None

    # Check if process is still running
    if pid is not None:
        try:
            os.kill(pid, 0)  # Signal 0 just checks if process exists
            # Process is running - don't clean up
            return False
        except OSError:
            # Process doesn't exist - stale PID file
            pass

    # Clean up stale files
    for path in [pidfile_path, lock_path]:
        if os.path.exists(path):
            try:
                os.unlink(path)
                sys.stderr.write(f"Cleaned up stale file: {path}\n")
            except OSError as e:
                sys.stderr.write(f"Warning: Could not remove {path}: {e}\n")

    return True


def _run_as_daemon(pidfile_path: str | None, logfile_path: str | None):
    """Run the daemon using python-daemon (PEP 3143).

    Must be called BEFORE any asyncio operations.
    """
    if sys.platform == "win32":
        sys.stderr.write("Error: --daemon is not supported on Windows\n")
        sys.exit(1)

    # Clean up stale PID/lock files from crashed daemon before trying to acquire
    if pidfile_path:
        _cleanup_stale_pidfile(pidfile_path)

    # Ensure directories exist before opening files
    if pidfile_path:
        pidfile_dir = os.path.dirname(pidfile_path)
        if pidfile_dir and not os.path.exists(pidfile_dir):
            os.makedirs(pidfile_dir, exist_ok=True)
    if logfile_path:
        logfile_dir = os.path.dirname(logfile_path)
        if logfile_dir and not os.path.exists(logfile_dir):
            os.makedirs(logfile_dir, exist_ok=True)

    # Prepare file handles (must be opened before daemonizing)
    stdout_file = open(logfile_path, 'a+') if logfile_path else None
    stderr_file = stdout_file  # Share the same file handle

    # Create pidfile lock (TimeoutPIDLockFile with acquire_timeout=0 for immediate fail)
    pidfile_lock = daemon_pidfile.TimeoutPIDLockFile(pidfile_path, acquire_timeout=0) if pidfile_path else None

    context = daemon.DaemonContext(
        working_directory='/',
        umask=0,
        pidfile=pidfile_lock,
        stdout=stdout_file,
        stderr=stderr_file,
    )

    try:
        context.open()
    except (AlreadyLocked, LockTimeout):
        sys.stderr.write(f"Error: Daemon already running (pidfile locked: {pidfile_path})\n")
        sys.exit(1)
    # Note: Don't close context - daemon runs until terminated
    # atexit handler is registered automatically by DaemonContext.open()


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

    def __init__(
        self,
        model_id: Optional[str] = None,
        debug: bool = False,
        foreground: bool = False,
        use_python_daemon: bool = False
    ):
        self.socket_path = get_socket_path()
        self.model_id = model_id
        self.debug = debug
        self.foreground = foreground
        self.use_python_daemon = use_python_daemon
        self.sessions: Dict[str, SessionState] = {}
        self.server: Optional[asyncio.AbstractServer] = None
        self.last_activity = datetime.now()
        self.start_time = datetime.now()
        self.running = True
        self.console = Console(stderr=True)

        # Per-terminal request queues for concurrent handling
        self.request_queues: Dict[str, asyncio.Queue] = {}
        self.workers: Dict[str, asyncio.Task] = {}
        self.worker_last_activity: Dict[str, datetime] = {}

        # Logging enabled check
        self.logging_enabled = logs_on()

        # Request counter for foreground logging
        self.request_count = 0

        # Web UI server (for llm-guiassistant)
        self.web_port = int(os.environ.get('LLM_GUI_PORT', 8741))
        self.web_server: Optional["WebUIServer"] = None

    def _log_request(self, tid: str, direction: str, info: str, duration: float = None):
        """Log request activity in foreground mode."""
        if not self.foreground:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        arrow = "→" if direction == "in" else "←"
        if duration is not None:
            self.console.print(f"[dim]{timestamp}[/] {tid} {arrow} {info} [dim]({duration:.1f}s)[/]")
        else:
            self.console.print(f"[dim]{timestamp}[/] {tid} {arrow} {info}")

    def get_session_state(
        self,
        terminal_id: str,
        session_log: Optional[str] = None,
        source: Optional[str] = None
    ) -> SessionState:
        """Get or create session state for terminal.

        Args:
            terminal_id: Unique identifier for the terminal/session
            session_log: Path to asciinema session log (optional)
            source: Origin of the session ("gui", "tui", "cli", "api", or None)
        """
        if terminal_id not in self.sessions:
            session = HeadlessSession(
                model_name=self.model_id,
                debug=self.debug,
                session_log=session_log,
                terminal_id=terminal_id,
                source=source,
            )
            self.sessions[terminal_id] = SessionState(terminal_id, session)
        else:
            # Update source on existing session if provided
            # This ensures source is correct even if session was created earlier
            if source:
                state = self.sessions[terminal_id]
                state.session.source = source
                # Also update conversation source if one exists
                if state.session.conversation:
                    state.session.conversation.source = source
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

            # Log incoming request in foreground mode
            if cmd == 'query':
                mode = request.get('mode', 'assistant')
                query = request.get('q', '')[:50]
                self._log_request(tid, "in", f"query ({mode}) \"{query}{'...' if len(request.get('q', '')) > 50 else ''}\"")
            elif cmd != 'complete':  # Don't log tab completions (too noisy)
                self._log_request(tid, "in", cmd)

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
            elif cmd == 'truncate':
                await self.handle_truncate(tid, request, writer)
            elif cmd == 'pop_response':
                await self.handle_pop_response(tid, writer)
            elif cmd == 'fork':
                await self.handle_fork(tid, request, writer)
            elif cmd == 'rag_activate':
                await self.handle_rag_activate(tid, request, writer)
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
        response_future: asyncio.Future = asyncio.get_running_loop().create_future()
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
        start_time = time.time()

        query = request.get('q', '').strip()
        session_log = request.get('log', '')
        mode = request.get('mode', 'assistant')
        custom_system_prompt = request.get('sys', '')
        image_paths = request.get('images', [])  # List of image file paths

        # Create attachments from image paths
        attachments = []
        for path in image_paths:
            if path and os.path.isfile(path):
                try:
                    attachments.append(llm.Attachment(path=path))
                except Exception:
                    pass  # Skip invalid attachments

        if not query:
            await self._emit_error(writer, ErrorCode.EMPTY_QUERY, "Empty query")
            return

        # Handle slash commands (queries starting with /)
        if query.startswith('/'):
            handled = await self._handle_slash_command(tid, query, writer)
            if handled:
                return

        # Get session state
        state = self.get_session_state(tid, session_log)
        state.touch()
        session = state.session

        # Always update session_log from request (empty = no context available)
        # Prevents stale paths when terminal_id is reused across sessions
        session.session_log = session_log if session_log else None

        # Capture context with deduplication
        context = session.capture_context(session_log)

        # RAG context retrieval (if RAG collection is active)
        rag_context = ""
        if hasattr(session, 'pending_rag_context') and session.pending_rag_context:
            # One-shot: consume pending context from /rag search
            rag_context = session.pending_rag_context
            session.pending_rag_context = None
        elif hasattr(session, 'active_rag_collection') and session.active_rag_collection and query.strip():
            # Persistent mode: search on every prompt
            if hasattr(session, '_retrieve_rag_context'):
                rag_context = session._retrieve_rag_context(query) or ""

        # Build prompt with context (order: terminal context → RAG context → user input)
        prompt_parts = []
        if context and context != "<terminal_context>[Content unchanged]</terminal_context>":
            prompt_parts.append(context)
        elif "[Content unchanged]" in context:
            prompt_parts.append("<terminal_context>[Terminal context unchanged from previous message]</terminal_context>")

        if rag_context:
            prompt_parts.append(rag_context)

        prompt_parts.append(query)
        full_prompt = "\n\n".join(prompt_parts)

        # Determine tools and system prompt based on mode
        if mode == 'simple':
            tools = []  # No tools in simple mode
            system_prompt = custom_system_prompt or build_simple_system_prompt()
        else:  # 'assistant' mode (default)
            tools = session.get_tools()
            system_prompt = session.get_system_prompt()

        implementations = get_tool_implementations()

        # Add MCP and other active tool implementations if available
        if hasattr(session, '_get_active_external_tools'):
            active_impls = session._get_active_external_tools()
            implementations.update(active_impls)

        conversation = session.get_or_create_conversation()

        try:
            # Stream the response with system prompt, tools, and attachments
            if len(conversation.responses) == 0:
                response = conversation.prompt(
                    full_prompt,
                    system=system_prompt,
                    tools=tools if tools else None,
                    attachments=attachments if attachments else None
                )
            else:
                response = conversation.prompt(
                    full_prompt,
                    tools=tools if tools else None,
                    attachments=attachments if attachments else None
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
            # Build arg overrides based on session settings (sources toggle)
            sources_enabled = getattr(session, '_sources_enabled', True)
            arg_overrides = {
                "search_google": {"sources": sources_enabled},
                "microsoft_sources": {"sources": sources_enabled},
            }
            while tool_calls and iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                # Execute each tool call and collect results
                tool_results = []
                for tool_call in tool_calls:
                    result = await self._execute_tool_call(
                        tool_call, implementations, writer, arg_overrides
                    )
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
            duration = time.time() - start_time
            self._log_request(tid, "out", "done", duration)
            await self._emit(writer, {"type": "done"})

        except Exception as e:
            duration = time.time() - start_time
            self._log_request(tid, "out", f"error: {str(e)[:50]}", duration)
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
            loop = asyncio.get_running_loop()
            if not args:
                # List available models (run in executor to avoid blocking)
                def get_model_list():
                    lines = ["[bold]Available models:[/]"]
                    current = self.model_id or get_assistant_default_model()
                    for model in llm.get_models():
                        marker = " [green](current)[/]" if model.model_id == current else ""
                        lines.append(f"  - {model.model_id}{marker}")
                    return "\n".join(lines)
                content = await loop.run_in_executor(None, get_model_list)
                await self._emit(writer, {"type": "text", "content": content})
            else:
                # Switch model
                try:
                    new_model = await loop.run_in_executor(None, llm.get_model, args)
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

        elif cmd == "/sources":
            if session:
                if not args or args.lower() == "status":
                    status = "enabled" if getattr(session, '_sources_enabled', True) else "disabled"
                    await self._emit(writer, {"type": "text", "content": f"[bold]Sources:[/] {status}"})
                elif args.lower() == "on":
                    session._sources_enabled = True
                    await self._emit(writer, {"type": "text", "content": "[green]Sources enabled[/]"})
                elif args.lower() == "off":
                    session._sources_enabled = False
                    await self._emit(writer, {"type": "text", "content": "[yellow]Sources disabled[/]"})
                else:
                    await self._emit(writer, {"type": "text", "content": "[yellow]Usage: /sources [on|off|status][/]"})
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
        writer: asyncio.StreamWriter,
        arg_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> ToolResult:
        """Execute a single tool call and return the result.

        Uses shared execute_tool_call from llm_tools_core.
        """
        async def emit(event: dict) -> None:
            await self._emit(writer, event)

        return await execute_tool_call(tool_call, implementations, emit, arg_overrides)

    async def handle_complete(self, request: dict, writer: asyncio.StreamWriter):
        """Handle completion request for slash commands and fragments."""
        prefix = request.get('prefix', '')
        cwd = request.get('cwd')  # Optional working directory for file completions
        completions = []

        if prefix.startswith('/'):
            # Slash command completion
            completions = self._complete_slash_commands(prefix)
        elif prefix.startswith('@'):
            # Fragment completion (@github:, @pdf:, @yt:, @file:, etc.)
            completions = self._complete_fragments(prefix, cwd=cwd)
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

    def _complete_fragments(self, prefix: str, cwd: Optional[str] = None) -> List[Dict[str, str]]:
        """Complete fragment prefixes and file paths.

        Uses AtHandler from llm-tools-core for unified completion logic.
        """
        try:
            from llm_tools_core import AtHandler
            handler = AtHandler()
            completions = handler.get_completions(prefix, cwd=cwd)
            return [
                {"text": c.text, "description": c.description}
                for c in completions
            ]
        except ImportError:
            # Fallback: basic fragment types if AtHandler not available
            fragments = [
                ("@github:", "Load GitHub repository"),
                ("@pdf:", "Load PDF document"),
                ("@yt:", "Load YouTube transcript"),
                ("@arxiv:", "Load arXiv paper"),
                ("@url:", "Load web page"),
                ("@file:", "Load local file"),
                ("@dir:", "Load directory contents"),
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
        if writer.is_closing():
            return
        try:
            line = json.dumps(event) + '\n'
            writer.write(line.encode('utf-8'))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass  # Client disconnected

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
            model_id = self.model_id or get_assistant_default_model()

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

        Request: {"cmd": "get_responses", "tid": "...", "count": N, "all": false}
        Response: {"type": "responses", "items": [{"prompt": "...", "response": "..."}]}

        Note: Raw mode (markdown stripping) is handled client-side, not here.
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

    async def handle_truncate(self, terminal_id: str, request: dict, writer: asyncio.StreamWriter):
        """Truncate conversation to keep only N user turns.

        Request: {"cmd": "truncate", "tid": "...", "keep_turns": N}
        Response: {"type": "truncated", "remaining": N}

        Used by llm-guiassistant for edit + regenerate functionality.
        """
        state = self.sessions.get(terminal_id)
        if not state or not state.session.conversation:
            await self._emit(writer, {"type": "error", "message": "No active conversation"})
            await self._emit(writer, {"type": "done"})
            return

        keep_turns = request.get('keep_turns', 0)
        responses = state.session.conversation.responses

        # Each response corresponds to one turn
        if keep_turns < len(responses):
            state.session.conversation.responses = responses[:keep_turns]

        await self._emit(writer, {"type": "truncated", "remaining": len(state.session.conversation.responses)})
        await self._emit(writer, {"type": "done"})

    async def handle_pop_response(self, terminal_id: str, writer: asyncio.StreamWriter):
        """Remove the last response from conversation.

        Request: {"cmd": "pop_response", "tid": "..."}
        Response: {"type": "popped", "remaining": N}

        Used by llm-guiassistant for regenerate functionality.
        """
        state = self.sessions.get(terminal_id)
        if not state or not state.session.conversation:
            await self._emit(writer, {"type": "error", "message": "No active conversation"})
            await self._emit(writer, {"type": "done"})
            return

        responses = state.session.conversation.responses
        if responses:
            responses.pop()

        await self._emit(writer, {"type": "popped", "remaining": len(responses)})
        await self._emit(writer, {"type": "done"})

    async def handle_fork(self, terminal_id: str, request: dict, writer: asyncio.StreamWriter):
        """Fork conversation to a new session.

        Request: {"cmd": "fork", "tid": "...", "new_tid": "...", "messages": [...]}
        Response: {"type": "forked", "new_tid": "..."}

        Used by llm-guiassistant for branch functionality.
        The messages array contains conversation up to the fork point.
        """
        new_tid = request.get('new_tid')
        messages = request.get('messages', [])

        if not new_tid:
            await self._emit(writer, {"type": "error", "message": "new_tid required"})
            await self._emit(writer, {"type": "done"})
            return

        source_state = self.sessions.get(terminal_id)
        if not source_state:
            await self._emit(writer, {"type": "error", "message": "Source session not found"})
            await self._emit(writer, {"type": "done"})
            return

        # Count assistant messages to determine how many responses to copy
        # Each assistant message corresponds to one Response in conversation.responses
        turns_to_keep = sum(1 for m in messages if m.get('role') == 'assistant')

        # Create new session
        new_session = HeadlessSession(
            model_name=source_state.session.model_name,
            debug=source_state.session.debug,
            terminal_id=new_tid
        )

        # Copy conversation responses up to fork point only
        if source_state.session.conversation and turns_to_keep > 0:
            new_session.get_or_create_conversation()
            source_responses = source_state.session.conversation.responses
            new_session.conversation.responses = list(source_responses[:turns_to_keep])

        # Register new session
        new_state = SessionState(new_tid, new_session)
        self.sessions[new_tid] = new_state

        await self._emit(writer, {"type": "forked", "new_tid": new_tid, "messages": len(messages)})
        await self._emit(writer, {"type": "done"})

    async def handle_rag_activate(self, terminal_id: str, request: dict, writer: asyncio.StreamWriter):
        """Activate a RAG collection for this terminal's session.

        Request: {"cmd": "rag_activate", "tid": "...", "collection": "..."}
        Response: {"type": "text", "content": "RAG collection ... activated"}
        """
        collection = request.get('collection', '')

        if not collection:
            await self._emit(writer, {"type": "error", "message": "Collection name required"})
            await self._emit(writer, {"type": "done"})
            return

        # Get or create session for this terminal
        state = self.sessions.get(terminal_id)
        if not state:
            # Create session if it doesn't exist
            session = HeadlessSession(
                model_name=self.model_id,
                debug=self.debug,
                terminal_id=terminal_id,
            )
            state = SessionState(terminal_id, session)
            self.sessions[terminal_id] = state

        session = state.session

        # Check if RAG is available (HeadlessSession has active_rag_collection via RAGMixin)
        if hasattr(session, 'active_rag_collection'):
            try:
                # Try to activate RAG via the session's RAG mixin
                if hasattr(session, '_handle_rag_command'):
                    # Use the session's built-in RAG handling
                    result = session._handle_rag_command(collection)
                    if result:
                        await self._emit(writer, {"type": "text", "content": result})
                    else:
                        # Directly set active_rag_collection if handler didn't return output
                        session.active_rag_collection = collection
                        await self._emit(writer, {
                            "type": "text",
                            "content": f"RAG collection '{collection}' activated for this session"
                        })
                else:
                    # Direct RAG activation
                    session.active_rag_collection = collection
                    await self._emit(writer, {
                        "type": "text",
                        "content": f"RAG collection '{collection}' activated for this session"
                    })
            except Exception as e:
                await self._emit(writer, {"type": "error", "message": str(e)})
        else:
            await self._emit(writer, {"type": "error", "message": "RAG not available in this session"})

        await self._emit(writer, {"type": "done"})

    async def idle_checker(self):
        """Idle checker - currently disabled.

        The daemon stays running indefinitely until explicitly stopped.
        GUI clients (llm-guiassistant) depend on persistent daemon availability.
        """
        # Idle timeout disabled - daemon stays running until stopped
        return

    async def run(self):
        """Run the daemon server."""
        # When using python-daemon, PID file management is handled by DaemonContext
        # Only use llm_tools_core PID functions in foreground mode
        if not self.use_python_daemon:
            # Check if daemon is already running
            is_alive, existing_pid = is_daemon_process_alive()
            if is_alive:
                self.console.print(
                    f"[yellow]llm-assistant daemon is already running (PID {existing_pid})[/]",
                    highlight=False
                )
                self.console.print(
                    f"[dim]Socket: {self.socket_path}[/]",
                    highlight=False
                )
                return

            # Clean up stale files from crashed daemon
            cleanup_stale_daemon()

        # Ensure socket directory exists
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Write PID file (only in foreground mode, python-daemon handles it otherwise)
        if not self.use_python_daemon:
            write_pid_file()

        # Start server with secure permissions from the start
        # Use umask to ensure socket is created with 0o600 permissions (no race condition)
        old_umask = os.umask(0o177)  # 0o777 - 0o177 = 0o600
        try:
            self.server = await asyncio.start_unix_server(
                self.handle_client,
                path=str(self.socket_path)
            )
        finally:
            os.umask(old_umask)  # Restore original umask

        # Start web UI server
        try:
            self.web_server = WebUIServer(self, self.web_port)
            await self.web_server.start()
            web_url = self.web_server.get_url()
        except Exception as e:
            web_url = None
            if self.foreground:
                self.console.print(f"[yellow]Web UI server failed: {e}[/]", highlight=False)

        if self.foreground:
            self.console.print(
                f"llm-assistant daemon started [dim](socket: {self.socket_path})[/]",
                highlight=False
            )
            if web_url:
                self.console.print(f"Web UI available at [bold]{web_url}[/]", highlight=False)
            self.console.print("[dim]Press Ctrl+C to stop[/]", highlight=False)
            self.console.print()
        else:
            msg = f"[dim]llm-assistant daemon started (socket: {self.socket_path})"
            if web_url:
                msg += f" (web: {web_url})"
            msg += "[/]"
            self.console.print(msg, highlight=False)

        # Start idle checker
        idle_task = asyncio.create_task(self.idle_checker())

        try:
            while self.running:
                await asyncio.sleep(0.1)
        finally:
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass

            # Cancel all workers and await them
            for task in self.workers.values():
                task.cancel()
            if self.workers:
                await asyncio.gather(*self.workers.values(), return_exceptions=True)
            self.workers.clear()

            # Stop web UI server
            if self.web_server:
                await self.web_server.stop()

            self.server.close()
            await self.server.wait_closed()

            # Clean up socket and PID file
            if self.socket_path.exists():
                self.socket_path.unlink()
            # Only remove PID file if not using python-daemon (it handles its own cleanup)
            if not self.use_python_daemon:
                remove_pid_file()

            self.console.print("[dim]llm-assistant daemon stopped[/]", highlight=False)


def main(
    model_id: Optional[str] = None,
    debug: bool = False,
    foreground: bool = False,
    pidfile: Optional[str] = None,
    logfile: Optional[str] = None,
):
    """Entry point for llm-assistant --daemon.

    Args:
        model_id: LLM model to use
        debug: Enable debug output
        foreground: Run in foreground with request logging (no idle timeout)
        pidfile: PID file path (used with background daemon mode)
        logfile: Log file path (used with background daemon mode)
    """
    # Background daemon mode: use python-daemon for proper daemonization
    use_python_daemon = not foreground

    if use_python_daemon:
        # Set defaults for pidfile and logfile if not provided
        pidfile = pidfile or _get_default_pidfile()
        logfile = logfile or _get_default_logfile()

        # Daemonize BEFORE creating asyncio event loop
        _run_as_daemon(pidfile, logfile)

    daemon_instance = AssistantDaemon(
        model_id=model_id,
        debug=debug,
        foreground=foreground,
        use_python_daemon=use_python_daemon
    )

    # Handle signals
    def signal_handler(sig, frame):
        daemon_instance.running = False

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(daemon_instance.run())
    except KeyboardInterrupt:
        pass


@click.command()
@click.option('-m', '--model', 'model_id', help='Model to use')
@click.option('--debug', is_flag=True, help='Enable debug output')
@click.option('--foreground', is_flag=True, help='Run in foreground with request logging')
@click.option('--pidfile', default=None, help='PID file path (default: ~/.config/llm-assistant/daemon.pid)')
@click.option('--logfile', default=None, help='Log file path (default: ~/.config/llm-assistant/daemon.log)')
def daemon_cli(
    model_id: Optional[str],
    debug: bool,
    foreground: bool,
    pidfile: Optional[str],
    logfile: Optional[str]
):
    """llm-assistant daemon server."""
    main(model_id=model_id, debug=debug, foreground=foreground, pidfile=pidfile, logfile=logfile)


if __name__ == "__main__":
    daemon_cli()
