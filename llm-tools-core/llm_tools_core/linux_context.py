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
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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


def get_visible_window_ids() -> List[str]:
    """Get all visible window IDs via xdotool.

    Returns:
        List of window ID strings (e.g., ["0x2a00003", "0x1800005"]) or empty list

    Examples:
        >>> get_visible_window_ids()
        ['0x2a00003', '0x1800005', '0x2200007']
    """
    if not is_x11():
        return []

    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", ""],
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT
        )
        if result.returncode == 0:
            window_ids = []
            for wid in result.stdout.decode('utf-8').strip().split('\n'):
                if wid:
                    try:
                        window_ids.append(hex(int(wid)))
                    except ValueError:
                        window_ids.append(wid)
            return window_ids
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return []


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


def gather_all_visible_windows() -> List[Dict]:
    """Gather info for all visible windows.

    Returns:
        List of window info dicts with: window_id, app_class, window_title, pid, cwd

    Examples:
        >>> gather_all_visible_windows()
        [
            {'window_id': '0x2a00003', 'app_class': 'firefox', 'window_title': 'GitHub', 'pid': 1234, 'cwd': '/home/user'},
            {'window_id': '0x1800005', 'app_class': 'Terminator', 'window_title': '~/projects', 'pid': 5678, 'cwd': '/home/user/projects'}
        ]
    """
    windows = []
    for wid in get_visible_window_ids():
        pid = get_focused_window_pid(wid)
        windows.append({
            'window_id': wid,
            'app_class': get_wm_class(wid),
            'window_title': get_window_title(wid),
            'pid': pid,
            'cwd': get_cwd(pid),
        })
    return windows


def gather_context() -> dict:
    """Gather all context from current desktop state.

    Returns dict with:
    - session_type: Current session type (x11, wayland, etc.)
    - focused: Dict with focused window info (window_id, app_class, window_title, pid, cwd)
    - visible_windows: List of all visible window dicts
    - selection: Primary selection text
    - selection_truncated: Whether selection was truncated

    Legacy fields (for backward compat): app_class, window_title, working_dir,
    command_line, window_id, pid - these mirror the focused window data.

    On Wayland: Most fields will be None except session_type.

    Returns:
        Context dictionary with desktop state

    Examples:
        >>> gather_context()
        {
            'session_type': 'x11',
            'focused': {
                'window_id': '0x2a00003',
                'app_class': 'burpsuite',
                'window_title': 'Repeater - https://api.example.com',
                'pid': 12345,
                'cwd': '/home/kali/pentests/acme-corp'
            },
            'visible_windows': [...],
            'selection': 'GET /api/users?id=1 HTTP/1.1',
            'selection_truncated': False,
            # Legacy fields for backward compat:
            'app_class': 'burpsuite',
            'window_title': 'Repeater - https://api.example.com',
            'working_dir': '/home/kali/pentests/acme-corp',
            'command_line': 'java -jar burpsuite.jar',
            'window_id': '0x2a00003',
            'pid': 12345
        }
    """
    selection_text, was_truncated = get_selection()
    window_id = get_focused_window_id()
    pid = get_focused_window_pid(window_id)

    # Build focused window info
    focused = {
        'window_id': window_id,
        'app_class': get_wm_class(window_id),
        'window_title': get_window_title(window_id),
        'pid': pid,
        'cwd': get_cwd(pid),
    }

    return {
        'session_type': get_session_type(),
        # New structured fields
        'focused': focused,
        'visible_windows': gather_all_visible_windows(),
        'selection': selection_text,
        'selection_truncated': was_truncated,
        # Legacy fields for backward compat
        'app_class': focused['app_class'],
        'window_title': focused['window_title'],
        'working_dir': focused['cwd'],
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


def format_gui_context(
    ctx: dict,
    new_window_hashes: Set[str],
    is_first: bool,
    include_selection: bool = True
) -> str:
    """Format GUI context as plain text with deduplication support.

    Shows all visible windows equally (chat window is always focused, so
    treating it specially is not useful). Uses concise format with
    deduplication for incremental updates.

    Args:
        ctx: Context dictionary from gather_context()
        new_window_hashes: Set of hashes for newly appeared windows
        is_first: Whether this is the first message in session
        include_selection: Whether to include selection text

    Returns:
        Formatted string wrapped in <gui_context> tags

    Examples:
        >>> ctx = gather_context()
        >>> print(format_gui_context(ctx, set(), is_first=True))
        <gui_context>
        Desktop windows:
        - firefox: "GitHub" [0x2a00003] cwd:/home/user
        - Terminator: "~/projects" [0x1800005] cwd:/home/user/projects
        - Code: "project - VSCode" [0x2200007] cwd:/home/user/projects/myapp

        Selection: "selected text"
        </gui_context>
    """
    from .hashing import hash_window  # Import here to avoid circular import

    lines = []
    visible = ctx.get('visible_windows', [])

    def format_window(win: dict) -> str:
        """Format a single window concisely."""
        app = win.get('app_class') or 'unknown'
        title = win.get('window_title') or ''
        wid = win.get('window_id', '')
        # Truncate long titles
        if len(title) > 60:
            title = title[:57] + '...'
        cwd = win.get('cwd')
        cwd_suffix = f" cwd:{cwd}" if cwd else ""
        return f"- {app}: \"{title}\" [{wid}]{cwd_suffix}"

    if is_first:
        # Full context on first message - all visible windows
        if visible:
            lines.append("Desktop windows:")
            for win in visible:
                lines.append(format_window(win))
        else:
            lines.append("No visible windows detected")

    else:
        # Incremental update: only show new/changed windows
        if new_window_hashes:
            lines.append("New/changed windows:")
            for win in visible:
                if hash_window(win) in new_window_hashes:
                    lines.append(format_window(win))
        # If no new windows but something changed (e.g., window closed),
        # caller handles "[Desktop context unchanged]" case

    # Add selection if present
    if include_selection and ctx.get('selection'):
        if lines:
            lines.append("")
        selection = ctx['selection']
        truncated = " (truncated)" if ctx.get('selection_truncated') else ""
        # Show first 200 chars of selection
        preview = selection[:200] + ('...' if len(selection) > 200 else '')
        lines.append(f"Selection{truncated}: \"{preview}\"")

    if not lines:
        return "<gui_context>[No desktop context]</gui_context>"

    content = '\n'.join(lines)
    return f"<gui_context>\n{content}\n</gui_context>"
