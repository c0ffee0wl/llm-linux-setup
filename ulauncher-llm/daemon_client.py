"""Daemon client utilities for ulauncher-llm.

Bundled from llm_tools_core for self-contained Ulauncher extension.
Provides socket communication with the llm-assistant daemon.
"""

import os
import socket
import subprocess
import time
from pathlib import Path


# Socket communication constants
RECV_BUFFER_SIZE = 8192
REQUEST_TIMEOUT = 120.0
SOCKET_CONNECT_TIMEOUT = 0.5
DAEMON_STARTUP_TIMEOUT = 5.0


def get_socket_path() -> Path:
    """Get the Unix socket path for the daemon.

    Returns:
        Path to /tmp/llm-assistant-{UID}/daemon.sock
    """
    return Path(f"/tmp/llm-assistant-{os.getuid()}/daemon.sock")


def is_daemon_running() -> bool:
    """Check if daemon is running by testing socket connection.

    Returns:
        True if daemon socket accepts connections, False otherwise.
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


def start_daemon(model: str = None) -> bool:
    """Start the llm-assistant daemon in background.

    Tries absolute path first (for GUI apps where PATH
    may not include ~/.local/bin), then falls back to PATH lookup.

    Args:
        model: Optional model to use (passed via -m flag)

    Returns:
        True if daemon started successfully, False otherwise.
    """
    # Try absolute path first (for GUI apps)
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


def ensure_daemon(model: str = None) -> bool:
    """Ensure daemon is running, starting it if needed.

    Args:
        model: Optional model to use if daemon needs to be started

    Returns:
        True if daemon is available, False otherwise.
    """
    if is_daemon_running():
        return True
    return start_daemon(model)


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
