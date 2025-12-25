"""Utility functions for llm-shell.

Configuration, paths, and helper functions for the shell assistant.
"""

import os
from pathlib import Path
from typing import Optional


def get_config_dir() -> Path:
    """Get shared config directory (same as llm-assistant) using XDG spec.

    Returns: XDG_CONFIG_HOME/llm-assistant or ~/.config/llm-assistant

    llm-shell shares the config directory with llm-assistant but uses
    separate database and session tracking files.
    """
    xdg_config = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config:
        base = Path(xdg_config)
    else:
        base = Path.home() / '.config'

    config_dir = base / 'llm-assistant'
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_logs_db_path() -> Path:
    """Get path to llm-shell's conversation database.

    Returns: XDG_CONFIG_HOME/llm-assistant/logs-shell.db

    Uses logs-shell.db (not logs.db) to keep shell assistant conversations
    separate from llm-assistant's logs.db while sharing the config directory.
    """
    return get_config_dir() / 'logs-shell.db'


def logs_on() -> bool:
    """Check if logging is enabled (respects llm's global logs-off setting).

    Returns False if llm's logs-off file exists, True otherwise.
    This ensures llm-shell respects `llm logs off` command.
    """
    import llm
    return not (llm.user_dir() / "logs-off").exists()


def get_tmp_dir() -> Path:
    """Get llm-shell temp directory.

    Returns: /tmp/llm-shell-{UID}/

    All temp files (socket, suggestions) are stored here.
    """
    uid = os.getuid()
    tmp_dir = Path(f"/tmp/llm-shell-{uid}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def get_socket_path() -> Path:
    """Get Unix socket path for daemon communication.

    Returns: /tmp/llm-shell-{UID}/daemon.sock
    """
    return get_tmp_dir() / "daemon.sock"


def get_suggest_path() -> Path:
    """Get path for suggested command file.

    Returns: /tmp/llm-shell-{UID}/suggest

    Used by suggest_command tool to pass commands to the shell,
    which places them on the user's prompt for editing/execution.
    """
    return get_tmp_dir() / "suggest"


def write_suggested_command(command: str) -> None:
    """Write a suggested command for the shell to pick up.

    Args:
        command: The command to suggest (placed on user's prompt)
    """
    get_suggest_path().write_text(command)


def read_suggested_command() -> Optional[str]:
    """Read and clear the suggested command file.

    Returns:
        The suggested command if present, None otherwise.
        Clears the file after reading.
    """
    path = get_suggest_path()
    if path.exists():
        command = path.read_text().strip()
        path.unlink()  # Clear after reading
        return command if command else None
    return None


def get_terminal_session_id() -> str:
    """Get a unique identifier for the current terminal session.

    Checks these environment variables in priority order:
    1. TERMINAL_SESSION_ID - set by @() shell function (preferred)
    2. TMUX_PANE - tmux pane identifier
    3. TERM_SESSION_ID - iTerm2
    4. KONSOLE_DBUS_SESSION - Konsole
    5. WINDOWID - X11 window ID
    6. SESSION_LOG_FILE - asciinema recording path (fallback)
    7. $(tty) - TTY device (last resort)

    Returns:
        String identifier for the current terminal session
    """
    # First check if shell function already set the ID
    terminal_id = os.environ.get('TERMINAL_SESSION_ID')
    if terminal_id:
        return terminal_id

    # Check terminal-specific environment variables
    for var in ['TMUX_PANE', 'TERM_SESSION_ID', 'KONSOLE_DBUS_SESSION', 'WINDOWID']:
        value = os.environ.get(var)
        if value:
            return f"{var}:{value}"

    # Fallback to asciinema session log file
    session_log = os.environ.get('SESSION_LOG_FILE')
    if session_log:
        return f"asciinema:{Path(session_log).stem}"

    # Last resort: TTY device
    try:
        tty = os.ttyname(0)
        return f"tty:{tty.replace('/', '_')}"
    except (OSError, AttributeError):
        pass

    # Ultimate fallback: random ID (shouldn't happen)
    import uuid
    return f"fallback:{uuid.uuid4().hex[:8]}"


def get_active_conversation_path(terminal_id: str) -> Path:
    """Get path to active conversation file for a terminal.

    Args:
        terminal_id: Terminal session identifier

    Returns:
        Path to the active conversation tracking file

    Files are stored in ~/.config/llm-assistant/shell-sessions/ to track
    per-terminal conversation IDs for llm-shell.
    """
    sessions_dir = get_config_dir() / 'shell-sessions'
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
