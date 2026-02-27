"""Daemon socket paths and constants for llm-assistant.

Provides shared configuration for daemon communication used by:
- llm-assistant (daemon server)
- llm-inlineassistant (thin client)
- espanso-llm (text expansion client)

Socket path format: /tmp/llm-assistant-{UID}/daemon.sock
"""

from __future__ import annotations

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

# Tool execution limit
MAX_TOOL_ITERATIONS = 100


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


def get_sessions_dir(subdir: str) -> Path:
    """Get path for session tracking directory.

    Session tracking files map terminal IDs to conversation IDs.
    These are ephemeral and don't need to persist across reboots.

    Args:
        subdir: Subdirectory name (e.g., 'daemon', 'inlineassistant')

    Returns:
        Path to /tmp/llm-assistant-{UID}/sessions/{subdir}/
    """
    # Ensure base directory exists with correct permissions first
    # (mkdir parents=True doesn't apply mode to intermediate directories)
    socket_dir = ensure_socket_dir()

    # Create sessions/ with proper permissions
    sessions_base = socket_dir / "sessions"
    sessions_base.mkdir(mode=0o700, exist_ok=True)

    # Create the specific subdirectory
    sessions_dir = sessions_base / subdir
    sessions_dir.mkdir(mode=0o700, exist_ok=True)

    return sessions_dir


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


def _atomic_write(target: Path, content: str) -> None:
    """Write content to a file atomically using temp file + rename.

    Prevents partial reads by writing to a temporary file first,
    then atomically replacing the target.
    """
    import tempfile

    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=target.parent)
        os.write(fd, content.encode('utf-8'))
        os.close(fd)
        fd = None  # Mark as closed
        os.replace(tmp_path, target)
        tmp_path = None  # Mark as moved
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def write_suggested_command(command: str) -> None:
    """Write a suggested command for the shell to pick up.

    Uses atomic write (temp file + rename) to prevent partial reads.

    Args:
        command: The command to suggest (placed on user's prompt via Ctrl+G)
    """
    ensure_socket_dir()
    _atomic_write(get_suggest_path(), command)


def read_suggested_command() -> str | None:
    """Read and clear the suggested command file atomically.

    Uses rename-then-read to prevent TOCTOU races where concurrent
    readers could both read the same command, or a new command written
    between read and unlink gets lost.

    Returns:
        The suggested command if present, None otherwise.
    """
    import tempfile

    path = get_suggest_path()
    try:
        # Atomically claim the file by renaming it — only one reader wins
        fd, tmp_path = tempfile.mkstemp(dir=path.parent)
        os.close(fd)
        os.replace(str(path), tmp_path)
    except FileNotFoundError:
        # No suggest file exists
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None

    try:
        command = Path(tmp_path).read_text().strip()
        return command if command else None
    except OSError:
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# PID file for single-instance daemon
PID_FILENAME = "daemon.pid"


def get_pid_path() -> Path:
    """Get the daemon PID file path.

    Returns:
        Path to /tmp/llm-assistant-{UID}/daemon.pid
    """
    return get_socket_dir() / PID_FILENAME


def is_daemon_process_alive() -> tuple[bool, int | None]:
    """Check if a daemon process is alive using PID file.

    This checks the PID file and verifies the process exists.
    Use this for single-instance enforcement (before starting daemon).
    Use is_daemon_running() from daemon_client for socket-based check.

    Returns:
        Tuple of (is_alive, pid).
        - (True, pid) if daemon process exists with that PID
        - (False, pid) if PID file exists but process is dead (stale)
        - (False, None) if no PID file exists
    """
    pid_path = get_pid_path()

    if not pid_path.exists():
        return False, None

    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        # Corrupt PID file - treat as stale
        return False, None

    # Check if process is alive
    try:
        os.kill(pid, 0)  # Signal 0 just checks if process exists
        return True, pid
    except PermissionError:
        # Process exists but we lack permission to signal it — treat as alive
        return True, pid
    except ProcessLookupError:
        # Process doesn't exist - stale PID file
        return False, pid
    except OSError:
        # Other OS errors - assume stale
        return False, pid


def write_pid_file() -> None:
    """Write current process PID to the PID file atomically."""
    ensure_socket_dir()
    _atomic_write(get_pid_path(), str(os.getpid()))


def remove_pid_file() -> None:
    """Remove the PID file."""
    pid_path = get_pid_path()
    if pid_path.exists():
        try:
            pid_path.unlink()
        except OSError:
            pass  # Ignore errors (might already be deleted)


def cleanup_stale_daemon() -> None:
    """Clean up stale daemon files (socket and PID) if daemon is not running.

    Call this before starting a new daemon to handle cases where
    a previous daemon crashed without cleanup.
    """
    running, pid = is_daemon_process_alive()

    if running:
        # Daemon is actually running - don't clean up
        return

    # Clean up stale files
    socket_path = get_socket_path()
    pid_path = get_pid_path()

    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError:
            pass

    if pid_path.exists():
        try:
            pid_path.unlink()
        except OSError:
            pass
