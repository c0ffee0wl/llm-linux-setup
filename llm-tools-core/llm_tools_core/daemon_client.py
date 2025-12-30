"""Daemon client utilities for llm-assistant.

Provides shared client functions for connecting to the llm-assistant daemon:
- Daemon availability checking
- Daemon startup
- Socket communication helpers

Used by:
- llm-inlineassistant (thin client)
- espanso-llm (text expansion client)
"""

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from .daemon import (
    get_socket_path,
    DAEMON_STARTUP_TIMEOUT,
    REQUEST_TIMEOUT,
    SOCKET_CONNECT_TIMEOUT,
    RECV_BUFFER_SIZE,
)


def is_daemon_running() -> bool:
    """Check if daemon is running by testing socket connection.

    Returns:
        True if daemon socket accepts connections, False otherwise.

    Examples:
        >>> is_daemon_running()
        True  # if daemon is running
    """
    socket_path = get_socket_path()
    if not socket_path.exists():
        return False

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(SOCKET_CONNECT_TIMEOUT)
        sock.connect(str(socket_path))
        sock.close()
        return True
    except (socket.error, OSError):
        return False


def start_daemon(model: Optional[str] = None) -> bool:
    """Start the llm-assistant daemon in background.

    Tries absolute path first (for espanso and GUI apps where PATH
    may not include ~/.local/bin), then falls back to PATH lookup.

    Args:
        model: Optional model to use (passed via -m flag)

    Returns:
        True if daemon started successfully, False otherwise.
    """
    # Try absolute path first (for espanso and GUI apps)
    daemon_path = Path.home() / ".local" / "bin" / "llm-assistant"
    if daemon_path.exists():
        cmd = [str(daemon_path), "--daemon"]
    else:
        cmd = ["llm-assistant", "--daemon"]

    if model:
        cmd.extend(["-m", model])

    try:
        # Start daemon in background
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        # Wait for socket to appear
        socket_path = get_socket_path()
        start_time = time.time()

        while time.time() - start_time < DAEMON_STARTUP_TIMEOUT:
            if socket_path.exists() and is_daemon_running():
                return True
            time.sleep(0.1)

        return False

    except FileNotFoundError:
        return False
    except Exception:
        return False


def ensure_daemon(model: Optional[str] = None) -> bool:
    """Ensure daemon is running, starting it if needed.

    Args:
        model: Optional model to use if daemon needs to be started

    Returns:
        True if daemon is available, False otherwise.

    Examples:
        >>> ensure_daemon()
        True  # daemon was already running or started successfully
    """
    if is_daemon_running():
        return True
    return start_daemon(model)


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

    Examples:
        >>> get_terminal_session_id()
        'TMUX_PANE:%1'  # in tmux
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


def connect_to_daemon(timeout: float = REQUEST_TIMEOUT) -> socket.socket:
    """Connect to the daemon socket.

    Args:
        timeout: Socket timeout in seconds

    Returns:
        Connected socket object

    Raises:
        ConnectionError: If daemon is not running or connection fails
    """
    socket_path = get_socket_path()

    if not socket_path.exists():
        raise ConnectionError("Daemon socket does not exist")

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        return sock
    except (socket.error, OSError) as e:
        raise ConnectionError(f"Failed to connect to daemon: {e}")
