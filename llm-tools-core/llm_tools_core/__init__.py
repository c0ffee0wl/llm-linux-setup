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
- ConversationHistory: Shared history access (history module)
- AtHandler: @ reference parsing and resolution (at_handler module)
- RAGHandler: RAG integration wrapper (rag_handler module)
"""

from .prompt_detection import PromptDetector
from .hashing import hash_blocks, filter_new_blocks, hash_window, hash_gui_context
from .console import ConsoleHelper
from .xdg import get_config_dir, get_temp_dir, get_logs_db_path
from .markdown import strip_markdown, strip_markdown_for_tts, extract_code_blocks

# Model context limits and assistant model selection
from .models import (
    MODEL_CONTEXT_LIMITS,
    PROVIDER_DEFAULT_LIMITS,
    DEFAULT_CONTEXT_LIMIT,
    get_model_context_limit,
    ASSISTANT_MODEL_UPGRADES,
    ASSISTANT_MODEL_FALLBACK,
    get_assistant_default_model,
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
    get_pid_path,
    ensure_socket_dir,
    write_suggested_command,
    read_suggested_command,
    is_daemon_process_alive,
    write_pid_file,
    remove_pid_file,
    cleanup_stale_daemon,
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
    get_visible_window_ids,
    get_wm_class,
    get_window_title,
    get_focused_window_pid,
    get_cwd,
    get_cmdline,
    get_selection,
    gather_all_visible_windows,
    gather_context,
    format_context_for_llm,
    format_gui_context,
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
    DaemonTimeoutError,
    DaemonUnavailableError,
    format_error_response,
)

# Conversation history
from .history import (
    ConversationHistory,
    ConversationSummary,
    FullConversation,
    Message,
    strip_context_tags,
    format_tool_call_markdown,
    TOOL_RESULT_TRUNCATE_LIMIT,
)

# @ reference handling
from .at_handler import (
    AtHandler,
    Completion,
    ResolvedReference,
)

# RAG integration
from .rag_handler import (
    RAGHandler,
    SearchResult,
    AddResult,
)

# Tool display configuration
from .tool_display import (
    TOOL_DISPLAY,
    get_action_verb,
    get_tool_info,
    get_action_verb_map,
)

# Tool execution
from .tool_execution import (
    execute_tool_call,
    ToolEvent,
)

# MCP citation post-processing
from .mcp_citations import (
    MICROSOFT_DOC_TOOLS,
    MCP_CITATION_RULES,
    is_microsoft_doc_tool,
    format_microsoft_citations,
)

__all__ = [
    # Prompt detection
    "PromptDetector",
    # Hashing
    "hash_blocks",
    "filter_new_blocks",
    "hash_window",
    "hash_gui_context",
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
    # Model context limits and assistant model selection
    "MODEL_CONTEXT_LIMITS",
    "PROVIDER_DEFAULT_LIMITS",
    "DEFAULT_CONTEXT_LIMIT",
    "get_model_context_limit",
    "ASSISTANT_MODEL_UPGRADES",
    "ASSISTANT_MODEL_FALLBACK",
    "get_assistant_default_model",
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
    "get_visible_window_ids",
    "get_wm_class",
    "get_window_title",
    "get_focused_window_pid",
    "get_cwd",
    "get_cmdline",
    "get_selection",
    "gather_all_visible_windows",
    "gather_context",
    "format_context_for_llm",
    "format_gui_context",
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
    "DaemonTimeoutError",
    "DaemonUnavailableError",
    "format_error_response",
    # Conversation history
    "ConversationHistory",
    "ConversationSummary",
    "FullConversation",
    "Message",
    "strip_context_tags",
    "format_tool_call_markdown",
    "TOOL_RESULT_TRUNCATE_LIMIT",
    # @ reference handling
    "AtHandler",
    "Completion",
    "ResolvedReference",
    # RAG integration
    "RAGHandler",
    "SearchResult",
    "AddResult",
    # Tool display configuration
    "TOOL_DISPLAY",
    "get_action_verb",
    "get_tool_info",
    "get_action_verb_map",
    # Tool execution
    "execute_tool_call",
    "ToolEvent",
    # MCP citation post-processing
    "MICROSOFT_DOC_TOOLS",
    "MCP_CITATION_RULES",
    "is_microsoft_doc_tool",
    "format_microsoft_citations",
]

__version__ = "1.3.0"
