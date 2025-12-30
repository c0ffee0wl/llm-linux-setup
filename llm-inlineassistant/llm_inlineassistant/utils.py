"""Utility functions for llm-inlineassistant.

Configuration, paths, and helper functions for the inline assistant.
Re-exports shared utilities from llm_tools_core.
"""

from pathlib import Path
from typing import Optional

# Import shared utilities from llm_tools_core
from llm_tools_core import (
    get_config_dir as _core_get_config_dir,
    get_socket_path,
    get_suggest_path,
    write_suggested_command,
    read_suggested_command,
    get_terminal_session_id,
)

# Shared config directory name (llm-inlineassistant shares with llm-assistant)
_APP_NAME = "llm-assistant"

# Re-export for backward compatibility
__all__ = [
    "get_config_dir",
    "get_logs_db_path",
    "logs_on",
    "get_socket_path",
    "get_suggest_path",
    "write_suggested_command",
    "read_suggested_command",
    "get_terminal_session_id",
    "get_active_conversation_path",
    "get_active_conversation",
    "save_active_conversation",
    "clear_active_conversation",
]


def get_config_dir() -> Path:
    """Get shared config directory (same as llm-assistant) using XDG spec.

    Returns: XDG_CONFIG_HOME/llm-assistant or ~/.config/llm-assistant

    llm-inlineassistant shares the config directory with llm-assistant but uses
    separate database and session tracking files.
    """
    config_dir = _core_get_config_dir(_APP_NAME)
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_logs_db_path() -> Path:
    """Get path to llm-inlineassistant's conversation database.

    Returns: XDG_CONFIG_HOME/llm-assistant/logs-inlineassistant.db

    Uses logs-inlineassistant.db (not logs.db) to keep inline assistant conversations
    separate from llm-assistant's logs.db while sharing the config directory.
    """
    return get_config_dir() / 'logs-inlineassistant.db'


def logs_on() -> bool:
    """Check if logging is enabled (respects llm's global logs-off setting).

    Returns False if llm's logs-off file exists, True otherwise.
    This ensures llm-inlineassistant respects `llm logs off` command.
    """
    import llm
    return not (llm.user_dir() / "logs-off").exists()


def get_active_conversation_path(terminal_id: str) -> Path:
    """Get path to active conversation file for a terminal.

    Args:
        terminal_id: Terminal session identifier

    Returns:
        Path to the active conversation tracking file

    Files are stored in ~/.config/llm-assistant/inlineassistant-sessions/ to track
    per-terminal conversation IDs for llm-inlineassistant.
    """
    sessions_dir = get_config_dir() / 'inlineassistant-sessions'
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize terminal_id for filename
    safe_id = terminal_id.replace('/', '_').replace(':', '_')
    return sessions_dir / safe_id


def get_active_conversation(terminal_id: str) -> Optional[str]:
    """Get the active conversation ID for a terminal.

    Args:
        terminal_id: Terminal session identifier

    Returns:
        Conversation ID if exists, None otherwise
    """
    path = get_active_conversation_path(terminal_id)
    if path.exists():
        cid = path.read_text().strip()
        return cid if cid else None
    return None


def save_active_conversation(terminal_id: str, conversation_id: str) -> None:
    """Save the active conversation ID for a terminal.

    Args:
        terminal_id: Terminal session identifier
        conversation_id: The conversation ID to save
    """
    path = get_active_conversation_path(terminal_id)
    path.write_text(conversation_id)


def clear_active_conversation(terminal_id: str) -> None:
    """Clear the active conversation for a terminal.

    Args:
        terminal_id: Terminal session identifier
    """
    path = get_active_conversation_path(terminal_id)
    if path.exists():
        path.unlink()
