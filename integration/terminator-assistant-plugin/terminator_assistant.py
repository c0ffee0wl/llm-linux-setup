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
import tempfile
import os
import math
import threading
import gi
gi.require_version('Vte', '2.91')
gi.require_version('Gdk', '3.0')
from gi.repository import Vte, Gdk
import terminatorlib.plugin as plugin
from terminatorlib.terminator import Terminator
from terminatorlib.util import dbg, err
import dbus.service
from dbus.exceptions import DBusException

# Import prompt detection for TUI heuristic (shell prompt = not TUI)
try:
    from llm_tools.prompt_detection import PromptDetector
    PROMPT_DETECTOR_AVAILABLE = True
except ImportError:
    PROMPT_DETECTOR_AVAILABLE = False

# D-Bus service constants
PLUGIN_BUS_NAME = 'net.tenshu.Terminator2.Assistant'
PLUGIN_BUS_PATH = '/net/tenshu/Terminator2/Assistant'

# Plugin version for diagnostics
PLUGIN_VERSION = "3.3-cache-eviction"

# Cache size limit to prevent unbounded memory growth
CACHE_MAX_SIZE = 100

AVAILABLE = ['TerminatorAssistant']


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
                    # Fallback if vadjustment returns None
                    dbg('vadjustment returned None, using fallback')
                    # Try scrollback position as fallback
                    try:
                        scrollback = vte.get_scrollback_lines()
                        capture_start = scrollback
                        capture_end = scrollback + lines - 1
                        dbg(f'Using scrollback fallback: scrollback={scrollback}, range=[{capture_start}, {capture_end}]')
                    except Exception:
                        # Ultimate fallback: top of buffer
                        capture_start = 0
                        capture_end = lines - 1
                        dbg(f'Using ultimate fallback: range=[{capture_start}, {capture_end}]')
            except Exception as e:
                # Exception during vadjustment access
                dbg(f'vadjustment exception: {type(e).__name__}: {e}')
                capture_start = 0
                capture_end = lines - 1

            # DEBUG: Log capture parameters
            dbg(f'CAPTURE DEBUG for {terminal_uuid}:')
            dbg(f'  Capture mode: viewport-based')
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
                return f"ERROR: Terminal window not realized (widget not visible)"

            # Get widget dimensions
            width = vte.get_allocated_width()
            height = vte.get_allocated_height()

            dbg(f'Screenshot capture for {terminal_uuid}: {width}x{height}px')

            # Capture screenshot as pixbuf
            pixbuf = Gdk.pixbuf_get_from_window(gdk_window, 0, 0, width, height)

            if not pixbuf:
                err(f'Failed to capture pixbuf from terminal {terminal_uuid}')
                return f"ERROR: Screenshot capture failed (pixbuf is None)"

            # Save to temporary file
            temp_path = None
            try:
                # Create temp file with .png extension
                temp_fd, temp_path = tempfile.mkstemp(suffix='.png', prefix='terminator_screenshot_')
                os.close(temp_fd)  # Close the file descriptor, we'll use the path

                # Save pixbuf to PNG
                pixbuf.savev(temp_path, "png", [], [])

                dbg(f'Screenshot saved to {temp_path}')

                # Read file and encode as base64
                with open(temp_path, 'rb') as f:
                    image_data = f.read()

                base64_data = base64.b64encode(image_data).decode('utf-8')

                dbg(f'Screenshot captured: {len(base64_data)} base64 chars ({len(image_data)} bytes PNG)')
                return base64_data

            except Exception as e:
                err(f'Error saving screenshot: {e}')
                return f"ERROR: Failed to save screenshot: {str(e)}"
            finally:
                # Guaranteed cleanup of temp file
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass  # Ignore cleanup errors (file may not exist)

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
        # Special key mappings to escape sequences
        special_keys = {
            # Basic navigation and editing
            'Enter': b'\r',
            'Return': b'\r',
            'Escape': b'\x1b',
            'Esc': b'\x1b',
            'Tab': b'\t',
            'Backspace': b'\x7f',
            'Delete': b'\x1b[3~',
            'Insert': b'\x1b[2~',
            'Home': b'\x1b[H',
            'End': b'\x1b[F',
            'PageUp': b'\x1b[5~',
            'PageDown': b'\x1b[6~',
            'Up': b'\x1b[A',
            'Down': b'\x1b[B',
            'Right': b'\x1b[C',
            'Left': b'\x1b[D',
            'Space': b' ',

            # Function keys (F1-F12) - VT sequences
            'F1': b'\x1bOP',
            'F2': b'\x1bOQ',
            'F3': b'\x1bOR',
            'F4': b'\x1bOS',
            'F5': b'\x1b[15~',
            'F6': b'\x1b[17~',
            'F7': b'\x1b[18~',
            'F8': b'\x1b[19~',
            'F9': b'\x1b[20~',
            'F10': b'\x1b[21~',
            'F11': b'\x1b[23~',
            'F12': b'\x1b[24~',

            # Control keys (ASCII control codes)
            'Ctrl+A': b'\x01',
            'Ctrl+B': b'\x02',
            'Ctrl+C': b'\x03',
            'Ctrl+D': b'\x04',
            'Ctrl+E': b'\x05',
            'Ctrl+F': b'\x06',
            'Ctrl+G': b'\x07',
            'Ctrl+H': b'\x08',
            'Ctrl+I': b'\t',   # Same as Tab
            'Ctrl+J': b'\n',   # Same as Enter
            'Ctrl+K': b'\x0b',
            'Ctrl+L': b'\x0c',
            'Ctrl+M': b'\r',   # Carriage return
            'Ctrl+N': b'\x0e',
            'Ctrl+O': b'\x0f',
            'Ctrl+P': b'\x10',
            'Ctrl+Q': b'\x11',
            'Ctrl+R': b'\x12',
            'Ctrl+S': b'\x13',
            'Ctrl+T': b'\x14',
            'Ctrl+U': b'\x15',
            'Ctrl+V': b'\x16',
            'Ctrl+W': b'\x17',
            'Ctrl+X': b'\x18',
            'Ctrl+Y': b'\x19',
            'Ctrl+Z': b'\x1a',

            # Alt/Meta combinations (ESC prefix)
            'Alt+B': b'\x1bb',
            'Alt+F': b'\x1bf',
            'Alt+D': b'\x1bd',
            'Alt+Backspace': b'\x1b\x7f',
            'Alt+Left': b'\x1b[1;3D',
            'Alt+Right': b'\x1b[1;3C',

            # Shift combinations
            'Shift+Tab': b'\x1b[Z',
        }

        try:
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                err(f'Terminal {terminal_uuid} not found')
                return False

            vte = terminal.get_vte()
            if not vte:
                err(f'Could not access VTE for terminal {terminal_uuid}')
                return False

            # Check if it's a special key (case-insensitive)
            keypress_bytes = None
            for special_name, special_seq in special_keys.items():
                if keypress.lower() == special_name.lower():
                    keypress_bytes = special_seq
                    dbg(f'Mapped special key "{keypress}" to escape sequence')
                    break

            # If not a special key, send as literal text
            if keypress_bytes is None:
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
            import traceback
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
            import traceback
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
        import heapq

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

    def unload(self):
        """Clean up when plugin is unloaded"""
        self.clear_cache()

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
