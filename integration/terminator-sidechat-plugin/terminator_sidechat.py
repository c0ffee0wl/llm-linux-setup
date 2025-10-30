"""
Terminator Sidechat Plugin

Provides terminal content capture and command injection capabilities for
llm-sidechat. This plugin bridges the standalone sidechat application
with Terminator's VTE terminals.

Capabilities:
- Capture visible scrollback content from VTE terminals
- Send commands to terminals via feed_child
- Get terminal metadata (UUIDs, titles, focus state)
- Content caching for performance

Author: c0ffee0wl
License: GPL v2 only
"""

import time
import gi
gi.require_version('Vte', '2.91')
from gi.repository import Vte
import terminatorlib.plugin as plugin
from terminatorlib.terminator import Terminator
from terminatorlib.util import dbg, err
import dbus.service
from dbus.exceptions import DBusException

# D-Bus service constants
PLUGIN_BUS_NAME = 'net.tenshu.Terminator2.Sidechat'
PLUGIN_BUS_PATH = '/net/tenshu/Terminator2/Sidechat'

# Plugin version for diagnostics
PLUGIN_VERSION = "2.0-vadjustment"

AVAILABLE = ['TerminatorSidechatPlugin']


class TerminatorSidechatPlugin(plugin.Plugin, dbus.service.Object):
    """Plugin providing terminal content capture for llm-sidechat via D-Bus"""

    capabilities = ['sidechat_bridge']

    def __init__(self):
        """Initialize plugin and D-Bus service"""
        plugin.Plugin.__init__(self)

        # Try to register D-Bus service (only works when running inside Terminator)
        self.bus_name = None
        try:
            bus = dbus.SessionBus()
            # Claim the bus name with replacement flags
            self.bus_name = dbus.service.BusName(
                PLUGIN_BUS_NAME,
                bus=bus,
                allow_replacement=True,
                replace_existing=True,
                do_not_queue=True
            )
            dbus.service.Object.__init__(self, self.bus_name, PLUGIN_BUS_PATH)
            dbg('TerminatorSidechatPlugin: D-Bus service registered at %s' % PLUGIN_BUS_NAME)
        except (DBusException, KeyError) as e:
            # KeyError occurs when object path handler is already registered (plugin loaded multiple times)
            # DBusException occurs when D-Bus is not available or bus name cannot be claimed
            dbg('TerminatorSidechatPlugin: Could not register D-Bus service: %s (continuing anyway)' % e)
            # Continue without D-Bus (fallback for when loaded by external process or already loaded)

        self.terminator = Terminator()

        # Content caching to avoid repeated VTE queries
        self.content_cache = {}  # uuid -> content
        self.last_capture = {}   # uuid -> timestamp
        self.cache_ttl = 0.5     # Cache valid for 0.5 seconds

        dbg('TerminatorSidechatPlugin initialized')

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

            # Check cache after determining lines
            cache_key = f"{terminal_uuid}:{lines}"
            if cache_key in self.content_cache:
                if time.time() - self.last_capture.get(cache_key, 0) < self.cache_ttl:
                    dbg(f'Returning cached content for {terminal_uuid}')
                    return self.content_cache[cache_key]

            # Get terminal dimensions
            try:
                term_width = vte.get_column_count()
                term_height = vte.get_row_count()
            except Exception as e:
                err(f'Error getting terminal dimensions: {e}')
                return "ERROR: Could not access terminal state"

            # Calculate capture range using vertical adjustment (scrollbar position)
            # This gives us the actual visible viewport in buffer coordinates
            try:
                vadj = vte.get_vadjustment()
                dbg(f'get_vadjustment() returned: {vadj}')
                if vadj:
                    scroll_pos = vadj.get_value()
                    page_size = vadj.get_page_size()
                    start_row = int(scroll_pos)
                    end_row = int(scroll_pos + page_size) - 1
                    dbg(f'Using vadjustment: scroll_pos={scroll_pos}, page_size={page_size}')
                else:
                    dbg('vadjustment returned None')
                    raise ValueError("vadjustment not available")
            except Exception as e:
                # Fallback: capture entire visible viewport from top of buffer
                dbg(f'vadjustment failed with exception: {type(e).__name__}: {e}')
                start_row = 0
                end_row = term_height - 1

            # DEBUG: Log capture parameters
            dbg(f'CAPTURE DEBUG for {terminal_uuid}:')
            dbg(f'  Capture method: {"vadjustment" if "scroll_pos" in locals() else "fallback"}')
            dbg(f'  Terminal size: width={term_width}, height={term_height}')
            dbg(f'  Requested lines: {lines}')
            dbg(f'  Calculated range: start_row={start_row}, end_row={end_row}')
            dbg(f'  Range span: {end_row - start_row + 1} rows')

            # Version-aware content capture with error handling
            content = None
            vte_version = Vte.get_minor_version()

            if vte_version >= 72:
                # Modern VTE: use get_text_range_format
                dbg(f'Using get_text_range_format (VTE {vte_version})')
                try:
                    result = vte.get_text_range_format(
                        Vte.Format.TEXT,
                        start_row, 0,           # start row, start col
                        end_row, term_width - 1     # end row, end col (full width)
                    )
                    # Verify it's a tuple and extract text
                    if isinstance(result, tuple) and len(result) > 0:
                        content = result[0]
                    else:
                        content = str(result)  # Fallback to string conversion
                except Exception as e:
                    err(f'VTE 72+ capture failed: {e}')
                    return f"ERROR: Content capture failed: {str(e)}"
            else:
                # Older VTE: use get_text_range with lambda
                dbg(f'Using get_text_range (VTE {vte_version})')
                try:
                    content = vte.get_text_range(
                        start_row, 0,           # start row, start col
                        end_row, term_width - 1,    # end row, end col (full width)
                        lambda *a: True  # Include all cells
                    )
                except Exception as e:
                    err(f'VTE legacy capture failed: {e}')
                    return f"ERROR: Content capture failed: {str(e)}"

            if content is None:
                err('Content capture returned None')
                return f"ERROR: Failed to capture content from terminal {terminal_uuid}"

            # Cache the result
            self.content_cache[cache_key] = content
            self.last_capture[cache_key] = time.time()

            dbg(f'Captured {len(content)} characters from {terminal_uuid}')
            return content

        except Exception as e:
            err(f'TerminatorSidechatPlugin: Error capturing terminal content: {e}')
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

            # Optionally execute by sending newline
            if execute:
                vte.feed_child(b'\n')

            dbg(f'Sent {len(text)} characters to {terminal_uuid} (execute={execute})')
            return True

        except Exception as e:
            err(f'TerminatorSidechatPlugin: Error sending keys: {e}')
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
            'Enter': b'\n',
            'Return': b'\n',
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
            # Control keys
            'Ctrl+C': b'\x03',
            'Ctrl+D': b'\x04',
            'Ctrl+Z': b'\x1a',
            'Ctrl+L': b'\x0c',
            'Ctrl+A': b'\x01',
            'Ctrl+E': b'\x05',
            'Ctrl+K': b'\x0b',
            'Ctrl+U': b'\x15',
            'Ctrl+W': b'\x17',
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
            err(f'TerminatorSidechatPlugin: Error sending keypress: {e}')
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

            for term in self.terminator.terminals:
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

            dbg(f'Retrieved metadata for {len(terminals_info)} terminals')
            return terminals_info

        except Exception as e:
            import traceback
            err(f'TerminatorSidechatPlugin: Error getting terminals metadata: {e}')
            err(f'Traceback: {traceback.format_exc()}')
            return []

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='s', out_signature='aa{ss}')
    def get_terminals_in_same_window(self, reference_terminal_uuid):
        """
        Get metadata for terminals in the same window as the reference terminal.

        Args:
            reference_terminal_uuid: UUID of reference terminal (e.g., chat terminal)

        Returns:
            List of dicts with terminal information (same format as get_all_terminals_metadata):
            - uuid: Terminal UUID (string)
            - title: Terminal title
            - focused: "True" or "False" indicating if terminal has focus
            - cwd: Current working directory
        """
        dbg(f'[SIDECHAT-PLUGIN] get_terminals_in_same_window called with UUID: {reference_terminal_uuid}')
        terminals_info = []

        try:
            # Find the reference terminal
            reference_term = self.terminator.find_terminal_by_uuid(reference_terminal_uuid)
            if not reference_term:
                err(f'[SIDECHAT-PLUGIN] Reference terminal {reference_terminal_uuid} not found')
                return []

            # Get the window containing the reference terminal
            reference_window = reference_term.get_toplevel()
            if not reference_window:
                err('[SIDECHAT-PLUGIN] Could not get toplevel window for reference terminal')
                return []

            dbg(f'[SIDECHAT-PLUGIN] Reference window: {reference_window}')
            dbg(f'[SIDECHAT-PLUGIN] Total terminals in Terminator instance: {len(self.terminator.terminals)}')

            # Filter terminals to only those in the same window
            for term in self.terminator.terminals:
                term_window = term.get_toplevel()

                # Compare window objects (same instance = same window)
                if term_window is reference_window:
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

            dbg(f'[SIDECHAT-PLUGIN] Retrieved metadata for {len(terminals_info)} terminals in same window as {reference_terminal_uuid}')
            return terminals_info

        except Exception as e:
            import traceback
            err(f'[SIDECHAT-PLUGIN] Error getting terminals in same window: {e}')
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
            for term in self.terminator.terminals:
                vte = term.get_vte()
                if vte and vte.has_focus():
                    return term.uuid.urn
            return ''  # Return empty string instead of None for D-Bus
        except Exception as e:
            err(f'TerminatorSidechatPlugin: Error getting focused terminal: {e}')
            return ''  # Return empty string instead of None for D-Bus

    @dbus.service.method(PLUGIN_BUS_NAME, in_signature='', out_signature='s')
    def get_plugin_version(self):
        """
        Get plugin version for diagnostics.

        Returns:
            Version string
        """
        return PLUGIN_VERSION

    def clear_cache(self):
        """Clear content cache (useful when explicitly requested)"""
        self.content_cache.clear()
        self.last_capture.clear()
        dbg('Content cache cleared')

    def unload(self):
        """Clean up when plugin is unloaded"""
        self.clear_cache()
        dbg('TerminatorSidechatPlugin unloaded')
