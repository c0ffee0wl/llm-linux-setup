"""Systemd user service management for llm-assistant daemon.

Provides functions to install, enable, and manage a systemd user service
that keeps the llm-assistant daemon running. This improves startup latency
for the @ command by avoiding subprocess-based daemon spawning.

Unlike llm-server which uses socket activation (FD 3), this uses a simple
service that starts the daemon directly - the daemon creates its own socket.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple


# Service unit template (simple service, no socket activation)
# Note: {path} is captured at install time to include user's full PATH
# (e.g., ~/.cargo/bin for asciinema, ~/.local/bin for tools)
SERVICE_UNIT_TEMPLATE = """\
[Unit]
Description=LLM Assistant Daemon
Documentation=https://github.com/c0ffee0wl/llm-linux-setup
After=network.target

[Service]
Type=simple
ExecStart={executable} --foreground
Restart=on-failure
RestartSec=5
# Daemon handles SIGTERM via asyncio signal handler; 10s is plenty
TimeoutStopSec=10
Environment=PYTHONUNBUFFERED=1
# PATH captured at install time - needed for asciinema, context CLI, etc.
Environment=PATH={path}
# Disable filesystem isolation - daemon needs access to user's /tmp for:
# - Session logs in /tmp/session_logs/asciinema/
# - Socket in /tmp/llm-assistant-{{UID}}/
PrivateTmp=no

[Install]
WantedBy=default.target
"""


def get_unit_directory() -> Path:
    """Get the systemd user unit directory (~/.config/systemd/user/)."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "systemd" / "user"


def get_service_name() -> str:
    """Get the service unit name."""
    return "llm-assistant.service"


def get_executable() -> str:
    """Get the llm-assistant executable path for the service.

    Returns the wrapper script path if it exists, otherwise falls back
    to running as a Python module.
    """
    # Prefer the wrapper script (created by install-llm-tools.sh)
    wrapper = Path.home() / ".local" / "bin" / "llm-assistant"
    if wrapper.exists():
        return str(wrapper)

    # Fallback: run as Python module
    python_executable = sys.executable
    return f"{python_executable} -m llm_assistant"


def generate_service_unit() -> str:
    """Generate the .service unit file content.

    Captures the current PATH at generation time so that the systemd service
    can find tools like asciinema (in ~/.cargo/bin) and context CLI.
    """
    executable = get_executable()
    current_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    # Non-login installers (sudo'd setup scripts) often lack these in PATH.
    home = Path.home()
    path_parts = current_path.split(":")
    extras = [str(home / ".cargo" / "bin"), str(home / ".local" / "bin")]
    current_path = ":".join([p for p in extras if p not in path_parts] + path_parts)
    return SERVICE_UNIT_TEMPLATE.format(executable=executable, path=current_path)


def is_service_enabled() -> bool:
    """Check if the systemd service is enabled."""
    ok, _ = _run_systemctl("is-enabled", get_service_name())
    return ok


def is_service_active() -> bool:
    """Check if the systemd service is currently running."""
    ok, _ = _run_systemctl("is-active", get_service_name())
    return ok


def start_via_systemctl() -> bool:
    """Start the daemon via systemctl."""
    ok, _ = _run_systemctl("start", get_service_name())
    return ok


def _run_systemctl(*args, check: bool = False) -> Tuple[bool, str]:
    """Run a systemctl --user command.

    Args:
        *args: Arguments to pass to systemctl --user
        check: If True, print output on failure

    Returns:
        Tuple of (success, output)
    """
    try:
        result = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=10
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except FileNotFoundError:
        return False, "systemctl not found"
    except Exception as e:
        return False, str(e)


def install_service() -> bool:
    """Install and enable the systemd user service.

    Creates the service unit file, reloads systemd, and enables the service.

    Returns:
        True on success, False on failure.
    """
    service_name = get_service_name()
    unit_dir = get_unit_directory()
    service_path = unit_dir / service_name

    # Create unit directory if needed
    unit_dir.mkdir(parents=True, exist_ok=True)

    service_content = generate_service_unit()
    needs_update = (not service_path.exists()
                    or service_path.read_text() != service_content)
    if needs_update:
        print(f"Writing {service_path}")
        service_path.write_text(service_content)
        print("Reloading systemd daemon...")
        success, output = _run_systemctl("daemon-reload")
        if not success:
            print(f"Warning: daemon-reload failed: {output}")

    print(f"Enabling {service_name}...")
    success, output = _run_systemctl("enable", service_name)
    if not success:
        print(f"Warning: enable failed: {output}")
        return False

    # restart only when the unit actually changed, so no-op updates don't
    # churn a healthy daemon (and drop in-flight @ queries).
    is_active, _ = _run_systemctl("is-active", service_name)
    if needs_update or not is_active:
        print(f"Restarting {service_name}...")
        success, output = _run_systemctl("restart", service_name)
        if not success:
            print(f"Warning: restart failed: {output}")

    print(f"\nService installed: {service_path}")
    print("\nTo check status:")
    print(f"  systemctl --user status {service_name}")

    return True


def uninstall_service() -> bool:
    """Stop, disable, and remove the systemd user service.

    Returns:
        True on success, False on failure.
    """
    service_name = get_service_name()
    unit_dir = get_unit_directory()
    service_path = unit_dir / service_name

    # Stop the service
    print(f"Stopping {service_name}...")
    _run_systemctl("stop", service_name)  # Ignore errors - may not be running

    # Disable the service
    print(f"Disabling {service_name}...")
    _run_systemctl("disable", service_name)  # Ignore errors - may not be enabled

    # Remove the unit file
    if service_path.exists():
        print(f"Removing {service_path}")
        service_path.unlink()

    # Reload systemd daemon
    _run_systemctl("daemon-reload")

    print("\nService uninstalled.")
    return True
