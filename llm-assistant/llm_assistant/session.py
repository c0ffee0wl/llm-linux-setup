"""Main session manager for llm-assistant.

This module contains the TerminatorAssistantSession class which orchestrates:
- Terminal management via D-Bus
- LLM conversation flow
- Tool execution
- Context management
- Watch mode
- Web companion
"""

import sys
import base64
import json
import uuid

import llm
from llm import Tool, ToolResult, ToolOutput, Attachment
from llm.cli import process_fragments_in_chat
from llm.migrations import migrate
import sqlite_utils

# Add system site-packages to path for dbus and other system-only packages
# (Must be AFTER llm imports to avoid typing_extensions conflicts)
sys.path.insert(0, '/usr/lib/python3/dist-packages')
# Add user site-packages for llm_tools module (uv's isolated env doesn't include it)
import site
sys.path.insert(0, site.getusersitepackages())
import os
import re
import readline  # Required for \001/\002 prompt markers to work with input()
import time
import hashlib
import tempfile
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import dbus
import fcntl
import signal
import atexit
from pathlib import Path
from datetime import date
from collections import deque
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.status import Status
from rich.text import Text
from typing import List, Optional, Tuple, Dict, Union, Set

# YAML for findings file parsing
import yaml

# prompt_toolkit for keybindings (Ctrl+Space for voice) and completion
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PTStyle

# Local module imports
from llm_tools_core import PromptDetector
from .system_info import detect_os, detect_shell, detect_environment
from .voice import VoiceInput, VOICE_AVAILABLE, VOICE_UNAVAILABLE_REASON
from .speech import SpeechOutput, SentenceBuffer, TTS_AVAILABLE
from .ui import Spinner, Confirm
from .completer import SlashCommandCompleter
from .config import (
    SLASH_COMMANDS,
    TUI_COMMANDS,
    EXTERNAL_TOOL_DISPLAY,
    is_tui_command,
)
from .schemas import FindingSchema, SafetySchema
from .utils import (
    strip_markdown_for_tts, strip_markdown_for_clipboard, validate_language_code,
    get_config_dir, get_temp_dir, get_logs_db_path, logs_on,
    process_exists, md_table_escape, yaml_escape, is_watch_response_dismissive,
    get_model_context_limit, ConsoleHelper,
)
from .templates import render
from .kb import KnowledgeBaseMixin
from .memory import MemoryMixin
from .rag import RAGMixin
from .skills import SkillsMixin
from .report import ReportMixin
from .web import WebMixin
from .terminal import TerminalMixin
from .context import ContextMixin
from llm_tools_core import filter_new_blocks
from .watch import WatchMixin
from .workflow import WorkflowMixin
from .mcp import (
    MCPMixin,
    _ensure_mcp_loaded,
    _all_tools,
    ASSISTANT_TOOLS,
    EXTERNAL_TOOLS,
    AGENT_EXTERNAL_TOOLS,
    OPTIONAL_EXTERNAL_TOOLS,
)

# Clipboard support for /copy command
try:
    import pyperclip
    CLIPBOARD_AVAILABLE = True
except ImportError:
    CLIPBOARD_AVAILABLE = False

# Web companion (optional - graceful degradation if not installed)
try:
    from fastapi import FastAPI, WebSocket
    from fastapi.responses import HTMLResponse
    import uvicorn
    import webbrowser
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False



class TerminatorAssistantSession(KnowledgeBaseMixin, MemoryMixin, RAGMixin, SkillsMixin, WorkflowMixin, ReportMixin, WebMixin, TerminalMixin, ContextMixin, WatchMixin, MCPMixin):
    """Main assistant session manager for Terminator"""

    def __init__(self, model_name: Optional[str] = None, debug: bool = False, max_context_size: Optional[int] = None,
                 continue_: bool = False, conversation_id: Optional[str] = None, no_log: bool = False,
                 agent_mode: bool = False, no_exec: bool = False):
        self.console = Console()

        # Debug mode flag
        self.debug = debug

        # No-exec mode: run without Terminator/D-Bus, use asciinema context
        self.no_exec_mode = no_exec
        self._asciinema_prev_hashes: set = set()  # Hash tracking for asciinema context dedup

        # Conversation persistence settings
        # Respect both --no-log flag and llm's global logs-off setting
        self.logging_enabled = logs_on() and not no_log
        self.continue_ = continue_
        self.conversation_id = conversation_id

        # Initialize database early (like llm chat command)
        # This ensures the config directory and database exist before any operations
        if self.logging_enabled:
            log_path = get_logs_db_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite_utils.Database(log_path)
            migrate(db)

        # Initialize shutdown state and lock file handle
        self._shutdown_initiated = False
        self._last_interrupt_time = 0.0  # For double-press exit protection
        self.lock_file = None

        # Early D-Bus detection - require Terminator (unless --no-exec mode)
        if not self.no_exec_mode:
            self.early_terminal_uuid = self._get_current_terminal_uuid_early()

            if not self.early_terminal_uuid:
                ConsoleHelper.error(self.console, "llm-assistant requires Terminator terminal (use --no-exec for other terminals)")
                ConsoleHelper.warning(self.console, "Please run this from inside a Terminator terminal, or use --no-exec flag.")
                sys.exit(1)

            # Acquire per-tab lock (held for entire session, prevents duplicates)
            self._acquire_instance_lock()
        else:
            self.early_terminal_uuid = None
            # No instance lock in no-exec mode - can run multiple instances

        # Register shutdown handlers EARLY (before creating resources)
        self._register_shutdown_handlers()

        self.model_name = model_name or self._get_default_model()

        try:
            self.model = llm.get_model(self.model_name)
        except Exception as e:
            ConsoleHelper.error(self.console, f"Error loading model '{self.model_name}': {e}")
            ConsoleHelper.warning(self.console, "Available models:")
            for model in llm.get_models():
                self.console.print(f"  - {model.model_id}")
            sys.exit(1)

        self._init_conversation()

        # Operating mode must be set BEFORE rendering system prompt
        # (Jinja2 template uses mode to conditionally include agent/assistant content)
        self.mode: str = "agent" if agent_mode else "assistant"

        # Watch mode state (must be initialized before _render_system_prompt)
        self.watch_mode = False
        self.watch_goal = None

        # RAG state (must be initialized before _render_system_prompt)
        self.active_rag_collection: Optional[str] = None

        # Skills state (must be initialized before _render_system_prompt)
        # Uses generic dict since SkillProperties is imported dynamically
        self.loaded_skills: Dict[str, Tuple[Path, any]] = {}  # name -> (path, props)
        self._skill_invoke_tool: Optional[Tool] = None
        self._skill_load_file_tool: Optional[Tool] = None

        # Render system prompt using Jinja2 template
        # Template handles mode filtering and environment injection
        self.system_prompt = self._render_system_prompt()
        self._debug(f"System prompt rendered for {self.mode} mode ({len(self.system_prompt)} chars)")

        # Store original system prompt for context squashing
        # This prevents infinite growth when squashing multiple times
        self.original_system_prompt = self.system_prompt

        # Memory system (AGENTS.md)
        self._global_memory = ""
        self._global_memory_path = None
        self._local_memory = ""
        self._local_memory_path = None
        self._load_memories()

        # Terminal tracking
        self.chat_terminal_uuid = None
        self.exec_terminal_uuid = None

        # Context management (like tmuxai)
        # Resolve context limit: CLI override > model-specific > default
        if max_context_size is not None:
            self.max_context_size = max_context_size
        else:
            self.max_context_size = self._get_model_context_limit(self.model_name)
        self._debug(f"Context limit for {self.model_name}: {self.max_context_size:,} tokens")
        self.context_squash_threshold = 0.8  # 80%

        # Capture size limits (prevent memory exhaustion)
        self.MAX_CAPTURE_ROWS = 10000      # Maximum rows to capture
        self.MAX_CAPTURE_BYTES = 10_000_000  # 10MB byte limit
        self.MAX_CAPTURE_CHARS = 2_000_000   # 2M character limit (~500k tokens)

        # Note: self.mode is already set earlier (before _render_system_prompt)
        # Operating mode controls tool iteration limits and system prompt content

        # Dynamically loaded optional tools (e.g., {'imagemage'})
        # Tools here are added to the model's available tools list
        # Must be initialized before _estimate_tool_schema_tokens()
        self.loaded_optional_tools: set = set()

        # Active MCP servers - tracks which servers' tools are available
        # Initialized with default (non-optional) servers; optional servers must be loaded via /mcp load
        self.active_mcp_servers: set = self._get_default_mcp_servers()

        # Tool token overhead (estimated at startup, cached for session)
        self._tool_token_overhead = self._estimate_tool_schema_tokens()

        # Watch mode threading (watch_mode/watch_goal initialized earlier)
        self.watch_thread = None
        self.watch_task = None  # Asyncio task for graceful cancellation
        self.watch_interval = 5  # seconds
        self.watch_lock = threading.Lock()
        self.event_loop = None
        # Watch mode intelligent change detection
        self.previous_watch_context_hash = None  # SHA256 hash for deduplication
        self.previous_watch_iteration_count = 0   # Track iterations for prompt

        # Web companion (real-time browser view)
        self.web_server = None          # uvicorn server instance
        self.web_server_thread = None   # Thread running the server
        self.web_clients: set = set()   # Connected WebSocket clients
        self.web_event_loop = None      # Event loop for web server thread
        self.web_port = 8765            # Default port for web companion

        # Per-terminal content hash tracking for deduplication
        # Enables [Content unchanged] placeholder when terminal content hasn't changed
        self.terminal_content_hashes: Dict[str, str] = {}  # uuid -> content hash
        # Track terminals whose hash was updated by tool result (for specific message)
        self.toolresult_hash_updated: set = set()  # uuid set
        # Per-terminal block hashes from previous capture (for delta diffing)
        # Only send command blocks that weren't in the previous capture
        self.previous_capture_block_hashes: Dict[str, Set[str]] = {}  # uuid -> set of block hashes

        # D-Bus connections
        self.dbus_service = None

        # Track screenshot files for cleanup (use dedicated temp directory)
        self.screenshot_dir = self._setup_screenshot_dir()
        self.screenshot_files = []

        # Pending attachments for multi-modal viewing (deferred to next turn)
        self.pending_attachments = []

        # Pending summary from context squash (prepended to next user message)
        self.pending_summary: Optional[str] = None

        # Undo buffer for /rewind command (stores removed responses for single undo)
        self.rewind_undo_buffer: Optional[List] = None

        # Knowledge Base system (TmuxAI-style)
        self.loaded_kbs: Dict[str, str] = {}  # name -> content
        self._load_auto_kbs()  # Load from config on startup

        # Skills auto-load (all skills in skills directory)
        self._auto_load_all_skills()

        # RAG system (llm-tools-rag integration)
        # Note: self.active_rag_collection initialized earlier (before _render_system_prompt)
        self.rag_top_k: int = 5                           # Number of results to retrieve
        self.rag_search_mode: str = "hybrid"              # hybrid|vector|keyword
        self.pending_rag_context: Optional[str] = None    # One-shot search result

        # Workflow system (burr_workflow integration)
        self._workflow_init()

        # Auto mode: LLM-judged autonomous command execution
        # False = off, "normal" = safe only, "full" = safe + caution
        self.auto_mode: Union[bool, str] = False

        # Queued prompt from /capture with inline prompt
        self._queued_prompt: Optional[str] = None

        # Voice auto-submit: automatically send transcribed text
        self.voice_auto_submit: bool = False
        self.auto_command_history: deque = deque(maxlen=3)  # recent commands for judge context
        self.DANGEROUS_PATTERNS = [
            r"rm\s+-rf\s+/(?:\s|$)",      # rm -rf /
            r"rm\s+-rf\s+/\*",             # rm -rf /*
            r"rm\s+-rf\s+~",               # rm -rf ~
            r"dd\s+if=.*of=/dev/",         # dd to device
            r"mkfs\.",                     # format filesystem
            r">\s*/dev/sd",                # redirect to disk
            r":\(\)\{\s*:\|:&\s*\};:",     # fork bomb
            r"chmod\s+-R\s+777\s+/(?:\s|$)",  # chmod 777 /
            r"chown\s+-R.*:\s*/(?:\s|$)",  # chown / recursively
            r"curl.*\|\s*(ba)?sh",         # curl pipe to shell
            r"wget.*\|\s*(ba)?sh",         # wget pipe to shell
        ]

        # Pentest findings management
        self.findings_project: Optional[str] = None  # Current project name
        self.findings_base_dir: Path = get_config_dir() / "findings"

        # Voice input (STT) - lazy-loaded
        self.voice_input = VoiceInput(self.console) if VOICE_AVAILABLE else None

        # Speech output (TTS) - lazy-loaded, only for Vertex models
        self.speech_output = SpeechOutput(self.console) if TTS_AVAILABLE else None

        # prompt_toolkit session with Ctrl+Space voice toggle
        self.prompt_session = self._create_prompt_session()

        # Set session reference for slash command completer (dynamic completions need access to self)
        if hasattr(self.prompt_session, 'completer') and self.prompt_session.completer:
            self.prompt_session.completer.set_session(self)

    def _create_prompt_session(self) -> PromptSession:
        """Create prompt_toolkit session with voice toggle keybinding and slash command completion."""
        kb = KeyBindings()

        # Store reference to self for closure
        session = self

        @kb.add('c-space')
        def _(event):
            """Toggle voice recording on Ctrl+Space."""
            if not session.voice_input:
                session.console.print("[dim]Voice input not available[/]")
                return

            buffer = event.app.current_buffer

            # If not currently recording, save text before starting
            if not session.voice_input.recording:
                session.voice_input.preserved_text = buffer.text

            # Give VoiceInput access to app for invalidate() during transcription
            session.voice_input._app = event.app

            is_recording, text = session.voice_input.toggle()

            if is_recording:
                # Recording just started - invalidate to show ⏺ prompt
                event.app.invalidate()
            else:
                # Recording stopped - restore display
                existing_text = session.voice_input.preserved_text

                if text:
                    # Construct new text from preserved + transcribed
                    if existing_text:
                        if existing_text.endswith(' '):
                            new_text = existing_text + text
                        else:
                            new_text = existing_text + ' ' + text
                    else:
                        new_text = text
                    buffer.text = new_text
                    buffer.cursor_position = len(new_text)
                    # Auto-submit if enabled
                    if session.voice_auto_submit:
                        buffer.validate_and_handle()
                elif existing_text:
                    # Recording stopped but no transcription - restore existing text
                    buffer.text = existing_text
                    buffer.cursor_position = len(existing_text)

                # Invalidate to show > prompt and updated text
                event.app.invalidate()

        @kb.add('escape')
        def _(event):
            """Stop TTS playback on Escape."""
            if session.speech_output and session.speech_output.enabled:
                session.speech_output.stop()

        # Style for the prompt
        style = PTStyle.from_dict({
            'prompt': 'ansicyan bold',
            'prompt.recording': 'ansired',
            'prompt.transcribing': '#ff8800',  # orange
            'continuation': 'ansigray',
        })

        # Create completer for slash commands (session reference set after __init__)
        completer = SlashCommandCompleter()

        return PromptSession(
            key_bindings=kb,
            style=style,
            completer=completer,
            complete_while_typing=False,  # Only complete on Tab
        )

    def _acquire_instance_lock(self):
        """
        Acquire per-tab lock using TERMINATOR_UUID.

        This provides 100% reliable one-assistant-per-tab enforcement:
        - Lock is tied to specific tab via TERMINATOR_UUID
        - Kernel automatically releases lock on process death (no stale locks)
        - No need for PID tracking, title parsing, or D-Bus queries
        - Race-condition free (fcntl.flock is atomic)

        Security: Uses per-user runtime directory with 0600 permissions.
        """
        terminal_uuid = self.early_terminal_uuid

        # Use per-user lock directory
        runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
        if not runtime_dir or not Path(runtime_dir).is_dir():
            runtime_dir = get_temp_dir()
            runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            runtime_dir = str(runtime_dir)

        # Per-tab lock file
        lock_path = Path(runtime_dir) / f'llm-assistant-{terminal_uuid}.lock'

        try:
            fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o600)
            self.lock_file = os.fdopen(fd, 'w')
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file.write(f"{os.getpid()}\n")
            self.lock_file.flush()

        except BlockingIOError:
            # Another instance already has this tab's lock
            ConsoleHelper.error(self.console, "An assistant is already running in this tab")
            ConsoleHelper.warning(self.console, "You can run assistant in a different Terminator tab.")
            sys.exit(1)

        except Exception as e:
            ConsoleHelper.error(self.console, f"Error acquiring lock: {e}")
            if self.lock_file:
                try:
                    self.lock_file.close()
                except Exception:
                    pass
            sys.exit(1)

    def _release_instance_lock(self):
        """Release the instance lock (automatic on process exit, but explicit is better)"""
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
                self.lock_file = None
            except Exception:
                pass  # Lock will be released by kernel anyway

    def _setup_screenshot_dir(self) -> Path:
        """
        Set up dedicated directory for screenshot temp files.
        Also cleans up any orphaned files from crashed sessions.

        Returns:
            Path to screenshot directory
        """
        # Use user-specific temp directory for security
        base_dir = get_temp_dir()
        screenshot_dir = base_dir / 'screenshots'

        try:
            screenshot_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

            # Clean up orphaned screenshot files from crashed sessions
            # Files older than 1 hour are considered orphaned
            cleanup_threshold = time.time() - 3600  # 1 hour ago
            for old_file in screenshot_dir.glob('assistant_screenshot_*.png'):
                try:
                    if old_file.stat().st_mtime < cleanup_threshold:
                        old_file.unlink()
                except OSError:
                    pass  # File may have been deleted by another process
        except OSError as e:
            # Fallback to system temp if we can't create our directory
            self._debug(f"Could not create screenshot dir: {e}")
            screenshot_dir = get_temp_dir()

        return screenshot_dir

    def _get_model_capabilities(self):
        """
        Query current model's multi-modal capabilities.

        Returns a dict with capability flags based on model.attachment_types.
        """
        types = getattr(self.conversation.model, 'attachment_types', set())
        return {
            'vision': any(t.startswith("image/") for t in types),
            'pdf': "application/pdf" in types,
            'audio': any(t.startswith("audio/") for t in types),
            'video': any(t.startswith("video/") for t in types),
            'youtube': "video/youtube" in types,  # Gemini only
            'supported_types': types,
        }

    def _create_attachment(self, path_or_url: str) -> Attachment:
        """
        Create an Attachment from a path or URL.

        Automatically detects whether the input is a URL (http/https) or local path
        and sets the appropriate Attachment field.

        Args:
            path_or_url: Local file path or URL

        Returns:
            Attachment object with path or url set appropriately
        """
        is_url = path_or_url.startswith('http')
        return Attachment(
            path=None if is_url else path_or_url,
            url=path_or_url if is_url else None
        )

    def _register_shutdown_handlers(self):
        """Register signal handlers and atexit hook for cleanup"""
        # Register atexit fallback (runs on normal exit)
        atexit.register(self._shutdown)

        # Register signal handlers for graceful shutdown
        # Note: SIGINT (Ctrl+C) uses Python's default KeyboardInterrupt - handled in input loop
        signal.signal(signal.SIGTERM, self._signal_handler)  # kill command
        signal.signal(signal.SIGHUP, self._signal_handler)   # Terminal closed

    def _signal_handler(self, signum, frame):
        """Handle termination signals (SIGTERM, SIGHUP)."""
        signal_names = {
            signal.SIGTERM: "SIGTERM",
            signal.SIGHUP: "SIGHUP (terminal closed)"
        }
        signal_name = signal_names.get(signum, f"signal {signum}")

        self.console.print()
        ConsoleHelper.warning(self.console, f"Received {signal_name}, shutting down...")
        self._shutdown()
        sys.exit(0)

    def _shutdown(self):
        """
        Unified shutdown method - called by signal handlers, atexit, or manual exit.
        Idempotent - safe to call multiple times.
        """
        # Prevent double-cleanup
        if self._shutdown_initiated:
            return
        self._shutdown_initiated = True

        try:
            # STEP 1: Stop watch mode (most critical - prevents new threads)
            if hasattr(self, 'watch_mode') and self.watch_mode:
                try:
                    with self.watch_lock:
                        self.watch_mode = False
                        if self.watch_task and not self.watch_task.done():
                            try:
                                self.event_loop.call_soon_threadsafe(self.watch_task.cancel)
                            except RuntimeError:
                                pass  # Loop already closed

                    # Wait for watch thread to finish (with timeout)
                    if self.watch_thread and self.watch_thread.is_alive():
                        self.watch_thread.join(timeout=2.0)
                except Exception:
                    # Don't let watch mode cleanup prevent other cleanup
                    pass

            # STEP 2: Clean up screenshot files
            if hasattr(self, 'screenshot_files') and self.screenshot_files:
                for screenshot_path in self.screenshot_files:
                    try:
                        if os.path.exists(screenshot_path):
                            os.unlink(screenshot_path)
                    except (IOError, OSError):
                        pass  # Ignore cleanup errors
                self.screenshot_files.clear()

            # STEP 3: Clear plugin cache (if available)
            if hasattr(self, 'plugin_dbus'):
                try:
                    self.plugin_dbus.clear_cache()
                except Exception:
                    pass

            # STEP 3.25: Stop content change receiver (signal-based monitoring)
            if hasattr(self, 'content_change_receiver') and self.content_change_receiver:
                try:
                    self.content_change_receiver.stop()
                    self.content_change_receiver = None
                except Exception:
                    pass

            # STEP 3.5: Stop web companion server (if running)
            if hasattr(self, 'web_server') and self.web_server:
                try:
                    self.web_server.should_exit = True
                    self.web_server = None
                    self.web_server_thread = None
                    self.web_event_loop = None
                    self.web_clients.clear()
                except Exception:
                    pass

            # STEP 4: Close D-Bus connections (graceful disconnect)
            # D-Bus connections don't need explicit cleanup in Python - garbage collected
            # But we can dereference them to signal intent
            if hasattr(self, 'dbus_service'):
                self.dbus_service = None
            if hasattr(self, 'plugin_dbus'):
                self.plugin_dbus = None

            # STEP 5: Release instance lock (most important)
            self._release_instance_lock()

            # STEP 6: Save conversation state (if needed)
            # llm library auto-saves, but we can explicitly flush here
            if hasattr(self, 'conversation'):
                try:
                    # Force any pending writes to complete
                    # llm Conversation doesn't have explicit save(), it auto-saves
                    pass
                except Exception:
                    pass

        except Exception as e:
            # Ensure we log errors but don't prevent shutdown
            try:
                ConsoleHelper.warning(self.console, f"Warning during shutdown: {e}")
            except Exception:
                # If console fails, write to stderr
                print(f"Warning during shutdown: {e}", file=sys.stderr)

    def _get_default_model(self) -> str:
        """Get default model from llm configuration.

        If default is gpt-4.1-mini, upgrade to gpt-4.1 for assistant
        (assistant benefits from more capable model).
        """
        try:
            model = llm.get_default_model()
            # Upgrade mini to full model for assistant
            if model == "azure/gpt-4.1-mini":
                return "azure/gpt-4.1"
            return model
        except Exception:
            return "azure/gpt-4.1"

    def _is_vertex_model(self) -> bool:
        """Check if current model is a Vertex AI model (vertex/*)"""
        return self.model_name.startswith("vertex/")

    def _is_gemini_model(self) -> bool:
        """Check if current model is a Gemini model (vertex/* or gemini-*)"""
        return self.model_name.startswith("vertex/") or self.model_name.startswith("gemini-")

    def _get_model_context_limit(self, model_name: str) -> int:
        """Get the appropriate context limit for a model.

        Delegates to get_model_context_limit() utility function.
        """
        return get_model_context_limit(model_name)

    def _debug(self, msg: str):
        """Print debug message if debug mode is enabled"""
        if self.debug:
            ConsoleHelper.dim(self.console, f"DEBUG: {msg}")

    def _stream_response_with_display(self, response, tts_enabled: bool = False) -> str:
        """
        Stream response with Live markdown display and optional TTS.

        Args:
            response: LLM Response object (iterable)
            tts_enabled: Whether to queue sentences to TTS

        Returns:
            Full accumulated response text
        """
        accumulated_text = ""
        sentence_buffer = SentenceBuffer() if tts_enabled else None

        # Use transient=True initially, switch to False only if we have content
        with Live(Markdown(""), refresh_per_second=10, console=self.console, transient=True) as live:
            for chunk in response:
                accumulated_text += chunk
                live.update(Markdown(accumulated_text))
                # Once we have content, make it persistent
                if accumulated_text.strip():
                    live.transient = False

                # Broadcast to web companion (non-blocking)
                if self.web_clients:
                    self._broadcast_to_web({
                        "type": "assistant_chunk",
                        "content": accumulated_text,
                        "done": False
                    })

                # Queue TTS if enabled (non-blocking)
                if tts_enabled and sentence_buffer:
                    sentence = sentence_buffer.add(chunk)
                    if sentence:
                        self.speech_output.speak_sentence(sentence)

        # Flush remaining TTS
        if tts_enabled and sentence_buffer:
            remaining = sentence_buffer.flush()
            if remaining:
                self.speech_output.speak_sentence(remaining)

        # Broadcast completion to web companion (always send for cleanup, web UI filters empty)
        if self.web_clients:
            complete_msg = {
                "type": "assistant_complete",
                "content": accumulated_text,
                "done": True
            }
            # Include thinking traces if available
            if hasattr(response, 'response_json') and response.response_json:
                thinking = response.response_json.get("thinking_traces", [])
                if thinking:
                    complete_msg["thinking_traces"] = thinking
            self._broadcast_to_web(complete_msg)
            # Update token usage bar after response (only if non-empty)
            if accumulated_text.strip():
                self._broadcast_token_update()

        return accumulated_text

    def _truncate_capture_if_needed(self, content: str, source_desc: str = "content") -> str:
        """
        Truncate captured content if it exceeds size limits.

        Adds warning message when truncation occurs.

        Args:
            content: Content to check/truncate
            source_desc: Description of content source (for warning message)

        Returns:
            Original or truncated content with warning
        """
        if not content:
            return content

        original_size = len(content)
        byte_size = len(content.encode('utf-8'))

        # Check if truncation needed
        needs_truncation = False
        truncation_reason = []

        if byte_size > self.MAX_CAPTURE_BYTES:
            needs_truncation = True
            truncation_reason.append(f"{byte_size:,} bytes > {self.MAX_CAPTURE_BYTES:,} byte limit")

        if original_size > self.MAX_CAPTURE_CHARS:
            needs_truncation = True
            truncation_reason.append(f"{original_size:,} chars > {self.MAX_CAPTURE_CHARS:,} char limit")

        if not needs_truncation:
            return content

        # Truncate to character limit (simpler than byte-aware truncation)
        truncated = content[:self.MAX_CAPTURE_CHARS]

        # Add warning header
        warning = (
            f"\n{'='*60}\n"
            f"WARNING: {source_desc.capitalize()} truncated\n"
            f"Reason: {', '.join(truncation_reason)}\n"
            f"Showing first {len(truncated):,} of {original_size:,} characters\n"
            f"{'='*60}\n\n"
        )

        result = warning + truncated

        self._debug(f"Truncated {source_desc}: {original_size:,} -> {len(truncated):,} chars")
        ConsoleHelper.warn_icon(self.console, f"{source_desc.capitalize()} truncated ({original_size:,} chars)")

        return result

    def _render_system_prompt(self) -> str:
        """Render system prompt using Jinja2 template.

        Called at init and when mode changes (/assistant or /agent).
        Jinja2 template handles mode filtering and environment injection.
        """
        os_info = detect_os()
        shell_name, shell_version = detect_shell()
        shell_info = f"{shell_name} {shell_version}".strip() if shell_version else shell_name
        env_type = detect_environment()

        return render('system_prompt.j2',
            mode=self.mode,
            date=date.today().isoformat(),
            platform=os_info,
            shell=shell_info,
            environment=env_type,
            watch_mode=self.watch_mode,
            watch_goal=self.watch_goal,
            rag_active=bool(self.active_rag_collection),
            skills_active=bool(self.loaded_skills),
            skills_xml=self._get_skills_xml() if self.loaded_skills else "",
            exec_active=not self.no_exec_mode,
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt with memory, KB, and workflow context appended.

        The base system prompt is rendered by Jinja2 at init/mode change.
        This method appends memory, KB, and workflow context for the current request.
        """
        prompt = self.system_prompt

        # Append memory content (AGENTS.md) - before KB
        memory_content = self._get_memory_content()
        if memory_content:
            prompt = f"{prompt}\n\n<memory>\n# Persistent Memory\n\n{memory_content}\n</memory>"

        # Append KB content if any loaded
        kb_content = self._get_loaded_kb_content()
        if kb_content:
            prompt = f"{prompt}\n\n<knowledge>\n# Knowledge Base\n\n{kb_content}\n</knowledge>"

        # Append workflow context if a workflow is active (from WorkflowMixin)
        workflow_context = self._get_workflow_context()
        if workflow_context:
            prompt = f"{prompt}\n\n{workflow_context}"

        return prompt

    # =========================================================================
    # Configuration
    # =========================================================================

    def _get_config_file(self) -> Path:
        """Get config file path in config directory."""
        return get_config_dir() / "assistant-config.yaml"

    def _load_config(self) -> dict:
        """Load config.yaml if it exists."""
        config_file = self._get_config_file()
        if config_file.exists():
            try:
                import yaml
                with open(config_file) as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                return {}
        return {}

    # KB, RAG, Skills, Terminal, Context, Watch methods are now in mixin classes

    def prompt_based_capture(self, terminal_uuid, max_wait=60, initial_content=None) -> Tuple[bool, str, str]:
        """
        Capture terminal content using prompt detection instead of stability checks.

        Uses signal-based monitoring when available: subscribes to terminal content
        changes and reacts immediately when content changes, instead of fixed polling.
        Falls back to polling if signal receiver is not available.

        Stops when content has changed from initial state AND a shell prompt is
        detected at the end. This prevents false positives from the old prompt
        that was visible before the command started.

        Falls back to timeout for long-running commands or TUI applications.

        This is ideal for:
        - Command-line tools that return to prompt quickly
        - Exec terminal command execution
        - Cases where prompt detection is more reliable than stability

        Args:
            terminal_uuid: Terminal to capture
            max_wait: Maximum wait time in seconds (default: 60)
            initial_content: Terminal content before command was sent (for change detection)

        Returns:
            Tuple of (prompt_detected, content, detection_method)
            detection_method is "marker", "regex", or "" if not detected
        """
        # Configuration
        signal_timeout = 0.5  # Timeout for signal wait (also serves as poll fallback)
        status_display_threshold = 0.5  # Only show status if waiting longer than this

        # Subscribe to content changes if receiver is available (check early for delay decision)
        use_signals = (hasattr(self, 'content_change_receiver') and
                      self.content_change_receiver and
                      self.content_change_receiver.is_running())

        # Reduced initial delay when signals available (faster response)
        initial_delay = 0.1 if use_signals else 0.3
        time.sleep(initial_delay)

        content_changed = initial_content is None  # If no initial content, skip change detection
        content = ""  # Initialize to avoid NameError if loop doesn't execute
        start_time = time.time()

        # Subscribe to content changes (use_signals already determined above)
        if use_signals:
            self.subscribe_content_changes(terminal_uuid)
            # Drain any pending signals from before subscription
            self.content_change_receiver.get_all_changes()
            self._debug("Using signal-based prompt detection")

        try:
            # Use Rich Status for visual feedback (initially hidden until threshold)
            with Status("", console=self.console, spinner="dots", spinner_style="cyan") as status:
                while time.time() - start_time < max_wait:
                    try:
                        # Wait for content change signal or timeout
                        if use_signals:
                            # Block until signal or timeout
                            changed_uuid = self.content_change_receiver.get_change(timeout=signal_timeout)
                            # Debug logging for signal monitoring
                            if changed_uuid:
                                self._debug(f"Signal received for terminal {changed_uuid}")
                            else:
                                self._debug("Timeout waiting for signal")
                            # Only process if it's our terminal (or timeout)
                            if changed_uuid and changed_uuid != terminal_uuid:
                                continue  # Signal for different terminal, keep waiting
                        else:
                            # Fallback to polling
                            time.sleep(signal_timeout)

                        # Capture current content
                        content = self.plugin_dbus.capture_terminal_content(terminal_uuid, -1)

                        # First, check if content has changed from initial state
                        if not content_changed:
                            if content != initial_content:
                                content_changed = True
                                self._debug("Content changed from initial state")

                        # Only check for prompt after content has changed
                        if content_changed and content:
                            detected, method = PromptDetector.detect_prompt_at_end_with_method(content)
                            if detected:
                                return (True, content, method)

                        # Visual feedback only after threshold (skip for fast commands)
                        elapsed = time.time() - start_time + initial_delay
                        if elapsed >= status_display_threshold:
                            status_msg = "Waiting for output" if not content_changed else "Waiting for prompt"
                            status.update(f"[cyan]{status_msg} ({elapsed:.1f}s)[/]")

                    except dbus.exceptions.DBusException as e:
                        ConsoleHelper.error(self.console, f"Plugin D-Bus error during capture: {e}")
                        return (False, "", "")
                    except Exception as e:
                        ConsoleHelper.error(self.console, f"Prompt-based capture error ({type(e).__name__}): {e}")
                        return (False, "", "")

            # Timeout - return last content
            return (False, content if content else "", "")

        finally:
            # Always unsubscribe to prevent resource leaks
            if use_signals:
                self.unsubscribe_content_changes(terminal_uuid)

    def _capture_last_command_output(self, terminal_uuid: str) -> str:
        """
        Capture recent commands' output intelligently using dynamic extension.
        Starts with viewport-sized capture, expands if needed to find command boundaries.

        This ensures that even if the user manually runs a command with output
        exceeding the viewport, the full output is captured (similar to tool execution).

        Captures the last MAX_RECENT_COMMANDS commands to provide context when
        user runs multiple commands between assistant interactions.

        Args:
            terminal_uuid: Terminal UUID to capture from

        Returns:
            String containing recent commands' prompts + outputs
        """
        MAX_LINES = 5000        # Hard limit on capture range
        MAX_RECENT_COMMANDS = 3 # Number of recent commands to capture

        # Get actual viewport size instead of hardcoded value
        scrollback_info = self.get_scrollback_info(terminal_uuid)
        initial_lines = scrollback_info.get('visible_lines', 50) if scrollback_info else 50

        try:
            _, cursor_row = self.plugin_dbus.get_cursor_position(terminal_uuid)
        except Exception:
            # Fallback to viewport capture
            return self.plugin_dbus.capture_terminal_content(terminal_uuid, -1)

        lines_to_capture = initial_lines

        while lines_to_capture <= MAX_LINES:
            start_row = max(0, cursor_row - lines_to_capture)

            try:
                content = self.plugin_dbus.capture_from_row(terminal_uuid, start_row)
            except Exception:
                return self.plugin_dbus.capture_terminal_content(terminal_uuid, -1)

            if not content or content.startswith('ERROR'):
                return self.plugin_dbus.capture_terminal_content(terminal_uuid, -1)

            # Find prompt lines to identify command boundaries
            prompts = PromptDetector.find_all_prompts(content)

            # Filter out false positives: in VTE terminals with Unicode markers,
            # only prompts containing INPUT_START_MARKER are real shell prompts.
            # Command output (like README content) won't have markers.
            # Note: For Kali two-line prompts, find_all_prompts returns the header line,
            # but INPUT_START_MARKER is on the next line, so check line and line+1.
            # DON'T check line-1: that would find markers from PREVIOUS commands.
            if PromptDetector.has_unicode_markers(content):
                lines = content.split('\n')
                real_prompts = []
                for line_num, line_content in prompts:
                    # Check this line and next line for marker (handles Kali header → prompt)
                    has_marker = False
                    for offset in [0, 1]:  # Check line and line+1 only
                        check_line = line_num + offset
                        if 0 <= check_line < len(lines):
                            if PromptDetector.INPUT_START_MARKER in lines[check_line]:
                                has_marker = True
                                break
                    if has_marker:
                        real_prompts.append((line_num, line_content))
                prompts = real_prompts

            # Check if we should return or continue expanding:
            # - Need N+1 prompts for N commands (e.g., 4 prompts for 3 commands)
            # - OR we've reached start of scrollback (can't get more history)
            have_enough_prompts = len(prompts) >= MAX_RECENT_COMMANDS + 1
            at_start_of_scrollback = start_row == 0

            if have_enough_prompts or at_start_of_scrollback:
                # Either we have enough commands, or we've captured all available history
                if len(prompts) >= 2:
                    # At least one complete command - extract last N
                    start_idx = max(0, len(prompts) - (MAX_RECENT_COMMANDS + 1))
                    start_line = prompts[start_idx][0]
                    lines = content.split('\n')
                    return '\n'.join(lines[start_line:])
                elif prompts:
                    lines = content.split('\n')
                    # Check if shell is waiting for input (prompt at end)
                    # If so, the single prompt is the END marker - return content BEFORE it
                    if PromptDetector.detect_prompt_at_end(content):
                        return '\n'.join(lines[:prompts[0][0]]).rstrip()
                    else:
                        # Prompt at start or command running - return from prompt
                        return '\n'.join(lines[prompts[0][0]:])
                return content

            # Not enough prompts and not at start - keep expanding
            lines_to_capture *= 2

        # Hit max - capture last MAX_LINES rows (not entire scrollback)
        fallback_start = max(0, cursor_row - MAX_LINES)
        content = self.plugin_dbus.capture_from_row(terminal_uuid, fallback_start)
        prompts = PromptDetector.find_all_prompts(content)

        # Filter out false positives using Unicode markers (same as main loop)
        if PromptDetector.has_unicode_markers(content):
            lines_list = content.split('\n')
            real_prompts = []
            for line_num, line_content in prompts:
                # Check this line and next line for marker (handles Kali header → prompt)
                has_marker = False
                for offset in [0, 1]:  # Don't check -1: would find previous command's marker
                    check_line = line_num + offset
                    if 0 <= check_line < len(lines_list):
                        if PromptDetector.INPUT_START_MARKER in lines_list[check_line]:
                            has_marker = True
                            break
                if has_marker:
                    real_prompts.append((line_num, line_content))
            prompts = real_prompts

        if len(prompts) >= 2:
            # Extract last N commands (same logic as main loop)
            start_idx = max(0, len(prompts) - (MAX_RECENT_COMMANDS + 1))
            lines = content.split('\n')
            return '\n'.join(lines[prompts[start_idx][0]:])
        elif prompts:
            # Single prompt - check if it's at the end (scrollback exceeded)
            lines = content.split('\n')
            if PromptDetector.detect_prompt_at_end(content):
                # Prompt at end = return content BEFORE it
                return '\n'.join(lines[:prompts[0][0]]).rstrip()
            else:
                # Prompt at start or command running - return from prompt
                return '\n'.join(lines[prompts[0][0]:])
        return content

    def _split_into_command_blocks(self, content: str) -> List[str]:
        """
        Split terminal content into individual command blocks.

        Each block contains: prompt + command + output (until next prompt).
        Used for granular deduplication - skip blocks that were in previous capture.

        Args:
            content: Terminal content string

        Returns:
            List of command blocks. If splitting fails, returns [content] as fallback.
        """
        prompts = PromptDetector.find_all_prompts(content)
        if len(prompts) < 2:
            return [content]  # Can't split meaningfully, return as-is

        lines = content.split('\n')
        blocks = []
        for i, (line_num, _) in enumerate(prompts):
            # Block extends from this prompt to the next (or end of content)
            end = prompts[i + 1][0] if i + 1 < len(prompts) else len(lines)
            block = '\n'.join(lines[line_num:end])
            if block.strip():
                blocks.append(block)

        return blocks if blocks else [content]

    def _capture_context_asciinema(self, dedupe: bool = True) -> Tuple[str, List[llm.Attachment]]:
        """
        Intelligent context capture from asciinema recordings.

        Uses block-level hashing (same as llm-inlineassistant) to:
        - Only include NEW command blocks not seen before
        - Return [Content unchanged] when no new activity
        - Reuse filter_new_blocks() from llm-tools-core

        Args:
            dedupe: Whether to apply block-level deduplication

        Returns:
            Tuple of (context_string, attachments_list):
            - context_string: Formatted terminal context
            - attachments_list: Always empty (no TUI screenshots in this mode)
        """
        from llm_tools_context import get_command_blocks
        from llm_tools_core import filter_new_blocks

        # Get recent command blocks (more than we need, filtering handles dedup)
        blocks = get_command_blocks(n_commands=10)

        if not blocks:
            return "## Terminal Context\n\nNo session recording available.", []

        if dedupe:
            # Apply block-level hash filtering (shared with llm-inlineassistant)
            new_blocks, current_hashes = filter_new_blocks(blocks, self._asciinema_prev_hashes)
            self._asciinema_prev_hashes = current_hashes

            if not new_blocks:
                return "## Terminal Context\n\n[Content unchanged]", []
            blocks_to_show = new_blocks
        else:
            # No deduplication - show all recent blocks (reset hashes too)
            self._asciinema_prev_hashes = set()
            blocks_to_show = blocks

        # Format blocks for context
        context = '\n\n'.join(blocks_to_show)
        formatted = f"## Terminal Context (from session recording)\n\n```\n{context}\n```"
        return formatted, []

    def capture_context(self, include_exec_output=False, dedupe_unchanged=False) -> Tuple[str, List[llm.Attachment]]:
        """
        Capture visible content from all terminals (like tmuxai).

        Excludes:
        - Chat terminal (where assistant is running) - to avoid self-reference
        - Exec terminal (unless include_exec_output=True)

        For TUI applications (htop, glances, vim, etc.), captures screenshots
        instead of text since TUI content doesn't extract well as plain text.

        Args:
            include_exec_output: Whether to include exec terminal in context
            dedupe_unchanged: If True and content matches previous hash for a terminal,
                             emit [Content unchanged] placeholder instead of full content

        Returns:
            Tuple of (context_string, attachments_list):
            - context_string: XML-wrapped text content for non-TUI terminals
            - attachments_list: Screenshot attachments for TUI terminals
        """
        # Early return for no-exec mode - use asciinema-based capture
        if self.no_exec_mode:
            return self._capture_context_asciinema(dedupe_unchanged)

        import base64

        try:
            terminals = self.plugin_dbus.get_terminals_in_same_tab(self.chat_terminal_uuid)
            context_parts = []
            attachments = []

            for term in terminals:
                term_uuid = self._normalize_uuid(term['uuid'])

                # Skip chat terminal (self-awareness)
                if term_uuid == self.chat_terminal_uuid:
                    continue

                # Optionally skip exec terminal
                if not include_exec_output and term_uuid == self.exec_terminal_uuid:
                    continue

                # Check if TUI is active in this terminal
                is_tui = False
                try:
                    is_tui = self.plugin_dbus.is_likely_tui_active(term['uuid'])
                except Exception:
                    pass  # Fall back to text capture if TUI detection fails

                if is_tui:
                    # Capture screenshot for TUI terminal
                    self._debug(f"TUI detected in terminal {term['title']}, capturing screenshot")
                    try:
                        screenshot_data = self.plugin_dbus.capture_terminal_screenshot(term['uuid'])
                        if screenshot_data and not screenshot_data.startswith('ERROR'):
                            # Save to temp file and create attachment
                            image_bytes = base64.b64decode(screenshot_data)
                            # Use mkstemp for atomic, secure temp file creation in dedicated directory
                            temp_fd, temp_path = tempfile.mkstemp(
                                suffix='.png',
                                prefix=f'assistant_ctx_{term_uuid[:8]}_',
                                dir=str(self.screenshot_dir)
                            )
                            with os.fdopen(temp_fd, 'wb') as f:
                                f.write(image_bytes)
                            self.screenshot_files.append(temp_path)
                            attachments.append(llm.Attachment(path=temp_path))
                            # Add marker in context so AI knows about the screenshot
                            # TUI screenshots always have unique timestamps, so no deduplication
                            context_parts.append(f'<terminal uuid="{term_uuid}" title="{term["title"]}" type="tui-screenshot">Screenshot attached for this TUI terminal</terminal>')
                            continue  # Don't fall through to text capture
                        else:
                            self._debug(f"Screenshot failed for {term['title']}: {screenshot_data}")
                    except Exception as e:
                        self._debug(f"TUI screenshot failed for {term['title']}: {e}")
                    # Fall through to text capture if screenshot fails

                # Intelligent capture: get full last command output (not just viewport)
                content = self._capture_last_command_output(term['uuid'])

                if content and not content.startswith('ERROR'):
                    # Compute hash for change detection (normalize whitespace for stability)
                    content_hash = hashlib.sha256(content.strip().encode()).hexdigest()

                    # Per-terminal deduplication: emit placeholder if content unchanged
                    # Only active after first message (AI needs initial context)
                    if dedupe_unchanged and term_uuid in self.terminal_content_hashes:
                        if self.terminal_content_hashes[term_uuid] == content_hash:
                            # Choose message based on whether hash came from tool result
                            if term_uuid in self.toolresult_hash_updated:
                                placeholder = "[Output already in tool result above]"
                                self.toolresult_hash_updated.discard(term_uuid)
                            else:
                                placeholder = "[Content unchanged]"
                            context_parts.append(f'''<terminal uuid="{term['uuid']}" title="{term['title']}" cwd="{term['cwd']}">
{placeholder}
</terminal>''')
                            continue

                    # Content changed - clear stale tool result tracking
                    # (prevents incorrect message if user modified terminal after tool call)
                    self.toolresult_hash_updated.discard(term_uuid)

                    # Always store hash for future comparison (even on first message)
                    self.terminal_content_hashes[term_uuid] = content_hash

                    # Block-level deduplication: only send command blocks not in previous capture
                    # This prevents re-sending old command outputs that accumulate in scrollback
                    blocks = self._split_into_command_blocks(content)
                    prev_hashes = self.previous_capture_block_hashes.get(term_uuid, set())

                    # Use shared filter function (also used by llm-shell)
                    new_blocks, current_hashes = filter_new_blocks(blocks, prev_hashes)

                    # Update previous = current for next capture
                    self.previous_capture_block_hashes[term_uuid] = current_hashes

                    # Apply block filtering only when deduplication is active
                    if dedupe_unchanged and prev_hashes:
                        # Use filtered content if we have new blocks
                        if new_blocks:
                            content = '\n'.join(new_blocks)
                        elif blocks:
                            # All blocks same as previous - use placeholder
                            context_parts.append(f'''<terminal uuid="{term['uuid']}" title="{term['title']}" cwd="{term['cwd']}">
[Content unchanged]
</terminal>''')
                            continue

                    # Format like tmux-fragments
                    context_parts.append(f'''<terminal uuid="{term['uuid']}" title="{term['title']}" cwd="{term['cwd']}">
{content}
</terminal>''')

            # Check if ALL context parts are placeholders (no real content)
            # Placeholders: "[Content unchanged]", "[Output already in tool result above]"
            PLACEHOLDER_MARKERS = frozenset(['[Content unchanged]', '[Output already in tool result above]'])

            def is_placeholder_only(part: str) -> bool:
                """Check if a terminal context part contains only placeholder content."""
                # Extract content between terminal tags
                lines = part.split('\n')
                content_lines = [l for l in lines if not l.startswith('<terminal') and not l.startswith('</terminal') and l.strip()]
                # Exact match only - avoids false positives from user content containing placeholder text
                return len(content_lines) == 1 and content_lines[0] in PLACEHOLDER_MARKERS

            # If ALL parts are placeholders, return minimal marker instead of full XML
            if context_parts and all(is_placeholder_only(p) for p in context_parts):
                return "[Context: unchanged]", attachments

            combined_context = "\n\n".join(context_parts)
            # Truncate if needed to prevent memory/token issues
            combined_context = self._truncate_capture_if_needed(combined_context, "terminal context")
            return combined_context, attachments
        except Exception as e:
            ConsoleHelper.error(self.console, f"Error capturing context: {e}")
            return "", []

    def handle_rewind_command(self, args: str) -> bool:
        """Handle /rewind command with interactive picker and undo support."""
        args = args.strip() if args else ""
        responses = self.conversation.responses

        # Handle /rewind undo
        if args == 'undo':
            return self._handle_rewind_undo()

        if not responses:
            ConsoleHelper.warning(self.console, "No conversation history to rewind.")
            return True

        # Handle quick rewind: /rewind N or /rewind -N
        if args:
            try:
                n = int(args)
                return self._quick_rewind(n)
            except ValueError:
                ConsoleHelper.error(self.console, f"Invalid argument: {args}. Use 'undo', a turn number, or no argument for picker.")
                return True

        # Interactive picker mode
        return self._interactive_rewind_picker()

    def _interactive_rewind_picker(self) -> bool:
        """Display interactive picker for selecting rewind point.

        Uses Rich Panel for display + simple input() for selection.
        This avoids raw tty manipulation that could conflict with prompt_toolkit.
        """
        responses = self.conversation.responses
        total_turns = len(responses)

        if total_turns < 2:
            ConsoleHelper.warning(self.console, "Need at least 2 turns to rewind.")
            return True

        # Build turn list with token counts
        turns = []
        total_tokens = 0
        for i, response in enumerate(responses, 1):
            prompt_preview = self._extract_user_prompt_preview(response)
            turn_tokens = (response.input_tokens or 0) + (response.output_tokens or 0)
            total_tokens += turn_tokens
            turns.append({
                'num': i,
                'tokens': turn_tokens,
                'preview': prompt_preview,
                'is_current': i == total_turns
            })

        # Display picker
        self._draw_rewind_picker(turns, total_tokens)

        # Get selection via simple input
        max_valid = total_turns - 1  # Can't rewind to current turn
        try:
            selection = input(f"Select turn to rewind to (1-{max_valid}), or q to cancel: ").strip()
        except (EOFError, KeyboardInterrupt):
            ConsoleHelper.dim(self.console, "Rewind cancelled.")
            return True

        if selection.lower() == 'q' or selection == '':
            ConsoleHelper.dim(self.console, "Rewind cancelled.")
            return True

        try:
            target_turn = int(selection)
        except ValueError:
            ConsoleHelper.error(self.console, f"Invalid selection: {selection}")
            return True

        # Validate range
        if target_turn < 1 or target_turn >= total_turns:
            ConsoleHelper.error(self.console, f"Turn must be between 1 and {max_valid}")
            return True

        # Calculate impact
        tokens_freed = sum(t['tokens'] for t in turns[target_turn:])
        turns_removed = total_turns - target_turn

        # Confirm
        ConsoleHelper.info(self.console, f"Rewinding to {target_turn} removes {turns_removed} turn(s), freeing ~{tokens_freed:,} tokens.")
        confirm = input("Proceed? [Y/n] ").strip().lower()
        if confirm and confirm != 'y':
            ConsoleHelper.dim(self.console, "Rewind cancelled.")
            return True

        self._perform_rewind(target_turn)
        ConsoleHelper.success(self.console, f"Rewound to turn {target_turn}. Use /rewind undo to restore.")
        return True

    def _draw_rewind_picker(self, turns: list, total_tokens: int):
        """Draw the picker panel showing conversation turns."""
        # Build content (Panel and Text already imported at top level)
        lines = Text()

        # Show last 10 turns (or all if fewer)
        display_start = max(0, len(turns) - 10)
        if display_start > 0:
            lines.append(f"  ... ({display_start} earlier turns)\n", style="dim")

        # Filter out current turn and build display lines
        display_turns = [t for t in turns[display_start:] if not t['is_current']]
        for i, turn in enumerate(display_turns):
            line = f"  {turn['num']:3}. [{turn['tokens']:5} tok] \"{turn['preview']}\""
            # Add newline except for last line (avoids empty line before border)
            if i < len(display_turns) - 1:
                line += "\n"
            lines.append(line)

        panel = Panel(
            lines,
            title=f"Rewind Conversation ({len(turns)} turns, ~{total_tokens:,} tokens)",
            border_style="cyan"
        )

        self.console.print(panel)

    def _quick_rewind(self, n: int) -> bool:
        """Handle quick rewind: /rewind N or /rewind -N."""
        responses = self.conversation.responses
        total_turns = len(responses)

        # Handle negative indexing
        if n < 0:
            target_turn = total_turns + n
        else:
            target_turn = n

        # Validate
        if target_turn < 1:
            ConsoleHelper.error(self.console, f"Invalid turn: must be >= 1 (got {n} → {target_turn})")
            return True
        if target_turn >= total_turns:
            ConsoleHelper.warning(self.console, f"Already at turn {total_turns}. Nothing to rewind.")
            return True

        # Calculate impact
        tokens_freed = sum(
            (r.input_tokens or 0) + (r.output_tokens or 0)
            for r in responses[target_turn:]
        )
        turns_removed = total_turns - target_turn

        # Confirm
        ConsoleHelper.info(self.console, f"Rewind to turn {target_turn}? Removes {turns_removed} turn(s), frees ~{tokens_freed:,} tokens.")
        confirm = input("Proceed? [Y/n] ").strip().lower()
        if confirm and confirm != 'y':
            ConsoleHelper.dim(self.console, "Rewind cancelled.")
            return True

        self._perform_rewind(target_turn)
        ConsoleHelper.success(self.console, f"Rewound to turn {target_turn}. Use /rewind undo to restore.")
        return True

    def _perform_rewind(self, target_turn: int):
        """Truncate conversation and save undo buffer."""
        responses = self.conversation.responses

        # Save removed turns for undo (single undo only)
        self.rewind_undo_buffer = list(responses[target_turn:])

        # Truncate
        self.conversation.responses = responses[:target_turn]

        # Clear pending summary (may be stale)
        self.pending_summary = None

        # Clear deduplication hashes
        self.terminal_content_hashes.clear()
        self.toolresult_hash_updated.clear()
        self.previous_capture_block_hashes.clear()

        # Broadcast to web companion
        if self.web_clients:
            self._broadcast_to_web({
                "type": "rewind",
                "turn": target_turn,
                "total_turns": len(self.conversation.responses)
            })

    def _handle_rewind_undo(self) -> bool:
        """Restore last rewound turns."""
        if not self.rewind_undo_buffer:
            ConsoleHelper.warning(self.console, "No rewind to undo.")
            return True

        # Restore
        restored_count = len(self.rewind_undo_buffer)
        self.conversation.responses.extend(self.rewind_undo_buffer)
        self.rewind_undo_buffer = None

        new_total = len(self.conversation.responses)
        ConsoleHelper.success(self.console, f"Restored {restored_count} turn(s). Back to turn {new_total}.")

        # Broadcast to web companion
        if self.web_clients:
            self._broadcast_to_web({
                "type": "rewind_undo",
                "total_turns": new_total
            })

        return True

    def _extract_user_prompt_preview(self, response) -> str:
        """Extract compact preview of user's prompt (50 chars max)."""
        # Defensive access to prompt text (matches pattern in squash_context)
        if hasattr(response, 'prompt') and response.prompt:
            prompt_text = response.prompt.prompt or ""
        else:
            prompt_text = "[no prompt]"

        # Strip context tags
        cleaned = re.sub(r'<terminal_context>.*?</terminal_context>', '', prompt_text, flags=re.DOTALL)
        cleaned = re.sub(r'<conversation_summary>.*?</conversation_summary>', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'<rag_context>.*?</rag_context>', '', cleaned, flags=re.DOTALL)
        cleaned = cleaned.strip()

        # Handle empty prompts (context-only submissions)
        if not cleaned:
            return "[context only]"

        # Truncate
        if len(cleaned) > 47:
            return cleaned[:47] + "..."
        return cleaned

    def _prompt(self, *args, **kwargs):
        """
        Wrapper for conversation.prompt() that fixes attachment persistence.

        llm's model plugins have a bug where build_messages() checks response.attachments
        (which is always empty) instead of response.prompt.attachments. This wrapper
        copies attachments from the prompt to the response, allowing follow-up messages
        to see images from previous turns in the conversation history.

        This is a workaround until the bug is fixed upstream in llm and its model plugins.
        """
        response = self.conversation.prompt(*args, **kwargs)
        # Copy attachments from prompt to response for history persistence
        if response.prompt and response.prompt.attachments:
            response.attachments = list(response.prompt.attachments)
        return response

    def _format_tool_result(self, name: str, content: str, cwd: str = None) -> str:
        """Format tool result with XML tags, timestamp, and optional cwd.

        Tool results use <tool_result> tags (distinct from <terminal> context tags)
        to provide structured immediate feedback in the tool calling loop.
        """
        from datetime import datetime
        timestamp = datetime.now().isoformat(timespec='seconds')
        cwd_attr = f' cwd="{cwd}"' if cwd else ''
        return f'<tool_result name="{name}"{cwd_attr} timestamp="{timestamp}">\n{content}\n</tool_result>'

    def _log_response(self, response):
        """Log response to database with context stripping.

        Strips terminal context from prompts before saving to preserve privacy
        while maintaining conversation history for --continue functionality.
        """
        if not self.logging_enabled:
            return
        if not hasattr(response, 'log_to_db'):
            return

        # Database already initialized in __init__
        db = sqlite_utils.Database(get_logs_db_path())
        migrate(db)
        # Strip terminal context and restore to preserve in-memory history
        original_prompt = response.prompt._prompt
        response.prompt._prompt = self._strip_context(original_prompt)
        try:
            response.log_to_db(db)
        finally:
            response.prompt._prompt = original_prompt

    def _init_conversation(self):
        """Initialize conversation, optionally loading from database.

        Handles -c (continue most recent) and --cid (continue specific ID) flags.
        Falls back to creating a new conversation if not continuing or if loading fails.
        """
        if self.continue_ or self.conversation_id:
            cid = self.conversation_id  # None means "most recent"
            try:
                from llm.cli import load_conversation
                import click
                loaded = load_conversation(cid, database=str(get_logs_db_path()))
                if loaded:
                    self.conversation = loaded
                    self.model = loaded.model
                    # Check for linked conversations (squash chain)
                    self._load_squash_chain_info(loaded.id)
                    ConsoleHelper.success(self.console, f"Continuing conversation {loaded.id}")
                    self.console.print(f"  {len(loaded.responses)} previous exchanges loaded")
                    return
                else:
                    ConsoleHelper.warning(self.console, "No previous conversations found, starting fresh")
            except click.ClickException as e:
                # load_conversation raises ClickException if specific ID not found
                ConsoleHelper.error(self.console, f"Could not load conversation: {e.message}")
                sys.exit(1)
            except Exception as e:
                ConsoleHelper.warning(self.console, f"Could not load conversation: {e}")

        # Create new conversation
        self.conversation = llm.Conversation(model=self.model)
        if self.logging_enabled:
            self.console.print(f"Session: [cyan]{self.conversation.id}[/]")

    def process_fragments(self, prompt: str):
        """
        Process !fragment commands in a prompt.
        Uses llm.cli.process_fragments_in_chat for correct database access.

        Returns:
            (modified_prompt, fragments, attachments)
        """
        try:
            # Database already initialized in __init__
            db = sqlite_utils.Database(get_logs_db_path())
            migrate(db)
            return process_fragments_in_chat(db, prompt)
        except Exception as ex:
            ConsoleHelper.error(self.console, f"Fragment error: {ex}")
            return prompt, [], []

    def should_use_screenshot_capture(self, command: str) -> bool:
        """
        Determine if screenshot capture should be used instead of text capture.

        Uses hybrid detection approach:
        1. Command-based detection (known TUI commands)
        2. Foreground process detection (actual running process name)
        3. Terminal state detection (alternate screen buffer heuristic)

        This provides better accuracy than command-based detection alone,
        catching cases where a TUI is launched via script or alias.

        Args:
            command: The command being executed

        Returns:
            True if screenshot capture should be used, False for text capture
        """
        # First check: known TUI command
        if is_tui_command(command):
            self._debug(f"TUI detected via command name: {command.split()[0] if command else ''}")
            return True

        # Second check: actual foreground process
        # More reliable than command parsing for aliases, scripts, wrappers
        try:
            process_info = self.get_foreground_process(self.exec_terminal_uuid)
            if process_info and process_info.get('name'):
                process_name = process_info['name'].lower()
                if process_name in TUI_COMMANDS:
                    self._debug(f"TUI detected via foreground process: {process_name}")
                    return True
        except Exception as e:
            self._debug(f"Foreground process detection error: {e}")

        # Third check: terminal state suggests TUI is active
        # This catches TUIs launched via scripts, aliases, or complex pipelines
        try:
            is_tui_active = self.plugin_dbus.is_likely_tui_active(self.exec_terminal_uuid)
            if is_tui_active:
                self._debug("TUI detected via terminal state (alternate screen heuristic)")
                ConsoleHelper.info(self.console, "TUI detected via terminal state")
                return True
        except dbus.exceptions.DBusException as e:
            # Method might not exist in older plugin versions
            self._debug(f"TUI state detection unavailable: {e}")
        except Exception as e:
            # Fall back to command-based detection only
            self._debug(f"TUI state detection error: {e}")

        return False

    def _judge_command_safety(self, command: str) -> Tuple[bool, str, str]:
        """
        Use LLM as judge to evaluate command safety for auto mode.

        Uses hybrid approach:
        1. Static pattern blocking for known dangerous commands (fast, no API call)
        2. LLM judge with native schema support for structured output

        Returns: (is_safe, risk_level, reason)
        """
        # Layer 1: Static pattern blocking (instant, no API call)
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return (False, "dangerous", "Blocked by static rule: matches dangerous pattern")

        # Layer 2: LLM judge with structured output
        history_context = ""
        if self.auto_command_history:
            history_context = "\nRecent commands:\n" + "\n".join(
                f"  {i}. {c}" for i, c in enumerate(self.auto_command_history, 1)
            )

        judge_prompt = render('prompts/safety_evaluation.j2', command=command, history_context=history_context)

        try:
            model = self.conversation.model

            # Require schema support for reliable safety evaluation
            if not model.supports_schema:
                return (False, "dangerous", f"Model {model.model_id} doesn't support schema - auto mode disabled")

            response = model.prompt(judge_prompt, schema=SafetySchema, temperature=0.2)
            result = json.loads(response.text())
            # CoT: analysis for reasoning, reason for display
            return (result.get("safe", False), result.get("risk_level", "dangerous"), result.get("reason", ""))

        except Exception as e:
            self._debug(f"Safety judge error: {e}")
            return (False, "dangerous", f"Safety check failed: {str(e)}")

    def _prepare_for_interactive_prompt(self):
        """Reset terminal state before displaying command/keypress Panel.

        Resets terminal modes that prompt_toolkit may have enabled to ensure
        Rich output renders correctly.
        """
        # Comprehensive terminal state reset:
        # - \x1b[?2004l  Disable bracketed paste mode (prompt_toolkit enables this)
        # - \x1b>        Disable application keypad mode (DECKPNM)
        # - \x1b[?25h    Show cursor (DECTCEM)
        # - \x1b[0m      Reset all SGR attributes
        sys.stdout.write('\x1b[?2004l\x1b>\x1b[?25h\x1b[0m')
        sys.stdout.flush()
        self.console.file.flush()
        time.sleep(0.05)

        # Clear buffered input
        try:
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except Exception:
            pass

    def _force_display(self):
        """Force immediate display of any buffered Rich output.

        Bypasses Rich's buffering by using print() directly, then flushes
        all output buffers and gives the terminal time to render.
        """
        print(end="", flush=True)  # Bypass Rich buffering
        self.console.file.flush()
        sys.stdout.flush()
        time.sleep(0.02)  # Give terminal time to render

    def _ask_confirmation(self, prompt_text: str, choices: List[str], default: str) -> str:
        """Ask for confirmation, ensuring prompt renders on first call.

        Works around Rich console.input() not rendering on first call after prompt_toolkit.
        Uses Python's built-in input() with prompt - the most basic I/O approach.
        """
        # Build the prompt string with ANSI colors (cyan for choices, green for default)
        choice_str = "/".join(choices)
        full_prompt = f"{prompt_text} [\x1b[36m{choice_str}\x1b[0m] (\x1b[32m{default}\x1b[0m): "

        # Flush stdout before the prompt to ensure clean state
        sys.stdout.flush()

        while True:
            try:
                # Use input() with prompt - this is the most basic approach
                response = input(full_prompt).strip().lower()
            except EOFError:
                response = ""

            if not response:
                return default
            if response in choices:
                return response

            # Invalid choice - re-prompt
            print(f"Please enter one of: {', '.join(choices)}")

    def execute_command(self, command: str) -> Tuple[bool, str]:
        """
        Execute command in Exec terminal with user approval and intelligent completion detection.
        In auto mode, uses LLM judge instead of user approval.

        Returns:
            Tuple of (executed: bool, output: str or tuple)
        """
        # Prepare terminal state BEFORE printing Panel to ensure immediate visibility
        self._prepare_for_interactive_prompt()

        self.console.print(Panel(
            Text(command, style="bold cyan"),
            title="[bold]Command to Execute[/]",
            border_style="cyan"
        ))

        # Force immediate display of Panel
        self._force_display()

        # AUTO MODE: Use LLM judge instead of user approval
        if self.auto_mode:
            with self.console.status("[bold blue]Evaluating command safety...[/]"):
                is_safe, risk_level, reason = self._judge_command_safety(command)

            # Display risk assessment with visual indicators
            risk_colors = {"safe": "green", "caution": "yellow", "dangerous": "red"}
            risk_icons = {"safe": "✓", "caution": "⚠", "dangerous": "☠"}
            color = risk_colors.get(risk_level, "red")
            icon = risk_icons.get(risk_level, "?")

            if risk_level == "safe":
                self.console.print(f"[{color}]{icon}[/] auto")
            elif risk_level == "caution":
                if self.auto_mode == "full":
                    self.console.print(f"[{color}]{icon}[/] auto")
                else:
                    self.console.print(f"[{color}]{icon} {risk_level.upper()}[/] - {reason}")
                    choice = self._ask_confirmation("Execute?", ["y", "n", "e"], "y")
                    if choice == "n":
                        return (False, "")
                    if choice == "e":
                        edited = Prompt.ask("Edit command", default=command)
                        return self.execute_command(edited)
            else:  # dangerous
                self.console.print(f"[{color}]{icon} {risk_level.upper()}[/] - {reason}")
                ConsoleHelper.error(self.console, "BLOCKED - manual approval required")
                choice = self._ask_confirmation("Override?", ["yes", "no", "edit"], "no")
                if choice == "no":
                    return (False, "")
                if choice == "edit":
                    edited = Prompt.ask("Edit command", default=command)
                    return self.execute_command(edited)
        else:
            # MANUAL MODE: Original approval flow
            choice = self._ask_confirmation("Execute in Exec terminal?", ["y", "n", "e"], "y")

            if choice == "n":
                return (False, "")

            if choice == "e":
                # Allow editing
                edited = Prompt.ask("Edit command", default=command)
                command = edited

        # Verify exec terminal exists
        if not self._ensure_exec_terminal():
            return (False, "")

        # Capture terminal content BEFORE sending command (for change detection)
        try:
            initial_content = self.plugin_dbus.capture_terminal_content(
                self.exec_terminal_uuid, -1
            )
        except Exception:
            initial_content = None  # Fallback: skip change detection

        # Record cursor position BEFORE command (for smart full-output capture)
        cmd_start_row = -1
        try:
            _, cmd_start_row = self.plugin_dbus.get_cursor_position(self.exec_terminal_uuid)
            self._debug(f"Command start cursor row: {cmd_start_row}")
        except Exception as e:
            self._debug(f"Could not get cursor position: {e}")

        # Send to Exec terminal
        try:
            success = self.plugin_dbus.send_keys_to_terminal(
                self.exec_terminal_uuid,
                command,
                execute=True
            )

            if success:
                ConsoleHelper.success(self.console, "Command sent to Exec terminal")

                # Scroll to bottom to ensure prompt detection sees the new prompt
                # Critical when user has scrolled up in the terminal
                try:
                    self.plugin_dbus.scroll_to_bottom(self.exec_terminal_uuid)
                except Exception as e:
                    self._debug(f"scroll_to_bottom failed (non-fatal): {e}")

                # Detect if this is a TUI application using hybrid detection
                # Checks both command name AND terminal state (alternate screen heuristic)
                if self.should_use_screenshot_capture(command):
                    # TUI detected - use screenshot capture
                    ConsoleHelper.info(self.console, "TUI application detected - using screenshot capture")

                    # Adaptive wait for TUI to render (replaces fixed 1.5s delay)
                    # Pass initial_content so we wait for content to change first
                    self.wait_for_tui_render(self.exec_terminal_uuid, max_wait=2.0, initial_content=initial_content)

                    temp_path, error = self._capture_screenshot(self.exec_terminal_uuid)
                    if error:
                        escaped_error = error.replace('[', '[[').replace(']', ']]')
                        ConsoleHelper.error(self.console, f"Screenshot capture failed: {escaped_error}")
                        return True, f"Screenshot capture failed: {error}"

                    ConsoleHelper.success(self.console, f"TUI screenshot captured: {temp_path}")

                    # Return a message with the screenshot path
                    # The AI will be able to see this image via attachments
                    file_size = os.path.getsize(temp_path)
                    output = f"""TUI application screenshot saved to: {temp_path}

This is an interactive TUI application (like htop, vim, or less). The screenshot shows its current display state.

Screenshot size: {file_size} bytes"""

                    if self.auto_mode:
                        self.auto_command_history.append(command)
                    return True, (output, temp_path)  # Return both text and image path
                else:
                    # Regular command - use prompt-based capture
                    prompt_detected, output, detection_method = self.prompt_based_capture(
                        self.exec_terminal_uuid,
                        max_wait=60,
                        initial_content=initial_content
                    )

                    if prompt_detected:
                        method_label = f"via {detection_method}" if detection_method else ""
                        ConsoleHelper.success(self.console, f"Command completed (prompt detected {method_label})")
                        self.console.print()  # Blank line for visual separation

                        # Smart full-output capture: capture from command start row
                        # This ensures we get complete output even if it scrolled past viewport
                        if cmd_start_row >= 0:
                            try:
                                _, cmd_end_row = self.plugin_dbus.get_cursor_position(self.exec_terminal_uuid)

                                # Validate range (protects against clear/reset commands)
                                # Cap max capture (protects against memory issues)
                                MAX_CAPTURE_ROWS = 10000
                                if (cmd_end_row >= cmd_start_row and
                                    (cmd_end_row - cmd_start_row) < MAX_CAPTURE_ROWS):
                                    full_output = self.plugin_dbus.capture_from_row(
                                        self.exec_terminal_uuid, cmd_start_row
                                    )
                                    if full_output and not full_output.startswith('ERROR'):
                                        # Truncate if needed to prevent memory issues
                                        output = self._truncate_capture_if_needed(full_output, "command output")
                                        output_rows = cmd_end_row - cmd_start_row
                                        self._debug(f"Full capture: {output_rows} rows from row {cmd_start_row}")
                                else:
                                    # Invalid range (clear/reset) or huge output - keep viewport
                                    self._debug(f"Skipping full capture: end={cmd_end_row}, start={cmd_start_row}")
                            except Exception as e:
                                self._debug(f"Smart capture failed, using viewport: {e}")

                        if self.auto_mode:
                            self.auto_command_history.append(command)
                        return True, output
                    else:
                        ConsoleHelper.warn_icon(self.console, "Timeout or long-running command")

                        # Post-timeout TUI check: command may have launched a TUI we didn't expect
                        # (e.g., git log with pager, script that invokes vim, etc.)
                        try:
                            if self.plugin_dbus.is_likely_tui_active(self.exec_terminal_uuid):
                                ConsoleHelper.info(self.console, "TUI detected after timeout - capturing screenshot")
                                temp_path, error = self._capture_screenshot(self.exec_terminal_uuid)
                                if temp_path:
                                    ConsoleHelper.success(self.console, f"TUI screenshot captured: {temp_path}")
                                    if self.auto_mode:
                                        self.auto_command_history.append(command)
                                    return True, (output, temp_path)
                        except Exception as e:
                            self._debug(f"Post-timeout TUI check failed: {e}")

                        if self.auto_mode:
                            self.auto_command_history.append(command)
                        return True, output
            else:
                ConsoleHelper.error(self.console, "Failed to send command")
                return False, ""
        except dbus.exceptions.DBusException as e:
            ConsoleHelper.error(self.console, f"D-Bus error executing command: {e}")
            ConsoleHelper.warning(self.console, "Plugin may have disconnected. Try /reset")
            return False, ""
        except Exception as e:
            ConsoleHelper.error(self.console, f"Error executing command ({type(e).__name__}): {e}")
            return False, ""

    def execute_keypress(self, keypress: str) -> bool:
        """
        Send keypress to Exec terminal with user approval.
        Does NOT automatically execute (no newline unless keypress is "Enter").

        Returns:
            True if sent, False if skipped
        """
        # Prepare terminal state BEFORE printing Panel to ensure immediate visibility
        self._prepare_for_interactive_prompt()

        self.console.print(Panel(
            Text(keypress, style="bold magenta"),
            title="[bold]Keypress to Send[/]",
            border_style="magenta"
        ))

        # Force immediate display of Panel
        self._force_display()

        # Ask for approval
        choice = self._ask_confirmation("Send this key(s)?", ["y", "n", "e"], "y")

        if choice == "n":
            return False

        if choice == "e":
            # Allow editing
            edited = Prompt.ask("Edit keypress", default=keypress)
            keypress = edited

        # Verify exec terminal exists
        if not self._ensure_exec_terminal():
            return False

        # Send keypress to Exec terminal using new D-Bus method
        try:
            success = self.plugin_dbus.send_keypress_to_terminal(
                self.exec_terminal_uuid,
                keypress
            )

            if success:
                ConsoleHelper.success(self.console, f"Keypress '{keypress}' sent to Exec terminal")
                return True
            else:
                ConsoleHelper.error(self.console, "Failed to send keypress")
                return False
        except Exception as e:
            ConsoleHelper.error(self.console, f"Error sending keypress: {e}")
            return False

    def handle_slash_command(self, command: str) -> Union[bool, str]:
        """
        Handle slash commands.

        Returns:
            True if should continue REPL, False to exit,
            "prompt_queued" if /capture queued a prompt for immediate processing
        """
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            if self.voice_input:
                voice_status = "[green]available[/]"
            elif VOICE_UNAVAILABLE_REASON:
                voice_status = f"[dim]{VOICE_UNAVAILABLE_REASON}[/]"
            else:
                voice_status = "[dim]not installed[/]"
            help_text = render('help_text.j2', voice_status=voice_status)
            self.console.print(Panel(help_text, title="Assistant Help", border_style="cyan"))
            return True

        elif cmd == "/clear":
            # Reset conversation (system prompt will be passed on next interaction)
            try:
                self.conversation = llm.Conversation(model=self.model)
                # Clear per-terminal content hashes (AI needs fresh context)
                self.terminal_content_hashes.clear()
                self.toolresult_hash_updated.clear()
                self.previous_capture_block_hashes.clear()
                # Clear rewind undo buffer (fresh start = no undo)
                self.rewind_undo_buffer = None
                # Broadcast clear to web companion
                if self.web_clients:
                    self._broadcast_to_web({"type": "clear"})
                ConsoleHelper.success(self.console, "Conversation cleared")
            except Exception as e:
                ConsoleHelper.error(self.console, f"Error clearing conversation: {e}")
            return True

        elif cmd == "/reset":
            # Clear conversation and reset terminal states (like tmuxai /reset)
            try:
                # Clear conversation
                self.conversation = llm.Conversation(model=self.model)

                # Disable watch mode if active
                if self.watch_mode:
                    with self.watch_lock:
                        self.watch_mode = False
                        self.watch_goal = None
                        if self.event_loop and not self.event_loop.is_closed():
                            try:
                                self.event_loop.call_soon_threadsafe(self.event_loop.stop)
                            except RuntimeError:
                                pass  # Loop already closed

                # Re-render system prompt (ensures watch mode state is correct)
                self._update_system_prompt()
                self.original_system_prompt = self.system_prompt

                # Clear plugin cache
                if hasattr(self, 'plugin_dbus') and self.plugin_dbus:
                    self.plugin_dbus.clear_cache()

                # Clear per-terminal content hashes (AI needs fresh context)
                self.terminal_content_hashes.clear()
                self.toolresult_hash_updated.clear()
                self.previous_capture_block_hashes.clear()

                # Clear rewind undo buffer (full reset = no undo)
                self.rewind_undo_buffer = None

                # Reset watch mode tracking state
                self.previous_watch_context_hash = None
                self.previous_watch_iteration_count = 0

                # Broadcast clear to web companion
                if self.web_clients:
                    self._broadcast_to_web({"type": "clear"})

                ConsoleHelper.success(self.console, "Conversation cleared and terminal states reset")
            except Exception as e:
                ConsoleHelper.error(self.console, f"Error resetting: {e}")
            return True

        elif cmd == "/rewind":
            return self.handle_rewind_command(args)

        elif cmd == "/copy":
            if not CLIPBOARD_AVAILABLE:
                ConsoleHelper.error(self.console, "Clipboard not available. Install pyperclip: llm install pyperclip")
                return True

            raw_mode = "raw" in args.lower()
            copy_all = "all" in args.lower()

            # Extract number if present
            num_match = re.search(r'\d+', args)
            count = int(num_match.group()) if num_match else 1

            responses = self.conversation.responses
            if not responses:
                ConsoleHelper.warning(self.console, "No responses to copy")
                return True

            if copy_all:
                texts = []
                for r in responses:
                    prompt_text = r.prompt.prompt if hasattr(r, 'prompt') and r.prompt else "[no prompt]"
                    texts.append(f"User: {prompt_text}\n\nAssistant: {r.text()}")
            else:
                texts = [r.text() for r in responses[-count:]]

            combined = "\n\n---\n\n".join(texts)

            if not raw_mode:
                combined = strip_markdown_for_clipboard(combined)

            try:
                pyperclip.copy(combined)
                what = "conversation" if copy_all else f"{len(texts)} response(s)"
                mode = "raw markdown" if raw_mode else "plain text"
                ConsoleHelper.success(self.console, f"Copied {what} to clipboard ({mode})")
            except Exception as e:
                ConsoleHelper.error(self.console, f"Clipboard error: {e}")
            return True

        elif cmd == "/web":
            if "stop" in args.lower() or "off" in args.lower():
                self._stop_web_server()
                return True

            if not WEB_AVAILABLE:
                ConsoleHelper.error(self.console, "Web companion not available. Install: llm install fastapi uvicorn")
                return True

            if self._start_web_server():
                url = f"http://localhost:{self.web_port}"
                try:
                    # Suppress browser stderr (Firefox GFX warnings, etc.)
                    # Use nohup + disown pattern to fully detach and suppress output
                    import subprocess
                    import shlex
                    subprocess.Popen(
                        f"nohup xdg-open {shlex.quote(url)} >/dev/null 2>&1 &",
                        shell=True,
                        start_new_session=True
                    )
                    ConsoleHelper.success(self.console, f"Web companion opened at {url}")
                except Exception as e:
                    # Fallback to webbrowser if shell approach fails
                    try:
                        webbrowser.open(url)
                        ConsoleHelper.success(self.console, f"Web companion opened at {url}")
                    except Exception as e2:
                        ConsoleHelper.warning(self.console, f"Web server running at {url} (browser open failed: {e2})")
            return True

        elif cmd == "/refresh":
            # Re-capture terminal content and show preview
            ConsoleHelper.info(self.console, "Refreshing terminal context...")

            # Reload memories (in case AGENTS.md changed externally)
            self._load_memories()

            # Clear plugin cache
            try:
                self.plugin_dbus.clear_cache()
            except Exception:
                pass

            # Clear per-terminal content hashes to ensure full fresh capture
            self.terminal_content_hashes.clear()
            self.toolresult_hash_updated.clear()
            self.previous_capture_block_hashes.clear()
            # Also clear asciinema hashes for no-exec mode
            self._asciinema_prev_hashes.clear()

            # Simple snapshot capture (includes exec terminal)
            # Returns (context_text, tui_attachments) tuple for TUI screenshot support
            # Use watch_lock to avoid racing with watch mode (thread-safe)
            # No deduplication - user explicitly wants to see current full state
            with self.watch_lock:
                context, tui_attachments = self.capture_context(include_exec_output=True)

            if context or tui_attachments:
                ConsoleHelper.success(self.console, f"Captured {len(context)} characters of context")

                # Show TUI screenshots info
                if tui_attachments:
                    ConsoleHelper.success(self.console, f"Captured {len(tui_attachments)} TUI screenshot(s)")

                # Show per-terminal breakdown
                terminals = re.findall(r'<terminal uuid="([^"]+)" title="([^"]+)"', context)
                if terminals:
                    self.console.print()
                    ConsoleHelper.bold(self.console, "Terminals captured:")
                    for uuid_match, title in terminals:
                        # Check if this is a TUI screenshot
                        tui_pattern = rf'<terminal uuid="{re.escape(uuid_match)}"[^>]*type="tui-screenshot"'
                        if re.search(tui_pattern, context):
                            self.console.print(f"  • [cyan]{title}[/]: [magenta]TUI screenshot[/]")
                        else:
                            # Extract content for this terminal
                            term_pattern = rf'<terminal uuid="{re.escape(uuid_match)}"[^>]*>(.*?)</terminal>'
                            term_match = re.search(term_pattern, context, re.DOTALL)
                            if term_match:
                                term_content = term_match.group(1).strip()
                                lines = len([l for l in term_content.split('\n') if l.strip()])
                                chars = len(term_content)
                                self.console.print(f"  • [cyan]{title}[/]: {lines} lines, {chars} chars")

                # Show preview of first terminal (skip if only TUI screenshots)
                text_only = re.sub(r'<terminal[^>]*type="tui-screenshot"[^>]*>.*?</terminal>', '', context, flags=re.DOTALL)
                if text_only.strip():
                    ConsoleHelper.dim(self.console, "\nFirst 300 chars of text content:")
                    preview = text_only[:300].replace('\n', ' ')
                    ConsoleHelper.dim(self.console, f"{preview}...")
            else:
                ConsoleHelper.warning(self.console, "No context captured")

            return True

        elif cmd == "/model":
            if not args:
                # List available models
                ConsoleHelper.bold(self.console, "Available models:")
                for model in llm.get_models():
                    current = " [green](current)[/]" if model.model_id == self.model_name else ""
                    self.console.print(f"  - {model.model_id}{current}")
            elif args.startswith("-q") or args.startswith("--query"):
                # Query-based selection: /model -q haiku -q claude
                query_parts = args.split()[1:]  # Remove -q/--query prefix
                queries = [q for q in query_parts if not q.startswith("-")]
                if not queries:
                    ConsoleHelper.warning(self.console, "Usage: /model -q <query> [-q <query>...]")
                else:
                    resolved = resolve_model_query(queries)
                    if resolved:
                        try:
                            self.model = llm.get_model(resolved)
                            self.model_name = resolved
                            self.conversation.model = self.model
                            # Recalculate tool token overhead (tools change with model)
                            self._tool_token_overhead = self._estimate_tool_schema_tokens()
                            ConsoleHelper.success(self.console, f"Switched to model: {resolved}")
                            # Notify web companion of model change
                            self._broadcast_to_web({
                                "type": "session_info",
                                "model": self.model_name,
                                "mode": self.mode
                            })
                        except Exception as e:
                            ConsoleHelper.error(self.console, f"Error switching model: {e}")
                    else:
                        ConsoleHelper.warning(self.console, f"No model matching queries: {queries}")
            else:
                # Direct model name
                try:
                    self.model = llm.get_model(args)
                    self.model_name = args
                    # Update conversation model
                    self.conversation.model = self.model
                    # Recalculate tool token overhead (tools change with model)
                    self._tool_token_overhead = self._estimate_tool_schema_tokens()
                    ConsoleHelper.success(self.console, f"Switched to model: {args}")
                    # Notify web companion of model change
                    self._broadcast_to_web({
                        "type": "session_info",
                        "model": self.model_name,
                        "mode": self.mode
                    })
                except Exception as e:
                    ConsoleHelper.error(self.console, f"Error switching model: {e}")
            return True

        elif cmd == "/info":
            tokens, token_source = self.estimate_tokens(with_source=True)
            percentage = (tokens * 100 // self.max_context_size) if self.max_context_size > 0 else 0
            # System prompt status (already rendered for current mode by Jinja2)
            if self.system_prompt:
                prompt_len = len(self.system_prompt)
                is_template = "terminal assistant" in self.system_prompt.lower()
                system_status = f"System prompt: llm-assistant ({prompt_len:,} chars)"
            else:
                system_status = "System prompt: NOT LOADED"
            watch_goal_line = f"\nWatch goal: {self.watch_goal}" if self.watch_mode else ""
            # Memory status
            memory_parts = []
            if self._global_memory:
                memory_parts.append("global")
            if self._local_memory:
                memory_parts.append("local")
            memory_status = ", ".join(memory_parts) if memory_parts else "none"
            # Mode display
            mode_display = f"[green]{self.mode}[/]" if self.mode == "assistant" else f"[cyan]{self.mode}[/]"
            # Active tools display
            active_tools = self._get_active_tools()
            tools_info = f"Active tools: {len(active_tools)}"
            if self.loaded_optional_tools:
                tools_info += f" (+{', '.join(sorted(self.loaded_optional_tools))})"
            if self.mode == "agent":
                tools_info += " [+agent-mode tools]"
            if self._is_gemini_model():
                tools_info += " [+gemini-only tools]"
            # Terminal info (only in normal mode, not no-exec)
            if self.no_exec_mode:
                terminal_info = "Mode: --no-exec (asciinema context)"
            else:
                terminal_info = f"Chat terminal: {self.chat_terminal_uuid}\nExec terminal: {self.exec_terminal_uuid}"
            self.console.print(Panel(f"""Model: {self.model_name}
{system_status}
Mode: {mode_display}
{tools_info}
Memory: {memory_status}
Context size: ~{tokens:,} tokens / {self.max_context_size:,} ({percentage}%) [{token_source}]
Exchanges: {len(self.conversation.responses)}
Watch mode: {"enabled" if self.watch_mode else "disabled"}{watch_goal_line}

{terminal_info}""", title="Session Info", border_style="cyan"))
            return True

        elif cmd == "/watch":
            # Watch mode is not available in --no-exec mode (requires Terminator D-Bus)
            if self.no_exec_mode:
                ConsoleHelper.warning(self.console, "Watch mode is not available in --no-exec mode (requires Terminator D-Bus)")
                return True
            if not args:
                # No args: show status with usage hint
                if self.watch_mode:
                    ConsoleHelper.success(self.console, "Watch mode: enabled")
                    self.console.print(f"Goal: {self.watch_goal}")
                    self.console.print(f"Interval: {self.watch_interval}s")
                else:
                    ConsoleHelper.warning(self.console, "Watch mode: disabled")
                    ConsoleHelper.dim(self.console, "Usage: /watch <goal> to enable")
            elif args.lower() == "off":
                # Disable watch mode
                if self.watch_mode:
                    with self.watch_lock:
                        self.watch_mode = False
                        self.watch_goal = None
                        # Reset state for next enable
                        self.previous_watch_context_hash = None
                        self.previous_watch_iteration_count = 0
                        # Re-render system prompt without watch mode context
                        self._update_system_prompt()
                        if self.watch_task and not self.watch_task.done():
                            # Cancel the task gracefully (interrupts asyncio.sleep)
                            try:
                                self.event_loop.call_soon_threadsafe(self.watch_task.cancel)
                            except RuntimeError:
                                pass  # Loop already closed
                    # Wait for watch thread to finish (prevents multiple threads)
                    if self.watch_thread and self.watch_thread.is_alive():
                        self.watch_thread.join(timeout=2.0)
                    ConsoleHelper.warning(self.console, "Watch mode disabled")
                    self._broadcast_status("watch")
                else:
                    ConsoleHelper.warning(self.console, "Watch mode is already off")
            elif args.lower() == "status":
                # Show watch mode status
                if self.watch_mode:
                    ConsoleHelper.success(self.console, "Watch mode: enabled")
                    self.console.print(f"Goal: {self.watch_goal}")
                    self.console.print(f"Interval: {self.watch_interval}s")
                else:
                    ConsoleHelper.warning(self.console, "Watch mode: disabled")
            else:
                # Enable watch mode with goal
                # First stop any existing watch thread to prevent multiple threads (thread-safe)
                thread_to_join = None
                with self.watch_lock:
                    if self.watch_thread and self.watch_thread.is_alive():
                        self.watch_mode = False
                        if self.watch_task and not self.watch_task.done():
                            try:
                                self.event_loop.call_soon_threadsafe(self.watch_task.cancel)
                            except RuntimeError:
                                pass  # Loop already closed
                        thread_to_join = self.watch_thread

                # Join outside lock (blocking operation)
                if thread_to_join:
                    thread_to_join.join(timeout=2.0)

                with self.watch_lock:
                    self.watch_mode = True
                    self.watch_goal = args
                    # Reset state for fresh analysis with new goal
                    self.previous_watch_context_hash = None
                    self.previous_watch_iteration_count = 0
                    # Re-render system prompt with watch mode context
                    self._update_system_prompt()
                    self._start_watch_mode_thread()
                ConsoleHelper.success(self.console, "Watch mode enabled")
                self.console.print(f"Goal: {self.watch_goal}")
                self.console.print(f"Monitoring all terminals every {self.watch_interval}s...")
                self._broadcast_status("watch")
            return True

        elif cmd == "/squash":
            # Support "keep" instruction: /squash keep API patterns
            keep_instruction = args if args else None
            self.squash_context(keep=keep_instruction)
            return True

        elif cmd == "/kb":
            return self._handle_kb_command(args)

        elif cmd == "/memory":
            return self._handle_memory_command(args)

        elif cmd == "/auto":
            # Auto mode: LLM-judged autonomous command execution
            if not args:
                self.auto_mode = "normal"
                ConsoleHelper.enabled(self.console, "Auto mode enabled - SAFE commands auto-execute")
                ConsoleHelper.dim(self.console, "/auto full for SAFE+CAUTION, /auto off to disable")
            elif args.lower() == "full":
                self.auto_mode = "full"
                ConsoleHelper.enabled(self.console, "Auto mode FULL - SAFE+CAUTION commands auto-execute")
                ConsoleHelper.dim(self.console, "/auto for SAFE only, /auto off to disable")
            elif args.lower() == "off":
                self.auto_mode = False
                self.auto_command_history.clear()
                ConsoleHelper.disabled(self.console, "Auto mode disabled")
            elif args.lower() == "status":
                if self.auto_mode == "full":
                    status = "[green]full[/] (SAFE+CAUTION)"
                elif self.auto_mode:
                    status = "[green]normal[/] (SAFE only)"
                else:
                    status = "[yellow]disabled[/]"
                self.console.print(f"Auto mode: {status}")
                if self.auto_command_history:
                    self.console.print(f"Recent commands: {len(self.auto_command_history)}")
                    for i, cmd_hist in enumerate(self.auto_command_history, 1):
                        self.console.print(f"  {i}. {cmd_hist[:60]}{'...' if len(cmd_hist) > 60 else ''}")
            else:
                ConsoleHelper.error(self.console, "Usage: /auto, /auto full, /auto off, /auto status")
            return True

        elif cmd == "/voice":
            # Voice auto-submit mode
            if not args or args.lower() == "auto":
                # Check if voice input is available before enabling
                if not self.voice_input:
                    if VOICE_UNAVAILABLE_REASON:
                        ConsoleHelper.error(self.console, f"Voice input unavailable ({VOICE_UNAVAILABLE_REASON})")
                    else:
                        ConsoleHelper.error(self.console, "Voice input not installed")
                        ConsoleHelper.dim(self.console, "Re-run install-llm-tools.sh to install")
                else:
                    self.voice_auto_submit = True
                    ConsoleHelper.enabled(self.console, "Voice auto-submit enabled - transcribed text sends automatically")
                    ConsoleHelper.dim(self.console, "/voice off to disable")
            elif args.lower() == "off":
                self.voice_auto_submit = False
                ConsoleHelper.disabled(self.console, "Voice auto-submit disabled")
            elif args.lower() == "status":
                status = "[green]enabled[/]" if self.voice_auto_submit else "[yellow]disabled[/]"
                if self.voice_input:
                    voice_avail = "[green]available[/]"
                elif VOICE_UNAVAILABLE_REASON:
                    voice_avail = f"[dim]{VOICE_UNAVAILABLE_REASON}[/]"
                else:
                    voice_avail = "[dim]not installed[/]"
                self.console.print(f"Voice auto-submit: {status}")
                self.console.print(f"Voice input: {voice_avail}")
            else:
                ConsoleHelper.error(self.console, "Usage: /voice auto, /voice off, /voice status")
            return True

        elif cmd == "/speech":
            # Text-to-speech output mode (requires Vertex model)
            if not self._is_vertex_model():
                ConsoleHelper.error(self.console, "TTS requires a Vertex model (vertex/*)")
                ConsoleHelper.dim(self.console, f"Current model: {self.model_name}")
                ConsoleHelper.dim(self.console, "Switch with: /model vertex/gemini-2.5-flash")
            elif not self.speech_output:
                ConsoleHelper.error(self.console, "google-cloud-texttospeech not installed")
                ConsoleHelper.dim(self.console, "Re-run install-llm-tools.sh to install")
            elif not args or args.lower() == "on":
                self.speech_output.enabled = True
                ConsoleHelper.enabled(self.console, "Speech output enabled - AI responses will be spoken")
                ConsoleHelper.dim(self.console, f"Voice: {self.speech_output.voice_name}")
                ConsoleHelper.dim(self.console, "/speech off to disable")
            elif args.lower() == "off":
                self.speech_output.enabled = False
                self.speech_output.stop()  # Stop any playing audio
                ConsoleHelper.disabled(self.console, "Speech output disabled")
            elif args.lower() == "status":
                status = "[green]enabled[/]" if self.speech_output.enabled else "[yellow]disabled[/]"
                tts_avail = "[green]available[/]" if TTS_AVAILABLE else "[dim]not installed[/]"
                vertex = "[green]yes[/]" if self._is_vertex_model() else "[yellow]no[/]"
                cred_method = self.speech_output.cred_method or "[dim]not loaded[/]"
                self.console.print(f"Speech output: {status}")
                self.console.print(f"TTS library: {tts_avail}")
                self.console.print(f"Vertex model: {vertex}")
                self.console.print(f"Credentials: {cred_method}")
                self.console.print(f"Voice: {self.speech_output.voice_name}")
            else:
                ConsoleHelper.error(self.console, "Usage: /speech on, /speech off, /speech status")
            return True

        elif cmd == "/rag":
            return self._handle_rag_command(args)

        elif cmd == "/skill":
            return self._handle_skill_command(args)

        elif cmd == "/assistant":
            if self.mode == "assistant":
                ConsoleHelper.dim(self.console, "Already in assistant mode")
            else:
                self.mode = "assistant"
                # Re-render system prompt and notify web companion
                self._update_system_prompt(broadcast_type="session")
                self.original_system_prompt = self.system_prompt
                ConsoleHelper.enabled(self.console, "Switched to assistant mode - conservative (10 tool iterations)")
                ConsoleHelper.dim(self.console, "/agent for agentic mode (100 iterations)")
            return True

        elif cmd == "/agent":
            if self.mode == "agent":
                ConsoleHelper.dim(self.console, "Already in agent mode")
            else:
                self.mode = "agent"
                # Re-render system prompt and notify web companion
                self._update_system_prompt(broadcast_type="session")
                self.original_system_prompt = self.system_prompt
                ConsoleHelper.enabled(self.console, "Switched to agent mode - agentic (100 tool iterations)")
                ConsoleHelper.dim(self.console, "/assistant for conservative mode (10 iterations)")
            return True

        elif cmd == "/capture":
            # Parse: /capture [mode] [delay] [prompt...]
            # delay can be a number (0-30) or "-" for default
            parts = command.split(maxsplit=3)
            mode = "window"
            delay = None  # None means use default
            prompt = None

            def parse_delay(val: str) -> int | None:
                """Parse delay value, return None for default, or validated int."""
                if val == "-":
                    return None
                try:
                    d = int(val)
                    return max(0, min(30, d))  # Clamp to 0-30 seconds
                except ValueError:
                    return None  # Not a valid number

            if len(parts) >= 2:
                if parts[1] in ("window", "region", "full", "rdp", "annotate"):
                    mode = parts[1]
                    # Check for delay parameter
                    if len(parts) >= 3:
                        parsed = parse_delay(parts[2])
                        if parsed is not None or parts[2] == "-":
                            delay = parsed
                            prompt = parts[3] if len(parts) > 3 else None
                        else:
                            # Not a delay, treat as prompt
                            prompt = " ".join(parts[2:])
                else:
                    # No mode specified, rest is prompt
                    prompt = " ".join(parts[1:])

            # Execute capture
            try:
                from llm_tools_capture_screen import capture_screen

                # Use default delay for interactive modes if not specified
                if delay is None:
                    delay = 5 if mode in ("window", "region", "annotate") else 0
                if delay > 0:
                    with Status(f"Capturing ({mode}): {delay}s", console=self.console) as status:
                        for i in range(delay, 0, -1):
                            status.update(f"Capturing ({mode}): {i}s")
                            time.sleep(1)

                result = capture_screen(mode=mode, delay=0)

                if hasattr(result, 'attachments') and result.attachments:
                    if prompt:
                        # Immediate analysis - inject attachment and send prompt
                        self.pending_attachments.extend(result.attachments)
                        # Process the prompt immediately (return to main loop with prompt)
                        # We can't directly call prompt() here, so we queue and return special
                        self._queued_prompt = prompt
                        return "prompt_queued"  # Special return value to trigger prompt processing
                    else:
                        # Queue for next message
                        self.pending_attachments.extend(result.attachments)
                        ConsoleHelper.success(self.console, f"Screenshot queued (mode={mode})")
                        ConsoleHelper.dim(self.console, "Attached to your next message")
                else:
                    ConsoleHelper.warning(self.console, "No screenshot captured")
            except ImportError:
                ConsoleHelper.error(self.console, "capture_screen tool not installed")
                ConsoleHelper.dim(self.console, "Install: llm install /opt/llm-tools-capture-screen")
            except Exception as e:
                ConsoleHelper.error(self.console, f"Capture failed: {e}")
            return True

        elif cmd == "/imagemage":
            parts = command.split()
            if len(parts) == 1:
                # Load imagemage
                if 'imagemage' in self.loaded_optional_tools:
                    ConsoleHelper.warning(self.console, "imagemage already loaded")
                else:
                    self.loaded_optional_tools.add('imagemage')
                    ConsoleHelper.success(self.console, "imagemage loaded (generate_image tool available)")
                    ConsoleHelper.dim(self.console, "/imagemage off to unload")
            elif parts[1] == "off":
                if 'imagemage' in self.loaded_optional_tools:
                    self.loaded_optional_tools.discard('imagemage')
                    ConsoleHelper.success(self.console, "imagemage unloaded")
                else:
                    ConsoleHelper.warning(self.console, "imagemage not loaded")
            elif parts[1] == "status":
                if 'imagemage' in self.loaded_optional_tools:
                    ConsoleHelper.success(self.console, "imagemage: loaded (generate_image available)")
                else:
                    ConsoleHelper.dim(self.console, "imagemage: not loaded")
                    ConsoleHelper.dim(self.console, "/imagemage to load")
            else:
                ConsoleHelper.error(self.console, "Usage: /imagemage, /imagemage off, /imagemage status")
            return True

        elif cmd == "/mcp":
            parts = command.split()
            if len(parts) == 1 or parts[1] == "status":
                self._handle_mcp_status()
            elif parts[1] == "load" and len(parts) >= 3:
                self._handle_mcp_load(parts[2])
            elif parts[1] == "unload" and len(parts) >= 3:
                self._handle_mcp_unload(parts[2])
            else:
                ConsoleHelper.error(self.console, "Usage: /mcp, /mcp load <server>, /mcp unload <server>")
            return True

        elif cmd == "/report":
            return self._handle_report_command(args)

        elif cmd == "/workflow":
            import asyncio
            return asyncio.get_event_loop().run_until_complete(
                self._handle_workflow_command(args)
            )

        elif cmd in ["/quit", "/exit"]:
            self._shutdown()  # Explicit cleanup before exit
            return False

        else:
            ConsoleHelper.error(self.console, f"Unknown command: {cmd}")
            self.console.print("Type /help for available commands")
            return True


    def _process_tool_call(self, tool_call) -> ToolResult:
        """Process a single tool call and return the result.

        Handles: execute_in_terminal, send_keypress, capture_terminal, refresh_context, + EXTERNAL_TOOLS
        Includes type validation for arguments and scope validation.
        """
        # Extract and normalize tool info (fixes case sensitivity issue)
        tool_name = (tool_call.name or "").lower().strip()
        tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        tool_call_id = tool_call.tool_call_id

        # Broadcast pending tool call to web companion
        self._broadcast_tool_call(tool_name, tool_args, None, "pending")

        # Helper for type-safe string extraction (fixes type validation issue)
        def get_str(key: str, default: str = "") -> str:
            val = tool_args.get(key, default)
            if not isinstance(val, str):
                return str(val) if val else default
            return val

        # Execute tool and broadcast result
        result = self._execute_tool_call_inner(tool_call, tool_name, tool_args, tool_call_id, get_str)

        # Broadcast completion with full output
        status = "error" if "Error" in (result.output or "")[:50] else "success"
        self._broadcast_tool_call(tool_name, tool_args, result.output, status)

        return result

    def _execute_tool_call_inner(self, tool_call, tool_name: str, tool_args: dict, tool_call_id, get_str) -> ToolResult:
        """Inner implementation of tool call execution (separated for broadcasting wrapper)."""
        # Handle each tool type
        if tool_name == "execute_in_terminal":
            cmd = get_str("command")
            if not cmd:
                return ToolResult(
                    name=tool_call.name,  # Preserve original name for model
                    output="Error: No command provided",
                    tool_call_id=tool_call_id
                )

            executed, exec_content = self.execute_command(cmd)

            if executed:
                # Handle TUI screenshot vs regular text output
                screenshot_path = None
                result_attachments = []
                if isinstance(exec_content, tuple):
                    exec_text, screenshot_path = exec_content
                    if screenshot_path:
                        result_attachments.append(Attachment(path=screenshot_path))
                else:
                    exec_text = exec_content

                # Debug output
                self._debug("═══ Captured Content ═══")
                self._debug(f"UUID: {self.exec_terminal_uuid}")
                if screenshot_path:
                    self._debug("Type: Screenshot (TUI)")
                    self._debug(f"Screenshot path: {screenshot_path}")
                else:
                    self._debug("Type: Text output")
                    self._debug(f"Length: {len(exec_text)} chars")
                self._debug("═══════════════════════════════")

                # Update terminal hash to current state for deduplication
                # This prevents the same content from appearing in both ToolResult and next context injection
                if exec_text and self.exec_terminal_uuid:
                    try:
                        current_content = self._capture_last_command_output(self.exec_terminal_uuid)
                        if current_content and not current_content.startswith('ERROR'):
                            content_hash = hashlib.sha256(current_content.strip().encode()).hexdigest()
                            self.terminal_content_hashes[self.exec_terminal_uuid] = content_hash
                            # Mark this terminal for specific dedup message
                            self.toolresult_hash_updated.add(self.exec_terminal_uuid)
                            # Also update block hashes for granular deduplication
                            # This prevents tool output from being re-sent in next context capture
                            blocks = self._split_into_command_blocks(current_content)
                            block_hashes = {hashlib.sha256(b.strip().encode()).hexdigest()
                                            for b in blocks if b.strip()}
                            self.previous_capture_block_hashes[self.exec_terminal_uuid] = block_hashes
                    except Exception:
                        pass  # Non-critical - deduplication is optimization only

                # Extract exit code and duration from temp file (VTE filters Unicode metadata)
                exit_code = None
                duration = None
                if exec_text:
                    # Try to read metadata from temp file written by shell
                    try:
                        shell_pid = self.plugin_dbus.get_shell_pid(self.exec_terminal_uuid)
                        self._debug(f"Exec terminal shell PID: {shell_pid}")
                        if shell_pid > 0:
                            meta_file = str(get_temp_dir() / f".prompt-meta-{shell_pid}")
                            if os.path.exists(meta_file):
                                # Only read if file was modified in the last 10 seconds
                                # (prevents false positives in nested shells without metadata)
                                file_age = time.time() - os.path.getmtime(meta_file)
                                if file_age > 10:
                                    self._debug(f"Metadata file too old ({file_age:.1f}s), ignoring")
                                else:
                                    with open(meta_file, 'r') as f:
                                        meta_content = f.read().strip()
                                    self._debug(f"Read metadata from file: {meta_content}")
                                    # Parse format: E<exit>T<timestamp>D<duration>
                                    match = re.match(r'E(\d+)T(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})D(\d+)', meta_content)
                                    if match:
                                        exit_code = int(match.group(1))
                                        duration = int(match.group(3))
                                        self._debug(f"Parsed metadata: exit_code={exit_code}, duration={duration}")
                            else:
                                self._debug(f"Metadata file not found: {meta_file}")
                    except Exception as e:
                        self._debug(f"Error reading metadata file: {e}")

                    # Clean any residual tag characters from output (legacy support)
                    exec_text = PromptDetector.strip_tag_metadata(exec_text)

                    if exit_code is not None:
                        status = "✓" if exit_code == 0 else "✗"
                        duration_str = f", duration: {duration}s" if duration is not None else ""
                        exec_text = f"[Exit code: {exit_code} {status}{duration_str}]\n{exec_text}"

                cwd = self._get_exec_terminal_cwd()
                return ToolResult(
                    name=tool_call.name,
                    output=self._format_tool_result(
                        "execute_in_terminal",
                        f"Command executed: {cmd}\n\nOutput:\n{exec_text}",
                        cwd=cwd
                    ),
                    attachments=result_attachments,
                    tool_call_id=tool_call_id
                )
            else:
                # User declined
                return ToolResult(
                    name=tool_call.name,
                    output=f"User declined to execute command: {cmd}",
                    tool_call_id=tool_call_id
                )

        elif tool_name == "send_keypress":
            kp = get_str("keypress")
            if not kp:
                return ToolResult(
                    name=tool_call.name,
                    output="Error: No keypress provided",
                    tool_call_id=tool_call_id
                )

            # execute_keypress returns True if executed, False if declined
            executed = self.execute_keypress(kp)

            if executed:
                cwd = self._get_exec_terminal_cwd()
                return ToolResult(
                    name=tool_call.name,
                    output=self._format_tool_result("send_keypress", f"Keypress sent: {kp}", cwd=cwd),
                    tool_call_id=tool_call_id
                )
            else:
                return ToolResult(
                    name=tool_call.name,
                    output=f"User declined to send keypress: {kp}",
                    tool_call_id=tool_call_id
                )

        elif tool_name == "capture_terminal":
            scope = get_str("scope", "exec")
            # Validate scope (fixes scope validation inconsistency)
            if scope not in {"exec", "all"}:
                scope = "exec"

            self.console.print()
            ConsoleHelper.info(self.console, f"Capturing screenshot ({scope})...")

            try:
                result_attachments = []
                captured_info = []

                if scope == 'all':
                    terminals = self.plugin_dbus.get_terminals_in_same_tab(self.chat_terminal_uuid)
                    # Filter out chat terminal (already normalized at assignment)
                    other_terminals = [t for t in terminals if str(t['uuid']) != self.chat_terminal_uuid]

                    for term in other_terminals:
                        temp_path, error = self._capture_screenshot(
                            term['uuid'],
                            unique_id=uuid.uuid4().hex[:12]
                        )
                        if temp_path:
                            result_attachments.append(Attachment(path=temp_path))
                            captured_info.append(term.get('title', 'Terminal'))
                            ConsoleHelper.success(self.console, f"Screenshot: {term.get('title', 'Terminal')}")
                        else:
                            ConsoleHelper.warning(self.console, f"Screenshot failed for {term.get('title', 'Terminal')}: {error}")
                else:
                    temp_path, error = self._capture_screenshot(
                        self.exec_terminal_uuid,
                        unique_id=uuid.uuid4().hex[:12]
                    )
                    if temp_path:
                        result_attachments.append(Attachment(path=temp_path))
                        captured_info.append("Exec terminal")
                        ConsoleHelper.success(self.console, "Screenshot captured")
                    else:
                        ConsoleHelper.error(self.console, f"Screenshot failed: {error}")
                        captured_info.append(f"Error: {error}")

                return ToolResult(
                    name=tool_call.name,
                    output=f"Captured screenshots: {', '.join(captured_info)}" if captured_info else "No screenshots captured",
                    attachments=result_attachments,
                    tool_call_id=tool_call_id
                )
            except Exception as e:
                ConsoleHelper.error(self.console, f"Screenshot error: {e}")
                return ToolResult(
                    name=tool_call.name,
                    output=f"Screenshot error: {e}",
                    tool_call_id=tool_call_id
                )

        elif tool_name == "refresh_context":
            self.console.print()
            ConsoleHelper.info(self.console, "Refreshing terminal context...")
            try:
                # Clear cache to get fresh content
                try:
                    self.plugin_dbus.clear_cache()
                except Exception:
                    pass

                # Clear per-terminal content hashes to ensure full fresh context
                self.terminal_content_hashes.clear()
                self.toolresult_hash_updated.clear()
                self.previous_capture_block_hashes.clear()

                # Capture fresh context (no deduplication - user/AI explicitly requested refresh)
                context_text, tui_attachments_refresh = self.capture_context(include_exec_output=True)
                result_attachments = []
                if tui_attachments_refresh:
                    result_attachments.extend(tui_attachments_refresh)

                if context_text or tui_attachments_refresh:
                    tui_info = f" + {len(tui_attachments_refresh)} TUI screenshot(s)" if tui_attachments_refresh else ""
                    ConsoleHelper.success(self.console, f"Context refreshed ({len(context_text)} chars{tui_info})")

                    return ToolResult(
                        name=tool_call.name,
                        output=f"Refreshed terminal context:\n\n{context_text}",
                        attachments=result_attachments,
                        tool_call_id=tool_call_id
                    )
                else:
                    ConsoleHelper.warning(self.console, "No terminal content captured")
                    return ToolResult(
                        name=tool_call.name,
                        output="No terminal content captured",
                        tool_call_id=tool_call_id
                    )
            except Exception as e:
                ConsoleHelper.error(self.console, f"Context refresh error: {e}")
                return ToolResult(
                    name=tool_call.name,
                    output=f"Context refresh error: {e}",
                    tool_call_id=tool_call_id
                )

        elif tool_name == "search_terminal":
            pattern = get_str("pattern")
            if not pattern:
                return ToolResult(
                    name=tool_call.name,
                    output="Error: No pattern provided",
                    tool_call_id=tool_call_id
                )

            scope = get_str("scope", "exec")
            if scope not in ("exec", "all"):
                scope = "exec"  # Default to exec for invalid scope
            case_sensitive = tool_args.get("case_sensitive", False)

            self.console.print()
            ConsoleHelper.info(self.console, f"Searching for: {pattern}")

            results = []
            try:
                if scope == "exec":
                    # Search only exec terminal
                    matches = self.search_in_scrollback(self.exec_terminal_uuid, pattern, case_sensitive)
                    if matches:
                        results.append(("Exec", matches))
                else:
                    # Search all terminals except chat
                    terminals = self.plugin_dbus.get_terminals_in_same_tab(self.chat_terminal_uuid)
                    for term in terminals:
                        term_uuid = term.get('uuid')
                        if term_uuid and term_uuid != self.chat_terminal_uuid:
                            matches = self.search_in_scrollback(term_uuid, pattern, case_sensitive)
                            if matches:
                                results.append((term.get('title', 'Unknown'), matches))

                # Format results
                if not results:
                    output = f"No matches found for pattern: {pattern}"
                    ConsoleHelper.warning(self.console, output)
                else:
                    total_matches = sum(len(m) for _, m in results)
                    ConsoleHelper.success(self.console, f"Found {total_matches} matches")

                    lines = []
                    for terminal_name, matches in results:
                        lines.append(f"## {terminal_name} ({len(matches)} matches)")
                        for m in matches[:20]:  # Limit to first 20 matches per terminal
                            line_num = m.get('line_number', '?')
                            text = m.get('text', '').strip()
                            lines.append(f"  Line {line_num}: {text}")
                        if len(matches) > 20:
                            lines.append(f"  ... and {len(matches) - 20} more matches")
                    output = "\n".join(lines)

                return ToolResult(
                    name=tool_call.name,
                    output=output,
                    tool_call_id=tool_call_id
                )

            except Exception as e:
                ConsoleHelper.error(self.console, f"Search error: {e}")
                return ToolResult(
                    name=tool_call.name,
                    output=f"Search error: {e}",
                    tool_call_id=tool_call_id
                )

        elif tool_name == "view_attachment":
            path_or_url = get_str("path_or_url")
            if not path_or_url:
                return ToolResult(
                    name=tool_call.name,
                    output="Error: No path_or_url provided",
                    tool_call_id=tool_call_id
                )

            caps = self._get_model_capabilities()

            # Create attachment and detect MIME type
            try:
                attachment = self._create_attachment(path_or_url)
                mime_type = attachment.resolve_type()
            except Exception as e:
                return ToolResult(
                    name=tool_call.name,
                    output=f"Error resolving attachment: {e}",
                    tool_call_id=tool_call_id
                )

            # Check if model supports this type
            if mime_type not in caps['supported_types']:
                if mime_type == "application/pdf" and not caps['pdf']:
                    result = f"Model doesn't support PDF viewing. Use load_pdf tool for text extraction."
                elif mime_type and mime_type.startswith("audio/") and not caps['audio']:
                    result = f"Model doesn't support audio ({mime_type}). Only Gemini models can process audio."
                elif mime_type and mime_type.startswith("video/") and not caps['video']:
                    result = f"Model doesn't support video ({mime_type}). Only Gemini models can process video."
                else:
                    result = f"Unsupported attachment type: {mime_type}"
                ConsoleHelper.warning(self.console, result)
                return ToolResult(
                    name=tool_call.name,
                    output=result,
                    tool_call_id=tool_call_id
                )

            self.pending_attachments.append(attachment)
            ConsoleHelper.success(self.console, f"Queued {mime_type}: {path_or_url}")
            return ToolResult(
                name=tool_call.name,
                output=f"Queued for viewing: {path_or_url} ({mime_type}). Will be visible in next turn.",
                tool_call_id=tool_call_id
            )

        elif tool_name == "view_pdf":
            path_or_url = get_str("path_or_url")

            if not path_or_url:
                return ToolResult(
                    name=tool_call.name,
                    output="Error: No path_or_url provided",
                    tool_call_id=tool_call_id
                )

            caps = self._get_model_capabilities()

            # Native PDF viewing only - no text extraction
            if not caps['pdf']:
                return ToolResult(
                    name=tool_call.name,
                    output="Error: Model doesn't support native PDF viewing. Use load_pdf for text extraction instead.",
                    tool_call_id=tool_call_id
                )

            try:
                attachment = self._create_attachment(path_or_url)
                self.pending_attachments.append(attachment)
                ConsoleHelper.success(self.console, f"PDF queued for native viewing: {path_or_url}")
                result = f"PDF queued for native viewing in next turn: {path_or_url}"
            except Exception as e:
                result = f"Failed to queue PDF: {e}"

            return ToolResult(
                name=tool_call.name,
                output=result,
                tool_call_id=tool_call_id
            )

        elif tool_name == "view_youtube_native":
            url = get_str("url")
            if not url:
                return ToolResult(
                    name=tool_call.name,
                    output="Error: No URL provided",
                    tool_call_id=tool_call_id
                )

            caps = self._get_model_capabilities()

            if not caps['youtube']:
                # Non-Gemini model - suggest load_yt() instead
                return ToolResult(
                    name=tool_call.name,
                    output="Error: Current model doesn't support native YouTube video. Use load_yt(url) for transcript extraction instead (faster, cheaper, works with any model).",
                    tool_call_id=tool_call_id
                )

            try:
                attachment = Attachment(url=url)
                attachment.type = "video/youtube"  # Force YouTube type
                self.pending_attachments.append(attachment)
                ConsoleHelper.success(self.console, "YouTube video queued for native viewing")
                return ToolResult(
                    name=tool_call.name,
                    output=f"YouTube video queued for native viewing (visual + audio). Will process in next turn.",
                    tool_call_id=tool_call_id
                )
            except Exception as e:
                return ToolResult(
                    name=tool_call.name,
                    output=f"Failed to queue YouTube video: {e}",
                    tool_call_id=tool_call_id
                )

        # Check if tool is in active external tools (mode-aware dispatch)
        active_external = self._get_active_external_tools()
        if tool_name in active_external:
            # Generic auto-dispatch for external tools (google_search, web_fetch, etc.)
            impl = active_external[tool_name]

            # Special handling for capture_screen with countdown display
            if tool_name == 'capture_screen':
                mode = tool_args.get('mode') or 'window'
                # Different default delays: automatic modes (rdp, full) don't need user prep time
                # Interactive modes (window, region, annotate) need time for user to position/click
                default_delay = 0 if mode in ('rdp', 'full') else 5
                try:
                    raw_delay = tool_args.get('delay')
                    delay = int(raw_delay) if raw_delay is not None else default_delay
                except (ValueError, TypeError):
                    delay = default_delay
                delay = max(0, min(delay, 30))

                # Display countdown if delay > 0 using Rich Status
                if delay > 0:
                    with Status(f"[cyan]Capturing screenshot ({mode}): {delay}s[/]", console=self.console, spinner="dots", spinner_style="cyan") as status:
                        for i in range(delay, 0, -1):
                            status.update(f"[cyan]Capturing screenshot ({mode}): {i}s[/]")
                            time.sleep(1)

                # Call tool with delay=0 since we already waited
                tool_args = dict(tool_args)
                tool_args['delay'] = 0

                # Use spinner while capturing
                try:
                    with Spinner(f"Capturing screenshot ({mode})...", console=self.console):
                        result = impl(**tool_args)
                    ConsoleHelper.success(self.console, f"{tool_name} completed")
                    self.console.print()  # Blank line for visual separation

                    # Handle ToolOutput return type
                    attachments = []
                    if isinstance(result, ToolOutput):
                        attachments = result.attachments
                        result = result.output

                    if result is None:
                        result = ""
                    elif not isinstance(result, str):
                        result = json.dumps(result, default=repr)

                    return ToolResult(
                        name=tool_call.name,
                        output=result,
                        attachments=attachments,
                        tool_call_id=tool_call_id
                    )
                except Exception as e:
                    ConsoleHelper.error(self.console, f"{tool_name} error: {e}")
                    return ToolResult(
                        name=tool_call.name,
                        output=f"Error calling {tool_name}: {e}",
                        tool_call_id=tool_call_id
                    )

            # Display primary parameter if configured, otherwise generic message
            # Build status message for spinner
            if tool_name in EXTERNAL_TOOL_DISPLAY:
                param_name, action_verb, _ = EXTERNAL_TOOL_DISPLAY[tool_name]
                param_value = tool_args.get(param_name, '')
                if param_value:
                    status_msg = f"{action_verb}: {param_value}"
                else:
                    status_msg = f"Calling {tool_name}..."
            else:
                status_msg = f"Calling {tool_name}..."

            try:
                with Spinner(status_msg, console=self.console):
                    result = impl(**tool_args)
                self.console.print(f"[dim cyan]{status_msg}[/]")  # dim + cyan combined style
                ConsoleHelper.success(self.console, f"{tool_name} completed")
                self.console.print()  # Blank line for visual separation

                # Handle ToolOutput return type (e.g., capture_screen with attachments)
                attachments = []
                if isinstance(result, ToolOutput):
                    attachments = result.attachments
                    result = result.output

                # Ensure result is a string
                if result is None:
                    result = ""
                elif not isinstance(result, str):
                    result = json.dumps(result, default=repr)

                return ToolResult(
                    name=tool_call.name,
                    output=result,
                    attachments=attachments,
                    tool_call_id=tool_call_id
                )
            except Exception as e:
                ConsoleHelper.error(self.console, f"{tool_name} error: {e}")
                return ToolResult(
                    name=tool_call.name,
                    output=f"Error calling {tool_name}: {e}",
                    tool_call_id=tool_call_id
                )

        else:
            # Unknown tool - shouldn't happen with our defined tools
            ConsoleHelper.warning(self.console, f"Unknown tool: {tool_call.name}")
            return ToolResult(
                name=tool_call.name,
                output=f"Unknown tool: {tool_call.name}",
                tool_call_id=tool_call_id
            )

    def run(self):
        """Main REPL loop with health checks"""
        # Connect to Terminator and setup terminals (skip in no-exec mode)
        if not self.no_exec_mode:
            self._connect_to_terminator()
            self.setup_terminals()

        # Build external tools list for display (mode-aware)
        external_tools_list = ""
        active_external = self._get_active_external_tools()
        if active_external:
            tools_info = []
            for name in sorted(active_external.keys()):
                if name in EXTERNAL_TOOL_DISPLAY:
                    _, _, desc = EXTERNAL_TOOL_DISPLAY[name]
                    tools_info.append(f"  [dim]•[/] {name}: {desc}")
                else:
                    tools_info.append(f"  [dim]•[/] {name}")
            if tools_info:
                external_tools_list = "\n\n[bold]External Tools:[/]\n" + "\n".join(tools_info)

        # Display welcome message
        self.console.print(Panel.fit(
            f"""Model: [cyan]{self.model_name}[/]{external_tools_list}

[bold]Commands:[/]
Type /help for slash commands
Type !multi to enter multiple lines, then !end to finish
Type !fragment <name> [...] to insert fragments""",
            title="llm-assistant",
            border_style="green"
        ))

        # Main REPL loop with periodic health checks
        check_counter = 0

        # Multi-line input state
        in_multi = False
        accumulated_lines = []
        end_token = "!end"

        try:
            while True:
                # Periodic health check every 10 iterations (skip in no-exec mode)
                check_counter += 1
                if check_counter >= 10 and not self.no_exec_mode:
                    # Check plugin availability
                    if not self._check_plugin_available():
                        ConsoleHelper.warning(self.console, "Plugin unavailable, attempting reconnect...")
                        if not self._reconnect_plugin():
                            ConsoleHelper.error(self.console, "Plugin reconnection failed. Please restart assistant.")
                            break

                    # Check D-Bus connection
                    if not self._check_dbus_connection():
                        ConsoleHelper.warning(self.console, "D-Bus disconnected, attempting reconnect...")
                        if not self._reconnect_dbus():
                            ConsoleHelper.error(self.console, "D-Bus reconnection failed. Please restart assistant.")
                            break

                    check_counter = 0

                # Get user input (change prompt based on mode)
                # Uses prompt_toolkit for Ctrl+Space voice toggle support
                try:
                    if in_multi:
                        # Multi-line mode - dim continuation prompt
                        user_input = self.prompt_session.prompt([('class:continuation', '... ')])
                    else:
                        # Dynamic prompt based on voice status
                        def get_prompt():
                            if self.voice_input and self.voice_input.status_message:
                                msg = self.voice_input.status_message
                                # Extract animated symbol (first char of status_message)
                                symbol = msg[0] if msg else "●"
                                if "Recording" in msg:
                                    return [('class:prompt.recording', f'\n{symbol} ')]
                                elif "Transcribing" in msg:
                                    return [('class:prompt.transcribing', f'\n{symbol} ')]
                            return [('class:prompt', '\n> ')]
                        user_input = self.prompt_session.prompt(get_prompt).strip()
                except (KeyboardInterrupt, EOFError):
                    # Double-press protection
                    now = time.time()
                    if now - self._last_interrupt_time > 2.0:
                        self._last_interrupt_time = now
                        # Go up to overwrite the > prompt line, clear it, print message
                        # Then prompt's \n> will show blank line + new >
                        sys.stdout.write("\033[A\r\033[K")
                        sys.stdout.write("\033[90m(press again within 2 seconds to exit)\033[0m\n")
                        sys.stdout.flush()
                        continue
                    ConsoleHelper.warning(self.console, "\nExiting...")
                    break

                # Reset terminal state after prompt_toolkit to ensure clean state for Rich output
                # prompt_toolkit enables bracketed paste mode and application keypad mode
                sys.stdout.write('\x1b[?2004l\x1b>\x1b[?25h\x1b[0m')
                sys.stdout.flush()

                # Handle !multi command (start multi-line mode)
                if user_input.startswith("!multi"):
                    in_multi = True
                    bits = user_input.split()
                    if len(bits) > 1:
                        end_token = f"!end {' '.join(bits[1:])}"
                    else:
                        end_token = "!end"
                    ConsoleHelper.dim(self.console, f"Multi-line mode. Type '{end_token}' to finish")
                    continue

                # Handle multi-line input accumulation
                if in_multi:
                    if user_input == end_token:
                        # Join accumulated lines and process
                        user_input = "\n".join(accumulated_lines)
                        accumulated_lines = []
                        in_multi = False
                    else:
                        # Accumulate this line
                        accumulated_lines.append(user_input)
                        continue

                if not user_input:
                    continue

                # Handle exit/quit commands (like llm chat mode)
                if user_input in ("exit", "quit"):
                    self.console.print()
                    ConsoleHelper.warning(self.console, "Exiting...")
                    self._shutdown()
                    break

                # Handle memory command (# prefix) - not shebang (#!)
                if user_input.startswith('#') and not user_input.startswith('#!'):
                    rest = user_input[1:].strip()
                    if rest:
                        self._handle_hash_command(rest)
                        continue

                # Handle slash commands
                if user_input.startswith('/'):
                    result = self.handle_slash_command(user_input)
                    if result == "prompt_queued" and self._queued_prompt:
                        # /capture with inline prompt - continue with the queued prompt
                        user_input = self._queued_prompt
                        self._queued_prompt = None
                        # Fall through to AI processing (don't continue)
                    elif not result:
                        break
                    else:
                        continue

                # Check if we need to squash context (before lock to avoid holding it during squash)
                self.check_and_squash_context()

                # Send to AI with streaming
                # Pass system prompt on first interaction or when conversation is empty
                response_text = ""
                stream_success = False

                try:
                    self.console.print()
                    ConsoleHelper.enabled(self.console, "llm")

                    # Thread-safe context capture AND conversation access
                    # Extended lock scope fixes race condition with watch mode
                    with self.watch_lock:
                        # Clear plugin cache inside lock to ensure atomic operation
                        try:
                            self.plugin_dbus.clear_cache()
                        except Exception:
                            pass  # Ignore if clear_cache not available

                        # Simple snapshot capture (no stability detection)
                        # Works better for TUI applications and provides instant feedback
                        # Returns (context_text, tui_attachments) tuple for TUI screenshot support
                        # Enable per-terminal deduplication after first message
                        has_prior_context = len(self.conversation.responses) > 0
                        context, tui_attachments = self.capture_context(
                            include_exec_output=True,
                            dedupe_unchanged=has_prior_context
                        )

                        # Process fragments if present
                        processed_input, fragments, fragment_attachments = self.process_fragments(user_input)

                        # Combine fragment attachments, TUI screenshots, and pending attachments
                        all_attachments = fragment_attachments + tui_attachments + self.pending_attachments

                        # Clear pending attachments after including them
                        self.pending_attachments = []

                        # RAG context retrieval (one-shot or persistent mode)
                        rag_context = ""
                        if self.pending_rag_context:
                            # One-shot: consume pending context from /rag search
                            rag_context = self.pending_rag_context
                            self.pending_rag_context = None
                            self._debug("RAG: using one-shot search results")
                        elif self.active_rag_collection and processed_input.strip():
                            # Persistent mode: search on every prompt
                            self._debug(f"RAG: searching {self.active_rag_collection}...")
                            rag_context = self._retrieve_rag_context(processed_input)
                            if rag_context:
                                self._debug("RAG: injecting context")

                        # Build prompt with context (order: terminal context → RAG context → user input)
                        prompt_parts = []

                        if context:
                            prompt_parts.append(f"<terminal_context>\n{context}\n</terminal_context>")

                        if rag_context:
                            prompt_parts.append(rag_context)

                        prompt_parts.append(processed_input)

                        full_prompt = "\n\n".join(prompt_parts)

                        # Prepend pending summary from squash (one-time use)
                        if self.pending_summary:
                            full_prompt = f"""<conversation_summary>
{self.pending_summary}
</conversation_summary>

{full_prompt}"""
                            self.pending_summary = None

                        # Broadcast user message to web companion
                        if self.web_clients:
                            self._broadcast_to_web({
                                "type": "user_message",
                                "content": processed_input
                            })

                        # Always pass system prompt on every call (required for Gemini/Vertex
                        # which is stateless - systemInstruction must be sent on every request)
                        # Also pass fragments and attachments (TUI screenshots)
                        # Include tools for structured output (schema validation)
                        response = self._prompt(
                            full_prompt,
                            system=self._build_system_prompt(),
                            fragments=[str(f) for f in fragments] if fragments else None,
                            attachments=all_attachments if all_attachments else None,
                            tools=self._get_active_tools()
                        )

                        # Stream response with Live markdown display INSIDE lock to prevent
                        # race condition with watch mode - watch thread will block until
                        # streaming completes, which is the desired behavior.
                        #
                        # TTS: If speech output is enabled, queue sentences to TTS
                        # as they complete (for low-latency audio output)
                        tts_enabled = (self.speech_output and self.speech_output.enabled
                                       and self._is_vertex_model())

                        # Stream response with Live markdown display
                        response_text = self._stream_response_with_display(response, tts_enabled=tts_enabled)

                        # Extract tool calls from the response (structured output)
                        tool_calls = list(response.tool_calls())

                        # Log response to database for --continue functionality
                        # INSIDE lock to prevent race with watch mode reading stripped prompt
                        self._log_response(response)

                    # Force display to ensure clean terminal state before tool processing
                    # (Live already displayed the response during streaming)
                    if response_text.strip():
                        self._force_display()

                    stream_success = True

                except Exception as e:
                    print()  # Ensure newline even on error
                    ConsoleHelper.error(self.console, f"Streaming error: {e}")
                    ConsoleHelper.warning(self.console, "Response may be incomplete. Please try again.")
                    # Don't process commands or update conversation on stream failure
                    continue

                # Only process tool calls if streaming succeeded
                if stream_success:
                    try:
                        # Process tool calls (structured output from model)
                        if tool_calls:
                            if self.debug:
                                self.console.print()
                                ConsoleHelper.info(self.console, f"Processing {len(tool_calls)} tool call(s)")

                            # Collect tool results for sending back to model
                            tool_results = []

                            for i, tool_call in enumerate(tool_calls, 1):
                                if len(tool_calls) > 1:
                                    self.console.print()
                                    ConsoleHelper.bold(self.console, f"Tool {i}/{len(tool_calls)}: {tool_call.name}")
                                tool_results.append(self._process_tool_call(tool_call))

                            # After processing all tool calls, send results back to model
                            # Loop to handle multi-round tool calling
                            # Tool iteration limits:
                            # - auto mode: 1000 (unlimited, overrides mode)
                            # - agent mode: 100 (agentic)
                            # - assistant mode: 10 (conservative)
                            if self.auto_mode:
                                MAX_TOOL_ITERATIONS = 1000
                            elif self.mode == "agent":
                                MAX_TOOL_ITERATIONS = 100
                            else:  # assistant mode
                                MAX_TOOL_ITERATIONS = 10
                            iteration = 0

                            while tool_results and iteration < MAX_TOOL_ITERATIONS:
                                iteration += 1
                                if self.debug:
                                    self.console.print()
                                    ConsoleHelper.dim(self.console, f"Sending {len(tool_results)} tool result(s) to model...")

                                # Extract attachments from tool results (e.g., capture_screen screenshots)
                                # This mirrors llm's ChainResponse behavior
                                tool_result_attachments = []
                                for tr in tool_results:
                                    if tr.attachments:
                                        tool_result_attachments.extend(tr.attachments)

                                # Include pending attachments from view_attachment calls (auto-send)
                                followup_attachments = list(tool_result_attachments)
                                if self.pending_attachments:
                                    followup_attachments.extend(self.pending_attachments)
                                    self.pending_attachments = []  # Clear after including

                                if self.debug and followup_attachments:
                                    ConsoleHelper.dim(self.console, f"Including {len(followup_attachments)} attachment(s) ({len(tool_result_attachments)} from tools)")

                                with self.watch_lock:
                                    # Continue conversation with tool results (and attachments)
                                    followup_response = self._prompt(
                                        "",  # Empty prompt - tool results drive the continuation
                                        tools=self._get_active_tools(),
                                        tool_results=tool_results,
                                        attachments=followup_attachments if followup_attachments else None
                                    )

                                    # Stream follow-up response with Live markdown display
                                    followup_text = self._stream_response_with_display(followup_response, tts_enabled=tts_enabled)

                                    # Check if the model made more tool calls
                                    more_tool_calls = list(followup_response.tool_calls())

                                    # Log followup response to database
                                    # INSIDE lock to prevent race with watch mode reading stripped prompt
                                    self._log_response(followup_response)

                                # (Live already displayed the response during streaming)

                                # Process additional tool calls if any
                                if not more_tool_calls:
                                    break

                                if self.debug:
                                    self.console.print()
                                    ConsoleHelper.info(self.console, f"Processing {len(more_tool_calls)} additional tool call(s) (round {iteration + 1})")
                                tool_results = []

                                for j, tool_call in enumerate(more_tool_calls, 1):
                                    if len(more_tool_calls) > 1:
                                        self.console.print()
                                        ConsoleHelper.bold(self.console, f"Tool {j}/{len(more_tool_calls)}: {tool_call.name}")
                                    tool_results.append(self._process_tool_call(tool_call))

                            # Warn if max iterations reached and model still wants more
                            if iteration >= MAX_TOOL_ITERATIONS and more_tool_calls:
                                self.console.print()
                                ConsoleHelper.warning(self.console, f"Max tool iterations ({MAX_TOOL_ITERATIONS}) reached. Model requested {len(more_tool_calls)} more tool call(s). Please continue the conversation.")

                            # Auto-send any remaining pending attachments (e.g., view_attachment was last tool call)
                            if self.pending_attachments:
                                if self.debug:
                                    self.console.print()
                                    ConsoleHelper.dim(self.console, f"Auto-sending {len(self.pending_attachments)} pending attachment(s)")

                                attachments_to_send = self.pending_attachments
                                self.pending_attachments = []

                                with self.watch_lock:
                                    attachment_response = self._prompt(
                                        "",  # Empty prompt - attachments drive the continuation
                                        tools=self._get_active_tools(),
                                        attachments=attachments_to_send
                                    )

                                    # Stream attachment response with Live markdown display
                                    response_text = self._stream_response_with_display(attachment_response, tts_enabled=tts_enabled)

                                    more_calls = list(attachment_response.tool_calls())

                                    # Log attachment response to database
                                    # INSIDE lock to prevent race with watch mode reading stripped prompt
                                    self._log_response(attachment_response)

                                # (Live already displayed the response during streaming)

                                # Process any tool calls from viewing the attachment
                                if more_calls:
                                    for tc in more_calls:
                                        self._process_tool_call(tc)

                    except Exception as e:
                        self.console.print()
                        ConsoleHelper.error(self.console, f"Tool execution error: {e}")

        finally:
            # Unified cleanup - handles all resources
            self._shutdown()


