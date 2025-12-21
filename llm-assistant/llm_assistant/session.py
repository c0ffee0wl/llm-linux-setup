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
from llm.cli import process_fragments_in_chat, logs_db_path
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
from .prompt_detection import PromptDetector
from .system_info import detect_os, detect_shell, detect_environment
from .voice import VoiceInput, VOICE_AVAILABLE, VOICE_UNAVAILABLE_REASON
from .speech import SpeechOutput, SentenceBuffer, TTS_AVAILABLE
from .ui import Spinner, Confirm
from .completer import SlashCommandCompleter
from .config import (
    SLASH_COMMANDS,
    TUI_COMMANDS,
    MODEL_CONTEXT_LIMITS,
    PROVIDER_DEFAULT_LIMITS,
    DEFAULT_CONTEXT_LIMIT,
    EXTERNAL_TOOL_PLUGINS,
    AGENT_MODE_TOOLS,
    OPTIONAL_TOOL_PLUGINS,
    GEMINI_ONLY_TOOL_NAMES,
    EXTERNAL_TOOL_DISPLAY,
    is_tui_command,
)
from .schemas import FindingSchema, SafetySchema
from .utils import strip_markdown_for_tts, strip_markdown_for_clipboard, validate_language_code
from .templates import render

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


# =============================================================================
# Tool Setup
# =============================================================================
# Always-on external tools (always available and auto-dispatch)
_all_tools = llm.get_tools()

# =============================================================================
# Background MCP Loading
# =============================================================================
# Load MCP tools in background to avoid blocking startup
# User sees prompt immediately, MCP tools become available when loading completes

_mcp_future = None
_mcp_toolbox = None
_mcp_loaded = False
_mcp_lock = threading.Lock()
_mcp_executor = ThreadPoolExecutor(max_workers=1)


def _load_mcp_background():
    """Load MCP tools in background thread."""
    try:
        from llm_tools_mcp.register_tools import MCP
        return MCP()
    except ImportError:
        return None  # llm-tools-mcp not installed
    except Exception:
        return None  # MCP config missing or server unavailable


# Start loading immediately but don't block module import
_mcp_future = _mcp_executor.submit(_load_mcp_background)


def _ensure_mcp_loaded():
    """Wait for background MCP load to complete, then register tools.

    Call this before accessing ASSISTANT_TOOLS or EXTERNAL_TOOLS to ensure
    MCP tools are available. The first call waits for background load to
    complete, subsequent calls return immediately.
    """
    global _mcp_toolbox, _mcp_loaded, ASSISTANT_TOOLS, EXTERNAL_TOOLS
    if _mcp_loaded:
        return

    with _mcp_lock:
        if _mcp_loaded:
            return
        if _mcp_future:
            try:
                _mcp_toolbox = _mcp_future.result(timeout=30)
                if _mcp_toolbox:
                    for tool in _mcp_toolbox.tools():
                        _all_tools[tool.name] = tool
                    _rebuild_tool_lists()
            except Exception:
                pass  # Failed to load MCP tools
        _mcp_loaded = True


def _rebuild_tool_lists():
    """Rebuild ASSISTANT_TOOLS and EXTERNAL_TOOLS after MCP tools load."""
    global ASSISTANT_TOOLS, EXTERNAL_TOOLS

    # Rebuild ASSISTANT_TOOLS with MCP tools included
    ASSISTANT_TOOLS = [
        tool for tool in _all_tools.values()
        if isinstance(tool, Tool)
        and getattr(tool, 'plugin', None) in ('assistant',) + EXTERNAL_TOOL_PLUGINS
        and tool.name not in GEMINI_ONLY_TOOL_NAMES
    ]

    # Rebuild EXTERNAL_TOOLS dispatch dict
    EXTERNAL_TOOLS = {
        name: tool.implementation
        for name, tool in _all_tools.items()
        if isinstance(tool, Tool)
        and hasattr(tool, 'implementation') and tool.implementation is not None
        and getattr(tool, 'plugin', None) in EXTERNAL_TOOL_PLUGINS
    }

# Build ASSISTANT_TOOLS - base tools always offered to model
# (agent-mode, optional, and Gemini-only tools added dynamically via _get_active_tools())
ASSISTANT_TOOLS = [
    tool for tool in _all_tools.values()
    if isinstance(tool, Tool)
    and getattr(tool, 'plugin', None) in ('assistant',) + EXTERNAL_TOOL_PLUGINS
    and tool.name not in GEMINI_ONLY_TOOL_NAMES  # Exclude Gemini-only tools from base
]

# Build EXTERNAL_TOOLS - base dispatch dict (always-on tools)
EXTERNAL_TOOLS = {
    name: tool.implementation
    for name, tool in _all_tools.items()
    if isinstance(tool, Tool)
    and hasattr(tool, 'implementation') and tool.implementation is not None
    and getattr(tool, 'plugin', None) in EXTERNAL_TOOL_PLUGINS
}

# Separate dispatch dicts for conditional tools (agent-mode and optional)
AGENT_EXTERNAL_TOOLS = {
    name: tool.implementation
    for name, tool in _all_tools.items()
    if isinstance(tool, Tool)
    and hasattr(tool, 'implementation') and tool.implementation is not None
    and getattr(tool, 'plugin', None) in AGENT_MODE_TOOLS
}

OPTIONAL_EXTERNAL_TOOLS = {
    name: tool.implementation
    for name, tool in _all_tools.items()
    if isinstance(tool, Tool)
    and hasattr(tool, 'implementation') and tool.implementation is not None
    and getattr(tool, 'plugin', None) in OPTIONAL_TOOL_PLUGINS
}

class TerminatorAssistantSession:
    """Main assistant session manager for Terminator"""

    def __init__(self, model_name: Optional[str] = None, debug: bool = False, max_context_size: Optional[int] = None,
                 continue_: bool = False, conversation_id: Optional[str] = None, no_log: bool = False,
                 agent_mode: bool = False):
        self.console = Console()

        # Debug mode flag
        self.debug = debug

        # Conversation persistence settings
        self.logging_enabled = not no_log
        self.continue_ = continue_
        self.conversation_id = conversation_id

        # Initialize shutdown state and lock file handle
        self._shutdown_initiated = False
        self._last_interrupt_time = 0.0  # For double-press exit protection
        self.lock_file = None

        # Early D-Bus detection - require Terminator
        self.early_terminal_uuid = self._get_current_terminal_uuid_early()

        if not self.early_terminal_uuid:
            self.console.print("[red]Error: llm-assistant requires Terminator terminal[/]")
            self.console.print("[yellow]Please run this from inside a Terminator terminal.[/]")
            sys.exit(1)

        # Acquire per-tab lock (held for entire session, prevents duplicates)
        self._acquire_instance_lock()

        # Register shutdown handlers EARLY (before creating resources)
        self._register_shutdown_handlers()

        self.model_name = model_name or self._get_default_model()

        try:
            self.model = llm.get_model(self.model_name)
        except Exception as e:
            self.console.print(f"[red]Error loading model '{self.model_name}': {e}[/]")
            self.console.print("[yellow]Available models:[/]")
            for model in llm.get_models():
                self.console.print(f"  - {model.model_id}")
            sys.exit(1)

        self._init_conversation()

        # Operating mode must be set BEFORE rendering system prompt
        # (Jinja2 template uses mode to conditionally include agent/assistant content)
        self.mode: str = "agent" if agent_mode else "assistant"

        # Render system prompt using Jinja2 template
        # Template handles mode filtering and environment injection
        self.system_prompt = self._render_system_prompt()
        self._debug(f"System prompt rendered for {self.mode} mode ({len(self.system_prompt)} chars)")

        # Store original system prompt for context squashing
        # This prevents infinite growth when squashing multiple times
        self.original_system_prompt = self.system_prompt

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

        # Watch mode (thread-safe)
        self.watch_mode = False
        self.watch_goal = None
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

        # RAG system (llm-tools-rag integration)
        self.active_rag_collection: Optional[str] = None  # Active collection (persistent mode)
        self.rag_top_k: int = 5                           # Number of results to retrieve
        self.rag_search_mode: str = "hybrid"              # hybrid|vector|keyword
        self.pending_rag_context: Optional[str] = None    # One-shot search result

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
        self.findings_base_dir: Path = llm.user_dir() / "llm-assistant" / "findings"

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
            runtime_dir = Path(tempfile.gettempdir()) / f'llm-assistant-{os.getuid()}'
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
            self.console.print("[red]Error: An assistant is already running in this tab[/]")
            self.console.print("[yellow]You can run assistant in a different Terminator tab.[/]")
            sys.exit(1)

        except Exception as e:
            self.console.print(f"[red]Error acquiring lock: {e}[/]")
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
        base_dir = Path(tempfile.gettempdir()) / f'llm-assistant-{os.getuid()}'
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
            screenshot_dir = Path(tempfile.gettempdir())

        return screenshot_dir

    def _get_current_terminal_uuid_early(self) -> Optional[str]:
        """
        Get current terminal UUID from environment BEFORE acquiring lock.
        This enables tab-specific locking for multi-tab support.

        Uses TERMINATOR_UUID environment variable which is stable and set
        at terminal creation time (doesn't change when focus changes).

        Returns:
            Terminal UUID string, or None if not in Terminator
        """
        # Use TERMINATOR_UUID env var - stable, doesn't change with focus
        env_uuid = os.environ.get('TERMINATOR_UUID')
        if env_uuid:
            return self._normalize_uuid(env_uuid)
        return None  # Not running in Terminator

    def _process_exists(self, pid: int) -> bool:
        """
        Check if a process with the given PID exists.

        Args:
            pid: Process ID to check

        Returns:
            True if process exists, False otherwise
        """
        try:
            os.kill(pid, 0)  # Signal 0 = check existence without killing
            return True
        except (OSError, ProcessLookupError):
            return False

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

        self.console.print(f"\n[yellow]Received {signal_name}, shutting down...[/]")
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
                self.console.print(f"[yellow]Warning during shutdown: {e}[/]")
            except Exception:
                # If console fails, write to stderr
                print(f"Warning during shutdown: {e}", file=sys.stderr)

    def _get_default_model(self) -> str:
        """Get default model from llm configuration"""
        try:
            return llm.get_default_model()
        except Exception:
            return "azure/gpt-4.1-mini"

    def _is_vertex_model(self) -> bool:
        """Check if current model is a Vertex AI model (vertex/*)"""
        return self.model_name.startswith("vertex/")

    def _is_gemini_model(self) -> bool:
        """Check if current model is a Gemini model (vertex/* or gemini-*)"""
        return self.model_name.startswith("vertex/") or self.model_name.startswith("gemini-")

    def _get_model_context_limit(self, model_name: str) -> int:
        """
        Get the appropriate context limit for a model.

        Resolution order:
        1. Explicit model name in MODEL_CONTEXT_LIMITS (with azure/ prefix stripped)
        2. Provider prefix default from PROVIDER_DEFAULT_LIMITS
        3. DEFAULT_CONTEXT_LIMIT fallback
        """
        # Strip azure/ prefix for lookup (azure/gpt-4.1 -> gpt-4.1)
        lookup_name = model_name
        if model_name.startswith("azure/"):
            lookup_name = model_name[6:]  # Remove "azure/"

        # Check explicit model limit
        if lookup_name in MODEL_CONTEXT_LIMITS:
            return MODEL_CONTEXT_LIMITS[lookup_name]

        # Check provider prefix defaults
        for prefix, limit in PROVIDER_DEFAULT_LIMITS.items():
            if model_name.startswith(prefix):
                return limit

        # Absolute fallback
        return DEFAULT_CONTEXT_LIMIT

    def _debug(self, msg: str):
        """Print debug message if debug mode is enabled"""
        if self.debug:
            self.console.print(f"[dim]DEBUG: {msg}[/dim]")

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

    # =========================================================================
    # Web Companion Methods
    # =========================================================================

    def _get_web_html(self) -> str:
        """Return the HTML for the web companion interface from Jinja2 template."""
        return render('web_companion.html')

    # NOTE: The inline HTML that was here has been moved to templates/web_companion.html
    # and is now rendered by the render() function.


    def _start_web_server(self):
        """Start the web companion server in a background thread."""
        if not WEB_AVAILABLE:
            self.console.print("[red]Web companion not available. Install: llm install fastapi uvicorn[/]")
            return False

        if self.web_server_thread and self.web_server_thread.is_alive():
            self.console.print(f"[yellow]Web server already running at http://localhost:{self.web_port}[/]")
            return True

        # Create FastAPI app
        app = FastAPI()
        session = self  # Closure reference

        @app.get("/", response_class=HTMLResponse)
        async def get_index():
            return session._get_web_html()

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            session.web_clients.add(websocket)
            try:
                # Send session info (model, mode)
                await websocket.send_json({
                    "type": "session_info",
                    "model": session.model_name,
                    "mode": session.mode  # "agent" or "assistant"
                })

                # Send watch mode status
                await websocket.send_json({
                    "type": "watch_status",
                    "active": session.watch_mode,
                    "goal": session.watch_goal if session.watch_mode else None
                })

                # Send current token usage
                current_tokens = session.estimate_tokens()
                max_tokens = session.max_context_size
                pct = (current_tokens / max_tokens * 100) if max_tokens > 0 else 0
                await websocket.send_json({
                    "type": "token_update",
                    "current_tokens": current_tokens,
                    "max_tokens": max_tokens,
                    "percentage": pct
                })

                # Send conversation history on connect
                history = []
                for r in session.conversation.responses:
                    # Skip responses that are still streaming - they'll be sent via live broadcast
                    # text() calls _force() which blocks until done, causing connection delays
                    if not getattr(r, '_done', False):
                        continue
                    if hasattr(r, 'prompt') and r.prompt:
                        # Strip terminal context from prompts (same as DB storage)
                        clean_prompt = session._strip_context(r.prompt.prompt or "")
                        if clean_prompt and clean_prompt.strip():
                            history.append({"role": "user", "content": clean_prompt})
                    assistant_text = r.text()
                    if assistant_text and assistant_text.strip():
                        assistant_msg = {"role": "assistant", "content": assistant_text}
                        # Include thinking traces if available
                        if hasattr(r, 'response_json') and r.response_json:
                            thinking = r.response_json.get("thinking_traces", [])
                            if thinking:
                                assistant_msg["thinking_traces"] = thinking
                        history.append(assistant_msg)
                await websocket.send_json({"type": "history", "messages": history})

                # Keep connection alive and handle requests
                while True:
                    try:
                        msg = await websocket.receive_text()
                        # Handle debug info request
                        if msg == "debug_request":
                            try:
                                debug_data = session._get_debug_info()
                                await websocket.send_json({"type": "debug_info", **debug_data})
                            except Exception as e:
                                await websocket.send_json({
                                    "type": "debug_info",
                                    "system_prompt": f"Error getting debug info: {e}",
                                    "tools": [],
                                    "messages": [],
                                    "model": "unknown",
                                    "mode": "unknown",
                                    "options": {},
                                    "max_context": 0,
                                    "current_tokens": 0,
                                    "loaded_kbs": [],
                                    "active_rag": None
                                })
                    except Exception:
                        break
            finally:
                session.web_clients.discard(websocket)

        # Configure uvicorn
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.web_port,
            log_level="warning",
            access_log=False
        )
        self.web_server = uvicorn.Server(config)

        # Run in background thread
        def run_server():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # Store loop reference for cross-thread broadcasting
            session.web_event_loop = loop
            loop.run_until_complete(self.web_server.serve())

        self.web_server_thread = threading.Thread(target=run_server, daemon=True)
        self.web_server_thread.start()

        # Give server time to start and event loop to be set
        time.sleep(0.5)
        return True

    def _stop_web_server(self):
        """Stop the web companion server."""
        if self.web_server:
            self.web_server.should_exit = True
            self.web_server = None
            self.web_server_thread = None
            self.web_event_loop = None
            self.web_clients.clear()
            self.console.print("[green]✓[/] Web server stopped")
        else:
            self.console.print("[yellow]Web server is not running[/]")

    def _broadcast_to_web(self, message: dict):
        """Broadcast a message to all connected web clients.

        Thread-safe: schedules the broadcast on the web server's event loop.
        """
        if not self.web_clients or not self.web_event_loop:
            return

        import asyncio

        async def send_to_all():
            disconnected = set()
            # Iterate over a copy to avoid modification during iteration
            for client in list(self.web_clients):
                try:
                    await client.send_json(message)
                except Exception:
                    disconnected.add(client)
            # Remove disconnected clients
            for client in disconnected:
                self.web_clients.discard(client)

        # Schedule coroutine on the web server's event loop (thread-safe)
        try:
            asyncio.run_coroutine_threadsafe(send_to_all(), self.web_event_loop)
        except RuntimeError:
            # Event loop closed or not running
            pass

    def _broadcast_watch_status(self):
        """Broadcast current watch mode status to web clients."""
        self._broadcast_to_web({
            "type": "watch_status",
            "active": self.watch_mode,
            "goal": self.watch_goal if self.watch_mode else None
        })

    def _broadcast_token_update(self):
        """Broadcast current token usage to web clients."""
        current_tokens = self.estimate_tokens()
        max_tokens = self.max_context_size
        pct = (current_tokens / max_tokens * 100) if max_tokens > 0 else 0
        self._broadcast_to_web({
            "type": "token_update",
            "current_tokens": current_tokens,
            "max_tokens": max_tokens,
            "percentage": pct
        })

    def _broadcast_tool_call(self, tool_name: str, arguments: dict, result: str = None, status: str = "pending"):
        """Broadcast a tool call event to web clients."""
        self._broadcast_to_web({
            "type": "tool_call",
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "status": status
        })

    def _get_default_mcp_servers(self) -> set:
        """Get non-optional MCP servers (loaded by default)."""
        _ensure_mcp_loaded()
        servers = set()
        for tool in _all_tools.values():
            server = getattr(tool, 'server_name', None)
            is_optional = getattr(tool, 'mcp_optional', False)
            if server and not is_optional:
                servers.add(server)
        return servers

    def _get_all_mcp_servers(self) -> dict:
        """Get all MCP servers with their optional status.

        Returns dict mapping server_name -> is_optional (bool)
        """
        _ensure_mcp_loaded()
        servers = {}
        for tool in _all_tools.values():
            server = getattr(tool, 'server_name', None)
            if server and server not in servers:
                servers[server] = getattr(tool, 'mcp_optional', False)
        return servers

    def _count_tools_for_server(self, server_name: str) -> int:
        """Count tools available from a specific MCP server."""
        count = 0
        for tool in _all_tools.values():
            if getattr(tool, 'server_name', None) == server_name:
                count += 1
        return count

    def _get_active_tools(self) -> list:
        """Get currently active tools based on mode, model, and loaded state.

        Returns tools that should be offered to the model. Includes:
        - Base ASSISTANT_TOOLS (always available, includes capture_screen)
        - Agent-mode tools when in /agent mode (currently none)
        - Optional tools (imagemage) when manually loaded via /imagemage
        - Gemini-only tools (view_youtube_native) when using Gemini/Vertex model

        MCP tools are filtered by active_mcp_servers set - only tools from
        active servers are included.
        """
        # Ensure MCP tools are loaded (waits for background load on first call)
        _ensure_mcp_loaded()

        tools = list(ASSISTANT_TOOLS)  # Base tools (always available)

        # Filter MCP tools by active server set (handles both default and optional servers)
        tools = [t for t in tools if (
            getattr(t, 'server_name', None) is None or  # Not an MCP tool
            getattr(t, 'server_name', None) in self.active_mcp_servers  # MCP server is active
        )]

        # Add agent-mode tools if in agent mode
        if self.mode == "agent":
            for plugin_name in AGENT_MODE_TOOLS:
                for tool in _all_tools.values():
                    if getattr(tool, 'plugin', None) == plugin_name:
                        tools.append(tool)

        # Add optional tools if manually loaded via /imagemage etc.
        for plugin_name in self.loaded_optional_tools:
            for tool in _all_tools.values():
                if getattr(tool, 'plugin', None) == plugin_name:
                    tools.append(tool)

        # Add Gemini-only tools if using Gemini/Vertex model
        if self._is_gemini_model():
            for tool in _all_tools.values():
                if tool.name in GEMINI_ONLY_TOOL_NAMES:
                    tools.append(tool)

        return tools

    def _get_active_external_tools(self) -> dict:
        """Get dispatch dict for currently active external tools.

        Returns tool implementations for auto-dispatch. Includes:
        - Base EXTERNAL_TOOLS (always available)
        - Agent-mode tools when in /agent mode
        - Optional tools when manually loaded

        MCP tools are filtered by active_mcp_servers set - only tools from
        active servers are included.
        """
        # Ensure MCP tools are loaded (waits for background load on first call)
        _ensure_mcp_loaded()

        tools = dict(EXTERNAL_TOOLS)  # Base dispatch (always available)

        # Filter out MCP tools from inactive servers
        def is_active_tool(name):
            tool = _all_tools.get(name)
            server = getattr(tool, 'server_name', None) if tool else None
            return server is None or server in self.active_mcp_servers

        tools = {name: impl for name, impl in tools.items() if is_active_tool(name)}

        # Add agent-mode tools if in agent mode
        if self.mode == "agent":
            tools.update(AGENT_EXTERNAL_TOOLS)

        # Add optional tools if loaded
        for plugin_name in self.loaded_optional_tools:
            for name, impl in OPTIONAL_EXTERNAL_TOOLS.items():
                tool = _all_tools.get(name)
                if tool and getattr(tool, 'plugin', None) == plugin_name:
                    tools[name] = impl

        return tools

    def _estimate_tool_schema_tokens(self) -> int:
        """
        Estimate token count for all tool schemas.

        Uses char-based estimation (4 chars = 1 token) which is fast and
        consistent. The actual token count varies by model tokenizer, but
        this estimate is sufficient for context window tracking.

        Returns:
            Estimated token count for all tool schemas
        """
        try:
            # Build JSON representation of tool schemas (as sent to API)
            active_tools = self._get_active_tools()
            tool_schemas = []
            for tool in active_tools:
                # Get parameters - handle both input_schema (llm.Tool) and schema (other tools)
                if hasattr(tool, 'input_schema'):
                    params = tool.input_schema
                elif hasattr(tool, 'schema') and isinstance(tool.schema, dict):
                    params = tool.schema.get('parameters', {})
                else:
                    params = {}
                schema = {
                    'name': tool.name,
                    'description': tool.description or '',
                    'parameters': params
                }
                tool_schemas.append(schema)

            tools_json = json.dumps(tool_schemas, indent=2)

            # Estimate tokens using char-based method (4 chars = 1 token)
            tokens = len(tools_json) // 4
            self._debug(f"Estimated tool schemas: {tokens} tokens ({len(active_tools)} tools, {len(tools_json)} chars)")
            return tokens

        except Exception as e:
            # Fallback
            active_tools = self._get_active_tools()
            fallback = len(active_tools) * 200
            self._debug(f"Tool schema measurement exception: {e}, using estimate: {fallback}")
            return fallback

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
        self.console.print(f"[yellow]⚠ {source_desc.capitalize()} truncated ({original_size:,} chars)[/]")

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
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt with KB content appended.

        The base system prompt is rendered by Jinja2 at init/mode change.
        This method just appends KB content for the current request.
        """
        prompt = self.system_prompt

        # Append KB content if any loaded
        kb_content = self._get_loaded_kb_content()
        if kb_content:
            prompt = f"{prompt}\n# Knowledge Base\n\n{kb_content}"

        return prompt

    def _get_debug_info(self) -> dict:
        """Return debug information for the web companion."""
        # Build current system prompt (what would be sent on next request)
        current_system_prompt = self._build_system_prompt()

        # Build full conversation with ALL context (unstripped)
        messages = []
        for r in self.conversation.responses:
            # Skip responses that are still streaming to avoid blocking
            if not getattr(r, '_done', False):
                continue
            if hasattr(r, 'prompt') and r.prompt:
                # Include FULL prompt (not stripped) - use .prompt property
                full_prompt = r.prompt.prompt or ""
                # Also capture the system prompt used for this turn
                turn_system = r.prompt.system or ""

                # Build attachments info
                attachments_info = []
                if r.prompt.attachments:
                    for att in r.prompt.attachments:
                        att_info = {"type": att.type or "unknown"}
                        if att.path:
                            att_info["path"] = att.path
                        elif att.url:
                            att_info["url"] = att.url
                        else:
                            att_info["inline"] = True
                        # Include content size if available
                        if att.content:
                            att_info["size"] = len(att.content)
                        attachments_info.append(att_info)

                messages.append({
                    "role": "user",
                    "content": full_prompt,
                    "system": turn_system,  # System prompt for this turn
                    "has_context": "<terminal_context>" in full_prompt,
                    "attachments": attachments_info,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens
                })

                # Include tool results if present (tool outputs sent as input)
                if r.prompt.tool_results:
                    for tr in r.prompt.tool_results:
                        messages.append({
                            "role": "tool_result",
                            "tool_name": tr.name,
                            "content": tr.output or "",
                            "tool_call_id": tr.tool_call_id
                        })

            # Assistant response
            assistant_text = r.text()
            if assistant_text:
                assistant_msg = {"role": "assistant", "content": assistant_text}
                # Include thinking traces if available
                if hasattr(r, 'response_json') and r.response_json:
                    thinking = r.response_json.get("thinking_traces", [])
                    if thinking:
                        assistant_msg["thinking_traces"] = thinking
                messages.append(assistant_msg)

            # Tool calls made by assistant
            try:
                tool_calls = list(r.tool_calls())
                for tc in tool_calls:
                    messages.append({
                        "role": "tool_call",
                        "tool_name": tc.name,
                        "arguments": tc.arguments,
                        "tool_call_id": tc.tool_call_id
                    })
            except Exception:
                pass  # No tool calls or error accessing them

        # Build tool definitions (what's sent to the model)
        # Uses _get_active_tools() to reflect current mode and loaded optional tools
        tools = []
        for tool in self._get_active_tools():
            tool_info = {
                "name": tool.name,
                "description": tool.description or "",
            }
            # Get input schema - different attribute names depending on tool type
            if hasattr(tool, 'input_schema'):
                tool_info["parameters"] = tool.input_schema
            elif hasattr(tool, 'schema') and isinstance(tool.schema, dict):
                tool_info["parameters"] = tool.schema.get('parameters', {})
            else:
                tool_info["parameters"] = {}
            # Include plugin source if available
            if hasattr(tool, 'plugin') and tool.plugin:
                tool_info["plugin"] = tool.plugin
            tools.append(tool_info)

        # Get model options from most recent response (or empty if no responses yet)
        options = {}
        if self.conversation.responses:
            last_response = self.conversation.responses[-1]
            if hasattr(last_response, 'prompt') and last_response.prompt:
                try:
                    options = {
                        k: v for k, v in dict(last_response.prompt.options).items()
                        if v is not None
                    }
                except Exception:
                    pass

        # Get MCP server status
        mcp_servers = {}
        try:
            all_servers = self._get_all_mcp_servers()
            mcp_servers = {
                "active": list(self.active_mcp_servers),
                "all": {name: {"optional": is_opt, "loaded": name in self.active_mcp_servers}
                        for name, is_opt in all_servers.items()}
            }
        except Exception:
            pass

        return {
            "system_prompt": current_system_prompt,
            "tools": tools,
            "messages": messages,
            "model": self.model_name or "unknown",
            "mode": self.mode,
            "options": options,
            "max_context": self.max_context_size,
            "current_tokens": self.estimate_tokens(),
            "loaded_kbs": list(self.loaded_kbs.keys()) if self.loaded_kbs else [],
            "active_rag": self.active_rag_collection,
            "mcp_servers": mcp_servers
        }

    # =========================================================================
    # Knowledge Base System
    # =========================================================================

    def _get_kb_dir(self) -> Path:
        """Get or create KB directory in llm's config directory."""
        kb_dir = llm.user_dir() / "kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        return kb_dir

    def _get_config_file(self) -> Path:
        """Get config file path in llm's config directory."""
        return llm.user_dir() / "assistant-config.yaml"

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

    def _load_auto_kbs(self):
        """Load KBs listed in config.yaml auto_load."""
        config = self._load_config()
        auto_load = config.get("knowledge_base", {}).get("auto_load", [])
        for name in auto_load:
            self._load_kb(name, silent=True)

    def _load_kb(self, name: str, silent: bool = False) -> bool:
        """Load a KB file by name."""
        kb_dir = self._get_kb_dir()

        # Try with .md extension first
        kb_path = kb_dir / f"{name}.md"
        if not kb_path.exists():
            # Try without extension
            kb_path = kb_dir / name
            if not kb_path.exists():
                if not silent:
                    self.console.print(f"[red]KB not found: {name}[/]")
                    self.console.print(f"[dim]Looking in: {kb_dir}[/]")
                return False

        try:
            content = kb_path.read_text()
            self.loaded_kbs[name] = content
            if not silent:
                self.console.print(f"[green]✓[/] Loaded KB: {name} ({len(content)} chars)")
            return True
        except Exception as e:
            if not silent:
                self.console.print(f"[red]Failed to load {name}: {e}[/]")
            return False

    def _unload_kb(self, name: str) -> bool:
        """Unload a KB from session."""
        if name in self.loaded_kbs:
            del self.loaded_kbs[name]
            self.console.print(f"[green]✓[/] Unloaded KB: {name}")
            return True
        self.console.print(f"[yellow]KB not loaded: {name}[/]")
        return False

    def _get_loaded_kb_content(self) -> str:
        """Get combined content of all loaded KBs."""
        if not self.loaded_kbs:
            return ""
        parts = []
        for name, content in self.loaded_kbs.items():
            parts.append(f"## {name}\n\n{content}")
        return "\n\n---\n\n".join(parts)

    def _handle_kb_command(self, args: str) -> bool:
        """Handle /kb commands. Returns True to continue REPL."""
        parts = args.strip().split(maxsplit=1)

        if not parts or parts[0] == "":
            # /kb - list KBs
            self._list_kbs()
        elif parts[0] == "load" and len(parts) > 1:
            # /kb load <name> or /kb load name1,name2,name3
            names = [n.strip() for n in parts[1].split(",") if n.strip()]
            for name in names:
                self._load_kb(name)
        elif parts[0] == "unload" and len(parts) > 1:
            # /kb unload <name> or /kb unload name1,name2,name3
            names = [n.strip() for n in parts[1].split(",") if n.strip()]
            for name in names:
                self._unload_kb(name)
        elif parts[0] == "reload":
            # /kb reload - reload all loaded KBs
            names = list(self.loaded_kbs.keys())
            if names:
                for name in names:
                    self._load_kb(name)
            else:
                self.console.print("[yellow]No KBs loaded to reload[/]")
        else:
            self.console.print("[yellow]Usage: /kb [load|unload|reload] [name][/]")

        return True

    def _list_kbs(self):
        """List available and loaded KBs."""
        kb_dir = self._get_kb_dir()
        available = sorted([f.stem for f in kb_dir.glob("*.md")])

        self.console.print("\n[bold]Knowledge Bases[/]")

        if self.loaded_kbs:
            self.console.print("\n[green]Loaded:[/]")
            for name in sorted(self.loaded_kbs.keys()):
                chars = len(self.loaded_kbs[name])
                self.console.print(f"  • {name} ({chars} chars)")

        unloaded = [n for n in available if n not in self.loaded_kbs]
        if unloaded:
            self.console.print("\n[dim]Available:[/]")
            for name in unloaded:
                self.console.print(f"  • {name}")

        if not available and not self.loaded_kbs:
            self.console.print(f"\n[dim]No KBs found in {kb_dir}[/]")
            self.console.print("[dim]Create markdown files to use as knowledge bases.[/]")

    # =========================================================================
    # RAG Integration (llm-tools-rag)
    # =========================================================================

    def _rag_available(self) -> bool:
        """Check if llm-tools-rag is installed."""
        try:
            import llm_tools_rag
            return True
        except ImportError:
            return False

    def _handle_rag_command(self, args: str) -> bool:
        """Handle /rag commands. Returns True to continue REPL."""
        if not self._rag_available():
            self.console.print("[red]RAG not available. Install llm-tools-rag.[/]")
            self.console.print("[dim]Run install-llm-tools.sh or: llm install git+https://github.com/c0ffee0wl/llm-tools-rag[/]")
            return True

        parts = args.strip().split(maxsplit=1)

        if not parts or parts[0] == "":
            # /rag - list collections and show active
            self._rag_list_collections()
        elif parts[0] == "off":
            self.active_rag_collection = None
            self.pending_rag_context = None
            self.console.print("[green]✓[/] RAG deactivated")
        elif parts[0] == "status":
            self._rag_show_status()
        elif parts[0] == "search":
            # /rag search <collection> <query>
            search_args = parts[1] if len(parts) > 1 else ""
            search_parts = search_args.split(maxsplit=1)
            if len(search_parts) == 2:
                self._rag_oneshot_search(search_parts[0], search_parts[1])
            else:
                self.console.print("[red]Usage: /rag search <collection> <query>[/]")
        elif parts[0] == "top-k":
            # /rag top-k <n>
            try:
                self.rag_top_k = int(parts[1]) if len(parts) > 1 else 5
                self.console.print(f"[green]✓[/] RAG top-k set to {self.rag_top_k}")
            except ValueError:
                self.console.print("[red]Invalid top-k value[/]")
        elif parts[0] == "mode":
            # /rag mode <hybrid|vector|keyword>
            mode = parts[1].strip() if len(parts) > 1 else ""
            if mode in ("hybrid", "vector", "keyword"):
                self.rag_search_mode = mode
                self.console.print(f"[green]✓[/] RAG mode set to {mode}")
            else:
                self.console.print("[red]Invalid mode. Use: hybrid, vector, keyword[/]")
        elif parts[0] == "add":
            # /rag add <collection> <path>
            add_args = parts[1] if len(parts) > 1 else ""
            add_parts = add_args.split(maxsplit=1)
            if len(add_parts) == 2:
                self._rag_add_documents(add_parts[0], add_parts[1])
            else:
                self.console.print("[red]Usage: /rag add <collection> <path|git:url|glob>[/]")
        elif parts[0] == "rebuild":
            # /rag rebuild <collection>
            collection = parts[1].strip() if len(parts) > 1 else ""
            if collection:
                self._rag_rebuild_collection(collection)
            else:
                self.console.print("[red]Usage: /rag rebuild <collection>[/]")
        elif parts[0] == "delete":
            # /rag delete <collection>
            collection = parts[1].strip() if len(parts) > 1 else ""
            if collection:
                self._rag_delete_collection(collection)
            else:
                self.console.print("[red]Usage: /rag delete <collection>[/]")
        else:
            # Assume it's a collection name for activation: /rag <collection>
            collection = parts[0]
            self._rag_activate_collection(collection)

        return True

    def _rag_list_collections(self):
        """List available RAG collections."""
        from llm_tools_rag import get_collection_list

        collections = get_collection_list()

        self.console.print("\n[bold]RAG Collections[/]")

        if not collections:
            self.console.print("\n[dim]No RAG collections found[/]")
            self.console.print("[dim]Create with: /rag add <name> <path>[/]")
            return

        for coll in collections:
            name = coll['name']
            chunks = coll.get('chunks', '?')
            docs = coll.get('documents', '?')
            is_active = name == self.active_rag_collection
            active_marker = " [bold green](ACTIVE)[/]" if is_active else ""
            self.console.print(f"  • {name}: {chunks} chunks, {docs} docs{active_marker}")

        if self.active_rag_collection:
            self.console.print(f"\n[green]Active:[/] {self.active_rag_collection}")
            self.console.print(f"[dim]Top-k: {self.rag_top_k}, Mode: {self.rag_search_mode}[/]")
        else:
            self.console.print("\n[dim]RAG not active. Activate with: /rag <collection>[/]")

    def _rag_show_status(self):
        """Show current RAG status."""
        if self.active_rag_collection:
            try:
                from llm_tools_rag import get_collection_stats
                stats = get_collection_stats(self.active_rag_collection)
                self.console.print(f"[green]Active collection:[/] {self.active_rag_collection}")
                self.console.print(f"[dim]Chunks:[/] {stats['total_chunks']}")
                self.console.print(f"[dim]Documents:[/] {stats['unique_documents']}")
            except Exception:
                self.console.print(f"[green]Active collection:[/] {self.active_rag_collection}")
            self.console.print(f"[dim]Top-k:[/] {self.rag_top_k}")
            self.console.print(f"[dim]Mode:[/] {self.rag_search_mode}")
        else:
            self.console.print("[yellow]RAG not active[/]")
            self.console.print("[dim]Activate with: /rag <collection>[/]")

    def _rag_activate_collection(self, name: str):
        """Activate a RAG collection for persistent search."""
        from llm_tools_rag import collection_exists

        if not collection_exists(name):
            self.console.print(f"[red]Collection '{name}' not found[/]")
            self.console.print(f"[dim]Create with: /rag add {name} <documents>[/]")
            return

        self.active_rag_collection = name
        self.console.print(f"[green]✓[/] RAG activated: {name}")
        self.console.print("[dim]Retrieved context will be injected into every prompt[/]")

    def _rag_oneshot_search(self, collection: str, query: str):
        """One-shot RAG search without activating persistent mode."""
        from llm_tools_rag import collection_exists, search_collection

        if not collection_exists(collection):
            self.console.print(f"[red]Collection '{collection}' not found[/]")
            return

        with Spinner(f"Searching {collection}...", self.console):
            results = search_collection(collection, query, self.rag_top_k, self.rag_search_mode)

        if not results:
            self.console.print("[yellow]No results found[/]")
            return

        # Store for next prompt injection (one-shot mode)
        self.pending_rag_context = self._format_rag_results(results)
        self.console.print(f"[green]✓[/] Found {len(results)} results. Context will be injected into next prompt.")

        # Show preview
        for i, chunk in enumerate(results[:3], 1):
            source = chunk.get('metadata', {}).get('source', 'unknown')
            preview = chunk.get('content', '')[:100].replace('\n', ' ') + "..."
            self.console.print(f"[dim]{i}. {source}:[/] {preview}")

    def _rag_add_documents(self, collection: str, path: str):
        """Add documents to a RAG collection (creates if needed)."""
        from llm_tools_rag import add_to_collection, collection_exists

        is_new = not collection_exists(collection)
        action = "Creating" if is_new else "Adding to"

        self.console.print(f"[cyan]{action} collection '{collection}'...[/]")

        try:
            with Spinner(f"Processing {path}...", self.console):
                result = add_to_collection(collection, path)

            if result["status"] == "success":
                self.console.print(f"[green]✓[/] Added {result.get('chunks', '?')} chunks")
                # Auto-activate the collection
                self.active_rag_collection = collection
                self.console.print(f"[dim]Collection '{collection}' now active[/]")
            elif result["status"] == "skipped":
                self.console.print(f"[yellow]⊘[/] Skipped: {result.get('reason', 'already indexed')}")
            else:
                self.console.print(f"[red]✗[/] Error: {result.get('error', 'unknown')}")

        except Exception as e:
            self.console.print(f"[red]Error: {e}[/]")

    def _rag_rebuild_collection(self, collection: str):
        """Rebuild a RAG collection's index."""
        from llm_tools_rag import collection_exists, rebuild_collection_index

        if not collection_exists(collection):
            self.console.print(f"[red]Collection '{collection}' not found[/]")
            return

        try:
            with Spinner(f"Rebuilding {collection}...", self.console):
                rebuild_collection_index(collection)
            self.console.print(f"[green]✓[/] Rebuilt index for '{collection}'")
        except Exception as e:
            self.console.print(f"[red]Error rebuilding: {e}[/]")

    def _rag_delete_collection(self, collection: str):
        """Delete a RAG collection."""
        from llm_tools_rag import collection_exists, remove_collection

        if not collection_exists(collection):
            self.console.print(f"[red]Collection '{collection}' not found[/]")
            return

        # Confirm deletion
        self.console.print(f"[yellow]Delete collection '{collection}'? (y/N)[/]")
        confirm = input().strip().lower()
        if confirm != 'y':
            self.console.print("[dim]Cancelled[/]")
            return

        try:
            remove_collection(collection)
            self.console.print(f"[green]✓[/] Deleted collection '{collection}'")

            # Deactivate if was active
            if self.active_rag_collection == collection:
                self.active_rag_collection = None
                self.console.print("[dim]RAG deactivated[/]")

        except Exception as e:
            self.console.print(f"[red]Error deleting: {e}[/]")

    def _retrieve_rag_context(self, query: str) -> str:
        """Retrieve and format RAG context for query."""
        if not self.active_rag_collection:
            return ""

        try:
            from llm_tools_rag import search_collection
            results = search_collection(
                self.active_rag_collection,
                query,
                top_k=self.rag_top_k,
                mode=self.rag_search_mode
            )

            if not results:
                return ""

            return self._format_rag_results(results)
        except Exception as e:
            self._debug(f"RAG retrieval error: {e}")
            return ""

    def _format_rag_results(self, results: list) -> str:
        """Format retrieved chunks for context injection."""
        if not results:
            return ""

        parts = ["<retrieved_documents>"]
        for i, r in enumerate(results, 1):
            source = r.get('metadata', {}).get('source', 'unknown')
            content = r.get('content', '')
            parts.append(f"\n[{i}. {source}]\n{content}")
        parts.append("\n</retrieved_documents>")

        return "\n".join(parts)

    def _normalize_uuid(self, uuid_value) -> Optional[str]:
        """Normalize UUID to Python string (ensures dbus.String -> str conversion)"""
        if uuid_value is None or uuid_value == '':
            return None
        # Explicitly convert to Python str (handles dbus.String)
        return str(uuid_value)

    def _reconnect_dbus(self) -> bool:
        """Attempt to reconnect to Terminator D-Bus with timeout.

        Uses SIGALRM for timeout protection. On timeout or error, ensures
        D-Bus state is cleaned up to prevent stale connections.
        """
        # Use SIGALRM for timeout (safe at startup, before asyncio event loop)
        def timeout_handler(signum, frame):
            raise TimeoutError("D-Bus connection timed out")

        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(10)  # 10 second timeout
        bus = None

        try:
            bus = dbus.SessionBus()

            # Discover actual Terminator service name (includes UUID suffix)
            # Check for multiple instances
            terminator_services = [
                name for name in bus.list_names()
                if name.startswith('net.tenshu.Terminator2') and not name.endswith('.Assistant')
            ]

            if len(terminator_services) > 1:
                # Multiple Terminator instances - try to pick the right one
                # Check if TERMINATOR_UUID environment variable can help identify our instance
                env_uuid = os.environ.get('TERMINATOR_UUID', '')
                matching_service = None
                for service in terminator_services:
                    if env_uuid and env_uuid in service:
                        matching_service = service
                        break
                if matching_service:
                    self._debug(f"Matched Terminator instance via TERMINATOR_UUID: {matching_service}")
                    service_name = matching_service
                else:
                    # Can't determine - use first and warn
                    self.console.print(f"[yellow]Multiple Terminator instances detected ({len(terminator_services)})[/]")
                    self.console.print(f"[yellow]Using first found: {terminator_services[0]}[/]")
                    service_name = terminator_services[0]
            else:
                service_name = terminator_services[0] if terminator_services else None

            if not service_name:
                service_name = 'net.tenshu.Terminator2'  # Fallback for older versions

            self._debug(f"Connected to Terminator D-Bus: {service_name}")
            self.dbus_service = bus.get_object(service_name, '/net/tenshu/Terminator2')
            return True
        except TimeoutError:
            self.console.print("[red]D-Bus connection timed out (10s)[/]")
            self.console.print("[yellow]Terminator may not be running or D-Bus is unresponsive[/]")
            # Clean up any partial state
            self.dbus_service = None
            return False
        except dbus.exceptions.DBusException as e:
            self.console.print(f"[red]D-Bus reconnection failed: {e}[/]")
            self.dbus_service = None
            return False
        except Exception as e:
            self.console.print(f"[red]D-Bus reconnection error ({type(e).__name__}): {e}[/]")
            self.dbus_service = None
            return False
        finally:
            signal.alarm(0)  # Cancel alarm
            signal.signal(signal.SIGALRM, old_handler)  # Restore handler

    def _check_dbus_connection(self) -> bool:
        """Verify D-Bus is still connected"""
        try:
            # Try a simple D-Bus operation
            self.dbus_service.get_terminals()
            return True
        except Exception:
            return False

    def _connect_to_terminator(self):
        """Connect to Terminator and plugin via D-Bus"""
        # Connect to Terminator's main D-Bus service for terminal management
        if not self._reconnect_dbus():
            self.console.print("[red]Error: Could not connect to Terminator D-Bus service[/]")
            self.console.print("Ensure Terminator is running with D-Bus enabled")
            sys.exit(1)

        # Connect to plugin's D-Bus service for terminal content/commands
        if not self._connect_to_plugin_dbus():
            self.console.print("[red]Error: Plugin D-Bus service not available[/]")
            self.console.print("Ensure TerminatorAssistant plugin is:")
            self.console.print("  1. Installed in ~/.config/terminator/plugins/")
            self.console.print("  2. Enabled in Terminator Preferences > Plugins")
            self.console.print("  3. Terminator has been restarted after enabling")
            sys.exit(1)

    def _connect_to_plugin_dbus(self) -> bool:
        """Connect to plugin's D-Bus service"""
        try:
            bus = dbus.SessionBus()
            self.plugin_dbus = bus.get_object(
                'net.tenshu.Terminator2.Assistant',
                '/net/tenshu/Terminator2/Assistant'
            )

            # Log plugin version for diagnostics (debug only)
            try:
                version = self.plugin_dbus.get_plugin_version()
                self._debug(f"Plugin version: {version}")
            except Exception:
                pass  # Ignore version check failures

            return True
        except dbus.exceptions.DBusException as e:
            # Provide specific guidance based on the error
            error_name = e.get_dbus_name() if hasattr(e, 'get_dbus_name') else str(e)
            if 'ServiceUnknown' in str(error_name) or 'ServiceUnknown' in str(e):
                self.console.print("[yellow]Plugin D-Bus service not registered[/]")
                self.console.print("[dim]The TerminatorAssistant plugin is not running.[/]")
            elif 'NoReply' in str(error_name):
                self.console.print("[yellow]Plugin D-Bus service not responding[/]")
            else:
                self.console.print(f"[yellow]Plugin D-Bus error: {e}[/]")
            return False
        except Exception as e:
            # Check if D-Bus session bus itself is unavailable
            if 'DBUS_SESSION_BUS_ADDRESS' not in os.environ:
                self.console.print("[yellow]D-Bus session bus not available[/]")
                self.console.print("[dim]Try: export $(dbus-launch)[/]")
            else:
                self.console.print(f"[yellow]Plugin D-Bus connection failed: {e}[/]")
            return False

    def _check_plugin_available(self) -> bool:
        """Verify plugin D-Bus service is available"""
        try:
            # Try a simple D-Bus call to check if service is alive
            self.plugin_dbus.get_focused_terminal_uuid()
            return True
        except Exception:
            return False

    def _reconnect_plugin(self) -> bool:
        """Attempt to reconnect to plugin D-Bus service"""
        return self._connect_to_plugin_dbus()

    def setup_terminals(self):
        """Auto-create Exec terminal with retry logic"""
        self.console.print("[cyan]Setting up terminals...[/]")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Check D-Bus connection
                if not self._check_dbus_connection():
                    if not self._reconnect_dbus():
                        raise Exception("D-Bus reconnection failed")

                # Use early_terminal_uuid captured at startup to avoid race condition
                # where user switches tabs before setup_terminals() is called
                self.chat_terminal_uuid = self.early_terminal_uuid

                # Check for existing Exec pane or offer to reuse single other pane
                try:
                    terminals = self.plugin_dbus.get_terminals_in_same_tab(self.chat_terminal_uuid)

                    # Filter out chat terminal (already normalized at assignment)
                    other_terminals = [t for t in terminals if str(t['uuid']) != self.chat_terminal_uuid]

                    # First: Look for existing Assistant Exec pane
                    # (Per-tab lock ensures only one assistant per tab, so any Exec pane is ours)
                    for t in other_terminals:
                        title = t.get('title', '')
                        if title.startswith('Assistant: Exec'):
                            self.exec_terminal_uuid = self._normalize_uuid(t['uuid'])
                            self.console.print("[green]✓[/] Terminals ready")
                            return  # Success - reusing existing exec terminal

                    # Second: If exactly one other pane, offer to use it
                    if len(other_terminals) == 1:
                        existing_pane = other_terminals[0]
                        pane_title = existing_pane.get('title', 'Untitled')
                        use_existing = Confirm.ask(f"Use '{pane_title}' as Exec pane?", default=True)

                        if use_existing:
                            self.exec_terminal_uuid = self._normalize_uuid(existing_pane['uuid'])
                            self.console.print("[green]✓[/] Terminals ready")
                            return  # Success - using existing terminal
                except dbus.exceptions.DBusException as e:
                    # D-Bus specific errors (method not found, connection issues, etc.)
                    error_msg = str(e)
                    if 'Unknown method' in error_msg or 'does not exist' in error_msg:
                        self.console.print("[red]ERROR: Plugin method 'get_terminals_in_same_tab' not found![/]")
                        self.console.print("[red]Please restart Terminator to load the updated plugin.[/red]")
                    else:
                        self.console.print(f"[red]D-Bus error enumerating terminals: {e}[/]")
                    self.console.print("[yellow]Creating new Exec terminal as fallback...[/]")
                except Exception as e:
                    # Other unexpected errors
                    self.console.print(f"[red]Unexpected error ({type(e).__name__}): {e}[/]")
                    self.console.print("[yellow]Creating new Exec terminal as fallback...[/]")

                # Split vertically to create Exec terminal (to the right)
                exec_uuid = self.dbus_service.vsplit(
                    self.chat_terminal_uuid,
                    dbus.Dictionary({
                        'title': 'Assistant: Exec'
                    }, signature='ss')
                )
                if str(exec_uuid).startswith('ERROR'):
                    raise Exception(f"Failed to split terminal: {exec_uuid}")
                self.exec_terminal_uuid = self._normalize_uuid(exec_uuid)

                self.console.print("[green]✓[/] Terminals ready")
                return  # Success

            except Exception as e:
                if attempt < max_retries - 1:
                    self.console.print(f"[yellow]Retry {attempt+1}/{max_retries}: {e}[/]")
                    time.sleep(1)
                else:
                    self.console.print(f"[red]Failed to setup terminals after {max_retries} attempts: {e}[/]")
                    sys.exit(1)

    def _verify_exec_terminal(self) -> bool:
        """Check if exec terminal still exists"""
        try:
            terminals = self.plugin_dbus.get_all_terminals_metadata()

            # Debug output for terminal verification
            self._debug(f"Looking for exec UUID: {repr(self.exec_terminal_uuid)} (type: {type(self.exec_terminal_uuid).__name__})")
            self._debug(f"Plugin returned {len(terminals)} terminals")
            for t in terminals:
                self._debug(f"  - {repr(t['uuid'])} (type: {type(t['uuid']).__name__}) | Title: {t.get('title', 'N/A')}")
                if t['uuid'] == self.exec_terminal_uuid:
                    self._debug("    ✓ EXACT MATCH!")

            # exec_terminal_uuid is already normalized at assignment
            return any(str(t['uuid']) == self.exec_terminal_uuid for t in terminals)
        except Exception as e:
            self._debug(f"Verification error: {e}")
            return False

    def _recreate_exec_terminal(self) -> bool:
        """Recreate exec terminal if closed"""
        try:
            self.console.print("[yellow]Recreating Exec terminal...[/]")

            # Create new exec terminal by splitting from chat terminal
            # Use chat_terminal_uuid (stable) instead of get_focused_terminal()
            exec_uuid = self.dbus_service.vsplit(
                self.chat_terminal_uuid,
                dbus.Dictionary({'title': 'Assistant: Exec'}, signature='ss')
            )
            if str(exec_uuid).startswith('ERROR'):
                raise Exception(f"Failed to split terminal: {exec_uuid}")

            self.exec_terminal_uuid = self._normalize_uuid(exec_uuid)

            # Clear plugin cache to avoid stale data from old terminal
            try:
                self.plugin_dbus.clear_cache()
                self._debug("Plugin cache cleared after exec terminal recreation")
            except Exception as e:
                self._debug(f"Could not clear plugin cache: {e}")

            # Wait for shell prompt to render (prevents false TUI detection)
            # New terminals have minimal scrollback which triggers TUI heuristic
            max_wait = 2.0
            poll_interval = 0.1
            start_time = time.time()
            while time.time() - start_time < max_wait:
                try:
                    content = self.plugin_dbus.capture_terminal_content(
                        self.exec_terminal_uuid, -1
                    )
                    if content and PromptDetector.detect_prompt_at_end(content):
                        self._debug(f"Shell prompt detected after {time.time() - start_time:.2f}s")
                        break
                except Exception:
                    pass
                time.sleep(poll_interval)
            else:
                self._debug("Shell prompt wait timed out (continuing anyway)")

            self.console.print(f"[green]✓[/] Exec terminal restored: {exec_uuid[:8]}...")
            return True

        except Exception as e:
            self.console.print(f"[red]Failed to recreate exec terminal: {e}[/]")
            return False

    def _capture_screenshot(self, terminal_uuid: str, unique_id: str = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Capture terminal screenshot and save to temp file.

        Args:
            terminal_uuid: UUID of terminal to capture
            unique_id: Optional unique identifier for filename (default: timestamp)

        Returns:
            Tuple of (temp_file_path, error_message). One will be None.
        """
        try:
            screenshot_data = self.plugin_dbus.capture_terminal_screenshot(terminal_uuid)

            if not screenshot_data or screenshot_data.startswith('ERROR'):
                error_msg = screenshot_data if screenshot_data else "No data returned"
                return None, error_msg

            image_bytes = base64.b64decode(screenshot_data)

            # Use mkstemp for atomic, secure temp file creation in dedicated directory
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.png',
                prefix='assistant_screenshot_',
                dir=str(self.screenshot_dir)
            )

            # Write image data
            with os.fdopen(temp_fd, 'wb') as f:
                f.write(image_bytes)

            self.screenshot_files.append(temp_path)
            return temp_path, None

        except Exception as e:
            return None, str(e)

    def wait_for_tui_render(self, terminal_uuid, max_wait=2.0, initial_content=None) -> bool:
        """
        Wait for TUI application to finish rendering by detecting content stability.

        First waits for content to CHANGE from initial state (TUI starting),
        then waits for stability (TUI finished rendering).

        Args:
            terminal_uuid: Terminal to monitor
            max_wait: Maximum wait time in seconds (default: 2.0)
            initial_content: Terminal content before command was sent (for change detection)

        Returns:
            True if content stabilized, False if timed out
        """
        start_time = time.time()
        poll_interval = 0.15
        previous_content = None
        stable_count = 0
        content_changed = initial_content is None  # Skip change detection if no initial

        while time.time() - start_time < max_wait:
            try:
                current_content = self.plugin_dbus.capture_terminal_content(terminal_uuid, -1)

                # First, wait for content to change from initial state
                if not content_changed:
                    if current_content != initial_content:
                        content_changed = True
                        self._debug(f"TUI content changed after {time.time() - start_time:.2f}s")
                        previous_content = current_content
                    time.sleep(poll_interval)
                    continue

                # Then check for stability
                if current_content == previous_content:
                    stable_count += 1
                    if stable_count >= 2:  # Stable for 2 consecutive polls
                        self._debug(f"TUI render stabilized after {time.time() - start_time:.2f}s")
                        return True
                else:
                    stable_count = 0
                    previous_content = current_content

                time.sleep(poll_interval)
            except Exception as e:
                self._debug(f"TUI render wait error: {e}")
                time.sleep(poll_interval)

        self._debug(f"TUI render wait timed out after {max_wait}s")
        return False  # Timeout - proceed anyway

    def prompt_based_capture(self, terminal_uuid, max_wait=60, initial_content=None) -> Tuple[bool, str, str]:
        """
        Capture terminal content using prompt detection instead of stability checks.

        Polls terminal and stops when content has changed from initial state AND
        a shell prompt is detected at the end. This prevents false positives from
        the old prompt that was visible before the command started.

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
        initial_delay = 0.3
        poll_interval = 0.5
        max_attempts = int(max_wait / poll_interval)

        # Initial delay for command to start
        time.sleep(initial_delay)

        content_changed = initial_content is None  # If no initial content, skip change detection
        content = ""  # Initialize to avoid NameError if loop doesn't execute

        # Use Rich Status for visual feedback during polling
        with Status("[cyan]Waiting for output (0.3s)[/]", console=self.console, spinner="dots", spinner_style="cyan") as status:
            for attempt in range(max_attempts):
                try:
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

                    # Visual feedback with dynamic status
                    elapsed = initial_delay + (attempt * poll_interval)
                    status_msg = "Waiting for output" if not content_changed else "Waiting for prompt"
                    status.update(f"[cyan]{status_msg} ({elapsed:.1f}s)[/]")

                    time.sleep(poll_interval)

                except dbus.exceptions.DBusException as e:
                    self.console.print(f"[red]Plugin D-Bus error during capture: {e}[/]")
                    return (False, "", "")
                except Exception as e:
                    self.console.print(f"[red]Prompt-based capture error ({type(e).__name__}): {e}[/]")
                    return (False, "", "")

        # Timeout - return last content
        return (False, content if content else "", "")

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
        INITIAL_LINES = 50      # Start with viewport-ish size
        MAX_LINES = 5000        # Hard limit on capture range
        MAX_RECENT_COMMANDS = 3 # Number of recent commands to capture

        try:
            _, cursor_row = self.plugin_dbus.get_cursor_position(terminal_uuid)
        except Exception:
            # Fallback to viewport capture
            return self.plugin_dbus.capture_terminal_content(terminal_uuid, -1)

        lines_to_capture = INITIAL_LINES

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

                    # Compute current block hashes (always, for next comparison)
                    current_hashes = set()
                    new_blocks = []
                    for block in blocks:
                        block_hash = hashlib.sha256(block.strip().encode()).hexdigest()
                        current_hashes.add(block_hash)
                        if block_hash not in prev_hashes:
                            new_blocks.append(block)

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
            self.console.print(f"[red]Error capturing context: {e}[/]")
            return "", []

    def estimate_tokens(self, with_source: bool = False):
        """Estimate current context window size in tokens.

        Returns the actual tokens that would be sent on the next API call:
        - Uses the last response's input_tokens + output_tokens (accurate from API)
        - Falls back to char-based estimation if API tokens unavailable

        Note: input_tokens from API is cumulative (includes full conversation history),
        so the last response's tokens represent the current context window size.

        Args:
            with_source: If True, returns tuple (tokens, source) where source is
                        "API" or "estimated". If False, returns just the token count.
        """
        source = "estimated"
        tokens = 0

        try:
            # System prompt is already rendered for current mode by Jinja2
            system_prompt_len = len(self.system_prompt)

            if not self.conversation.responses:
                # No responses yet - estimate system prompt + tools
                # System prompt chars / 4, plus measured tool overhead
                base_tokens = system_prompt_len // 4
                tool_tokens = self._tool_token_overhead  # Already measured in __init__
                tokens = base_tokens + tool_tokens
                source = "estimated"
            else:
                # Use the LAST response's tokens (represents current context window)
                last_response = self.conversation.responses[-1]

                if last_response.input_tokens is not None:
                    # API provided accurate token counts
                    source = "API"
                    # Use input_tokens only - output tokens are already included in next request's input
                    # When the next request is made, its input_tokens will include the previous output
                    # So we only need input_tokens to know how much context we're using
                    tokens = last_response.input_tokens
                else:
                    # Fallback: char-based estimation for current context
                    # Sum ALL responses - mirrors how build_messages() reconstructs history
                    source = "estimated"
                    total_chars = filtered_prompt_len  # System prompt

                    # Sum ALL responses (each API call sends full history)
                    for resp in self.conversation.responses:
                        # User prompt
                        if hasattr(resp, 'prompt') and resp.prompt and resp.prompt.prompt:
                            total_chars += len(resp.prompt.prompt)

                        # Assistant response - handle in-progress for last response
                        if resp is self.conversation.responses[-1] and not getattr(resp, '_done', False):
                            # In-progress: use accumulated chunks to avoid blocking
                            total_chars += len("".join(getattr(resp, '_chunks', [])))
                        else:
                            total_chars += len(resp.text())

                    # Add measured tool overhead
                    tokens = (total_chars // 4) + self._tool_token_overhead

        except Exception as e:
            self.console.print(f"[yellow]Warning: Token estimation failed: {e}[/]")
            # Ultimate fallback
            tokens = len(self.system_prompt) // 4 + len(self.conversation.responses) * 500
            source = "estimated"

        return (tokens, source) if with_source else tokens

    def check_and_squash_context(self):
        """Auto-squash when context reaches threshold (like tmuxai)"""
        current_tokens = self.estimate_tokens()

        if current_tokens >= self.max_context_size * self.context_squash_threshold:
            self.console.print("[yellow]Context approaching limit, auto-squashing...[/]")

            # Record pre-squash tokens
            pre_squash_tokens = current_tokens

            self.squash_context()

            # Validate squashing reduced tokens
            post_squash_tokens = self.estimate_tokens()
            tokens_saved = pre_squash_tokens - post_squash_tokens

            if tokens_saved > 0:
                self.console.print(
                    f"[green]✓[/] Context squashed: {pre_squash_tokens:,} → {post_squash_tokens:,} "
                    f"(-{tokens_saved:,} tokens, -{tokens_saved/pre_squash_tokens*100:.1f}%)"
                )
            else:
                # Squashing didn't help (or made it worse!)
                self.console.print(
                    f"[yellow]⚠ Warning: Squashing ineffective[/] "
                    f"(before: {pre_squash_tokens:,}, after: {post_squash_tokens:,})"
                )

                # Still over threshold? Warn user
                if post_squash_tokens >= self.max_context_size * 0.9:  # 90% threshold
                    self.console.print(
                        f"[red]⚠ Context still very large ({post_squash_tokens:,} tokens)[/]"
                    )
                    self.console.print(
                        "[yellow]Consider:[/]\n"
                        "  • Use /reset to clear conversation\n"
                        "  • Use a model with larger context window\n"
                        "  • Reduce terminal content in watch mode"
                    )

    def squash_context(self, keep: Optional[str] = None):
        """Compress earlier messages into summary (like Claude Code's /compact).

        Args:
            keep: Optional instruction for what to preserve (e.g., 'API patterns')
        """
        if len(self.conversation.responses) <= 5:  # Keep at least 5 recent exchanges
            self.console.print("[yellow]Too few messages to squash[/]")
            return

        try:
            # Get responses to squash (all but last 3 - we'll re-execute those)
            responses_to_squash = self.conversation.responses[:-3]

            # Build summary from old responses using public APIs
            summary_parts = []
            for i, response in enumerate(responses_to_squash, 1):
                # Extract prompt text using public API
                prompt_text = ""
                if hasattr(response, 'prompt') and response.prompt:
                    prompt_text = response.prompt.prompt or ""

                # Extract response text using public API
                response_text = response.text()

                if prompt_text:
                    summary_parts.append(f"{i}. User: {prompt_text[:200]}...")
                if response_text:
                    summary_parts.append(f"{i}. AI: {response_text[:200]}...")

            # Build keep instruction if provided
            keep_section = ""
            if keep:
                keep_section = f"\n\nIMPORTANT: Preserve full details about: {keep}"

            # Generate summary using a standalone prompt (not in conversation)
            summary_prompt = render('prompts/squash_prompt.j2',
                keep_section=keep_section,
                summary_parts=chr(10).join(summary_parts),
            )

            summary_response = self.model.prompt(summary_prompt)
            summary = summary_response.text()

            # Create new conversation and update system prompt
            # Build enhanced system prompt from ORIGINAL, not current
            # Store summary for next user message (keeps system prompt clean)
            self.pending_summary = summary
            self.system_prompt = self.original_system_prompt

            # Save old conversation ID before creating new one
            old_conversation_id = self.conversation.id

            # Create completely fresh conversation
            self.conversation = llm.Conversation(model=self.model)
            new_conversation_id = self.conversation.id

            # Record link between old and new conversation for --continue tracking
            if self.logging_enabled:
                self._record_squash_link(old_conversation_id, new_conversation_id)

            # Clear per-terminal content hashes (summary replaces full history)
            self.terminal_content_hashes.clear()
            self.toolresult_hash_updated.clear()
            self.previous_capture_block_hashes.clear()

            # Clear rewind undo buffer (new conversation = no undo)
            self.rewind_undo_buffer = None

            self.console.print(f"[green]✓[/] Context squashed")
            self.console.print(f"[cyan]New session: {new_conversation_id}[/]")
            self.console.print(f"[dim](Previous: {old_conversation_id})[/]")
            self.console.print(f"[cyan]Summary will be included with your next message[/]")

        except Exception as e:
            self.console.print(f"[red]Error squashing context: {e}[/]")

    def handle_rewind_command(self, args: str) -> bool:
        """Handle /rewind command with interactive picker and undo support."""
        args = args.strip() if args else ""
        responses = self.conversation.responses

        # Handle /rewind undo
        if args == 'undo':
            return self._handle_rewind_undo()

        if not responses:
            self.console.print("[yellow]No conversation history to rewind.[/yellow]")
            return True

        # Handle quick rewind: /rewind N or /rewind -N
        if args:
            try:
                n = int(args)
                return self._quick_rewind(n)
            except ValueError:
                self.console.print(f"[red]Invalid argument: {args}. Use 'undo', a turn number, or no argument for picker.[/red]")
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
            self.console.print("[yellow]Need at least 2 turns to rewind.[/yellow]")
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
            self.console.print("[dim]Rewind cancelled.[/dim]")
            return True

        if selection.lower() == 'q' or selection == '':
            self.console.print("[dim]Rewind cancelled.[/dim]")
            return True

        try:
            target_turn = int(selection)
        except ValueError:
            self.console.print(f"[red]Invalid selection: {selection}[/red]")
            return True

        # Validate range
        if target_turn < 1 or target_turn >= total_turns:
            self.console.print(f"[red]Turn must be between 1 and {max_valid}[/red]")
            return True

        # Calculate impact
        tokens_freed = sum(t['tokens'] for t in turns[target_turn:])
        turns_removed = total_turns - target_turn

        # Confirm
        self.console.print(f"\n[cyan]Rewinding to {target_turn} removes {turns_removed} turn(s), freeing ~{tokens_freed:,} tokens.[/cyan]")
        confirm = input("Proceed? [Y/n] ").strip().lower()
        if confirm and confirm != 'y':
            self.console.print("[dim]Rewind cancelled.[/dim]")
            return True

        self._perform_rewind(target_turn)
        self.console.print(f"[green]✓ Rewound to turn {target_turn}. Use /rewind undo to restore.[/green]")
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
            self.console.print(f"[red]Invalid turn: must be >= 1 (got {n} → {target_turn})[/red]")
            return True
        if target_turn >= total_turns:
            self.console.print(f"[yellow]Already at turn {total_turns}. Nothing to rewind.[/yellow]")
            return True

        # Calculate impact
        tokens_freed = sum(
            (r.input_tokens or 0) + (r.output_tokens or 0)
            for r in responses[target_turn:]
        )
        turns_removed = total_turns - target_turn

        # Confirm
        self.console.print(f"[cyan]Rewind to turn {target_turn}? Removes {turns_removed} turn(s), frees ~{tokens_freed:,} tokens.[/cyan]")
        confirm = input("Proceed? [Y/n] ").strip().lower()
        if confirm and confirm != 'y':
            self.console.print("[dim]Rewind cancelled.[/dim]")
            return True

        self._perform_rewind(target_turn)
        self.console.print(f"[green]✓ Rewound to turn {target_turn}. Use /rewind undo to restore.[/green]")
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
            self.console.print("[yellow]No rewind to undo.[/yellow]")
            return True

        # Restore
        restored_count = len(self.rewind_undo_buffer)
        self.conversation.responses.extend(self.rewind_undo_buffer)
        self.rewind_undo_buffer = None

        new_total = len(self.conversation.responses)
        self.console.print(f"[green]✓ Restored {restored_count} turn(s). Back to turn {new_total}.[/green]")

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

    def _strip_context(self, prompt_text):
        """Remove <terminal_context> and <conversation_summary> sections from prompt.

        Terminal context is ephemeral and captured fresh on each prompt.
        Stripping it for DB storage preserves privacy; stripping it for
        web companion shows clean user messages.

        Uses XML-style tags for robust parsing (less likely to appear in user content).
        """
        if not prompt_text:
            return prompt_text

        result = prompt_text

        # Remove terminal context section
        result = re.sub(
            r'<terminal_context>.*?</terminal_context>\s*',
            '',
            result,
            flags=re.DOTALL
        )

        # Remove conversation summary section
        result = re.sub(
            r'<conversation_summary>.*?</conversation_summary>\s*',
            '',
            result,
            flags=re.DOTALL
        )

        # Clean up multiple consecutive newlines
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()

    def _format_tool_result(self, name: str, content: str, cwd: str = None) -> str:
        """Format tool result with XML tags, timestamp, and optional cwd.

        Tool results use <tool_result> tags (distinct from <terminal> context tags)
        to provide structured immediate feedback in the tool calling loop.
        """
        from datetime import datetime
        timestamp = datetime.now().isoformat(timespec='seconds')
        cwd_attr = f' cwd="{cwd}"' if cwd else ''
        return f'<tool_result name="{name}"{cwd_attr} timestamp="{timestamp}">\n{content}\n</tool_result>'

    def _get_exec_terminal_cwd(self) -> str:
        """Get the current working directory of the exec terminal."""
        try:
            terminals = self.plugin_dbus.get_terminals_in_same_tab(self.chat_terminal_uuid)
            exec_term = next((t for t in terminals if str(t['uuid']) == self.exec_terminal_uuid), None)
            return exec_term['cwd'] if exec_term else "unknown"
        except Exception:
            return "unknown"

    def _log_response(self, response):
        """Log response to database with context stripping.

        Strips terminal context from prompts before saving to preserve privacy
        while maintaining conversation history for --continue functionality.
        """
        if not self.logging_enabled:
            return
        if not hasattr(response, 'log_to_db'):
            return

        db = sqlite_utils.Database(logs_db_path())
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
                loaded = load_conversation(cid)
                if loaded:
                    self.conversation = loaded
                    self.model = loaded.model
                    # Check for linked conversations (squash chain)
                    self._load_squash_chain_info(loaded.id)
                    self.console.print(f"[green]Continuing conversation {loaded.id}[/]")
                    self.console.print(f"  {len(loaded.responses)} previous exchanges loaded")
                    return
                else:
                    self.console.print("[yellow]No previous conversations found, starting fresh[/]")
            except click.ClickException as e:
                # load_conversation raises ClickException if specific ID not found
                self.console.print(f"[red]Could not load conversation: {e.message}[/]")
                sys.exit(1)
            except Exception as e:
                self.console.print(f"[yellow]Warning: Could not load conversation: {e}[/]")

        # Create new conversation
        self.conversation = llm.Conversation(model=self.model)
        if self.logging_enabled:
            self.console.print(f"Session: [cyan]{self.conversation.id}[/]")

    def _record_squash_link(self, old_id, new_id):
        """Record link between squashed conversations.

        Stores link in llm's config directory (squash-links.json)
        to allow tracking conversation history across squash boundaries.
        """
        from datetime import datetime
        links_path = llm.user_dir() / 'squash-links.json'

        links = {}
        if links_path.exists():
            try:
                links = json.loads(links_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass  # Start fresh if file is corrupted

        links[new_id] = {'previous': old_id, 'squashed_at': datetime.utcnow().isoformat()}
        links_path.write_text(json.dumps(links, indent=2))

    def _load_squash_chain_info(self, conversation_id):
        """Load info about squash chain for a conversation.

        Displays info if this conversation was created from a squash operation.
        """
        links_path = llm.user_dir() / 'squash-links.json'
        if not links_path.exists():
            return

        try:
            links = json.loads(links_path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        # Check if this conversation has a previous squash
        if conversation_id in links:
            prev_id = links[conversation_id].get('previous')
            if prev_id:
                self.console.print(f"  (Squashed from: {prev_id})")

    def process_fragments(self, prompt: str):
        """
        Process !fragment commands in a prompt.
        Uses llm.cli.process_fragments_in_chat for correct database access.

        Returns:
            (modified_prompt, fragments, attachments)
        """
        try:
            db = sqlite_utils.Database(logs_db_path())
            return process_fragments_in_chat(db, prompt)
        except Exception as ex:
            self.console.print(f"[red]Fragment error: {ex}[/]")
            return prompt, [], []

    def should_use_screenshot_capture(self, command: str) -> bool:
        """
        Determine if screenshot capture should be used instead of text capture.

        Uses hybrid detection approach:
        1. Command-based detection (known TUI commands)
        2. Terminal state detection (alternate screen buffer heuristic)

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

        # Second check: terminal state suggests TUI is active
        # This catches TUIs launched via scripts, aliases, or complex pipelines
        try:
            is_tui_active = self.plugin_dbus.is_likely_tui_active(self.exec_terminal_uuid)
            if is_tui_active:
                self._debug("TUI detected via terminal state (alternate screen heuristic)")
                self.console.print("[cyan]TUI detected via terminal state[/]")
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

    def _ensure_exec_terminal(self) -> bool:
        """Verify exec terminal exists, recreate if needed.

        Returns:
            True if exec terminal is ready, False if recreation failed.
        """
        if not self._verify_exec_terminal():
            self.console.print("[yellow]Exec terminal not found[/]")
            if not self._recreate_exec_terminal():
                return False
        return True

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
                self.console.print("[bold red]BLOCKED[/] - manual approval required")
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
                self.console.print("[green]✓[/] Command sent to Exec terminal")

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
                    self.console.print("[cyan]TUI application detected - using screenshot capture[/]")

                    # Adaptive wait for TUI to render (replaces fixed 1.5s delay)
                    # Pass initial_content so we wait for content to change first
                    self.wait_for_tui_render(self.exec_terminal_uuid, max_wait=2.0, initial_content=initial_content)

                    temp_path, error = self._capture_screenshot(self.exec_terminal_uuid)
                    if error:
                        escaped_error = error.replace('[', '[[').replace(']', ']]')
                        self.console.print(f"[red]Screenshot capture failed: {escaped_error}[/]")
                        return True, f"Screenshot capture failed: {error}"

                    self.console.print(f"[green]✓[/] TUI screenshot captured: {temp_path}")

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
                        self.console.print(f"[green]✓[/] Command completed (prompt detected {method_label})")
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
                        self.console.print("[yellow]⚠[/] Timeout or long-running command")

                        # Post-timeout TUI check: command may have launched a TUI we didn't expect
                        # (e.g., git log with pager, script that invokes vim, etc.)
                        try:
                            if self.plugin_dbus.is_likely_tui_active(self.exec_terminal_uuid):
                                self.console.print("[cyan]TUI detected after timeout - capturing screenshot[/]")
                                temp_path, error = self._capture_screenshot(self.exec_terminal_uuid)
                                if temp_path:
                                    self.console.print(f"[green]✓[/] TUI screenshot captured: {temp_path}")
                                    if self.auto_mode:
                                        self.auto_command_history.append(command)
                                    return True, (output, temp_path)
                        except Exception as e:
                            self._debug(f"Post-timeout TUI check failed: {e}")

                        if self.auto_mode:
                            self.auto_command_history.append(command)
                        return True, output
            else:
                self.console.print("[red]✗[/] Failed to send command")
                return False, ""
        except dbus.exceptions.DBusException as e:
            self.console.print(f"[red]D-Bus error executing command: {e}[/]")
            self.console.print("[yellow]Plugin may have disconnected. Try /reset[/]")
            return False, ""
        except Exception as e:
            self.console.print(f"[red]Error executing command ({type(e).__name__}): {e}[/]")
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
                self.console.print(f"[green]✓[/] Keypress '{keypress}' sent to Exec terminal")
                return True
            else:
                self.console.print("[red]✗[/] Failed to send keypress")
                return False
        except Exception as e:
            self.console.print(f"[red]Error sending keypress: {e}[/]")
            return False

    def _compute_context_hash(self, context: str, attachments: List[llm.Attachment]) -> str:
        """Compute SHA256 hash of context for change detection."""
        hasher = hashlib.sha256()
        normalized_context = ' '.join(context.split())  # Normalize whitespace
        hasher.update(normalized_context.encode('utf-8'))
        for attachment in attachments:
            if hasattr(attachment, 'path') and attachment.path:
                hasher.update(attachment.path.encode('utf-8'))
        return hasher.hexdigest()

    def _is_watch_response_dismissive(self, response_text: str) -> bool:
        """Determine if response indicates no action needed."""
        if not response_text:
            return True
        normalized = response_text.strip().lower().rstrip('.')
        dismissive_exact = {
            'ok', 'okay', 'k', 'no comment', 'no issues', 'nothing to report',
            'nothing new', 'all good', 'looks good', 'no action needed',
            'no changes', 'nothing notable', 'nothing unusual', 'all normal',
        }
        if normalized in dismissive_exact:
            return True
        # Short positive responses (1-3 words) are likely dismissive
        words = normalized.split()
        if len(words) <= 3:
            positive = {'ok', 'okay', 'good', 'fine', 'normal', 'clear', 'stable'}
            if any(word in positive for word in words):
                return True
        return False

    def _start_watch_mode_thread(self):
        """Start watch mode in a background thread with its own event loop"""
        def watch_thread_target():
            self.event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.event_loop)
            try:
                self.watch_task = self.event_loop.create_task(self.watch_loop())
                self.event_loop.run_until_complete(self.watch_task)
            except asyncio.CancelledError:
                pass  # Expected when watch mode is disabled
            except Exception as e:
                self.console.print(f"[red]Watch mode error: {e}[/]")
            finally:
                self.watch_task = None
                self.event_loop.close()

        self.watch_thread = threading.Thread(target=watch_thread_target, daemon=True)
        self.watch_thread.start()

    async def watch_loop(self):
        """
        Background monitoring of all terminals (like tmuxai watch mode).

        Implements intelligent change detection:
        1. Hash-based skip: Don't send unchanged context to AI
        2. History-aware prompt: Tell AI to focus on NEW content when changes detected
        """
        while self.watch_mode:
            try:
                # Thread-safe capture and prompt - hold lock during D-Bus calls and conversation
                # This prevents race conditions with main thread's D-Bus operations
                context = None
                tui_attachments = []
                response_text = None
                exec_status = ""
                should_skip = False

                with self.watch_lock:
                    # Capture all terminal content (including exec output for watch)
                    # Returns (context_text, tui_attachments) tuple for TUI screenshot support
                    # Enable per-terminal deduplication after first watch iteration
                    context, tui_attachments = self.capture_context(
                        include_exec_output=True,
                        dedupe_unchanged=self.previous_watch_iteration_count > 0
                    )

                    # Check exec terminal idle state using PromptDetector
                    try:
                        exec_content = self.plugin_dbus.capture_terminal_content(
                            self.exec_terminal_uuid, -1
                        )
                        if exec_content:
                            is_idle = PromptDetector.detect_prompt_at_end(exec_content)
                            exec_status = "[Exec: idle]" if is_idle else "[Exec: command running]"
                    except Exception:
                        exec_status = "[Exec: unknown]"

                    if not context.strip():
                        # No context to analyze
                        should_skip = True
                    else:
                        # CHANGE DETECTION: Compute hash and compare with previous
                        current_hash = self._compute_context_hash(context, tui_attachments)

                        if current_hash == self.previous_watch_context_hash:
                            # Context unchanged - skip AI call entirely
                            should_skip = True
                        else:
                            # Context changed - update hash and proceed
                            self.previous_watch_context_hash = current_hash
                            self.previous_watch_iteration_count += 1

                            # HISTORY-AWARE PROMPT: Tell AI to focus on new content
                            prompt = render('prompts/watch_prompt.j2',
                                iteration_count=self.previous_watch_iteration_count,
                                goal=self.watch_goal,
                                exec_status=exec_status,
                                context=context,
                            )

                            try:
                                # Include TUI screenshots if any were captured
                                # Always pass system prompt on every call (required for Gemini/Vertex
                                # which is stateless - systemInstruction must be sent on every request)
                                #
                                # IMPORTANT: stream=False minimizes lock hold time by getting the
                                # complete response in one call. Lock is necessary because the
                                # conversation object is not thread-safe and is shared with main thread.
                                response = self._prompt(
                                    prompt,
                                    system=self._build_system_prompt(),
                                    attachments=tui_attachments if tui_attachments else None,
                                    stream=False  # Reduce lock hold time
                                )
                                response_text = response.text()
                                # Log watch mode response to database
                                self._log_response(response)
                            except Exception as response_error:
                                # Don't update hash on error - will retry next iteration
                                self.previous_watch_context_hash = None
                                self.console.print(f"[yellow]Watch mode response error: {response_error}[/]")

                # Only show if AI has actionable feedback - outside lock
                if not should_skip and response_text and response_text.strip():
                    if not self._is_watch_response_dismissive(response_text):
                        self.console.print()
                        self.console.print(Panel(
                            Markdown(response_text),
                            title="[bold yellow]Watch Mode Alert[/]",
                            border_style="yellow"
                        ))
                        self.console.print()

            except Exception as e:
                self.console.print(f"[red]Watch mode error: {e}[/]")

            await asyncio.sleep(self.watch_interval)

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
                self.console.print("[green]✓[/] Conversation cleared")
            except Exception as e:
                self.console.print(f"[red]Error clearing conversation: {e}[/]")
            return True

        elif cmd == "/reset":
            # Clear conversation and reset terminal states (like tmuxai /reset)
            try:
                # Clear conversation
                self.conversation = llm.Conversation(model=self.model)

                # Reset system prompt to original
                self.system_prompt = self.original_system_prompt

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

                self.console.print("[green]✓[/] Conversation cleared and terminal states reset")
            except Exception as e:
                self.console.print(f"[red]Error resetting: {e}[/]")
            return True

        elif cmd == "/rewind":
            return self.handle_rewind_command(args)

        elif cmd == "/copy":
            if not CLIPBOARD_AVAILABLE:
                self.console.print("[red]Clipboard not available. Install pyperclip: llm install pyperclip[/]")
                return True

            raw_mode = "raw" in args.lower()
            copy_all = "all" in args.lower()

            # Extract number if present
            num_match = re.search(r'\d+', args)
            count = int(num_match.group()) if num_match else 1

            responses = self.conversation.responses
            if not responses:
                self.console.print("[yellow]No responses to copy[/]")
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
                self.console.print(f"[green]✓[/] Copied {what} to clipboard ({mode})")
            except Exception as e:
                self.console.print(f"[red]Clipboard error: {e}[/]")
            return True

        elif cmd == "/web":
            if "stop" in args.lower() or "off" in args.lower():
                self._stop_web_server()
                return True

            if not WEB_AVAILABLE:
                self.console.print("[red]Web companion not available. Install: llm install fastapi uvicorn[/]")
                return True

            if self._start_web_server():
                url = f"http://localhost:{self.web_port}"
                try:
                    # Suppress Firefox GFX warnings by setting MOZ_LOG
                    old_moz_log = os.environ.get("MOZ_LOG")
                    os.environ["MOZ_LOG"] = "GFX:0"
                    try:
                        webbrowser.open(url)
                    finally:
                        # Restore original value
                        if old_moz_log is None:
                            os.environ.pop("MOZ_LOG", None)
                        else:
                            os.environ["MOZ_LOG"] = old_moz_log
                    self.console.print(f"[green]✓[/] Web companion opened at {url}")
                except Exception as e:
                    self.console.print(f"[yellow]Web server running at {url} (browser open failed: {e})[/]")
            return True

        elif cmd == "/refresh":
            # Re-capture terminal content and show preview
            self.console.print("[cyan]Refreshing terminal context...[/]")

            # Clear plugin cache
            try:
                self.plugin_dbus.clear_cache()
            except Exception:
                pass

            # Clear per-terminal content hashes to ensure full fresh capture
            self.terminal_content_hashes.clear()
            self.toolresult_hash_updated.clear()
            self.previous_capture_block_hashes.clear()

            # Simple snapshot capture (includes exec terminal)
            # Returns (context_text, tui_attachments) tuple for TUI screenshot support
            # Use watch_lock to avoid racing with watch mode (thread-safe)
            # No deduplication - user explicitly wants to see current full state
            with self.watch_lock:
                context, tui_attachments = self.capture_context(include_exec_output=True)

            if context or tui_attachments:
                self.console.print(f"[green]✓[/] Captured {len(context)} characters of context")

                # Show TUI screenshots info
                if tui_attachments:
                    self.console.print(f"[green]✓[/] Captured {len(tui_attachments)} TUI screenshot(s)")

                # Show per-terminal breakdown
                terminals = re.findall(r'<terminal uuid="([^"]+)" title="([^"]+)"', context)
                if terminals:
                    self.console.print("\n[bold]Terminals captured:[/]")
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
                    self.console.print("\n[dim]First 300 chars of text content:[/]")
                    preview = text_only[:300].replace('\n', ' ')
                    self.console.print(f"[dim]{preview}...[/]")
            else:
                self.console.print("[yellow]No context captured[/]")

            return True

        elif cmd == "/model":
            if not args:
                # List available models
                self.console.print("[bold]Available models:[/]")
                for model in llm.get_models():
                    current = " [green](current)[/]" if model.model_id == self.model_name else ""
                    self.console.print(f"  - {model.model_id}{current}")
            elif args.startswith("-q") or args.startswith("--query"):
                # Query-based selection: /model -q haiku -q claude
                query_parts = args.split()[1:]  # Remove -q/--query prefix
                queries = [q for q in query_parts if not q.startswith("-")]
                if not queries:
                    self.console.print("[yellow]Usage: /model -q <query> [-q <query>...][/]")
                else:
                    resolved = resolve_model_query(queries)
                    if resolved:
                        try:
                            self.model = llm.get_model(resolved)
                            self.model_name = resolved
                            self.conversation.model = self.model
                            # Recalculate tool token overhead (tools change with model)
                            self._tool_token_overhead = self._estimate_tool_schema_tokens()
                            self.console.print(f"[green]✓[/] Switched to model: {resolved}")
                            # Notify web companion of model change
                            self._broadcast_to_web({
                                "type": "session_info",
                                "model": self.model_name,
                                "mode": self.mode
                            })
                        except Exception as e:
                            self.console.print(f"[red]Error switching model: {e}[/]")
                    else:
                        self.console.print(f"[yellow]No model matching queries: {queries}[/]")
            else:
                # Direct model name
                try:
                    self.model = llm.get_model(args)
                    self.model_name = args
                    # Update conversation model
                    self.conversation.model = self.model
                    # Recalculate tool token overhead (tools change with model)
                    self._tool_token_overhead = self._estimate_tool_schema_tokens()
                    self.console.print(f"[green]✓[/] Switched to model: {args}")
                    # Notify web companion of model change
                    self._broadcast_to_web({
                        "type": "session_info",
                        "model": self.model_name,
                        "mode": self.mode
                    })
                except Exception as e:
                    self.console.print(f"[red]Error switching model: {e}[/]")
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
            self.console.print(Panel(f"""Model: {self.model_name}
{system_status}
Mode: {mode_display}
{tools_info}
Context size: ~{tokens:,} tokens / {self.max_context_size:,} ({percentage}%) [{token_source}]
Exchanges: {len(self.conversation.responses)}
Watch mode: {"enabled" if self.watch_mode else "disabled"}{watch_goal_line}

Chat terminal: {self.chat_terminal_uuid}
Exec terminal: {self.exec_terminal_uuid}""", title="Session Info", border_style="cyan"))
            return True

        elif cmd == "/watch":
            if not args:
                # No args: show status with usage hint
                if self.watch_mode:
                    self.console.print(f"[green]Watch mode: enabled[/]")
                    self.console.print(f"Goal: {self.watch_goal}")
                    self.console.print(f"Interval: {self.watch_interval}s")
                else:
                    self.console.print("[yellow]Watch mode: disabled[/]")
                    self.console.print("[dim]Usage: /watch <goal> to enable[/]")
            elif args.lower() == "off":
                # Disable watch mode
                if self.watch_mode:
                    with self.watch_lock:
                        self.watch_mode = False
                        # Reset state for next enable
                        self.previous_watch_context_hash = None
                        self.previous_watch_iteration_count = 0
                        if self.watch_task and not self.watch_task.done():
                            # Cancel the task gracefully (interrupts asyncio.sleep)
                            try:
                                self.event_loop.call_soon_threadsafe(self.watch_task.cancel)
                            except RuntimeError:
                                pass  # Loop already closed
                    # Wait for watch thread to finish (prevents multiple threads)
                    if self.watch_thread and self.watch_thread.is_alive():
                        self.watch_thread.join(timeout=2.0)
                    self.console.print("[yellow]Watch mode disabled[/]")
                    self._broadcast_watch_status()
                else:
                    self.console.print("[yellow]Watch mode is already off[/]")
            elif args.lower() == "status":
                # Show watch mode status
                if self.watch_mode:
                    self.console.print(f"[green]Watch mode: enabled[/]")
                    self.console.print(f"Goal: {self.watch_goal}")
                    self.console.print(f"Interval: {self.watch_interval}s")
                else:
                    self.console.print("[yellow]Watch mode: disabled[/]")
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
                    self._start_watch_mode_thread()
                self.console.print(f"[green]✓[/] Watch mode enabled")
                self.console.print(f"Goal: {self.watch_goal}")
                self.console.print(f"Monitoring all terminals every {self.watch_interval}s...")
                self._broadcast_watch_status()
            return True

        elif cmd == "/squash":
            # Support "keep" instruction: /squash keep API patterns
            keep_instruction = args if args else None
            self.squash_context(keep=keep_instruction)
            return True

        elif cmd == "/kb":
            return self._handle_kb_command(args)

        elif cmd == "/auto":
            # Auto mode: LLM-judged autonomous command execution
            if not args:
                self.auto_mode = "normal"
                self.console.print("[bold green]Auto mode enabled[/] - SAFE commands auto-execute")
                self.console.print("[dim]/auto full for SAFE+CAUTION, /auto off to disable[/]")
            elif args.lower() == "full":
                self.auto_mode = "full"
                self.console.print("[bold green]Auto mode FULL[/] - SAFE+CAUTION commands auto-execute")
                self.console.print("[dim]/auto for SAFE only, /auto off to disable[/]")
            elif args.lower() == "off":
                self.auto_mode = False
                self.auto_command_history.clear()
                self.console.print("[bold yellow]Auto mode disabled[/]")
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
                self.console.print("[red]Usage: /auto, /auto full, /auto off, /auto status[/]")
            return True

        elif cmd == "/voice":
            # Voice auto-submit mode
            if not args or args.lower() == "auto":
                self.voice_auto_submit = True
                self.console.print("[bold green]Voice auto-submit enabled[/] - transcribed text sends automatically")
                self.console.print("[dim]/voice off to disable[/]")
            elif args.lower() == "off":
                self.voice_auto_submit = False
                self.console.print("[bold yellow]Voice auto-submit disabled[/]")
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
                self.console.print("[red]Usage: /voice auto, /voice off, /voice status[/]")
            return True

        elif cmd == "/speech":
            # Text-to-speech output mode (requires Vertex model)
            if not self._is_vertex_model():
                self.console.print("[red]TTS requires a Vertex model (vertex/*)[/]")
                self.console.print(f"[dim]Current model: {self.model_name}[/]")
                self.console.print("[dim]Switch with: /model vertex/gemini-2.5-flash[/]")
            elif not self.speech_output:
                self.console.print("[red]google-cloud-texttospeech not installed[/]")
                self.console.print("[dim]Re-run install-llm-tools.sh to install[/]")
            elif not args or args.lower() == "on":
                self.speech_output.enabled = True
                self.console.print("[bold green]Speech output enabled[/] - AI responses will be spoken")
                self.console.print(f"[dim]Voice: {self.speech_output.voice_name}[/]")
                self.console.print("[dim]/speech off to disable[/]")
            elif args.lower() == "off":
                self.speech_output.enabled = False
                self.speech_output.stop()  # Stop any playing audio
                self.console.print("[bold yellow]Speech output disabled[/]")
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
                self.console.print("[red]Usage: /speech on, /speech off, /speech status[/]")
            return True

        elif cmd == "/rag":
            return self._handle_rag_command(args)

        elif cmd == "/assistant":
            if self.mode == "assistant":
                self.console.print("[dim]Already in assistant mode[/]")
            else:
                self.mode = "assistant"
                # Re-render system prompt for new mode (Jinja2 includes mode-specific content)
                self.system_prompt = self._render_system_prompt()
                self.original_system_prompt = self.system_prompt
                self.console.print("[bold green]Switched to assistant mode[/] - conservative (10 tool iterations)")
                self.console.print("[dim]/agent for agentic mode (100 iterations)[/]")
                # Notify web companion of mode change
                self._broadcast_to_web({
                    "type": "session_info",
                    "model": self.model_name,
                    "mode": self.mode
                })
            return True

        elif cmd == "/agent":
            if self.mode == "agent":
                self.console.print("[dim]Already in agent mode[/]")
            else:
                self.mode = "agent"
                # Re-render system prompt for new mode (Jinja2 includes mode-specific content)
                self.system_prompt = self._render_system_prompt()
                self.original_system_prompt = self.system_prompt
                self.console.print("[bold green]Switched to agent mode[/] - agentic (100 tool iterations)")
                self.console.print("[dim]/assistant for conservative mode (10 iterations)[/]")
                # Notify web companion of mode change
                self._broadcast_to_web({
                    "type": "session_info",
                    "model": self.model_name,
                    "mode": self.mode
                })
            return True

        elif cmd == "/capture":
            # Parse: /capture [mode] [prompt...]
            parts = command.split(maxsplit=2)
            mode = "window"
            prompt = None

            if len(parts) >= 2:
                if parts[1] in ("window", "region", "full", "rdp", "annotate"):
                    mode = parts[1]
                    prompt = parts[2] if len(parts) > 2 else None
                else:
                    # No mode specified, rest is prompt
                    prompt = " ".join(parts[1:])

            # Execute capture
            try:
                from llm_tools_capture_screen import capture_screen

                # Show countdown for interactive modes
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
                        self.console.print(f"[green]✓[/] Screenshot queued (mode={mode})")
                        self.console.print("[dim]Attached to your next message[/]")
                else:
                    self.console.print("[yellow]No screenshot captured[/]")
            except ImportError:
                self.console.print("[red]capture_screen tool not installed[/]")
                self.console.print("[dim]Install: llm install /opt/llm-tools-capture-screen[/]")
            except Exception as e:
                self.console.print(f"[red]Capture failed: {e}[/]")
            return True

        elif cmd == "/imagemage":
            parts = command.split()
            if len(parts) == 1:
                # Load imagemage
                if 'imagemage' in self.loaded_optional_tools:
                    self.console.print("[yellow]imagemage already loaded[/]")
                else:
                    self.loaded_optional_tools.add('imagemage')
                    self.console.print("[green]✓[/] imagemage loaded (generate_image tool available)")
                    self.console.print("[dim]/imagemage off to unload[/]")
            elif parts[1] == "off":
                if 'imagemage' in self.loaded_optional_tools:
                    self.loaded_optional_tools.discard('imagemage')
                    self.console.print("[green]✓[/] imagemage unloaded")
                else:
                    self.console.print("[yellow]imagemage not loaded[/]")
            elif parts[1] == "status":
                if 'imagemage' in self.loaded_optional_tools:
                    self.console.print("[green]imagemage: loaded[/] (generate_image available)")
                else:
                    self.console.print("[dim]imagemage: not loaded[/]")
                    self.console.print("[dim]/imagemage to load[/]")
            else:
                self.console.print("[red]Usage: /imagemage, /imagemage off, /imagemage status[/]")
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
                self.console.print("[red]Usage: /mcp, /mcp load <server>, /mcp unload <server>[/]")
            return True

        elif cmd == "/report":
            return self._handle_report_command(args)

        elif cmd in ["/quit", "/exit"]:
            self._shutdown()  # Explicit cleanup before exit
            return False

        else:
            self.console.print(f"[red]Unknown command: {cmd}[/]")
            self.console.print("Type /help for available commands")
            return True

    # ========== MCP Server Management Methods ==========

    def _handle_mcp_load(self, server_name: str):
        """Load an MCP server (optional or previously unloaded default)."""
        all_servers = self._get_all_mcp_servers()
        if server_name not in all_servers:
            self.console.print(f"[red]Unknown server: {server_name}[/]")
            available = ', '.join(sorted(all_servers.keys()))
            self.console.print(f"[dim]Available: {available}[/]")
            return

        if server_name in self.active_mcp_servers:
            self.console.print(f"[yellow]{server_name} already loaded[/]")
            return

        self.active_mcp_servers.add(server_name)
        tool_count = self._count_tools_for_server(server_name)
        self.console.print(f"[green]✓[/] {server_name} loaded ({tool_count} tools)")

    def _handle_mcp_unload(self, server_name: str):
        """Unload any MCP server (default or optional)."""
        if server_name in self.active_mcp_servers:
            self.active_mcp_servers.discard(server_name)
            self.console.print(f"[green]✓[/] {server_name} unloaded")
        else:
            all_servers = self._get_all_mcp_servers()
            if server_name in all_servers:
                self.console.print(f"[yellow]{server_name} not loaded[/]")
            else:
                self.console.print(f"[red]Unknown server: {server_name}[/]")

    def _handle_mcp_status(self):
        """Show MCP server status (all servers, grouped by type)."""
        all_servers = self._get_all_mcp_servers()

        if not all_servers:
            self.console.print("[dim]No MCP servers configured[/]")
            return

        # Group by optional status
        default_servers = {s for s, opt in all_servers.items() if not opt}
        optional_servers = {s for s, opt in all_servers.items() if opt}

        self.console.print("[bold]MCP Servers:[/]")

        # Show default servers
        if default_servers:
            self.console.print("  [dim]Default:[/]")
            for server in sorted(default_servers):
                if server in self.active_mcp_servers:
                    tool_count = self._count_tools_for_server(server)
                    self.console.print(f"    [green]●[/] {server} ({tool_count} tools)")
                else:
                    self.console.print(f"    [dim]○[/] {server} [dim](unloaded)[/]")

        # Show optional servers
        if optional_servers:
            self.console.print("  [dim]Optional:[/]")
            for server in sorted(optional_servers):
                if server in self.active_mcp_servers:
                    tool_count = self._count_tools_for_server(server)
                    self.console.print(f"    [green]●[/] {server} ({tool_count} tools)")
                else:
                    self.console.print(f"    [dim]○[/] {server}")

        if not self.active_mcp_servers:
            self.console.print("[dim]Use /mcp load <server> to enable[/]")

    # ========== Pentest Finding Management Methods ==========

    def _handle_report_command(self, args: str) -> bool:
        """Route /report subcommands to appropriate handlers."""
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""

        if not subcmd:
            self.console.print("[yellow]Usage: /report <note> or /report <subcommand>[/]")
            self.console.print("[dim]Subcommands: init, list, edit, delete, export, severity, projects, open[/]")
            return True

        # Dispatch subcommands
        if subcmd == "init":
            return self._report_init(subargs)
        elif subcmd == "list":
            return self._report_list()
        elif subcmd == "edit":
            return self._report_edit(subargs)
        elif subcmd == "delete":
            return self._report_delete(subargs)
        elif subcmd == "export":
            return self._report_export(subargs)
        elif subcmd == "severity":
            return self._report_set_severity(subargs)
        elif subcmd == "projects":
            return self._report_projects()
        elif subcmd == "open":
            return self._report_open(subargs)
        else:
            # Not a subcommand - treat entire args as a quick note
            return self._report_add(args)

    def _report_init(self, args: str) -> bool:
        """Initialize a new pentest project with language selection."""
        parts = args.strip().split(maxsplit=1)

        if len(parts) < 2:
            self.console.print("[red]Usage: /report init <project-name> <language-code>[/]")
            self.console.print("[dim]Example: /report init acme-webapp-2025 en[/]")
            self.console.print("[dim]Language codes: en (English), de (German), es (Spanish), fr (French), ...[/]")
            return True

        project_name = parts[0]
        lang_code = parts[1].lower().strip()

        # Validate language code using iso639-lang library
        language_name = validate_language_code(lang_code)
        if not language_name:
            self.console.print(f"[red]Invalid language code: {lang_code}[/]")
            self.console.print("[dim]Use ISO 639-1 codes: en, de, es, fr, it, nl, pt, ru, ja, ko, zh, etc.[/]")
            return True

        # Sanitize project name (alphanumeric, hyphens, underscores)
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '-', project_name)
        project_dir = self.findings_base_dir / safe_name

        if project_dir.exists():
            # Project exists - switch to it
            self.findings_project = safe_name
            self.console.print(f"[yellow]Project already exists, switched to:[/] {safe_name}")
            return True

        # Create project directory and initial findings.md
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "evidence").mkdir(exist_ok=True)

        # Create initial findings.md with project YAML frontmatter including language
        from datetime import datetime
        findings_file = project_dir / "findings.md"
        initial_content = f"""---
project: {safe_name}
created: {datetime.now().strftime('%Y-%m-%d')}
assessor: Pentest Team
language: {lang_code}
language_name: {language_name}
---

# Penetration Test Findings: {safe_name}

| ID | Severity | Title |
|----|----------|-------|

"""
        findings_file.write_text(initial_content)

        self.findings_project = safe_name
        self.console.print(f"[green]✓[/] Created project: {project_dir} ({language_name})")
        self.console.print(f"[dim]Add findings with: /report \"<quick note>\"[/]")
        return True

    def _report_add(self, quick_note: str) -> bool:
        """Add a new finding with LLM-assisted analysis."""
        if not self.findings_project:
            self.console.print("[red]No project initialized. Use /report init <project> <lang>[/]")
            return True

        quick_note = quick_note.strip().strip('"\'')
        if not quick_note:
            self.console.print("[red]Usage: /report \"<quick note about vulnerability>\"[/]")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            self.console.print(f"[red]Project file not found: {findings_file}[/]")
            return True

        # Parse existing findings
        project_meta, findings = self._parse_findings_file(findings_file)

        # Safety check: if parsing returned empty project_meta, file may be corrupted
        if not project_meta:
            self.console.print(f"[red]Error: Could not parse project file (missing or invalid YAML frontmatter)[/]")
            self.console.print(f"[dim]Check file manually: {findings_file}[/]")
            return True

        # Get language from project metadata (default to English for legacy projects)
        language_name = project_meta.get('language_name', 'English')

        # Generate next finding ID
        finding_id = self._get_next_finding_id(findings)

        # Capture evidence (terminal context)
        evidence = self._capture_report_evidence(finding_id, project_dir)

        # Get terminal context for LLM analysis
        context = None
        try:
            terminals = self._get_all_terminals_with_content()
            if terminals:
                context_parts = []
                for t in terminals:
                    if t.get('uuid') != self.chat_terminal_uuid:
                        context_parts.append(f"=== Terminal {t.get('title', 'unknown')} ===\n{t.get('content', '')[:2000]}")
                context = "\n\n".join(context_parts)[:5000]
        except Exception:
            pass

        # LLM analysis with schema enforcement
        self.console.print(f"[dim]Analyzing finding ({language_name})...[/]")
        try:
            analysis = self._analyze_finding(quick_note, context, language=language_name)
        except Exception as e:
            self.console.print(f"[red]LLM analysis failed: {e}[/]")
            # Fallback to manual entry
            analysis = {
                "suggested_title": quick_note[:60],
                "severity": 5,
                "severity_rationale": "Manual entry - please review",
                "description": quick_note,
                "remediation": "To be determined"
            }

        # Build finding metadata
        from datetime import datetime
        finding_meta = {
            "id": finding_id,
            "title": analysis.get("suggested_title", quick_note[:60]),
            "severity": analysis.get("severity", 5),
            "severity_rationale": analysis.get("severity_rationale", ""),
            "created": datetime.now().isoformat(),
            "evidence": evidence
        }

        # Build finding markdown body (Description -> Remediation -> Evidence)
        finding_body = f"""
### Description

{analysis.get('description', quick_note)}

### Remediation

{analysis.get('remediation', 'To be determined')}

### Evidence

"""
        if evidence:
            for ev in evidence:
                ev_path = ev.get('path', '')
                finding_body += f"- {ev.get('type', 'file').title()}: [{Path(ev_path).name}]({ev_path})\n"
        else:
            finding_body += "(none captured)\n"

        # Append to findings list
        findings.append((finding_meta, finding_body))

        # Write updated file
        self._write_findings_file(findings_file, project_meta, findings)

        # Display confirmation with severity color
        severity = finding_meta['severity']
        sev_color = "red" if severity >= 7 else "yellow" if severity >= 4 else "green"
        sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"

        self.console.print(f"[green]✓[/] Added [{sev_color}]{finding_id}[/] ({severity} {sev_label}): {finding_meta['title']}")
        return True

    def _analyze_finding(self, quick_note: str, context: Optional[str] = None,
                         language: str = "English") -> dict:
        """Use LLM to analyze finding with template and conversation context.

        Creates an isolated LLM call (not added to main conversation) that has
        access to terminal context and conversation history for better assessment.
        """
        model = llm.get_model(self.model_name)

        # Render the report analysis prompt with language using Jinja2 template
        system_prompt = render('prompts/report_analysis.j2', language=language)

        # Build prompt with quick note
        prompt = f"Quick note from penetration tester: {quick_note}"

        # Add terminal context if available
        if context:
            prompt += f"\n\n## Terminal Context (recent commands/output):\n{context}"

        # Add entire conversation history for context (excluding only internal tool markers and thinking)
        # This gives the LLM full awareness of what the tester has been working on
        # IMPORTANT: Works with /squash - includes pending_summary and <conversation_summary> tags
        history_parts = []
        conversation_attachments = []

        # Include pending summary from /squash if finding is created immediately after squash
        if self.pending_summary:
            history_parts.append(f"## Previous Conversation Summary:\n{self.pending_summary}")

        if self.conversation.responses:
            for resp in self.conversation.responses:  # ALL responses, not limited
                # Get user prompt (if available)
                if hasattr(resp, 'prompt') and resp.prompt:
                    user_text = resp.prompt.prompt if hasattr(resp.prompt, 'prompt') else str(resp.prompt)

                    # Extract <conversation_summary> content if present (post-squash)
                    # This contains the compressed history from earlier in the session
                    summary_match = re.search(r'<conversation_summary>(.*?)</conversation_summary>', user_text, re.DOTALL)
                    if summary_match:
                        # Insert at beginning since it's older context
                        if history_parts and history_parts[0].startswith("## Previous Conversation Summary:"):
                            # Already have pending_summary, add this after
                            history_parts.insert(1, f"## Earlier Conversation Summary:\n{summary_match.group(1).strip()}")
                        else:
                            history_parts.insert(0, f"## Previous Conversation Summary:\n{summary_match.group(1).strip()}")
                        # Remove summary tag from user_text for cleaner processing
                        user_text = re.sub(r'<conversation_summary>.*?</conversation_summary>\s*', '', user_text, flags=re.DOTALL)

                    # Skip only internal tool-related messages (keep everything else including terminal context)
                    if user_text and user_text.strip() and not user_text.startswith('<tool'):
                        history_parts.append(f"User: {user_text.strip()}")

                    # Collect attachments (screenshots, images) for evidence context
                    if hasattr(resp.prompt, 'attachments') and resp.prompt.attachments:
                        conversation_attachments.extend(resp.prompt.attachments)

                # Get assistant response text
                if hasattr(resp, 'text'):
                    text = resp.text()
                    # Include all assistant responses (JSON, code blocks are valuable context)
                    # Only strip <thinking> traces (keep everything else)
                    if text and text.strip():
                        # Remove thinking traces but keep the rest
                        cleaned = re.sub(r'<thinking>.*?</thinking>\s*', '', text, flags=re.DOTALL)
                        if cleaned.strip():
                            history_parts.append(f"Assistant: {cleaned.strip()}")

        if history_parts:
            prompt += f"\n\n## Conversation Context:\n" + "\n\n".join(history_parts)

        # Limit total attachments to avoid token bloat (most recent 5)
        conversation_attachments = conversation_attachments[-5:] if conversation_attachments else []

        # Only pass attachments if model supports them
        attachments_to_pass = None
        if conversation_attachments and hasattr(model, 'attachment_types') and model.attachment_types:
            # Filter to only supported attachment types
            attachments_to_pass = [
                att for att in conversation_attachments
                if hasattr(att, 'type') and att.type in model.attachment_types
            ][:3]  # Max 3 attachments

        # Use schema enforcement if model supports it
        # This is an ISOLATED call - does NOT add to self.conversation
        if hasattr(model, 'supports_schema') and model.supports_schema:
            response = model.prompt(
                prompt,
                system=system_prompt,
                schema=FindingSchema,
                attachments=attachments_to_pass  # Include screenshots/images if supported
            )
            return json.loads(response.text())
        else:
            # Fallback with JSON instructions
            json_prompt = f"""{prompt}

Respond with a JSON object containing these fields:
- suggested_title: concise vulnerability title (max 60 chars) in {language}
- severity: integer 1-9 per OWASP Risk Matrix
- severity_rationale: brief explanation in {language}
- description: expanded technical description in {language}
- remediation: step-by-step recommendations in {language}"""
            response = model.prompt(
                json_prompt,
                system=system_prompt,
                attachments=attachments_to_pass
            )
            # Try to extract JSON from response
            text = response.text()
            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            result = json.loads(text.strip())
            # Validate and clamp severity to 1-9 range (no schema enforcement)
            if 'severity' in result:
                try:
                    result['severity'] = max(1, min(9, int(result['severity'])))
                except (ValueError, TypeError):
                    result['severity'] = 5  # Default to medium if invalid
            return result

    def _capture_report_evidence(self, finding_id: str, project_dir: Path) -> List[Dict]:
        """Capture evidence (terminal context) for a finding."""
        evidence = []
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        evidence_dir = project_dir / "evidence"
        evidence_dir.mkdir(exist_ok=True)

        # Capture terminal context
        try:
            terminals = self._get_all_terminals_with_content()
            context_parts = []
            for t in terminals:
                if t.get('uuid') != self.chat_terminal_uuid:
                    content = t.get('content', '')[:5000]
                    if content.strip():
                        context_parts.append(f"=== Terminal: {t.get('title', 'unknown')} ===\n{content}")

            if context_parts:
                context_file = evidence_dir / f"{finding_id}_context_{timestamp}.txt"
                context_file.write_text("\n\n".join(context_parts))
                evidence.append({
                    "type": "context",
                    "path": f"evidence/{context_file.name}"
                })
        except Exception:
            pass

        return evidence

    def _parse_findings_file(self, path: Path) -> Tuple[Dict, List[Tuple[Dict, str]]]:
        """Parse findings.md: return (project_frontmatter, list of (finding_yaml, finding_body) tuples).

        Note: The file format uses --- as YAML block delimiters. Avoid using bare ---
        lines in finding descriptions/remediation as they would be incorrectly parsed.
        """
        content = path.read_text()

        # Split on YAML frontmatter delimiter
        parts = content.split('---')
        if len(parts) < 3:
            # No valid frontmatter
            return {}, []

        # First block is project metadata
        project_meta = yaml.safe_load(parts[1]) or {}

        # Remaining content contains findings
        remaining = '---'.join(parts[2:])

        # Parse per-finding YAML blocks
        # Pattern: ---\n<yaml>\n---\n<markdown>
        findings = []

        # Split by --- delimiters
        blocks = remaining.split('---')

        # First block is the summary table (skip it)
        i = 1
        while i < len(blocks):
            yaml_block = blocks[i].strip()
            if yaml_block:
                # Try to parse as YAML and check for 'id' key (finding metadata)
                try:
                    finding_meta = yaml.safe_load(yaml_block)
                    if isinstance(finding_meta, dict) and 'id' in finding_meta:
                        # This is a finding YAML block
                        # Next block is the markdown body
                        body = blocks[i + 1] if i + 1 < len(blocks) else ""
                        findings.append((finding_meta, body))
                        i += 2
                        continue
                except yaml.YAMLError:
                    pass
            # Not a finding block, skip
            i += 1

        return project_meta, findings

    def _get_next_finding_id(self, findings: List[Tuple[Dict, str]]) -> str:
        """Generate next finding ID (F001, F002, ...)."""
        if not findings:
            return "F001"

        max_num = 0
        for meta, _ in findings:
            fid = meta.get('id', '')
            if fid.startswith('F') and fid[1:].isdigit():
                num = int(fid[1:])
                max_num = max(max_num, num)

        return f"F{max_num + 1:03d}"

    def _md_table_escape(self, value: str) -> str:
        """Escape a string for use in markdown table cells."""
        if not value:
            return value
        # Escape pipe characters which break table structure
        return str(value).replace('|', '\\|')

    def _yaml_escape(self, value) -> str:
        """Escape a value for safe YAML output."""
        if value is None:
            return '""'
        if isinstance(value, bool):
            return 'true' if value else 'false'  # YAML boolean literals
        if isinstance(value, (int, float)):
            return str(value)
        s = str(value)
        # Empty string must be quoted to avoid being parsed as null
        if not s:
            return '""'
        # Quote if contains special chars or looks like YAML syntax
        if any(c in s for c in [':', '#', '"', "'", '\n', '{', '}', '[', ']']) or s.startswith(('-', '!', '&', '*')):
            # Use double quotes and escape internal quotes/newlines
            s = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            return f'"{s}"'
        return s

    def _write_findings_file(self, path: Path, project_meta: Dict, findings: List[Tuple[Dict, str]]):
        """Write findings.md with project YAML frontmatter + per-finding YAML blocks."""
        lines = []

        # Project frontmatter
        lines.append("---")
        for key, value in project_meta.items():
            lines.append(f"{key}: {self._yaml_escape(value)}")
        lines.append("---")
        lines.append("")

        # Header and summary table
        project_name = project_meta.get('project', 'Unknown Project')
        lines.append(f"# Penetration Test Findings: {project_name}")
        lines.append("")
        lines.append("| ID | Severity | Title |")
        lines.append("|----|----------|-------|")

        for meta, _ in findings:
            fid = meta.get('id', '?')
            severity = meta.get('severity', 0)
            title = self._md_table_escape(meta.get('title', 'Untitled'))
            sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"
            lines.append(f"| {fid} | {severity} ({sev_label}) | {title} |")

        lines.append("")

        # Per-finding YAML blocks + bodies
        for meta, body in findings:
            lines.append("---")
            # Write finding metadata as YAML
            for key, value in meta.items():
                if key == 'evidence':
                    if value:
                        lines.append("evidence:")
                        for ev in value:
                            lines.append(f"  - type: {self._yaml_escape(ev.get('type', 'file'))}")
                            lines.append(f"    path: {self._yaml_escape(ev.get('path', ''))}")
                    else:
                        lines.append("evidence: []")
                else:
                    lines.append(f"{key}: {self._yaml_escape(value)}")
            lines.append("---")
            lines.append(body.rstrip())
            lines.append("")

        path.write_text("\n".join(lines))

    def _report_list(self) -> bool:
        """List all findings in current project."""
        if not self.findings_project:
            self.console.print("[red]No project initialized. Use /report init <project> <lang>[/]")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            self.console.print(f"[red]Project file not found: {findings_file}[/]")
            return True

        project_meta, findings = self._parse_findings_file(findings_file)

        if not findings:
            self.console.print(f"[dim]No findings in project: {self.findings_project}[/]")
            return True

        self.console.print(f"[bold]Findings for {self.findings_project}:[/]")
        for meta, _ in findings:
            fid = meta.get('id', '?')
            severity = meta.get('severity', 0)
            title = meta.get('title', 'Untitled')
            sev_color = "red" if severity >= 7 else "yellow" if severity >= 4 else "green"
            sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"
            self.console.print(f"  [{sev_color}]{fid}[/]  {severity} ({sev_label})  {title}")

        return True

    def _report_edit(self, args: str) -> bool:
        """Open finding for editing."""
        if not self.findings_project:
            self.console.print("[red]No project initialized.[/]")
            return True

        finding_id = args.strip().upper()
        if not finding_id:
            self.console.print("[red]Usage: /report edit <id> (e.g., /report edit F001)[/]")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        self.console.print(f"[dim]Edit the findings file directly:[/]")
        self.console.print(f"  {findings_file}")
        return True

    def _report_delete(self, args: str) -> bool:
        """Delete a finding by ID."""
        if not self.findings_project:
            self.console.print("[red]No project initialized.[/]")
            return True

        finding_id = args.strip().upper()
        if not finding_id:
            self.console.print("[red]Usage: /report delete <id> (e.g., /report delete F001)[/]")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            self.console.print(f"[red]Project file not found[/]")
            return True

        project_meta, findings = self._parse_findings_file(findings_file)

        # Safety check: refuse to modify corrupted files
        if not project_meta:
            self.console.print(f"[red]Error: Could not parse project file (invalid format)[/]")
            return True

        # Find the finding to delete (to get evidence paths)
        deleted_finding = None
        new_findings = []
        for m, b in findings:
            if m.get('id', '').upper() == finding_id:
                deleted_finding = m
            else:
                new_findings.append((m, b))

        if deleted_finding is None:
            self.console.print(f"[yellow]Finding {finding_id} not found[/]")
            return True

        # Delete associated evidence files
        evidence_dir = project_dir / "evidence"
        if evidence_dir.exists():
            for ev in deleted_finding.get('evidence', []):
                ev_path = project_dir / ev.get('path', '')
                if ev_path.exists():
                    try:
                        ev_path.unlink()
                    except Exception:
                        pass  # Best effort deletion

        self._write_findings_file(findings_file, project_meta, new_findings)
        self.console.print(f"[green]✓[/] Deleted {finding_id}")
        return True

    def _report_set_severity(self, args: str) -> bool:
        """Override severity for a finding."""
        if not self.findings_project:
            self.console.print("[red]No project initialized.[/]")
            return True

        parts = args.strip().split()
        if len(parts) != 2:
            self.console.print("[red]Usage: /report severity <id> <1-9> (e.g., /report severity F001 8)[/]")
            return True

        finding_id = parts[0].upper()
        try:
            new_severity = int(parts[1])
            if not 1 <= new_severity <= 9:
                raise ValueError()
        except ValueError:
            self.console.print("[red]Severity must be 1-9[/]")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            self.console.print(f"[red]Project file not found[/]")
            return True

        project_meta, findings = self._parse_findings_file(findings_file)

        # Safety check: refuse to modify corrupted files
        if not project_meta:
            self.console.print(f"[red]Error: Could not parse project file (invalid format)[/]")
            return True

        # Find and update the finding
        found = False
        for meta, body in findings:
            if meta.get('id', '').upper() == finding_id:
                meta['severity'] = new_severity
                meta['severity_rationale'] = f"Manually set to {new_severity}"
                found = True
                break

        if not found:
            self.console.print(f"[yellow]Finding {finding_id} not found[/]")
            return True

        self._write_findings_file(findings_file, project_meta, findings)
        sev_color = "red" if new_severity >= 7 else "yellow" if new_severity >= 4 else "green"
        self.console.print(f"[green]✓[/] Updated {finding_id} severity to [{sev_color}]{new_severity}[/]")
        return True

    def _report_projects(self) -> bool:
        """List all finding projects."""
        if not self.findings_base_dir.exists():
            self.console.print("[dim]No projects found[/]")
            return True

        projects = [d for d in self.findings_base_dir.iterdir() if d.is_dir()]
        if not projects:
            self.console.print("[dim]No projects found. Use /report init <name> <lang> to create one.[/]")
            return True

        self.console.print("[bold]Finding Projects:[/]")
        for project_dir in sorted(projects):
            name = project_dir.name
            findings_file = project_dir / "findings.md"
            count = 0
            if findings_file.exists():
                _, findings = self._parse_findings_file(findings_file)
                count = len(findings)

            active = " [green](active)[/]" if name == self.findings_project else ""
            self.console.print(f"  {name}{active} - {count} findings")

        return True

    def _report_open(self, project_name: str) -> bool:
        """Switch to an existing project."""
        project_name = project_name.strip()
        if not project_name:
            self.console.print("[red]Usage: /report open <project-name>[/]")
            return True

        project_dir = self.findings_base_dir / project_name

        if not project_dir.exists():
            self.console.print(f"[red]Project not found: {project_name}[/]")
            self.console.print("[dim]Use /report projects to list available projects[/]")
            return True

        self.findings_project = project_name
        self.console.print(f"[green]✓[/] Switched to project: {project_name}")
        return True

    def _report_export(self, args: str) -> bool:
        """Export findings to Word document via pandoc."""
        if not self.findings_project:
            self.console.print("[red]No project initialized.[/]")
            return True

        project_dir = self.findings_base_dir / self.findings_project
        findings_file = project_dir / "findings.md"

        if not findings_file.exists():
            self.console.print(f"[red]Project file not found[/]")
            return True

        # Check pandoc is available
        import subprocess
        try:
            subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.console.print("[red]pandoc not found. Install with: apt install pandoc[/]")
            return True

        # Create export file (strip per-finding YAML for clean Word output)
        project_meta, findings = self._parse_findings_file(findings_file)

        # Build clean markdown for export (no per-finding YAML)
        export_lines = []
        export_lines.append(f"# Penetration Test Findings: {project_meta.get('project', 'Unknown')}")
        export_lines.append("")
        export_lines.append(f"**Date:** {project_meta.get('created', 'Unknown')}")
        export_lines.append(f"**Assessor:** {project_meta.get('assessor', 'Unknown')}")
        export_lines.append("")

        # Summary table
        export_lines.append("## Summary")
        export_lines.append("")
        export_lines.append("| ID | Severity | Title |")
        export_lines.append("|----|----------|-------|")
        for meta, _ in findings:
            fid = meta.get('id', '?')
            severity = meta.get('severity', 0)
            title = self._md_table_escape(meta.get('title', 'Untitled'))
            sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"
            export_lines.append(f"| {fid} | {severity} ({sev_label}) | {title} |")
        export_lines.append("")

        # Each finding
        for meta, body in findings:
            fid = meta.get('id', '?')
            title = meta.get('title', 'Untitled')
            severity = meta.get('severity', 0)
            rationale = meta.get('severity_rationale', '')
            sev_label = "High" if severity >= 7 else "Med" if severity >= 4 else "Low"

            export_lines.append(f"## {fid}: {title}")
            export_lines.append("")
            export_lines.append(f"**Severity:** {severity} ({sev_label}) - {rationale}")
            export_lines.append(body)
            export_lines.append("")

        # Write temp file and convert
        export_md = project_dir / "findings_export.md"
        export_md.write_text("\n".join(export_lines))

        output_file = project_dir / "findings.docx"

        # Check for custom template
        template_file = llm.user_dir() / "llm-assistant" / "pentest-template.docx"
        cmd = ["pandoc", str(export_md), "-o", str(output_file)]
        if template_file.exists():
            cmd.extend(["--reference-doc", str(template_file)])

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            self.console.print(f"[green]✓[/] Exported to: {output_file}")
        except subprocess.CalledProcessError as e:
            self.console.print(f"[red]Export failed: {e.stderr.decode() if e.stderr else str(e)}[/]")
        finally:
            # Clean up temp file
            if export_md.exists():
                export_md.unlink()

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
                            meta_file = f"/tmp/llm-assistant/.prompt-meta-{shell_pid}"
                            import os
                            if os.path.exists(meta_file):
                                with open(meta_file, 'r') as f:
                                    meta_content = f.read().strip()
                                self._debug(f"Read metadata from file: {meta_content}")
                                # Parse format: E<exit>T<timestamp>D<duration>
                                import re
                                match = re.match(r'E(\d+)T(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})D(\d+)', meta_content)
                                if match:
                                    exit_code = int(match.group(1))
                                    duration = int(match.group(3))
                                    self._debug(f"Parsed metadata: exit_code={exit_code}, duration={duration}")
                                # Delete file after reading (one-time use)
                                os.unlink(meta_file)
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

            self.console.print(f"\n[cyan]Capturing screenshot ({scope})...[/]")

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
                            self.console.print(f"[green]✓[/] Screenshot: {term.get('title', 'Terminal')}")
                        else:
                            self.console.print(f"[yellow]Screenshot failed for {term.get('title', 'Terminal')}: {error}[/]")
                else:
                    temp_path, error = self._capture_screenshot(
                        self.exec_terminal_uuid,
                        unique_id=uuid.uuid4().hex[:12]
                    )
                    if temp_path:
                        result_attachments.append(Attachment(path=temp_path))
                        captured_info.append("Exec terminal")
                        self.console.print(f"[green]✓[/] Screenshot captured")
                    else:
                        self.console.print(f"[red]Screenshot failed: {error}[/]")
                        captured_info.append(f"Error: {error}")

                return ToolResult(
                    name=tool_call.name,
                    output=f"Captured screenshots: {', '.join(captured_info)}" if captured_info else "No screenshots captured",
                    attachments=result_attachments,
                    tool_call_id=tool_call_id
                )
            except Exception as e:
                self.console.print(f"[red]Screenshot error: {e}[/]")
                return ToolResult(
                    name=tool_call.name,
                    output=f"Screenshot error: {e}",
                    tool_call_id=tool_call_id
                )

        elif tool_name == "refresh_context":
            self.console.print("\n[cyan]Refreshing terminal context...[/]")
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
                    self.console.print(f"[green]✓[/] Context refreshed ({len(context_text)} chars{tui_info})")

                    return ToolResult(
                        name=tool_call.name,
                        output=f"Refreshed terminal context:\n\n{context_text}",
                        attachments=result_attachments,
                        tool_call_id=tool_call_id
                    )
                else:
                    self.console.print("[yellow]No terminal content captured[/]")
                    return ToolResult(
                        name=tool_call.name,
                        output="No terminal content captured",
                        tool_call_id=tool_call_id
                    )
            except Exception as e:
                self.console.print(f"[red]Context refresh error: {e}[/]")
                return ToolResult(
                    name=tool_call.name,
                    output=f"Context refresh error: {e}",
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
                self.console.print(f"[yellow]{result}[/]")
                return ToolResult(
                    name=tool_call.name,
                    output=result,
                    tool_call_id=tool_call_id
                )

            self.pending_attachments.append(attachment)
            self.console.print(f"[green]✓[/] Queued {mime_type}: {path_or_url}")
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
                self.console.print(f"[green]✓[/] PDF queued for native viewing: {path_or_url}")
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
                self.console.print(f"[green]✓[/] YouTube video queued for native viewing")
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
                    self.console.print(f"[green]✓[/] {tool_name} completed")
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
                    self.console.print(f"[red]{tool_name} error: {e}[/]")
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
                self.console.print(f"[dim cyan]{status_msg}[/]")
                self.console.print(f"[green]✓[/] {tool_name} completed")
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
                self.console.print(f"[red]{tool_name} error: {e}[/]")
                return ToolResult(
                    name=tool_call.name,
                    output=f"Error calling {tool_name}: {e}",
                    tool_call_id=tool_call_id
                )

        else:
            # Unknown tool - shouldn't happen with our defined tools
            self.console.print(f"[yellow]Unknown tool: {tool_call.name}[/]")
            return ToolResult(
                name=tool_call.name,
                output=f"Unknown tool: {tool_call.name}",
                tool_call_id=tool_call_id
            )

    def run(self):
        """Main REPL loop with health checks"""
        # Connect to Terminator
        self._connect_to_terminator()

        # Setup terminals
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
                # Periodic health check every 10 iterations
                check_counter += 1
                if check_counter >= 10:
                    # Check plugin availability
                    if not self._check_plugin_available():
                        self.console.print("[yellow]Plugin unavailable, attempting reconnect...[/]")
                        if not self._reconnect_plugin():
                            self.console.print("[red]Plugin reconnection failed. Please restart assistant.[/]")
                            break

                    # Check D-Bus connection
                    if not self._check_dbus_connection():
                        self.console.print("[yellow]D-Bus disconnected, attempting reconnect...[/]")
                        if not self._reconnect_dbus():
                            self.console.print("[red]D-Bus reconnection failed. Please restart assistant.[/]")
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
                    self.console.print("\n[yellow]Exiting...[/]")
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
                    self.console.print(f"[dim]Multi-line mode. Type '{end_token}' to finish[/]")
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
                    self.console.print("\n[yellow]Exiting...[/]")
                    self._shutdown()
                    break

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
                    self.console.print("\n[bold green]llm[/]")

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
                    self.console.print(f"\n[red]Streaming error: {e}[/]")
                    self.console.print("[yellow]Response may be incomplete. Please try again.[/]")
                    # Don't process commands or update conversation on stream failure
                    continue

                # Only process tool calls if streaming succeeded
                if stream_success:
                    try:
                        # Process tool calls (structured output from model)
                        if tool_calls:
                            if self.debug:
                                self.console.print(f"\n[cyan]Processing {len(tool_calls)} tool call(s)[/]")

                            # Collect tool results for sending back to model
                            tool_results = []

                            for i, tool_call in enumerate(tool_calls, 1):
                                if len(tool_calls) > 1:
                                    self.console.print(f"\n[bold]Tool {i}/{len(tool_calls)}: {tool_call.name}[/]")
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
                                    self.console.print(f"\n[dim]Sending {len(tool_results)} tool result(s) to model...[/]")

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
                                    self.console.print(f"[dim]Including {len(followup_attachments)} attachment(s) ({len(tool_result_attachments)} from tools)[/]")

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
                                    self.console.print(f"\n[cyan]Processing {len(more_tool_calls)} additional tool call(s) (round {iteration + 1})[/]")
                                tool_results = []

                                for j, tool_call in enumerate(more_tool_calls, 1):
                                    if len(more_tool_calls) > 1:
                                        self.console.print(f"\n[bold]Tool {j}/{len(more_tool_calls)}: {tool_call.name}[/]")
                                    tool_results.append(self._process_tool_call(tool_call))

                            # Warn if max iterations reached and model still wants more
                            if iteration >= MAX_TOOL_ITERATIONS and more_tool_calls:
                                self.console.print(f"\n[yellow]Max tool iterations ({MAX_TOOL_ITERATIONS}) reached. Model requested {len(more_tool_calls)} more tool call(s). Please continue the conversation.[/]")

                            # Auto-send any remaining pending attachments (e.g., view_attachment was last tool call)
                            if self.pending_attachments:
                                if self.debug:
                                    self.console.print(f"\n[dim]Auto-sending {len(self.pending_attachments)} pending attachment(s)[/]")

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
                        self.console.print(f"\n[red]Tool execution error: {e}[/]")

        finally:
            # Unified cleanup - handles all resources
            self._shutdown()


