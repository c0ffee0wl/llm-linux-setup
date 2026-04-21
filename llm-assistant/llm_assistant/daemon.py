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

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

import daemon
from daemon import pidfile as daemon_pidfile
from lockfile import AlreadyLocked, LockTimeout

import click
import llm
import sqlite_utils
from llm.migrations import migrate
from rich.console import Console

# Import shared utilities from llm_tools_core
from llm_tools_core import (
    AtHandler,
    CONTEXT_UNCHANGED_MARKER,
    get_socket_path,
    build_simple_system_prompt,
    ErrorCode,
    WORKER_IDLE_MINUTES,
    IDLE_TIMEOUT_MINUTES,
    MAX_TOOL_ITERATIONS,
    is_daemon_process_alive,
    write_pid_file,
    remove_pid_file,
    cleanup_stale_daemon,
    get_assistant_default_model,
)
from llm_tools_core.tool_execution import execute_tool_call

from .config import SLASH_COMMANDS, HEADLESS_AVAILABLE_COMMANDS, MIXIN_HANDLERS
from .headless_session import (
    HeadlessSession,
    get_headless_tools,
    get_tool_implementations,
)
from .utils import get_config_dir, get_logs_db_path, logs_on, parse_command

# GUI server (aiohttp - always available)
from .web_ui_server import WebUIServer

# Maximum request payload size (10 MB) - prevents unbounded memory consumption
MAX_REQUEST_SIZE = 10 * 1024 * 1024

# Slash-command dispatch groups used by _handle_slash_command. Kept at module
# scope so the sets are built once, not per call.
_COPY_COMMANDS = {"/copy"}
_RESET_COMMANDS = {"/clear", "/reset", "/new"}
_STATUS_COMMANDS = {"/info", "/status"}
_QUIT_COMMANDS = {"/quit", "/exit"}


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

    # Read PID from file
    try:
        with open(pidfile_path, 'r') as f:
            pid = int(f.read().strip())
    except FileNotFoundError:
        # No PID file, but lock file might exist from interrupted startup
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        return False
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
        try:
            os.unlink(path)
            sys.stderr.write(f"Cleaned up stale file: {path}\n")
        except FileNotFoundError:
            pass
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
        if pidfile_dir:
            os.makedirs(pidfile_dir, exist_ok=True)
    if logfile_path:
        logfile_dir = os.path.dirname(logfile_path)
        if logfile_dir:
            os.makedirs(logfile_dir, exist_ok=True)

    # Prepare file handles (must be opened before daemonizing)
    # Use separate file handles so closing one doesn't invalidate the other
    stdout_file = open(logfile_path, 'a') if logfile_path else None
    stderr_file = open(logfile_path, 'a') if logfile_path else None

    # Create pidfile lock (TimeoutPIDLockFile with acquire_timeout=0 for immediate fail)
    pidfile_lock = daemon_pidfile.TimeoutPIDLockFile(pidfile_path, acquire_timeout=0) if pidfile_path else None

    context = daemon.DaemonContext(
        working_directory='/',
        umask=0o077,
        pidfile=pidfile_lock,
        stdout=stdout_file,
        stderr=stderr_file,
    )

    try:
        context.open()
    except (AlreadyLocked, LockTimeout):
        # Clean up opened file handles before exiting
        for f in (stdout_file, stderr_file):
            if f:
                try:
                    f.close()
                except OSError:
                    pass
        sys.stderr.write(f"Error: Daemon already running (pidfile locked: {pidfile_path})\n")
        sys.exit(1)
    except Exception:
        # Clean up file handles on unexpected errors to prevent fd leak
        for f in (stdout_file, stderr_file):
            if f:
                try:
                    f.close()
                except OSError:
                    pass
        raise
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
        self.start_time = datetime.now()
        # Set inside run() once we have a running event loop; signal/shutdown
        # handlers set it to wake the main loop without a polling sleep.
        self._stop_event: Optional[asyncio.Event] = None
        self.console = Console(stderr=True)

        self.request_queues: Dict[str, asyncio.Queue] = {}
        self.workers: Dict[str, asyncio.Task] = {}

        self.logging_enabled = logs_on()
        self._db_migrated = False

        self.web_port = int(os.environ.get('LLM_GUI_PORT', 8741))
        self.web_server: Optional["WebUIServer"] = None

    def _handle_signal(self):
        """Signal handler for SIGTERM/SIGINT — stops the daemon gracefully."""
        if self._stop_event is not None:
            self._stop_event.set()

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
        elif source:
            # A terminal_id can be reused across clients (e.g. TUI then GUI);
            # refresh source so new responses are tagged correctly.
            self.sessions[terminal_id].session.source = source
        return self.sessions[terminal_id]

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a client connection with JSON protocol."""
        try:
            # Read the JSON request (client sends SHUT_WR after writing, so read until EOF)
            chunks = []
            total_size = 0
            while True:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=5.0)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_REQUEST_SIZE:
                    await self._emit_error(writer, ErrorCode.PARSE_ERROR, "Request too large")
                    return
                chunks.append(chunk)
            data = b''.join(chunks)
            if not data:
                return

            try:
                request = json.loads(data.decode('utf-8').strip())
            except json.JSONDecodeError as e:
                await self._emit_error(writer, ErrorCode.PARSE_ERROR, f"Invalid JSON: {e}")
                return

            cmd = request.get('cmd', '')
            tid = request.get('tid', 'unknown')

            if cmd == 'query':
                mode = request.get('mode', 'assistant')
                query = request.get('q', '')[:50]
                self._log_request(tid, "in", f"query ({mode}) \"{query}{'...' if len(request.get('q', '')) > 50 else ''}\"")
            elif cmd != 'complete':  # Tab completions are too noisy to log
                self._log_request(tid, "in", cmd)

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
            elif cmd in ('truncate', 'pop_response', 'fork'):
                # Route conversation-mutating commands through worker queue
                # to serialize with streaming queries and prevent data races
                await self._queue_request(tid, request, writer)
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
        if tid not in self.request_queues:
            self.request_queues[tid] = asyncio.Queue()
            self.workers[tid] = asyncio.create_task(self._worker(tid))

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

                try:
                    # Dispatch based on command type — all commands in
                    # the worker queue are serialized per terminal
                    cmd = request.get('cmd', '')
                    if cmd == 'truncate':
                        await self.handle_truncate(tid, request, writer)
                    elif cmd == 'pop_response':
                        await self.handle_pop_response(tid, writer)
                    elif cmd == 'fork':
                        await self.handle_fork(tid, request, writer)
                    else:
                        await self._process_query(tid, request, writer)
                    future.set_result(True)
                except Exception as e:
                    await self._emit_error(writer, ErrorCode.INTERNAL, str(e))
                    # Set result (not exception) — error already emitted to client,
                    # re-raising would cause handle_client to emit a second error
                    future.set_result(False)

        finally:
            # Clean up worker — use pop() to avoid KeyError if already removed
            self.request_queues.pop(tid, None)
            self.workers.pop(tid, None)

    async def _process_query(self, tid: str, request: dict, writer: asyncio.StreamWriter):
        """Process a query request with NDJSON streaming."""
        start_time = time.time()

        query = request.get('q', '').strip()
        session_log = request.get('log', '')
        mode = request.get('mode', 'assistant')
        custom_system_prompt = request.get('sys', '')
        source = request.get('source')  # Origin: "gui", "tui", "cli", "api"
        image_paths = request.get('images', [])  # List of image file paths

        attachments = []
        for path in image_paths:
            if not path:
                continue
            try:
                attachments.append(llm.Attachment(path=path))
            except Exception as e:
                if self.debug:
                    self.console.print(f"[yellow]Skipped attachment {path}: {e}[/]", highlight=False)

        if not query:
            await self._emit_error(writer, ErrorCode.EMPTY_QUERY, "Empty query")
            return

        # Handle slash commands (queries starting with /)
        if query.startswith('/'):
            handled = await self._handle_slash_command(tid, query, writer)
            if handled:
                return

        # Get session state (pass source so GUI clients get correct behavior)
        state = self.get_session_state(tid, session_log, source=source)
        state.touch()
        session = state.session

        # Always update session_log from request (empty = no context available)
        # Prevents stale paths when terminal_id is reused across sessions
        session.session_log = session_log if session_log else None

        # Capture context with deduplication (async to avoid blocking event loop)
        context = await session.capture_context(session_log)

        # RAG context retrieval (if RAG collection is active)
        rag_context = ""
        if session.pending_rag_context:
            # One-shot: consume pending context from /rag search
            rag_context = session.pending_rag_context
            session.pending_rag_context = None
        elif session.active_rag_collection and query.strip():
            # Persistent mode: search on every prompt. Vector search does disk
            # (and sometimes network) I/O — run off the event loop so other
            # clients aren't blocked while we wait.
            loop = asyncio.get_running_loop()
            rag_context = await loop.run_in_executor(
                None, session._retrieve_rag_context, query
            ) or ""

        # Build prompt with context (order: terminal context → RAG context → user input)
        unchanged_wrapped = f"<terminal_context>{CONTEXT_UNCHANGED_MARKER}</terminal_context>"
        prompt_parts = []
        if context and context != unchanged_wrapped:
            prompt_parts.append(context)
        elif context and CONTEXT_UNCHANGED_MARKER in context:
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
        implementations.update(session._get_active_external_tools())

        conversation = session.get_or_create_conversation()

        # Open a fresh DB handle per query (migration ran once at startup)
        db = None
        if self.logging_enabled:
            db = sqlite_utils.Database(get_logs_db_path())
            if not self._db_migrated:
                # Logging was off at startup but turned on later — migrate once now
                migrate(db)
                self._db_migrated = True

        try:
            # system prompt is only accepted on the first turn of a conversation
            prompt_kwargs = {
                "tools": tools if tools else None,
                "attachments": attachments if attachments else None,
            }
            if len(conversation.responses) == 0:
                prompt_kwargs["system"] = system_prompt
            response = conversation.prompt(full_prompt, **prompt_kwargs)

            for chunk in response:
                if chunk:
                    await self._emit(writer, {"type": "text", "content": chunk})

            tool_calls = list(response.tool_calls())

            if db:
                response.log_to_db(db)

            iteration = 0
            sources_enabled = session.sources_enabled
            arg_overrides = {
                "search_google": {"sources": sources_enabled},
                "microsoft_sources": {"sources": sources_enabled},
            }

            async def emit(event: dict) -> None:
                await self._emit(writer, event)

            while tool_calls and iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                tool_results = []
                for tool_call in tool_calls:
                    result = await execute_tool_call(
                        tool_call, implementations, emit, arg_overrides
                    )
                    tool_results.append(result)

                # Empty prompt - tool results drive the continuation
                followup_response = conversation.prompt(
                    "",
                    tools=tools if tools else None,
                    tool_results=tool_results
                )

                for chunk in followup_response:
                    if chunk:
                        await self._emit(writer, {"type": "text", "content": chunk})

                tool_calls = list(followup_response.tool_calls())

                if db:
                    followup_response.log_to_db(db)

            duration = time.time() - start_time
            self._log_request(tid, "out", "done", duration)
            await self._emit(writer, {"type": "done"})

        except Exception as e:
            duration = time.time() - start_time
            self._log_request(tid, "out", f"error: {str(e)[:50]}", duration)
            await self._emit_error(writer, ErrorCode.MODEL_ERROR, str(e))
        finally:
            if db is not None and db.conn:
                db.conn.close()

    async def _handle_slash_command(
        self,
        tid: str,
        query: str,
        writer: asyncio.StreamWriter
    ) -> bool:
        """Handle slash commands that come through as queries.

        Returns True if command was handled, False to pass to LLM.
        """
        cmd, args = parse_command(query)
        cmd = cmd.lower()

        if cmd in SLASH_COMMANDS and cmd not in HEADLESS_AVAILABLE_COMMANDS:
            await self._emit_text_done(writer, f"[yellow]{cmd} is not available in headless mode[/]")
            return True

        # Eagerly materialize the session; several handlers below expect one.
        state = self.get_session_state(tid) if tid != 'unknown' else None
        session = state.session if state else None

        if cmd in _COPY_COMMANDS:
            # /copy needs the host's clipboard, which only the thin client has access to.
            await self._emit_text_done(
                writer,
                "[yellow]/copy is handled by the thin client; not available via daemon directly[/]"
            )
            return True

        if cmd in _RESET_COMMANDS:
            await self.handle_new(tid, writer)
            return True

        if cmd in _STATUS_COMMANDS:
            await self.handle_status(tid, writer)
            return True

        if cmd == "/help":
            await self.handle_help(writer)
            return True

        if cmd in _QUIT_COMMANDS:
            await self.handle_shutdown(writer)
            return True

        if cmd == "/model":
            await self._cmd_model(session, args, writer)
            return True

        if cmd == "/squash":
            await self._cmd_squash(session, args, writer)
            return True

        if cmd == "/mcp":
            await self._cmd_mcp(session, args, writer)
            return True

        if cmd == "/sources":
            await self._cmd_sources(session, args, writer)
            return True

        if cmd in MIXIN_HANDLERS:
            handler_name, feature_name = MIXIN_HANDLERS[cmd]
            output = self._call_session_handler(session, handler_name, args)
            # Empty string output (handler printed nothing) still terminates with done.
            if output is None:
                await self._emit_text_done(writer, f"[yellow]{feature_name} not available[/]")
            elif output:
                await self._emit_text_done(writer, output)
            else:
                await self._emit(writer, {"type": "done"})
            return True

        # Not a recognized slash command - let queries that happen to start
        # with '/' pass through to the LLM.
        return False

    async def _cmd_model(self, session, args: str, writer: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()
        if not args:
            def get_model_list():
                lines = ["[bold]Available models:[/]"]
                current = self.model_id or get_assistant_default_model()
                for model in llm.get_models():
                    marker = " [green](current)[/]" if model.model_id == current else ""
                    lines.append(f"  - {model.model_id}{marker}")
                return "\n".join(lines)
            content = await loop.run_in_executor(None, get_model_list)
            await self._emit_text_done(writer, content)
            return
        try:
            # llm.get_model may do plugin discovery — keep it off the event loop
            if session is not None:
                await loop.run_in_executor(None, session.set_model, args)
            else:
                await loop.run_in_executor(None, llm.get_model, args)
            self.model_id = args
            await self._emit_text_done(writer, f"[green]Switched to model: {args}[/]")
        except Exception as e:
            await self._emit_text_done(writer, f"[red]Error switching model: {e}[/]")

    async def _cmd_squash(self, session, args: str, writer: asyncio.StreamWriter) -> None:
        if not session:
            await self._emit_text_done(writer, "[yellow]No active session to squash[/]")
            return
        keep = args if args else None
        try:
            session.squash_context(keep=keep)
            await self._emit_text_done(writer, "[green]Context squashed[/]")
        except Exception as e:
            await self._emit_text_done(writer, f"[red]Error squashing: {e}[/]")

    async def _cmd_mcp(self, session, args: str, writer: asyncio.StreamWriter) -> None:
        if not session:
            await self._emit_text_done(writer, "[yellow]MCP not available[/]")
            return

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
            await self._emit_text_done(writer, output)
        else:
            await self._emit(writer, {"type": "done"})

    async def _cmd_sources(self, session, args: str, writer: asyncio.StreamWriter) -> None:
        if not session:
            await self._emit_text_done(writer, "[yellow]No active session[/]")
            return
        action = args.lower() if args else "status"
        if action == "status":
            status = "enabled" if session.sources_enabled else "disabled"
            await self._emit_text_done(writer, f"[bold]Sources:[/] {status}")
        elif action == "on":
            session.sources_enabled = True
            await self._emit_text_done(writer, "[green]Sources enabled[/]")
        elif action == "off":
            session.sources_enabled = False
            await self._emit_text_done(writer, "[yellow]Sources disabled[/]")
        else:
            await self._emit_text_done(writer, "[yellow]Usage: /sources [on|off|status][/]")

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
            completions = await self._complete_models(prefix[6:])

        # Emit completions
        await self._emit(writer, {
            "type": "completions",
            "items": completions
        })
        await self._emit(writer, {"type": "done"})

    def _complete_slash_commands(self, prefix: str) -> List[Dict[str, str]]:
        """Complete slash commands (filtered for headless mode)."""
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
        handler = AtHandler()
        completions = handler.get_completions(prefix, cwd=cwd)
        return [
            {"text": c.text, "description": c.description}
            for c in completions
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

    async def _complete_models(self, prefix: str) -> List[Dict[str, str]]:
        """Complete model names. Runs llm.get_models() in an executor since
        plugin discovery can be slow and would otherwise block the event loop."""
        prefix_lower = prefix.lower()
        loop = asyncio.get_running_loop()

        try:
            models = await loop.run_in_executor(None, llm.get_models)
        except Exception as e:
            if self.debug:
                self.console.print(f"[yellow]Model completion failed: {e}[/]", highlight=False)
            return []

        return [
            {"text": m.model_id, "description": ""}
            for m in models
            if m.model_id.lower().startswith(prefix_lower)
        ]

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

    async def _emit_text_done(self, writer: asyncio.StreamWriter, content: str):
        """Emit a single text event followed by done — the most common reply shape."""
        await self._emit(writer, {"type": "text", "content": content})
        await self._emit(writer, {"type": "done"})

    async def handle_new(self, terminal_id: str, writer: asyncio.StreamWriter):
        """Handle /new command - start fresh conversation."""
        if terminal_id in self.sessions:
            state = self.sessions[terminal_id]
            state.session.reset_conversation()
            state.session.context_hashes = set()
            state.touch()
        await self._emit_text_done(writer, "New conversation started.")

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
            "uptime_minutes": int((datetime.now() - self.start_time).total_seconds()) // 60,
            "tools": tool_names,
            "active_workers": len(self.workers),
            "active_sessions": len(self.sessions),
        }

        await self._emit_text_done(writer, json.dumps(status, indent=2))

    async def handle_help(self, writer: asyncio.StreamWriter):
        """Handle help request - show available commands in headless mode."""
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

        await self._emit_text_done(writer, help_text)

    async def handle_shutdown(self, writer: asyncio.StreamWriter):
        """Handle shutdown request."""
        await self._emit_text_done(writer, "Shutting down daemon...")
        if self._stop_event is not None:
            self._stop_event.set()

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
            await self._emit_error(writer, ErrorCode.INTERNAL, "No active conversation")
            return

        remaining = state.session.truncate_responses(request.get('keep_turns', 0))
        await self._emit(writer, {"type": "truncated", "remaining": remaining})
        await self._emit(writer, {"type": "done"})

    async def handle_pop_response(self, terminal_id: str, writer: asyncio.StreamWriter):
        """Remove the last response from conversation.

        Request: {"cmd": "pop_response", "tid": "..."}
        Response: {"type": "popped", "remaining": N}

        Used by llm-guiassistant for regenerate functionality.
        """
        state = self.sessions.get(terminal_id)
        if not state or not state.session.conversation:
            await self._emit_error(writer, ErrorCode.INTERNAL, "No active conversation")
            return

        remaining = state.session.pop_response()
        await self._emit(writer, {"type": "popped", "remaining": remaining})
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
            await self._emit_error(writer, ErrorCode.PARSE_ERROR, "new_tid required")
            return

        source_state = self.sessions.get(terminal_id)
        if not source_state:
            await self._emit_error(writer, ErrorCode.INTERNAL, "Source session not found")
            return

        # One Response per assistant message.
        turns_to_keep = sum(1 for m in messages if m.get('role') == 'assistant')

        new_session = HeadlessSession(
            model_name=source_state.session.model_name,
            debug=source_state.session.debug,
            terminal_id=new_tid
        )

        responses_to_copy = source_state.session.fork_responses(turns_to_keep)
        if responses_to_copy:
            new_session.get_or_create_conversation()
            new_session.conversation.responses = responses_to_copy

        self.sessions[new_tid] = SessionState(new_tid, new_session)

        await self._emit(writer, {"type": "forked", "new_tid": new_tid, "messages": len(messages)})
        await self._emit(writer, {"type": "done"})

    async def handle_rag_activate(self, terminal_id: str, request: dict, writer: asyncio.StreamWriter):
        """Activate a RAG collection for this terminal's session.

        Request: {"cmd": "rag_activate", "tid": "...", "collection": "..."}
        Response: {"type": "text", "content": "RAG collection ... activated"}
        """
        collection = request.get('collection', '')

        if not collection:
            await self._emit_error(writer, ErrorCode.PARSE_ERROR, "Collection name required")
            return

        state = self.sessions.get(terminal_id)
        if not state:
            session = HeadlessSession(
                model_name=self.model_id,
                debug=self.debug,
                terminal_id=terminal_id,
            )
            state = SessionState(terminal_id, session)
            self.sessions[terminal_id] = state

        session = state.session

        # _handle_rag_command writes to session.console; capture it for the client.
        # Use the same helper that /rag uses in _handle_slash_command so output is consistent.
        try:
            output = self._call_session_handler(session, '_handle_rag_command', collection)
        except Exception as e:
            await self._emit_error(writer, ErrorCode.INTERNAL, str(e))
            return

        await self._emit_text_done(
            writer,
            output or f"RAG collection '{collection}' activated for this session"
        )

    async def idle_checker(self):
        """Periodically clean up inactive sessions to prevent memory leaks.

        The daemon stays running indefinitely until explicitly stopped.
        GUI clients (llm-guiassistant) depend on persistent daemon availability.
        Only idle sessions (no active worker, inactive for > IDLE_TIMEOUT_MINUTES)
        are evicted.
        """
        while self._stop_event is None or not self._stop_event.is_set():
            await asyncio.sleep(60)  # Check every minute
            now = datetime.now()
            stale_tids = []
            # Snapshot items to avoid RuntimeError if dict changes between awaits
            for tid, state in list(self.sessions.items()):
                idle_minutes = (now - state.last_activity).total_seconds() / 60
                if idle_minutes > IDLE_TIMEOUT_MINUTES and tid not in self.workers:
                    stale_tids.append(tid)
            for tid in stale_tids:
                self.sessions.pop(tid, None)

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

        # Run logs.db migration once at startup so it stays off the per-query path
        if self.logging_enabled:
            db_path = get_logs_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite_utils.Database(db_path)
            try:
                migrate(db)
                self._db_migrated = True
            finally:
                if db.conn:
                    db.conn.close()

        # Always write PID file to /tmp path so is_daemon_process_alive() works
        # in both foreground and daemon modes (python-daemon has its own pidfile
        # at ~/.config/, but clients check the /tmp path)
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
            # Always log web UI failure (not just foreground) so daemon mode
            # failures are visible in the log file
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

        # Ignore SIGPIPE — clients may disconnect while we're writing,
        # which is handled gracefully via writer.is_closing() checks
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        # Must be created BEFORE registering signal handlers, otherwise a signal
        # arriving in the window between registration and event creation would
        # leave the main loop waiting on an event that is never set.
        self._stop_event = asyncio.Event()

        # Register signal handlers with the event loop for proper asyncio integration
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

        # Start idle checker
        idle_task = asyncio.create_task(self.idle_checker())

        try:
            await self._stop_event.wait()
        finally:
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass

            # Cancel all workers and await them
            # Snapshot task list before cancelling — workers delete from self.workers
            # in their finally blocks, so we must not iterate the live dict
            worker_tasks = list(self.workers.values())
            for task in worker_tasks:
                task.cancel()
            if worker_tasks:
                await asyncio.gather(*worker_tasks, return_exceptions=True)
            self.workers.clear()

            # Stop web UI server
            if self.web_server:
                await self.web_server.stop()

            self.server.close()
            await self.server.wait_closed()

            # Clean up socket and PID file
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
            # Always remove /tmp PID file (python-daemon handles its own ~/.config/ pidfile)
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

    # Signal handlers are registered inside run() via loop.add_signal_handler()
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
