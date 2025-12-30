"""Daemon socket paths and constants for llm-assistant.

Provides shared configuration for daemon communication used by:
- llm-assistant (daemon server)
- llm-inlineassistant (thin client)
- espanso-llm (text expansion client)

Socket path format: /tmp/llm-assistant-{UID}/daemon.sock
"""

import os
from pathlib import Path

# Socket location pattern (UID substitution)
SOCKET_DIR_PATTERN = "/tmp/llm-assistant-{uid}"
SOCKET_FILENAME = "daemon.sock"

# Timeout constants (seconds)
DAEMON_STARTUP_TIMEOUT = 5.0  # Max wait for daemon to start
REQUEST_TIMEOUT = 120  # Max wait for request completion
SOCKET_CONNECT_TIMEOUT = 0.5  # Quick test for daemon availability

# Buffer size for socket communication
RECV_BUFFER_SIZE = 8192

# Idle timeouts (minutes)
IDLE_TIMEOUT_MINUTES = 30  # Daemon auto-terminates after this
WORKER_IDLE_MINUTES = 5  # Per-terminal worker cleanup

# Tool execution limits
MAX_TOOL_ITERATIONS = 10  # Default (assistant mode)
MAX_TOOL_ITERATIONS_AGENT = 100  # Agent mode


def get_socket_dir() -> Path:
    """Get the daemon socket directory.

    Returns:
        Path to /tmp/llm-assistant-{UID}/
    """
    uid = os.getuid()
    return Path(SOCKET_DIR_PATTERN.format(uid=uid))


def get_socket_path() -> Path:
    """Get the daemon socket path.

    Returns:
        Path to /tmp/llm-assistant-{UID}/daemon.sock

    Examples:
        >>> get_socket_path()
        PosixPath('/tmp/llm-assistant-1000/daemon.sock')
    """
    return get_socket_dir() / SOCKET_FILENAME


def get_suggest_path() -> Path:
    """Get path for suggested command file.

    The daemon writes suggested commands here for the shell to pick up
    via Ctrl+G keybinding.

    Returns:
        Path to /tmp/llm-assistant-{UID}/suggest
    """
    return get_socket_dir() / "suggest"


def ensure_socket_dir() -> Path:
    """Ensure the socket directory exists with proper permissions.

    Creates /tmp/llm-assistant-{UID}/ with mode 0700 (user-only access)
    if it doesn't exist.

    Returns:
        Path to the socket directory
    """
    socket_dir = get_socket_dir()
    socket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return socket_dir


def write_suggested_command(command: str) -> None:
    """Write a suggested command for the shell to pick up.

    Args:
        command: The command to suggest (placed on user's prompt via Ctrl+G)
    """
    ensure_socket_dir()
    get_suggest_path().write_text(command)


def read_suggested_command() -> str | None:
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
