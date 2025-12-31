"""
Terminator Assistant Plugin

Provides terminal content capture and command injection capabilities for
llm-assistant. This plugin bridges the standalone assistant application
with Terminator's VTE terminals.

Capabilities:
- Capture visible scrollback content from VTE terminals (text)
- Capture terminal screenshots (PNG) - works with TUI apps (htop, vim, less)
- Send commands to terminals via feed_child
- Get terminal metadata (UUIDs, titles, focus state)
- Content caching for performance

Author: c0ffee0wl
License: GPL v2 only
"""

import time
import base64
import heapq
import os
import math
import threading
import traceback
import gi
gi.require_version('Vte', '2.91')  # noqa: E402
gi.require_version('Gdk', '3.0')  # noqa: E402
from gi.repository import Vte, Gdk  # noqa: E402
import terminatorlib.plugin as plugin  # noqa: E402
from terminatorlib.terminator import Terminator  # noqa: E402
from terminatorlib.util import dbg, err  # noqa: E402
import dbus.service  # noqa: E402
from dbus.exceptions import DBusException  # noqa: E402

# Import prompt detection for TUI heuristic (shell prompt = not TUI)
try:
    from llm_tools_core import PromptDetector
    PROMPT_DETECTOR_AVAILABLE = True
except ImportError:
    try:
        from llm_tools.prompt_detection import PromptDetector  # legacy fallback
        PROMPT_DETECTOR_AVAILABLE = True
    except ImportError:
        PROMPT_DETECTOR_AVAILABLE = False

# D-Bus service constants
PLUGIN_BUS_NAME = 'net.tenshu.Terminator2.Assistant'
PLUGIN_BUS_PATH = '/net/tenshu/Terminator2/Assistant'

# Plugin version for diagnostics
PLUGIN_VERSION = "4.0-signals"

# Cache size limit to prevent unbounded memory growth
CACHE_MAX_SIZE = 100

AVAILABLE = ['TerminatorAssistant']

# Pre-normalized special key mappings (lowercase keys for O(1) lookup)
SPECIAL_KEYS = {
    # Basic navigation and editing
    'enter': b'\r',
    'return': b'\r',
    'escape': b'\x1b',
    'esc': b'\x1b',
    'tab': b'\t',
    'backspace': b'\x7f',
    'delete': b'\x1b[3~',
    'insert': b'\x1b[2~',
    'home': b'\x1b[H',
    'end': b'\x1b[F',
    'pageup': b'\x1b[5~',
    'pagedown': b'\x1b[6~',
    'up': b'\x1b[A',
    'down': b'\x1b[B',
    'right': b'\x1b[C',
    'left': b'\x1b[D',
    'space': b' ',
    # Function keys (F1-F12) - VT sequences
    'f1': b'\x1bOP',
    'f2': b'\x1bOQ',
    'f3': b'\x1bOR',
    'f4': b'\x1bOS',
    'f5': b'\x1b[15~',
    'f6': b'\x1b[17~',
    'f7': b'\x1b[18~',
    'f8': b'\x1b[19~',
    'f9': b'\x1b[20~',
    'f10': b'\x1b[21~',
    'f11': b'\x1b[23~',
    'f12': b'\x1b[24~',
    # Control keys (ASCII control codes)
    'ctrl+a': b'\x01',
    'ctrl+b': b'\x02',
    'ctrl+c': b'\x03',
    'ctrl+d': b'\x04',
    'ctrl+e': b'\x05',
    'ctrl+f': b'\x06',
    'ctrl+g': b'\x07',
    'ctrl+h': b'\x08',
    'ctrl+i': b'\t',   # Same as Tab
    'ctrl+j': b'\n',   # Same as Enter
    'ctrl+k': b'\x0b',
    'ctrl+l': b'\x0c',
    'ctrl+m': b'\r',   # Carriage return
    'ctrl+n': b'\x0e',
    'ctrl+o': b'\x0f',
    'ctrl+p': b'\x10',
    'ctrl+q': b'\x11',
    'ctrl+r': b'\x12',
    'ctrl+s': b'\x13',
    'ctrl+t': b'\x14',
    'ctrl+u': b'\x15',
    'ctrl+v': b'\x16',
    'ctrl+w': b'\x17',
    'ctrl+x': b'\x18',
    'ctrl+y': b'\x19',
    'ctrl+z': b'\x1a',
    # Alt/Meta combinations (ESC prefix)
    'alt+b': b'\x1bb',
    'alt+f': b'\x1bf',
    'alt+d': b'\x1bd',
    'alt+backspace': b'\x1b\x7f',
    'alt+left': b'\x1b[1;3D',
    'alt+right': b'\x1b[1;3C',
    # Shift combinations
    'shift+tab': b'\x1b[Z',
}


class TerminatorAssistant(plugin.Plugin, dbus.service.Object):
    """Plugin providing terminal content capture for llm-assistant via D-Bus"""

    capabilities = ['assistant_bridge']

    def __init__(self):
        """Initialize plugin and D-Bus service"""
        plugin.Plugin.__init__(self)

        # Try to register D-Bus service (only works when running inside Terminator)
        self.bus_name = None
        try:
            bus = dbus.SessionBus()
            # Claim the bus name - don't allow replacement by other processes for security
            # replace_existing=True only takes over from OTHER Terminator instances (same plugin)
            # allow_replacement=False prevents malicious processes from hijacking our service
            self.bus_name = dbus.service.BusName(
                PLUGIN_BUS_NAME,
                bus=bus,
                allow_replacement=False,  # Security: prevent hijacking by other processes
                replace_existing=True,    # Take over from crashed/old plugin instances
                do_not_queue=True
            )
            dbus.service.Object.__init__(self, self.bus_name, PLUGIN_BUS_PATH)
            dbg('TerminatorAssistant: D-Bus service registered at %s' % PLUGIN_BUS_NAME)
        except (DBusException, KeyError) as e:
            # KeyError occurs when object path handler is already registered (plugin loaded multiple times)
            # DBusException occurs when D-Bus is not available or bus name cannot be claimed
            dbg('TerminatorAssistant: Could not register D-Bus service: %s (continuing anyway)' % e)
            # Continue without D-Bus (fallback for when loaded by external process or already loaded)

        self.terminator = Terminator()

        # Content caching to avoid repeated VTE queries
        self.content_cache = {}  # uuid -> content
        self.last_capture = {}   # uuid -> timestamp
        self.cache_ttl = 0.5     # Cache valid for 0.5 seconds

        # TUI detection caching (separate from content cache for efficiency)
        self.tui_cache = {}      # uuid -> is_tui (bool)
        self.tui_cache_time = {} # uuid -> timestamp
        self.tui_cache_ttl = 0.5 # TUI detection cache valid for 0.5 seconds

        # Thread-safe lock for cache operations (D-Bus calls may come from different threads)
        self.cache_lock = threading.Lock()

        # Content change signal subscriptions (Phase 4)
        # Key: terminal_uuid, Value: dict with 'handler_id', 'ref_count', 'vte'
        self.content_watchers = {}

        dbg('TerminatorAssistant initialized')

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='(ii)')
    def get_cursor_position(self, terminal_uuid):
        """
        Get cursor position in scrollback buffer coordinates.

        Args:
            terminal_uuid: UUID of terminal

        Returns:
            Tuple of (column, row) in scrollback buffer coordinates.
            Returns (-1, -1) on error.
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return (-1, -1)

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return (-1, -1)

            col, row = vte.get_cursor_position()
            dbg(f'Cursor position for {terminal_uuid}: col={col}, row={row}')
            return (col, row)

        except Exception as e:
            err(f'Error getting cursor position: {e}')
            return (-1, -1)

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='si', out_signature='s')
    def capture_terminal_content(self, terminal_uuid, lines=-1):
        """
        Capture visible scrollback content from a VTE terminal.

        Args:
            terminal_uuid: UUID of terminal to capture (string format)
            lines: Number of lines to capture (default -1 = auto-detect visible viewport)

        Returns:
            String containing terminal content, or error message
        """
        try:
            # Handle D-Bus -1 as None for auto-detect
            if lines == -1:
                lines = None
            # Find terminal by UUID with validation
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return f"ERROR: Terminal with UUID {terminal_uuid} not found"

            # Get VTE widget with validation
            try:
                vte = terminal.get_vte()
                if not vte:
                    err(f'VTE not accessible for terminal {terminal_uuid}')
                    return f"ERROR: Could not access VTE for terminal {terminal_uuid}"
            except Exception as e:
                err(f'Error accessing VTE: {e}')
                return f"ERROR: VTE access failed: {str(e)}"

            # Auto-detect visible viewport size if not specified
            if lines is None:
                lines = vte.get_row_count()
                dbg(f'Auto-detected {lines} visible rows for {terminal_uuid}')

            # Check cache after determining lines (thread-safe)
            cache_key = f"{terminal_uuid}:{lines}"
            with self.cache_lock:
                if cache_key in self.content_cache:
                    if time.time() - self.last_capture.get(cache_key, 0) < self.cache_ttl:
                        dbg(f'Returning cached content for {terminal_uuid}')
                        return self.content_cache[cache_key]

            # Get terminal width for full-width capture
            try:
                term_width = vte.get_column_count()
            except Exception as e:
                err(f'Error getting terminal dimensions: {e}')
                return "ERROR: Could not access terminal state"

            # Calculate capture range from current viewport position in buffer
            # vadjustment tells us where the visible screen is in the buffer coordinates.
            # This is CRITICAL for alternate screen (TUI apps): the content is at the
            # buffer position indicated by scroll_pos, not at row 0!
            # Use `lines` parameter (already set to viewport height if auto-detected)
            try:
                vadj = vte.get_vadjustment()
                if vadj:
                    scroll_pos = vadj.get_value()
                    # Calculate capture range in buffer coordinates using requested lines
                    # Use math.floor consistently to ensure proper row alignment
                    capture_start = math.floor(scroll_pos)
                    capture_end = math.floor(scroll_pos + lines) - 1
                    dbg(f'Using vadjustment: scroll_pos={scroll_pos}, capture range=[{capture_start}, {capture_end}]')
                else:
                    # Fallback if vadjustment returns None: use top of buffer
                    dbg('vadjustment returned None, using top of buffer fallback')
                    capture_start = 0
                    capture_end = lines - 1
                    dbg(f'Using fallback: range=[{capture_start}, {capture_end}]')
            except Exception as e:
                # Exception during vadjustment access
                dbg(f'vadjustment exception: {type(e).__name__}: {e}')
                capture_start = 0
                capture_end = lines - 1

            # DEBUG: Log capture parameters
            dbg(f'CAPTURE DEBUG for {terminal_uuid}:')
            dbg('  Capture mode: viewport-based')
            dbg(f'  Terminal width: {term_width}')
            dbg(f'  Requested lines: {lines}')
            dbg(f'  Calculated range: start_row={capture_start}, end_row={capture_end}')
            dbg(f'  Range span: {capture_end - capture_start + 1} rows')

            # Version-aware content capture with error handling
            content = None
            vte_version = Vte.get_minor_version()

            if vte_version >= 72:
                # Modern VTE: use get_text_range_format
                dbg(f'Using get_text_range_format (VTE {vte_version})')
                try:
                    result = vte.get_text_range_format(
                        Vte.Format.TEXT,
                        capture_start, 0,           # start row, start col
                        capture_end, term_width - 1     # end row, end col (full width)
                    )
                    # Verify it's a tuple and extract text
                    # Handle edge cases: None, empty tuple, or direct string
                    if isinstance(result, tuple):
                        content = result[0] if result else ''
                    elif result is None:
                        content = ''
                    else:
                        content = str(result)
                except Exception as e:
                    err(f'VTE 72+ capture failed: {e}')
                    return f"ERROR: Content capture failed: {str(e)}"
            else:
                # Older VTE: use get_text_range with lambda
                # Returns tuple (text, attributes) - must extract [0]
                dbg(f'Using get_text_range (VTE {vte_version})')
                try:
                    result = vte.get_text_range(
                        capture_start, 0,           # start row, start col
                        capture_end, term_width - 1,    # end row, end col (full width)
                        lambda *a: True  # Include all cells
                    )
                    # Extract text from tuple (same pattern as logger.py, remote.py)
                    # Handle edge cases: None, empty tuple, or direct string
                    if isinstance(result, tuple):
                        content = result[0] if result else ''
                    elif result is None:
                        content = ''
                    else:
                        content = str(result) if result else ''
                except Exception as e:
                    err(f'VTE legacy capture failed: {e}')
                    return f"ERROR: Content capture failed: {str(e)}"

            if content is None:
                err('Content capture returned None')
                return f"ERROR: Failed to capture content from terminal {terminal_uuid}"

            # Cache the result (thread-safe)
            with self.cache_lock:
                self.content_cache[cache_key] = content
                self.last_capture[cache_key] = time.time()
                self._evict_old_cache_entries()

            dbg(f'Captured {len(content)} characters from {terminal_uuid}')
            return content

        except Exception as e:
            err(f'TerminatorAssistant: Error capturing terminal content: {e}')
            return f"ERROR: {str(e)}"

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='si', out_signature='s')
    def capture_from_row(self, terminal_uuid, start_row):
        """
        Capture terminal content from start_row to current cursor position.

        Used for capturing full command output that may exceed the viewport.

        Args:
            terminal_uuid: UUID of terminal to capture (string format)
            start_row: Row number in scrollback buffer to start capture from

        Returns:
            String containing terminal content, or error message
        """
        try:
            # Validate start_row parameter
            if start_row < 0:
                err(f'Invalid start_row: {start_row} (must be >= 0)')
                return f"ERROR: start_row must be non-negative, got {start_row}"

            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return f"ERROR: Terminal with UUID {terminal_uuid} not found"

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return f"ERROR: Could not access VTE for terminal {terminal_uuid}"

            term_width = vte.get_column_count()
            term_height = vte.get_row_count()

            # Calculate end row: use cursor position (where command output ends)
            # This allows dynamic expansion to capture full command output
            try:
                _, cursor_row = vte.get_cursor_position()
                if cursor_row >= 0:
                    capture_end = cursor_row
                else:
                    # Fallback if cursor position unavailable
                    vadj = vte.get_vadjustment()
                    if vadj:
                        scroll_pos = vadj.get_value()
                        capture_end = math.floor(scroll_pos + term_height) - 1
                    else:
                        capture_end = start_row + term_height
            except Exception:
                # Ultimate fallback
                try:
                    vadj = vte.get_vadjustment()
                    if vadj:
                        scroll_pos = vadj.get_value()
                        capture_end = math.floor(scroll_pos + term_height) - 1
                    else:
                        capture_end = start_row + term_height
                except Exception:
                    capture_end = start_row + term_height

            dbg(f'capture_from_row: start={start_row}, end={capture_end}, span={capture_end - start_row + 1} rows')

            # Version-aware content capture
            vte_version = Vte.get_minor_version()

            if vte_version >= 72:
                result = vte.get_text_range_format(
                    Vte.Format.TEXT,
                    start_row, 0,
                    capture_end, term_width - 1
                )
                content = result[0] if isinstance(result, tuple) else (result or '')
            else:
                result = vte.get_text_range(
                    start_row, 0,
                    capture_end, term_width - 1,
                    lambda *a: True
                )
                content = result[0] if isinstance(result, tuple) else (result or '')

            dbg(f'capture_from_row: captured {len(content)} characters')
            return content

        except Exception as e:
            err(f'TerminatorAssistant: Error in capture_from_row: {e}')
            return f"ERROR: {str(e)}"

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='s')
    def capture_terminal_screenshot(self, terminal_uuid):
        """
        Capture a screenshot of a VTE terminal as a PNG image.

        This method works for ALL terminal content, including TUI applications
        that use alternate screen buffers (htop, vim, less, etc.).

        Args:
            terminal_uuid: UUID of terminal to screenshot (string format)

        Returns:
            Base64-encoded PNG image data, or error message starting with "ERROR:"
        """
        try:
            # Find terminal by UUID
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return f"ERROR: Terminal with UUID {terminal_uuid} not found"

            # Get VTE widget
            try:
                vte = terminal.get_vte()
                if not vte:
                    err(f'VTE not accessible for terminal {terminal_uuid}')
                    return f"ERROR: Could not access VTE for terminal {terminal_uuid}"
            except Exception as e:
                err(f'Error accessing VTE: {e}')
                return f"ERROR: VTE access failed: {str(e)}"

            # Get the widget's GdkWindow (the actual rendered window)
            gdk_window = vte.get_window()
            if not gdk_window:
                err(f'GdkWindow not available for terminal {terminal_uuid}')
                return "ERROR: Terminal window not realized (widget not visible)"

            # Get widget dimensions
            width = vte.get_allocated_width()
            height = vte.get_allocated_height()

            dbg(f'Screenshot capture for {terminal_uuid}: {width}x{height}px')

            # Capture screenshot as pixbuf
            pixbuf = Gdk.pixbuf_get_from_window(gdk_window, 0, 0, width, height)

            if not pixbuf:
                err(f'Failed to capture pixbuf from terminal {terminal_uuid}')
                return "ERROR: Screenshot capture failed (pixbuf is None)"

            # Convert pixbuf to PNG in memory (avoids disk I/O)
            try:
                success, buffer = pixbuf.save_to_bufferv("png", [], [])
                if not success:
                    err(f'Failed to encode pixbuf as PNG for terminal {terminal_uuid}')
                    return "ERROR: Failed to encode screenshot as PNG"

                base64_data = base64.b64encode(buffer).decode('utf-8')

                dbg(f'Screenshot captured: {len(base64_data)} base64 chars ({len(buffer)} bytes PNG)')
                return base64_data

            except Exception as e:
                err(f'Error encoding screenshot: {e}')
                return f"ERROR: Failed to encode screenshot: {str(e)}"

        except Exception as e:
            err(f'TerminatorAssistant: Error capturing screenshot: {e}')
            return f"ERROR: {str(e)}"

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='ssb', out_signature='b')
    def send_keys_to_terminal(self, terminal_uuid, text, execute=True):
        """
        Send keys/text to a terminal.

        Args:
            terminal_uuid: UUID of terminal to send keys to
            text: Text to send
            execute: If True, append newline to execute command

        Returns:
            True on success, False on error
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return False

            vte = terminal.get_vte()
            if not vte:
                err(f'Could not access VTE for terminal {terminal_uuid}')
                return False

            # Feed text to VTE (convert to bytes)
            vte.feed_child(text.encode('utf-8'))

            # Optionally execute by sending carriage return (Enter key)
            if execute:
                vte.feed_child(b'\r')

            dbg(f'Sent {len(text)} characters to {terminal_uuid} (execute={execute})')
            return True

        except Exception as e:
            err(f'TerminatorAssistant: Error sending keys: {e}')
            return False

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='ss', out_signature='b')
    def send_keypress_to_terminal(self, terminal_uuid, keypress):
        """
        Send a keypress to a terminal, with support for special keys.
        Does NOT automatically execute (no newline appended).

        Args:
            terminal_uuid: UUID of terminal to send keypress to
            keypress: Keypress to send (can be text or special key name)
                     Special keys: Enter, Escape, Tab, Backspace,
                                   Ctrl+C, Ctrl+D, Ctrl+Z, Ctrl+L,
                                   Up, Down, Left, Right, Home, End,
                                   PageUp, PageDown, Delete, Insert

        Returns:
            True on success, False on error
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return False

            vte = terminal.get_vte()
            if not vte:
                err(f'Could not access VTE for terminal {terminal_uuid}')
                return False

            # O(1) lookup using module-level SPECIAL_KEYS constant (case-insensitive)
            keypress_bytes = SPECIAL_KEYS.get(keypress.lower())
            if keypress_bytes is not None:
                dbg(f'Mapped special key "{keypress}" to escape sequence')
            else:
                # Not a special key, send as literal text
                keypress_bytes = keypress.encode('utf-8')

            # Feed keypress to VTE (NO automatic newline)
            vte.feed_child(keypress_bytes)

            dbg(f'Sent keypress "{keypress}" to {terminal_uuid}')
            return True

        except Exception as e:
            err(f'TerminatorAssistant: Error sending keypress: {e}')
            return False

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='', out_signature='aa{ss}')
    def get_all_terminals_metadata(self):
        """
        Get metadata for all terminals in current Terminator instance.

        Returns:
            List of dicts with terminal information (all values as strings for D-Bus):
            - uuid: Terminal UUID (string)
            - title: Terminal title
            - focused: "True" or "False" indicating if terminal has focus
            - cwd: Current working directory
        """
        terminals_info = []

        try:
            dbg(f'DEBUG: self.terminator = {self.terminator}')
            dbg(f'DEBUG: hasattr terminals = {hasattr(self.terminator, "terminals")}')
            dbg(f'DEBUG: self.terminator.terminals = {self.terminator.terminals}')
            dbg(f'DEBUG: len(self.terminator.terminals) = {len(self.terminator.terminals)}')

            # Copy terminals list to avoid race with GTK thread modifying it during iteration
            terminals_snapshot = list(self.terminator.terminals)
            for term in terminals_snapshot:
                try:
                    dbg(f'DEBUG: Processing terminal {term}')
                    # Get custom title if set, otherwise use automatic title
                    title = term.titlebar.get_custom_string()
                    if not title:
                        title = term.get_window_title() or 'Terminal'

                    # Check focus state
                    focused = False
                    vte = term.get_vte()
                    if vte:
                        focused = vte.has_focus()

                    # Get current working directory
                    cwd = term.get_cwd() or '~'

                    terminals_info.append({
                        'uuid': term.uuid.urn,
                        'title': title,
                        'focused': str(focused),  # Convert boolean to string for D-Bus
                        'cwd': cwd
                    })
                except Exception as term_error:
                    # Terminal may have been destroyed between snapshot and access
                    dbg(f'DEBUG: Skipping terminal (may be destroyed): {term_error}')

            dbg(f'Retrieved metadata for {len(terminals_info)} terminals')
            return terminals_info

        except Exception as e:
            err(f'TerminatorAssistant: Error getting terminals metadata: {e}')
            err(f'Traceback: {traceback.format_exc()}')
            return []

    def _get_tab_container(self, terminal):
        """Get the root container for the tab containing this terminal.

        Walks up the widget hierarchy until finding a Notebook (tab container).
        Returns the widget that is the direct child of the Notebook (the tab's root).

        Args:
            terminal: A Terminator terminal widget

        Returns:
            The tab root container widget. For single-tab windows (no Notebook),
            returns the toplevel window, which is correct since all terminals
            in a single-tab window are effectively in the same "tab".
        """
        widget = terminal
        parent = widget.get_parent()

        while parent is not None:
            # Check if parent is a Notebook (Gtk.Notebook for tabs)
            # Notebook has get_n_pages method that containers don't have
            if hasattr(parent, 'get_n_pages'):
                dbg(f'_get_tab_container: found Notebook, returning tab container {widget}')
                return widget  # widget is the tab's root container
            widget = parent
            parent = widget.get_parent()

        # No notebook found - single tab or no tabs, return toplevel as fallback.
        # This is correct behavior: in single-tab windows, all terminals share
        # the same toplevel, so they're all considered to be in the same "tab".
        toplevel = terminal.get_toplevel()
        dbg(f'_get_tab_container: no Notebook found (single-tab window), using toplevel {toplevel}')
        return toplevel

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='aa{ss}')
    def get_terminals_in_same_tab(self, reference_terminal_uuid):
        """
        Get metadata for terminals in the same TAB as the reference terminal.

        Args:
            reference_terminal_uuid: UUID of reference terminal (e.g., chat terminal)

        Returns:
            List of dicts with terminal information (same format as get_all_terminals_metadata):
            - uuid: Terminal UUID (string)
            - title: Terminal title
            - focused: "True" or "False" indicating if terminal has focus
            - cwd: Current working directory
        """
        dbg(f'[SIDECHAT-PLUGIN] get_terminals_in_same_tab called with UUID: {reference_terminal_uuid}')
        terminals_info = []

        try:
            # Find the reference terminal
            reference_term = self.terminator.find_terminal_by_uuid(reference_terminal_uuid)
            if not reference_term:
                err(f'[SIDECHAT-PLUGIN] Reference terminal {reference_terminal_uuid} not found')
                return []

            # Get the tab container for the reference terminal
            reference_tab = self._get_tab_container(reference_term)
            if not reference_tab:
                err('[SIDECHAT-PLUGIN] Could not get tab container for reference terminal')
                return []

            dbg(f'[SIDECHAT-PLUGIN] Reference tab container: {reference_tab}')
            dbg(f'[SIDECHAT-PLUGIN] Total terminals in Terminator instance: {len(self.terminator.terminals)}')

            # Filter terminals to only those in the same TAB
            # Copy terminals list to avoid race with GTK thread modifying it during iteration
            terminals_snapshot = list(self.terminator.terminals)
            for term in terminals_snapshot:
                try:
                    term_tab = self._get_tab_container(term)
                except Exception as e:
                    dbg(f'[SIDECHAT-PLUGIN] Could not get tab container for terminal: {e}')
                    continue

                # Compare tab container objects (same instance = same tab)
                # Both containers are obtained in the same call, so object identity is stable
                if term_tab is None:
                    continue
                if term_tab is reference_tab:
                    # Get terminal metadata (same code as get_all_terminals_metadata)
                    title = term.titlebar.get_custom_string()
                    if not title:
                        title = term.get_window_title() or 'Terminal'

                    focused = False
                    vte = term.get_vte()
                    if vte:
                        focused = vte.has_focus()

                    cwd = term.get_cwd() or '~'

                    terminals_info.append({
                        'uuid': term.uuid.urn,
                        'title': title,
                        'focused': str(focused),
                        'cwd': cwd
                    })

            dbg(f'[SIDECHAT-PLUGIN] Retrieved metadata for {len(terminals_info)} terminals in same tab as {reference_terminal_uuid}')
            return terminals_info

        except Exception as e:
            err(f'[SIDECHAT-PLUGIN] Error getting terminals in same tab: {e}')
            err(f'[SIDECHAT-PLUGIN] Traceback: {traceback.format_exc()}')
            return []

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='', out_signature='s')
    def get_focused_terminal_uuid(self):
        """
        Get UUID of currently focused terminal.

        Returns:
            UUID string, or empty string if no terminal is focused
        """
        try:
            # Copy terminals list to avoid race with GTK thread modifying it during iteration
            terminals_snapshot = list(self.terminator.terminals)
            for term in terminals_snapshot:
                vte = term.get_vte()
                if vte and vte.has_focus():
                    return term.uuid.urn
            return ''  # Return empty string instead of None for D-Bus
        except Exception as e:
            err(f'TerminatorAssistant: Error getting focused terminal: {e}')
            return ''  # Return empty string instead of None for D-Bus

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='', out_signature='s')
    def get_plugin_version(self):
        """
        Get plugin version for diagnostics.

        Returns:
            Version string
        """
        return PLUGIN_VERSION

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='i')
    def get_shell_pid(self, terminal_uuid):
        """
        Get the PID of the interactive shell running in a terminal.

        Used to locate the prompt metadata file written by the shell.

        Note: When asciinema is recording, the process tree is:
          VTE child -> asciinema -> actual shell (writes metadata with $$)
        We need the shell's PID, not asciinema's.

        Args:
            terminal_uuid: UUID of terminal

        Returns:
            Shell PID as integer, or -1 on error
        """

        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return -1

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return -1

            # Get the PTY file descriptor
            pty = vte.get_pty()
            if not pty:
                err(f'PTY not accessible for terminal {terminal_uuid}')
                return -1

            # Find shell by traversing from VTE's direct child
            # Walk the process tree to find the deepest interactive shell
            # Note: tcgetpgrp() doesn't work here because when asciinema is recording,
            # VTE's PTY has asciinema as foreground, but the shell is on asciinema's PTY
            if hasattr(terminal, 'pid') and terminal.pid:
                shell_pid = self._find_shell_in_tree(terminal.pid)
                if shell_pid > 0:
                    return shell_pid
                # If no shell found, return the direct child (might be asciinema)
                return terminal.pid

            err(f'Could not determine shell PID for terminal {terminal_uuid}')
            return -1

        except Exception as e:
            err(f'Error getting shell PID: {e}')
            return -1

    def _find_shell_in_tree(self, root_pid):
        """
        Find an interactive shell in the process tree starting from root_pid.

        Walks the process tree via BFS to find bash/zsh/fish/sh.
        Returns the deepest shell found (handles asciinema -> shell case).

        Args:
            root_pid: PID to start searching from

        Returns:
            Shell PID or -1 if not found
        """
        shell_names = {b'bash', b'zsh', b'fish', b'sh', b'dash', b'ksh'}
        found_shell = -1

        def get_children(pid):
            """Get child PIDs of a process by scanning /proc."""
            children = []
            # Method 1: Try /proc/{pid}/task/{pid}/children (fast, needs CONFIG_PROC_CHILDREN)
            try:
                with open(f'/proc/{pid}/task/{pid}/children', 'rb') as f:
                    return [int(p) for p in f.read().split()]
            except (OSError, ValueError):
                pass
            # Method 2: Scan /proc/*/stat for processes with this PPID (portable)
            import os
            try:
                for entry in os.listdir('/proc'):
                    if not entry.isdigit():
                        continue
                    try:
                        with open(f'/proc/{entry}/stat', 'rb') as f:
                            stat = f.read()
                            # Format: pid (comm) state ppid ...
                            # Find closing paren to handle comm with spaces/parens
                            paren_end = stat.rfind(b')')
                            if paren_end > 0:
                                fields = stat[paren_end+2:].split()
                                if len(fields) >= 2:
                                    ppid = int(fields[1])
                                    if ppid == pid:
                                        children.append(int(entry))
                    except (OSError, ValueError, IndexError):
                        continue
            except OSError:
                pass
            return children

        def get_comm(pid):
            """Get process command name."""
            try:
                with open(f'/proc/{pid}/comm', 'rb') as f:
                    return f.read().strip()
            except OSError:
                return b''

        # BFS through process tree
        to_visit = [root_pid]
        while to_visit:
            pid = to_visit.pop(0)
            comm = get_comm(pid)
            if comm in shell_names:
                found_shell = pid  # Keep looking for deeper shells
            to_visit.extend(get_children(pid))

        return found_shell

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='b')
    def is_likely_tui_active(self, terminal_uuid):
        """
        Heuristic detection of whether a TUI is active in the terminal.

        Detection strategy:
        1. Check TUI detection cache first (0.5s TTL for performance)
        2. If shell prompt visible at end of content -> NOT a TUI (early exit)
        3. Fall back to vadjustment heuristic for alternate screen detection

        Args:
            terminal_uuid: UUID of terminal to check

        Returns:
            True if terminal likely has TUI active, False otherwise
        """
        try:
            # Check TUI detection cache first (avoids expensive capture calls, thread-safe)
            with self.cache_lock:
                if terminal_uuid in self.tui_cache:
                    if time.time() - self.tui_cache_time.get(terminal_uuid, 0) < self.tui_cache_ttl:
                        dbg(f'TUI detection for {terminal_uuid}: using cached result')
                        return self.tui_cache[terminal_uuid]

            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                return False

            vte = terminal.get_vte()
            if not vte:
                return False

            # First check: If shell prompt is visible, definitely not a TUI
            # This prevents false positives on empty terminals
            if PROMPT_DETECTOR_AVAILABLE:
                content = self.capture_terminal_content(terminal_uuid, -1)
                if content and not content.startswith('ERROR'):
                    if PromptDetector.detect_prompt_at_end(content):
                        dbg(f'TUI detection for {terminal_uuid}: shell prompt found, not TUI')
                        # Invalidate TUI cache when prompt detected (TUI just exited, thread-safe)
                        # Delete cache entry entirely to force re-evaluation on next call
                        with self.cache_lock:
                            if terminal_uuid in self.tui_cache:
                                dbg(f'TUI detection: invalidating stale cache for {terminal_uuid}')
                                del self.tui_cache[terminal_uuid]
                                del self.tui_cache_time[terminal_uuid]
                        return False  # Don't re-cache result - let next call rebuild

                    # Second check: If Unicode markers present but no prompt, command is running
                    # TUI apps use alternate screen buffer which is FRESH (no markers)
                    # Running commands stay in main buffer which HAS markers from the prompt
                    if PromptDetector.has_unicode_markers(content):
                        dbg(f'TUI detection for {terminal_uuid}: markers present, no prompt = running command, not TUI')
                        return False

            # Fallback: vadjustment heuristic for alternate screen detection
            vadj = vte.get_vadjustment()
            if not vadj:
                return False

            scroll_pos = vadj.get_value()
            upper = vadj.get_upper()
            page_size = vadj.get_page_size()

            # Alternate screen heuristic:
            # - Scroll position near top (< 5 rows from start)
            # - Minimal scrollable area (< 10 rows beyond visible)
            scrollback_rows = upper - page_size
            is_near_top = scroll_pos < 5
            has_minimal_scrollback = scrollback_rows < 10

            result = is_near_top and has_minimal_scrollback
            dbg(f'TUI detection for {terminal_uuid}: scroll_pos={scroll_pos}, scrollback={scrollback_rows}, likely_tui={result}')
            self._cache_tui_result(terminal_uuid, result)
            return result

        except Exception as e:
            err(f'TerminatorAssistant: Error detecting TUI state: {e}')
            return False

    def _cache_tui_result(self, terminal_uuid, is_tui):
        """Cache TUI detection result for performance (thread-safe)."""
        with self.cache_lock:
            self.tui_cache[terminal_uuid] = is_tui
            self.tui_cache_time[terminal_uuid] = time.time()
            self._evict_old_cache_entries()

    def _evict_old_cache_entries(self):
        """Remove oldest cache entries if cache exceeds max size.

        Must be called while holding cache_lock.
        Uses LRU eviction based on timestamps.

        Optimized: O(n log n) instead of O(n²) by sorting once and deleting in batch.
        """
        # Evict content cache entries - find all entries to evict in one pass
        excess_content = len(self.content_cache) - CACHE_MAX_SIZE
        if excess_content > 0 and self.last_capture:
            # Use heapq.nsmallest for O(n log k) where k = excess_content
            oldest_keys = heapq.nsmallest(excess_content, self.last_capture,
                                          key=self.last_capture.get)
            for key in oldest_keys:
                self.content_cache.pop(key, None)
                self.last_capture.pop(key, None)

        # Evict TUI cache entries
        excess_tui = len(self.tui_cache) - CACHE_MAX_SIZE
        if excess_tui > 0 and self.tui_cache_time:
            oldest_keys = heapq.nsmallest(excess_tui, self.tui_cache_time,
                                          key=self.tui_cache_time.get)
            for key in oldest_keys:
                self.tui_cache.pop(key, None)
                self.tui_cache_time.pop(key, None)

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='b')
    def scroll_to_bottom(self, terminal_uuid):
        """
        Scroll a terminal to the bottom of its scrollback buffer.

        This is critical for prompt detection when user has scrolled up -
        ensures the viewport shows the current prompt, not old content.

        Args:
            terminal_uuid: UUID of terminal to scroll

        Returns:
            True on success, False on failure
        """
        terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
        if not terminal:
            err(f'scroll_to_bottom: Terminal {terminal_uuid} not found')
            return False

        vte = terminal.get_vte()
        if not vte:
            err(f'scroll_to_bottom: No VTE for terminal {terminal_uuid}')
            return False

        try:
            vadj = vte.get_vadjustment()
            if vadj:
                # Calculate bottom position: total height - visible page size
                bottom = vadj.get_upper() - vadj.get_page_size()
                vadj.set_value(bottom)
                dbg(f'scroll_to_bottom: Scrolled {terminal_uuid} to bottom ({bottom})')
                return True
            return False
        except Exception as e:
            err(f'scroll_to_bottom: Error for {terminal_uuid}: {e}')
            return False

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='', out_signature='')
    def clear_cache(self):
        """Clear all caches (content and TUI detection, thread-safe)"""
        with self.cache_lock:
            self.content_cache.clear()
            self.last_capture.clear()
            self.tui_cache.clear()
            self.tui_cache_time.clear()
        dbg('All caches cleared (content + TUI detection)')

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='b')
    def has_selection(self, terminal_uuid):
        """
        Check if terminal has text selected.

        Args:
            terminal_uuid: UUID of terminal to check

        Returns:
            True if terminal has active text selection, False otherwise
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return False

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return False

            has_sel = vte.get_has_selection()
            dbg(f'has_selection for {terminal_uuid}: {has_sel}')
            return has_sel

        except Exception as e:
            err(f'TerminatorAssistant: Error checking selection: {e}')
            return False

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='s')
    def get_selection(self, terminal_uuid):
        """
        Get currently selected text in terminal.

        Uses GTK clipboard to retrieve the selection text after copying
        from VTE's selection buffer.

        Args:
            terminal_uuid: UUID of terminal

        Returns:
            Selected text as string, empty string if no selection,
            or error message starting with "ERROR:"
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return "ERROR: Terminal not found"

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return "ERROR: VTE not accessible"

            # Check if there's actually a selection
            if not vte.get_has_selection():
                dbg(f'get_selection for {terminal_uuid}: no selection')
                return ""

            # Get the GTK clipboard
            from gi.repository import Gtk
            clipboard = Gtk.Clipboard.get_default(Gdk.Display.get_default())
            if not clipboard:
                err('Could not access clipboard')
                return "ERROR: Clipboard not accessible"

            # Save current clipboard content to restore later
            original_text = clipboard.wait_for_text()

            # Copy VTE selection to clipboard
            # Use copy_clipboard() which copies the VTE selection to CLIPBOARD
            vte.copy_clipboard()

            # Wait for clipboard to update (GTK operations may be async)
            # Give GTK a chance to process the copy
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

            # Get the selection text from clipboard
            selected_text = clipboard.wait_for_text()

            # Restore original clipboard content if it was different
            if original_text is not None and original_text != selected_text:
                clipboard.set_text(original_text, -1)
                clipboard.store()

            if selected_text is None:
                dbg(f'get_selection for {terminal_uuid}: clipboard returned None')
                return ""

            dbg(f'get_selection for {terminal_uuid}: {len(selected_text)} chars')
            return selected_text

        except Exception as e:
            err(f'TerminatorAssistant: Error getting selection: {e}')
            return f"ERROR: {str(e)}"

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='b')
    def paste_from_clipboard(self, terminal_uuid):
        """
        Paste clipboard content to terminal.

        Uses Terminator's built-in paste_clipboard() method which handles
        bracketed paste mode and other terminal-specific escaping.

        Args:
            terminal_uuid: UUID of terminal to paste to

        Returns:
            True on success, False on error
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return False

            # Use Terminator's built-in paste method
            # This handles bracketed paste mode, escaping, etc.
            terminal.paste_clipboard()

            dbg(f'paste_from_clipboard: pasted to {terminal_uuid}')
            return True

        except Exception as e:
            err(f'TerminatorAssistant: Error pasting from clipboard: {e}')
            return False

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='a{ss}')
    def get_foreground_process(self, terminal_uuid):
        """
        Get info about the foreground process in the terminal.

        Uses tcgetpgrp() on the PTY file descriptor to get the foreground
        process group, then reads process info from /proc.

        Args:
            terminal_uuid: UUID of terminal

        Returns:
            Dict with process info (all string values for D-Bus compatibility):
            - pid: Process ID as string
            - name: Command name (from /proc/PID/comm)
            - cmdline: Full command line (from /proc/PID/cmdline)
            Returns empty dict on error.
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return {}

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return {}

            # Get the PTY object
            pty = vte.get_pty()
            if not pty:
                err(f'PTY not accessible for terminal {terminal_uuid}')
                return {}

            # Get PTY file descriptor
            fd = pty.get_fd()
            if fd < 0:
                err(f'Invalid PTY fd for terminal {terminal_uuid}')
                return {}

            try:
                # Get foreground process group ID
                fg_pid = os.tcgetpgrp(fd)
                if fg_pid <= 0:
                    dbg(f'No foreground process for terminal {terminal_uuid}')
                    return {}

                # Read process info from /proc
                result = {'pid': str(fg_pid)}

                # Get command name (short name)
                try:
                    with open(f'/proc/{fg_pid}/comm', 'r') as f:
                        result['name'] = f.read().strip()
                except (OSError, IOError):
                    result['name'] = ''

                # Get full command line
                try:
                    with open(f'/proc/{fg_pid}/cmdline', 'r') as f:
                        cmdline = f.read()
                        # cmdline is null-separated, convert to space-separated
                        result['cmdline'] = cmdline.replace('\0', ' ').strip()
                except (OSError, IOError):
                    result['cmdline'] = ''

                dbg(f'get_foreground_process for {terminal_uuid}: {result}')
                return result

            except OSError as e:
                # tcgetpgrp can fail if the process has terminated
                dbg(f'tcgetpgrp failed for terminal {terminal_uuid}: {e}')
                return {}

        except Exception as e:
            err(f'TerminatorAssistant: Error getting foreground process: {e}')
            return {}

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='si', out_signature='b')
    def scroll_by_lines(self, terminal_uuid, lines):
        """
        Scroll terminal by N lines (positive=down, negative=up).

        Args:
            terminal_uuid: UUID of terminal to scroll
            lines: Number of lines to scroll (positive=down, negative=up)

        Returns:
            True on success, False on error
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return False

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return False

            vadj = vte.get_vadjustment()
            if not vadj:
                err(f'Vadjustment not accessible for terminal {terminal_uuid}')
                return False

            # Calculate new position with bounds checking
            current_pos = vadj.get_value()
            max_pos = vadj.get_upper() - vadj.get_page_size()
            new_pos = current_pos + lines

            # Clamp to valid range
            new_pos = max(0.0, min(new_pos, max_pos))

            vadj.set_value(new_pos)
            dbg(f'scroll_by_lines for {terminal_uuid}: {lines} lines, new_pos={new_pos}')
            return True

        except Exception as e:
            err(f'TerminatorAssistant: Error scrolling terminal: {e}')
            return False

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='a{si}')
    def get_scrollback_info(self, terminal_uuid):
        """
        Get scrollback buffer information for a terminal.

        Args:
            terminal_uuid: UUID of terminal

        Returns:
            Dict with scrollback info (integer values for D-Bus compatibility):
            - total_lines: Total lines in buffer (including scrollback)
            - visible_lines: Number of visible lines (page size)
            - current_position: Current scroll position (row number)
            - scrollback_lines: Lines above visible viewport
            Returns empty dict on error.
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return {}

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return {}

            vadj = vte.get_vadjustment()
            if not vadj:
                err(f'Vadjustment not accessible for terminal {terminal_uuid}')
                return {}

            result = {
                'total_lines': int(vadj.get_upper()),
                'visible_lines': int(vadj.get_page_size()),
                'current_position': int(vadj.get_value()),
                'scrollback_lines': int(vadj.get_upper() - vadj.get_page_size())
            }

            dbg(f'get_scrollback_info for {terminal_uuid}: {result}')
            return result

        except Exception as e:
            err(f'TerminatorAssistant: Error getting scrollback info: {e}')
            return {}

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='ssb', out_signature='aa{sv}')
    def search_in_scrollback(self, terminal_uuid, pattern, case_sensitive):
        """
        Search for regex/text pattern in terminal scrollback.

        Args:
            terminal_uuid: UUID of terminal to search
            pattern: Regex pattern to search for
            case_sensitive: If True, search is case-sensitive

        Returns:
            List of dicts with match info:
            - line_number: Line number in buffer (int)
            - text: Matching line text (string)
            - start_col: Start column of match (int)
            - end_col: End column of match (int)
            Returns empty list on error or no matches.
        """
        import re

        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return []

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return []

            # Get full scrollback content
            vadj = vte.get_vadjustment()
            if not vadj:
                return []

            total_lines = int(vadj.get_upper())
            term_width = vte.get_column_count()

            # Capture full scrollback
            vte_version = Vte.get_minor_version()
            if vte_version >= 72:
                result = vte.get_text_range_format(
                    Vte.Format.TEXT,
                    0, 0,
                    total_lines - 1, term_width - 1
                )
                content = result[0] if isinstance(result, tuple) else (result or '')
            else:
                result = vte.get_text_range(
                    0, 0,
                    total_lines - 1, term_width - 1,
                    lambda *a: True
                )
                content = result[0] if isinstance(result, tuple) else (result or '')

            if not content:
                return []

            # Compile regex pattern
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                regex = re.compile(pattern, flags)
            except re.error as e:
                err(f'Invalid regex pattern "{pattern}": {e}')
                return []

            # Search line by line
            matches = []
            lines = content.split('\n')
            for line_num, line in enumerate(lines):
                for match in regex.finditer(line):
                    matches.append({
                        'line_number': dbus.Int32(line_num),
                        'text': line,
                        'start_col': dbus.Int32(match.start()),
                        'end_col': dbus.Int32(match.end())
                    })

            dbg(f'search_in_scrollback for {terminal_uuid}: found {len(matches)} matches')
            return matches

        except Exception as e:
            err(f'TerminatorAssistant: Error searching scrollback: {e}')
            return []

    @dbus.service.signal(PLUGIN_BUS_NAME, signature='s')
    def content_changed(self, terminal_uuid):
        """D-Bus signal emitted when terminal content changes.

        This signal is emitted whenever the VTE 'contents-changed' signal fires
        for a subscribed terminal. Clients can listen to this to get real-time
        notifications instead of polling.

        Args:
            terminal_uuid: UUID of the terminal that changed
        """
        pass  # Signal body is handled by D-Bus

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='s')
    def subscribe_content_changes(self, terminal_uuid):
        """
        Register for VTE content-changed signal notifications.

        Connects to the VTE's 'contents-changed' signal and emits a D-Bus signal
        when content changes. Uses reference counting to handle multiple subscribers.

        Args:
            terminal_uuid: UUID of terminal to watch

        Returns:
            "OK" on success, or error message starting with "ERROR:"
        """
        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return "ERROR: Terminal not found"

            vte = terminal.get_vte()
            if not vte:
                err(f'VTE not accessible for terminal {terminal_uuid}')
                return "ERROR: VTE not accessible"

            # Check if already subscribed (ref counting)
            if terminal_uuid in self.content_watchers:
                self.content_watchers[terminal_uuid]['ref_count'] += 1
                dbg(f'subscribe_content_changes: incremented ref_count for {terminal_uuid} to {self.content_watchers[terminal_uuid]["ref_count"]}')
                return "OK"

            # Connect to VTE's contents-changed signal
            handler_id = vte.connect('contents-changed',
                                     self._on_content_changed,
                                     terminal_uuid)

            self.content_watchers[terminal_uuid] = {
                'handler_id': handler_id,
                'ref_count': 1,
                'vte': vte  # Keep reference to VTE for cleanup
            }

            dbg(f'subscribe_content_changes: subscribed to {terminal_uuid}')
            return "OK"

        except Exception as e:
            err(f'TerminatorAssistant: Error subscribing to content changes: {e}')
            return f"ERROR: {str(e)}"

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='b')
    def unsubscribe_content_changes(self, terminal_uuid):
        """
        Unregister from VTE content-changed signal notifications.

        Uses reference counting - only disconnects when ref_count reaches 0.

        Args:
            terminal_uuid: UUID of terminal to stop watching

        Returns:
            True on success (including decremented ref count), False on error
        """
        try:
            if terminal_uuid not in self.content_watchers:
                dbg(f'unsubscribe_content_changes: {terminal_uuid} not subscribed')
                return False

            watcher = self.content_watchers[terminal_uuid]
            watcher['ref_count'] -= 1

            if watcher['ref_count'] > 0:
                dbg(f'unsubscribe_content_changes: decremented ref_count for {terminal_uuid} to {watcher["ref_count"]}')
                return True

            # Ref count reached 0, disconnect signal
            try:
                vte = watcher.get('vte')
                if vte:
                    vte.disconnect(watcher['handler_id'])
            except Exception as e:
                dbg(f'Error disconnecting signal for {terminal_uuid}: {e}')
                # Continue with cleanup even if disconnect fails

            del self.content_watchers[terminal_uuid]
            dbg(f'unsubscribe_content_changes: unsubscribed from {terminal_uuid}')
            return True

        except Exception as e:
            err(f'TerminatorAssistant: Error unsubscribing from content changes: {e}')
            return False

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='', out_signature='as')
    def get_subscribed_terminals(self):
        """
        Get list of terminal UUIDs currently subscribed for content changes.

        Returns:
            List of terminal UUID strings
        """
        return list(self.content_watchers.keys())

    def _on_content_changed(self, vte, terminal_uuid):
        """VTE signal handler - emits D-Bus signal when content changes.

        This is called by GTK when the VTE terminal content changes.
        It emits a D-Bus signal that clients can listen to.

        Args:
            vte: The VTE widget that changed
            terminal_uuid: UUID of the terminal (passed via connect)
        """
        try:
            # Emit D-Bus signal
            self.content_changed(terminal_uuid)
            dbg(f'_on_content_changed: emitted signal for {terminal_uuid}')
        except Exception as e:
            err(f'Error emitting content_changed signal: {e}')

    def _cleanup_content_watchers(self):
        """Clean up all content change subscriptions.

        Called during unload() to ensure all VTE signal handlers are disconnected.
        """
        for terminal_uuid, watcher in list(self.content_watchers.items()):
            try:
                vte = watcher.get('vte')
                if vte:
                    vte.disconnect(watcher['handler_id'])
            except Exception as e:
                dbg(f'Error cleaning up watcher for {terminal_uuid}: {e}')

        self.content_watchers.clear()
        dbg('All content watchers cleaned up')

    def unload(self):
        """Clean up when plugin is unloaded"""
        self.clear_cache()
        self._cleanup_content_watchers()

        # Release D-Bus service if we acquired it
        if self.bus_name:
            try:
                # Note: dbus-python doesn't have an explicit release method,
                # but the BusName will be released when the object is garbage collected.
                # Setting to None helps ensure this happens promptly.
                self.bus_name = None
                dbg('D-Bus service name reference released')
            except Exception as e:
                dbg(f'Could not release D-Bus service: {e}')

        dbg('TerminatorAssistant unloaded')

