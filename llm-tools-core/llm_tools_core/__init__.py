"""llm-tools-core - Core utilities for llm-assistant and llm-inlineassistant.

This package provides shared utilities:
- PromptDetector: Shell prompt detection (regex and Unicode markers)
- hash_blocks, filter_new_blocks: Block-level content hashing
- ConsoleHelper: Rich console output formatting
- get_config_dir, get_temp_dir, get_logs_db_path: XDG directory helpers
- strip_markdown, strip_markdown_for_tts, extract_code_blocks: Markdown processing
- Model context limits and detection (models module)
- System detection (system module)
- TUI command detection (tui module)
- Token estimation (tokens module)
- Daemon socket paths and constants (daemon module)
- Daemon client utilities (daemon_client module)
- Linux desktop context gathering (linux_context module)
- Shared system prompts (prompts module)
- Error codes and exceptions (errors module)
"""

from .prompt_detection import PromptDetector
from .hashing import hash_blocks, filter_new_blocks
from .console import ConsoleHelper
from .xdg import get_config_dir, get_temp_dir, get_logs_db_path
from .markdown import strip_markdown, strip_markdown_for_tts, extract_code_blocks

# Model context limits
from .models import (
    MODEL_CONTEXT_LIMITS,
    PROVIDER_DEFAULT_LIMITS,
    DEFAULT_CONTEXT_LIMIT,
    get_model_context_limit,
)

# System detection
from .system import (
    detect_shell,
    detect_os,
    detect_environment,
    detect_package_managers,
    get_system_context,
)

# TUI command detection
from .tui import TUI_COMMANDS, is_tui_command

# Token estimation
from .tokens import (
    CHARS_PER_TOKEN,
    estimate_tokens,
    estimate_tokens_json,
    estimate_context_usage,
    is_approaching_limit,
)

# Daemon socket paths and constants
from .daemon import (
    get_socket_path,
    get_socket_dir,
    get_suggest_path,
    ensure_socket_dir,
    write_suggested_command,
    read_suggested_command,
    DAEMON_STARTUP_TIMEOUT,
    REQUEST_TIMEOUT,
    SOCKET_CONNECT_TIMEOUT,
    RECV_BUFFER_SIZE,
    IDLE_TIMEOUT_MINUTES,
    WORKER_IDLE_MINUTES,
    MAX_TOOL_ITERATIONS,
    MAX_TOOL_ITERATIONS_AGENT,
)

# Daemon client utilities
from .daemon_client import (
    is_daemon_running,
    start_daemon,
    ensure_daemon,
    get_terminal_session_id,
    connect_to_daemon,
    stream_events,
)

# Linux desktop context (X11/Wayland)
from .linux_context import (
    is_x11,
    is_wayland,
    get_session_type,
    get_focused_window_id,
    get_wm_class,
    get_window_title,
    get_focused_window_pid,
    get_cwd,
    get_cmdline,
    get_selection,
    capture_screenshot,
    gather_context,
    format_context_for_llm,
    MAX_SELECTION_BYTES,
)

# Shared system prompts
from .prompts import (
    build_simple_system_prompt,
    build_context_section,
    wrap_terminal_context,
    wrap_conversation_summary,
    wrap_retrieved_documents,
)

# Error codes and exceptions
from .errors import (
    ErrorCode,
    DaemonError,
    EmptyQueryError,
    ModelError,
    ToolError,
    TimeoutError,
    DaemonUnavailableError,
    format_error_response,
)

__all__ = [
    # Prompt detection
    "PromptDetector",
    # Hashing
    "hash_blocks",
    "filter_new_blocks",
    # Console output
    "ConsoleHelper",
    # XDG directories
    "get_config_dir",
    "get_temp_dir",
    "get_logs_db_path",
    # Markdown processing
    "strip_markdown",
    "strip_markdown_for_tts",
    "extract_code_blocks",
    # Model context limits
    "MODEL_CONTEXT_LIMITS",
    "PROVIDER_DEFAULT_LIMITS",
    "DEFAULT_CONTEXT_LIMIT",
    "get_model_context_limit",
    # System detection
    "detect_shell",
    "detect_os",
    "detect_environment",
    "detect_package_managers",
    "get_system_context",
    # TUI command detection
    "TUI_COMMANDS",
    "is_tui_command",
    # Token estimation
    "CHARS_PER_TOKEN",
    "estimate_tokens",
    "estimate_tokens_json",
    "estimate_context_usage",
    "is_approaching_limit",
    # Daemon paths and constants
    "get_socket_path",
    "get_socket_dir",
    "get_suggest_path",
    "ensure_socket_dir",
    "write_suggested_command",
    "read_suggested_command",
    "DAEMON_STARTUP_TIMEOUT",
    "REQUEST_TIMEOUT",
    "SOCKET_CONNECT_TIMEOUT",
    "RECV_BUFFER_SIZE",
    "IDLE_TIMEOUT_MINUTES",
    "WORKER_IDLE_MINUTES",
    "MAX_TOOL_ITERATIONS",
    "MAX_TOOL_ITERATIONS_AGENT",
    # Daemon client utilities
    "is_daemon_running",
    "start_daemon",
    "ensure_daemon",
    "get_terminal_session_id",
    "connect_to_daemon",
    "stream_events",
    # Linux desktop context
    "is_x11",
    "is_wayland",
    "get_session_type",
    "get_focused_window_id",
    "get_wm_class",
    "get_window_title",
    "get_focused_window_pid",
    "get_cwd",
    "get_cmdline",
    "get_selection",
    "capture_screenshot",
    "gather_context",
    "format_context_for_llm",
    "MAX_SELECTION_BYTES",
    # System prompts
    "build_simple_system_prompt",
    "build_context_section",
    "wrap_terminal_context",
    "wrap_conversation_summary",
    "wrap_retrieved_documents",
    # Error codes and exceptions
    "ErrorCode",
    "DaemonError",
    "EmptyQueryError",
    "ModelError",
    "ToolError",
    "TimeoutError",
    "DaemonUnavailableError",
    "format_error_response",
]

__version__ = "1.1.0"
