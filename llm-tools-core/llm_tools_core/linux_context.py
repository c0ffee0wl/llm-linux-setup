"""Linux desktop context gathering utilities for GUI clients.

Provides functions to gather context from the desktop environment:
- Window title, WM_CLASS, PID (X11 only)
- Primary selection text (X11 only)
- Focused window working directory (X11 only)
- Screenshot capture (X11: maim, Wayland: gnome-screenshot)

Wayland graceful degradation:
- X11-specific functions return None on Wayland
- gather_context() returns partial dict (only session_type)
- Screenshot falls back to gnome-screenshot or similar

All functions use graceful fallbacks for sandboxed apps.

Used by:
- llm-guiassistant (GTK popup)
- Other GUI clients needing desktop context
"""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

# Selection size limit for security (prevents hangs on massive clipboard content)
MAX_SELECTION_BYTES = 100 * 1024  # 100KB hard limit
SUBPROCESS_TIMEOUT = 2.0  # 2-second timeout for xdotool/xclip/xprop


def is_x11() -> bool:
    """Check if running under X11 session.

    Returns:
        True if XDG_SESSION_TYPE is "x11"

    Examples:
        >>> is_x11()  # On X11
        True
    """
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "x11"


def is_wayland() -> bool:
    """Check if running under Wayland session.

    Returns:
        True if XDG_SESSION_TYPE is "wayland"

    Examples:
        >>> is_wayland()  # On Wayland
        True
    """
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def get_session_type() -> str:
    """Get current session type.

    Returns:
        Session type string: "x11", "wayland", "tty", or "unknown"

    Examples:
        >>> get_session_type()
        'x11'
    """
    return os.environ.get("XDG_SESSION_TYPE", "unknown").lower()


def get_focused_window_id() -> Optional[str]:
    """Get focused window ID via xdotool.

    Returns:
        Window ID string (e.g., "0x2a00003") or None on Wayland/error

    Examples:
        >>> get_focused_window_id()
        '0x2a00003'
    """
    if not is_x11():
        return None

    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT
        )
        if result.returncode == 0:
            window_id = result.stdout.decode('utf-8').strip()
            # Convert decimal to hex for consistency
            try:
                return hex(int(window_id))
            except ValueError:
                return window_id
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_wm_class(window_id: Optional[str] = None) -> Optional[str]:
    """Get WM_CLASS via xprop.

    Args:
        window_id: X11 window ID (uses focused window if None)

    Returns:
        WM_CLASS string (e.g., "burpsuite") or None on Wayland/error

    Examples:
        >>> get_wm_class()
        'burpsuite'
    """
    if not is_x11():
        return None

    if window_id is None:
        window_id = get_focused_window_id()
        if window_id is None:
            return None

    try:
        # Convert hex to decimal for xprop
        if window_id.startswith('0x'):
            window_id = str(int(window_id, 16))

        result = subprocess.run(
            ["xprop", "-id", window_id, "WM_CLASS"],
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT
        )
        if result.returncode == 0:
            output = result.stdout.decode('utf-8')
            # Parse: WM_CLASS(STRING) = "instance", "class"
            if '=' in output:
                parts = output.split('=', 1)[1].strip()
                # Extract the class name (second quoted string)
                if '"' in parts:
                    quoted = [p.strip('" ') for p in parts.split(',')]
                    if len(quoted) >= 2:
                        return quoted[1]  # Class name
                    elif quoted:
                        return quoted[0]  # Fall back to instance
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_window_title(window_id: Optional[str] = None) -> Optional[str]:
    """Get window title via xdotool.

    Args:
        window_id: X11 window ID (uses focused window if None)

    Returns:
        Window title string or None on Wayland/error

    Examples:
        >>> get_window_title()
        'Repeater - https://api.example.com'
    """
    if not is_x11():
        return None

    try:
        cmd = ["xdotool", "getactivewindow", "getwindowname"]
        if window_id:
            # Convert hex to decimal for xdotool
            if window_id.startswith('0x'):
                window_id = str(int(window_id, 16))
            cmd = ["xdotool", "getwindowname", window_id]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT
        )
        if result.returncode == 0:
            return result.stdout.decode('utf-8').strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_focused_window_pid(window_id: Optional[str] = None) -> Optional[int]:
    """Get PID of focused window via xdotool.

    Args:
        window_id: X11 window ID (uses focused window if None)

    Returns:
        Process ID or None on Wayland/error

    Examples:
        >>> get_focused_window_pid()
        12345
    """
    if not is_x11():
        return None

    try:
        cmd = ["xdotool", "getactivewindow", "getwindowpid"]
        if window_id:
            if window_id.startswith('0x'):
                window_id = str(int(window_id, 16))
            cmd = ["xdotool", "getwindowpid", window_id]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT
        )
        if result.returncode == 0:
            return int(result.stdout.decode('utf-8').strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        pass
    return None


def get_cwd(pid: Optional[int] = None) -> Optional[str]:
    """Get working directory via /proc/PID/cwd.

    Fails gracefully for sandboxed apps (Snap/Flatpak).

    Args:
        pid: Process ID (uses focused window's PID if None)

    Returns:
        Working directory path or None on error

    Examples:
        >>> get_cwd(12345)
        '/home/kali/pentests/acme-corp'
    """
    if pid is None:
        pid = get_focused_window_pid()
        if pid is None:
            return None

    try:
        cwd_path = Path(f"/proc/{pid}/cwd")
        if cwd_path.exists():
            return str(cwd_path.resolve())
    except (OSError, PermissionError):
        # Sandboxed apps may block /proc access
        pass
    return None


def get_cmdline(pid: Optional[int] = None) -> Optional[str]:
    """Get command line via /proc/PID/cmdline.

    Fails gracefully for sandboxed apps (Snap/Flatpak).

    Args:
        pid: Process ID (uses focused window's PID if None)

    Returns:
        Command line string or None on error

    Examples:
        >>> get_cmdline(12345)
        'java -jar burpsuite.jar --project-file=acme.burp'
    """
    if pid is None:
        pid = get_focused_window_pid()
        if pid is None:
            return None

    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.exists():
            content = cmdline_path.read_bytes()
            # cmdline is null-separated
            return content.decode('utf-8', errors='replace').replace('\x00', ' ').strip()
    except (OSError, PermissionError):
        # Sandboxed apps may block /proc access
        pass
    return None


def get_selection(selection: str = "PRIMARY") -> Tuple[Optional[str], bool]:
    """Get selection text via xclip.

    Returns (text, was_truncated) tuple.
    Returns (None, False) on Wayland (would need wl-paste).

    Security considerations:
    - Truncates at MAX_SELECTION_BYTES to prevent hangs on massive selections
    - Handles binary data gracefully (replaces invalid UTF-8 with replacement char)

    Args:
        selection: X11 selection name ("PRIMARY", "CLIPBOARD", "SECONDARY")

    Returns:
        Tuple of (text, was_truncated). was_truncated is True if text was cut.

    Examples:
        >>> get_selection()
        ('GET /api/users?id=1 HTTP/1.1', False)

        >>> get_selection("CLIPBOARD")
        ('very long content...', True)  # Truncated
    """
    if not is_x11():
        return None, False

    try:
        result = subprocess.run(
            ["xclip", "-selection", selection.lower(), "-o"],
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT
        )

        if result.returncode != 0:
            return None, False

        # Handle binary data: decode with errors='replace'
        raw = result.stdout
        was_truncated = len(raw) > MAX_SELECTION_BYTES
        if was_truncated:
            raw = raw[:MAX_SELECTION_BYTES]

        # Safe UTF-8 decode (replaces invalid bytes with U+FFFD)
        text = raw.decode('utf-8', errors='replace')
        return text, was_truncated

    except subprocess.TimeoutExpired:
        return None, False
    except (FileNotFoundError, OSError):
        return None, False


def capture_screenshot(
    mode: str = "window",
    output_path: Optional[str] = None
) -> Optional[str]:
    """Capture screenshot.

    Args:
        mode: Capture mode - "window" (active), "region" (select), "full" (screen)
        output_path: Custom path or auto-generated temp file

    Returns:
        Path to screenshot file or None on failure

    X11: Uses maim for all modes
    Wayland: Uses gnome-screenshot (full/window) or slurp+grim (region)

    Examples:
        >>> capture_screenshot("window")
        '/tmp/llm-guiassistant-screenshot-1234567890.png'

        >>> capture_screenshot("region", "/tmp/my-screenshot.png")
        '/tmp/my-screenshot.png'
    """
    if output_path is None:
        import time
        timestamp = int(time.time() * 1000)
        output_path = f"/tmp/llm-guiassistant-screenshot-{timestamp}.png"

    try:
        if is_x11():
            # Use maim on X11
            if mode == "window":
                # Capture active window
                window_id = get_focused_window_id()
                if window_id:
                    # Convert hex to decimal
                    if window_id.startswith('0x'):
                        window_id = str(int(window_id, 16))
                    cmd = ["maim", "-i", window_id, output_path]
                else:
                    cmd = ["maim", "-i", "$(xdotool getactivewindow)", output_path]
            elif mode == "region":
                # User selects region
                cmd = ["maim", "-s", output_path]
            else:  # full
                cmd = ["maim", output_path]

            result = subprocess.run(cmd, timeout=30.0)  # Longer timeout for user interaction
            if result.returncode == 0 and Path(output_path).exists():
                return output_path

        elif is_wayland():
            # Wayland fallback
            if mode == "window":
                # gnome-screenshot for window
                result = subprocess.run(
                    ["gnome-screenshot", "-w", "-f", output_path],
                    timeout=30.0
                )
            elif mode == "region":
                # Try slurp + grim first (wlroots-based compositors)
                try:
                    region = subprocess.run(
                        ["slurp"],
                        capture_output=True,
                        timeout=30.0
                    )
                    if region.returncode == 0:
                        geometry = region.stdout.decode().strip()
                        result = subprocess.run(
                            ["grim", "-g", geometry, output_path],
                            timeout=10.0
                        )
                    else:
                        # Fallback to gnome-screenshot area
                        result = subprocess.run(
                            ["gnome-screenshot", "-a", "-f", output_path],
                            timeout=30.0
                        )
                except FileNotFoundError:
                    # Fallback to gnome-screenshot area
                    result = subprocess.run(
                        ["gnome-screenshot", "-a", "-f", output_path],
                        timeout=30.0
                    )
            else:  # full
                result = subprocess.run(
                    ["gnome-screenshot", "-f", output_path],
                    timeout=30.0
                )

            if result.returncode == 0 and Path(output_path).exists():
                return output_path

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


def gather_context() -> dict:
    """Gather all context from current desktop state.

    Returns dict with: session_type, app_class, window_title, working_dir,
    command_line, selection, selection_truncated, window_id, pid

    On Wayland: Most fields will be None except session_type.

    Returns:
        Context dictionary with desktop state

    Examples:
        >>> gather_context()
        {
            'session_type': 'x11',
            'app_class': 'burpsuite',
            'window_title': 'Repeater - https://api.example.com',
            'selection': 'GET /api/users?id=1 HTTP/1.1',
            'selection_truncated': False,
            'working_dir': '/home/kali/pentests/acme-corp',
            'command_line': 'java -jar burpsuite.jar',
            'window_id': '0x2a00003',
            'pid': 12345
        }
    """
    selection_text, was_truncated = get_selection()
    window_id = get_focused_window_id()
    pid = get_focused_window_pid(window_id)

    return {
        'session_type': get_session_type(),
        'app_class': get_wm_class(window_id),
        'window_title': get_window_title(window_id),
        'selection': selection_text,
        'selection_truncated': was_truncated,
        'working_dir': get_cwd(pid),
        'command_line': get_cmdline(pid),
        'window_id': window_id,
        'pid': pid,
    }


def format_context_for_llm(ctx: dict) -> str:
    """Format context dict as text for LLM prompt.

    Skips None/empty values gracefully. Creates a structured text block
    suitable for prepending to user queries.

    Args:
        ctx: Context dictionary from gather_context()

    Returns:
        Formatted string for LLM context

    Examples:
        >>> ctx = gather_context()
        >>> print(format_context_for_llm(ctx))
        Application: burpsuite
        Window: Repeater - https://api.example.com
        Working directory: /home/kali/pentests/acme-corp
        Selected text (89 chars):
        ```
        GET /api/users?id=1 HTTP/1.1
        Host: api.example.com
        ```
    """
    lines = []

    if ctx.get('app_class'):
        lines.append(f"Application: {ctx['app_class']}")

    if ctx.get('window_title'):
        lines.append(f"Window: {ctx['window_title']}")

    if ctx.get('working_dir'):
        lines.append(f"Working directory: {ctx['working_dir']}")

    if ctx.get('command_line'):
        lines.append(f"Command: {ctx['command_line']}")

    selection = ctx.get('selection')
    if selection:
        char_count = len(selection)
        truncation_note = " (TRUNCATED)" if ctx.get('selection_truncated') else ""
        lines.append(f"Selected text ({char_count} chars){truncation_note}:")
        lines.append("```")
        lines.append(selection.strip())
        lines.append("```")

    return '\n'.join(lines)
