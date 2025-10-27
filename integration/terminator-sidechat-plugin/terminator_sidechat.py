"""
Terminator Sidechat Plugin

Provides terminal content capture and command injection capabilities for
llm-terminator-sidechat. This plugin bridges the standalone sidechat application
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

AVAILABLE = ['TerminatorSidechatPlugin']


class TerminatorSidechatPlugin(plugin.Plugin):
    """Plugin providing terminal content capture for llm-sidechat"""

    capabilities = ['sidechat_bridge']

    def __init__(self):
        """Initialize plugin"""
        plugin.Plugin.__init__(self)
        self.terminator = Terminator()

        # Content caching to avoid repeated VTE queries
        self.content_cache = {}  # uuid -> content
        self.last_capture = {}   # uuid -> timestamp
        self.cache_ttl = 0.5     # Cache valid for 0.5 seconds

        dbg('TerminatorSidechatPlugin initialized')

    def capture_terminal_content(self, terminal_uuid, lines=100):
        """
        Capture visible scrollback content from a VTE terminal.

        Args:
            terminal_uuid: UUID of terminal to capture (string format)
            lines: Number of lines to capture (default 100)

        Returns:
            String containing terminal content, or error message
        """
        # Check cache first
        cache_key = f"{terminal_uuid}:{lines}"
        if cache_key in self.content_cache:
            if time.time() - self.last_capture.get(cache_key, 0) < self.cache_ttl:
                dbg(f'Returning cached content for {terminal_uuid}')
                return self.content_cache[cache_key]

        try:
            # Find terminal by UUID
            terminal = self.terminator.find_terminal_by_uuid(terminal_uuid)
            if not terminal:
                return f"ERROR: Terminal with UUID {terminal_uuid} not found"

            # Get VTE widget
            vte = terminal.get_vte()
            if not vte:
                return f"ERROR: Could not access VTE for terminal {terminal_uuid}"

            # Get cursor position (defines end of content)
            row, col = vte.get_cursor_position()

            # Calculate start position (capture 'lines' lines of scrollback)
            start_row = max(0, row - lines)

            # Version-aware content capture (like dumptofile plugin)
            content = None
            vte_version = Vte.get_minor_version()

            if vte_version >= 72:
                # Modern VTE: use get_text_range_format
                dbg(f'Using get_text_range_format (VTE {vte_version})')
                content = vte.get_text_range_format(
                    Vte.Format.TEXT,
                    start_row, 0,  # start row, start col
                    row, col       # end row, end col
                )[0]  # Returns (text, attributes)
            else:
                # Older VTE: use get_text_range with lambda
                dbg(f'Using get_text_range (VTE {vte_version})')
                content = vte.get_text_range(
                    start_row, 0,  # start row, start col
                    row, col,      # end row, end col
                    lambda *a: True  # Include all cells
                )

            if content is None:
                return f"ERROR: Failed to capture content from terminal {terminal_uuid}"

            # Cache the result
            self.content_cache[cache_key] = content
            self.last_capture[cache_key] = time.time()

            dbg(f'Captured {len(content)} characters from {terminal_uuid}')
            return content

        except Exception as e:
            err(f'TerminatorSidechatPlugin: Error capturing terminal content: {e}')
            return f"ERROR: {str(e)}"

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

    def get_all_terminals_metadata(self):
        """
        Get metadata for all terminals in current Terminator instance.

        Returns:
            List of dicts with terminal information:
            - uuid: Terminal UUID (string)
            - title: Terminal title
            - focused: Boolean indicating if terminal has focus
            - cwd: Current working directory
        """
        terminals_info = []

        try:
            for term in self.terminator.terminals:
                # Get custom title if set, otherwise use automatic title
                title = term.titlebar.get_custom_string()
                if not title:
                    title = term.titlebar.get_terminal_title() or 'Terminal'

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
                    'focused': focused,
                    'cwd': cwd
                })

            dbg(f'Retrieved metadata for {len(terminals_info)} terminals')
            return terminals_info

        except Exception as e:
            err(f'TerminatorSidechatPlugin: Error getting terminals metadata: {e}')
            return []

    def get_focused_terminal_uuid(self):
        """
        Get UUID of currently focused terminal.

        Returns:
            UUID string, or None if no terminal is focused
        """
        try:
            for term in self.terminator.terminals:
                vte = term.get_vte()
                if vte and vte.has_focus():
                    return term.uuid.urn
            return None
        except Exception as e:
            err(f'TerminatorSidechatPlugin: Error getting focused terminal: {e}')
            return None

    def clear_cache(self):
        """Clear content cache (useful when explicitly requested)"""
        self.content_cache.clear()
        self.last_capture.clear()
        dbg('Content cache cleared')

    def unload(self):
        """Clean up when plugin is unloaded"""
        self.clear_cache()
        dbg('TerminatorSidechatPlugin unloaded')
