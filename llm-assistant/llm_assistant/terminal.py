"""Terminal management mixin for llm-assistant.

This module provides D-Bus connection management and terminal setup:
- Terminator D-Bus service connection
- Plugin D-Bus service connection
- Terminal setup and verification
- Screenshot capture
- TUI render detection
"""

import os
import sys
import signal
import time
import base64
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

import dbus

from .prompt_detection import PromptDetector
from .ui import Confirm

if TYPE_CHECKING:
    from rich.console import Console


class TerminalMixin:
    """Mixin providing terminal management via D-Bus.

    Expects these attributes on self:
    - console: Rich Console for output
    - debug: bool for debug mode
    - early_terminal_uuid: str from initial detection
    - dbus_service: D-Bus Terminator service object
    - plugin_dbus: D-Bus plugin service object
    - chat_terminal_uuid: str UUID of chat terminal
    - exec_terminal_uuid: str UUID of exec terminal
    - screenshot_dir: Path for screenshot storage
    - screenshot_files: list of screenshot file paths
    - _debug: method for debug output
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    debug: bool
    early_terminal_uuid: Optional[str]
    dbus_service: Optional[object]
    plugin_dbus: Optional[object]
    chat_terminal_uuid: Optional[str]
    exec_terminal_uuid: Optional[str]
    screenshot_dir: Path
    screenshot_files: List[str]

    def _get_current_terminal_uuid_early(self) -> Optional[str]:
        """
        Get current terminal UUID from environment BEFORE acquiring lock.
        This enables tab-specific locking for multi-tab support.

        Uses TERMINATOR_UUID environment variable which is stable and set
        at terminal creation time (doesn't change when focus changes).

        Returns:
            Terminal UUID string, or None if not in Terminator
        """
        # Use TERMINATOR_UUID env var - stable, doesn't change with focus
        env_uuid = os.environ.get('TERMINATOR_UUID')
        if env_uuid:
            return self._normalize_uuid(env_uuid)
        return None  # Not running in Terminator

    def _normalize_uuid(self, uuid_value) -> Optional[str]:
        """Normalize UUID to Python string (ensures dbus.String -> str conversion)"""
        if uuid_value is None or uuid_value == '':
            return None
        # Explicitly convert to Python str (handles dbus.String)
        return str(uuid_value)

    def _reconnect_dbus(self) -> bool:
        """Attempt to reconnect to Terminator D-Bus with timeout.

        Uses SIGALRM for timeout protection. On timeout or error, ensures
        D-Bus state is cleaned up to prevent stale connections.
        """
        # Use SIGALRM for timeout (safe at startup, before asyncio event loop)
        def timeout_handler(signum, frame):
            raise TimeoutError("D-Bus connection timed out")

        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(10)  # 10 second timeout
        bus = None

        try:
            bus = dbus.SessionBus()

            # Discover actual Terminator service name (includes UUID suffix)
            # Check for multiple instances
            terminator_services = [
                name for name in bus.list_names()
                if name.startswith('net.tenshu.Terminator2') and not name.endswith('.Assistant')
            ]

            if len(terminator_services) > 1:
                # Multiple Terminator instances - try to pick the right one
                # Check if TERMINATOR_UUID environment variable can help identify our instance
                env_uuid = os.environ.get('TERMINATOR_UUID', '')
                matching_service = None
                for service in terminator_services:
                    if env_uuid and env_uuid in service:
                        matching_service = service
                        break
                if matching_service:
                    self._debug(f"Matched Terminator instance via TERMINATOR_UUID: {matching_service}")
                    service_name = matching_service
                else:
                    # Can't determine - use first and warn
                    self.console.print(f"[yellow]Multiple Terminator instances detected ({len(terminator_services)})[/]")
                    self.console.print(f"[yellow]Using first found: {terminator_services[0]}[/]")
                    service_name = terminator_services[0]
            else:
                service_name = terminator_services[0] if terminator_services else None

            if not service_name:
                service_name = 'net.tenshu.Terminator2'  # Fallback for older versions

            self._debug(f"Connected to Terminator D-Bus: {service_name}")
            self.dbus_service = bus.get_object(service_name, '/net/tenshu/Terminator2')
            return True
        except TimeoutError:
            self.console.print("[red]D-Bus connection timed out (10s)[/]")
            self.console.print("[yellow]Terminator may not be running or D-Bus is unresponsive[/]")
            # Clean up any partial state
            self.dbus_service = None
            return False
        except dbus.exceptions.DBusException as e:
            self.console.print(f"[red]D-Bus reconnection failed: {e}[/]")
            self.dbus_service = None
            return False
        except Exception as e:
            self.console.print(f"[red]D-Bus reconnection error ({type(e).__name__}): {e}[/]")
            self.dbus_service = None
            return False
        finally:
            signal.alarm(0)  # Cancel alarm
            signal.signal(signal.SIGALRM, old_handler)  # Restore handler

    def _check_dbus_connection(self) -> bool:
        """Verify D-Bus is still connected"""
        try:
            # Try a simple D-Bus operation
            self.dbus_service.get_terminals()
            return True
        except Exception:
            return False

    def _connect_to_terminator(self):
        """Connect to Terminator and plugin via D-Bus"""
        # Connect to Terminator's main D-Bus service for terminal management
        if not self._reconnect_dbus():
            self.console.print("[red]Error: Could not connect to Terminator D-Bus service[/]")
            self.console.print("Ensure Terminator is running with D-Bus enabled")
            sys.exit(1)

        # Connect to plugin's D-Bus service for terminal content/commands
        if not self._connect_to_plugin_dbus():
            self.console.print("[red]Error: Plugin D-Bus service not available[/]")
            self.console.print("Ensure TerminatorAssistant plugin is:")
            self.console.print("  1. Installed in ~/.config/terminator/plugins/")
            self.console.print("  2. Enabled in Terminator Preferences > Plugins")
            self.console.print("  3. Terminator has been restarted after enabling")
            sys.exit(1)

        # Initialize content change receiver for signal-based monitoring
        self.content_change_receiver = ContentChangeReceiver(
            debug_callback=self._debug if hasattr(self, '_debug') else None
        )
        if self.content_change_receiver.start():
            self._debug("Content change receiver started")
        else:
            self._debug("Content change receiver not started (polling fallback)")
            self.content_change_receiver = None

    def _connect_to_plugin_dbus(self) -> bool:
        """Connect to plugin's D-Bus service"""
        try:
            bus = dbus.SessionBus()
            self.plugin_dbus = bus.get_object(
                'net.tenshu.Terminator2.Assistant',
                '/net/tenshu/Terminator2/Assistant'
            )

            # Log plugin version for diagnostics (debug only)
            try:
                version = self.plugin_dbus.get_plugin_version()
                self._debug(f"Plugin version: {version}")
            except Exception:
                pass  # Ignore version check failures

            return True
        except dbus.exceptions.DBusException as e:
            # Provide specific guidance based on the error
            error_name = e.get_dbus_name() if hasattr(e, 'get_dbus_name') else str(e)
            if 'ServiceUnknown' in str(error_name) or 'ServiceUnknown' in str(e):
                self.console.print("[yellow]Plugin D-Bus service not registered[/]")
                self.console.print("[dim]The TerminatorAssistant plugin is not running.[/]")
            elif 'NoReply' in str(error_name):
                self.console.print("[yellow]Plugin D-Bus service not responding[/]")
            else:
                self.console.print(f"[yellow]Plugin D-Bus error: {e}[/]")
            return False
        except Exception as e:
            # Check if D-Bus session bus itself is unavailable
            if 'DBUS_SESSION_BUS_ADDRESS' not in os.environ:
                self.console.print("[yellow]D-Bus session bus not available[/]")
                self.console.print("[dim]Try: export $(dbus-launch)[/]")
            else:
                self.console.print(f"[yellow]Plugin D-Bus connection failed: {e}[/]")
            return False

    def _check_plugin_available(self) -> bool:
        """Verify plugin D-Bus service is available"""
        try:
            # Try a simple D-Bus call to check if service is alive
            self.plugin_dbus.get_focused_terminal_uuid()
            return True
        except Exception:
            return False

    def _reconnect_plugin(self) -> bool:
        """Attempt to reconnect to plugin D-Bus service"""
        return self._connect_to_plugin_dbus()

    def setup_terminals(self):
        """Auto-create Exec terminal with retry logic"""
        self.console.print("[cyan]Setting up terminals...[/]")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Check D-Bus connection
                if not self._check_dbus_connection():
                    if not self._reconnect_dbus():
                        raise Exception("D-Bus reconnection failed")

                # Use early_terminal_uuid captured at startup to avoid race condition
                # where user switches tabs before setup_terminals() is called
                self.chat_terminal_uuid = self.early_terminal_uuid

                # Check for existing Exec pane or offer to reuse single other pane
                try:
                    terminals = self.plugin_dbus.get_terminals_in_same_tab(self.chat_terminal_uuid)

                    # Filter out chat terminal (already normalized at assignment)
                    other_terminals = [t for t in terminals if str(t['uuid']) != self.chat_terminal_uuid]

                    # First: Look for existing Assistant Exec pane
                    # (Per-tab lock ensures only one assistant per tab, so any Exec pane is ours)
                    for t in other_terminals:
                        title = t.get('title', '')
                        if title.startswith('Assistant: Exec'):
                            self.exec_terminal_uuid = self._normalize_uuid(t['uuid'])
                            self.console.print("[green]✓[/] Terminals ready")
                            return  # Success - reusing existing exec terminal

                    # Second: If exactly one other pane, offer to use it
                    if len(other_terminals) == 1:
                        existing_pane = other_terminals[0]
                        pane_title = existing_pane.get('title', 'Untitled')
                        use_existing = Confirm.ask(f"Use '{pane_title}' as Exec pane?", default=True)

                        if use_existing:
                            self.exec_terminal_uuid = self._normalize_uuid(existing_pane['uuid'])
                            self.console.print("[green]✓[/] Terminals ready")
                            return  # Success - using existing terminal
                except dbus.exceptions.DBusException as e:
                    # D-Bus specific errors (method not found, connection issues, etc.)
                    error_msg = str(e)
                    if 'Unknown method' in error_msg or 'does not exist' in error_msg:
                        self.console.print("[red]ERROR: Plugin method 'get_terminals_in_same_tab' not found![/]")
                        self.console.print("[red]Please restart Terminator to load the updated plugin.[/red]")
                    else:
                        self.console.print(f"[red]D-Bus error enumerating terminals: {e}[/]")
                    self.console.print("[yellow]Creating new Exec terminal as fallback...[/]")
                except Exception as e:
                    # Other unexpected errors
                    self.console.print(f"[red]Unexpected error ({type(e).__name__}): {e}[/]")
                    self.console.print("[yellow]Creating new Exec terminal as fallback...[/]")

                # Split vertically to create Exec terminal (to the right)
                exec_uuid = self.dbus_service.vsplit(
                    self.chat_terminal_uuid,
                    dbus.Dictionary({
                        'title': 'Assistant: Exec'
                    }, signature='ss')
                )
                if str(exec_uuid).startswith('ERROR'):
                    raise Exception(f"Failed to split terminal: {exec_uuid}")
                self.exec_terminal_uuid = self._normalize_uuid(exec_uuid)

                self.console.print("[green]✓[/] Terminals ready")
                return  # Success

            except Exception as e:
                if attempt < max_retries - 1:
                    self.console.print(f"[yellow]Retry {attempt+1}/{max_retries}: {e}[/]")
                    time.sleep(1)
                else:
                    self.console.print(f"[red]Failed to setup terminals after {max_retries} attempts: {e}[/]")
                    sys.exit(1)

    def _verify_exec_terminal(self) -> bool:
        """Check if exec terminal still exists"""
        try:
            terminals = self.plugin_dbus.get_all_terminals_metadata()

            # Debug output for terminal verification
            self._debug(f"Looking for exec UUID: {repr(self.exec_terminal_uuid)} (type: {type(self.exec_terminal_uuid).__name__})")
            self._debug(f"Plugin returned {len(terminals)} terminals")
            for t in terminals:
                self._debug(f"  - {repr(t['uuid'])} (type: {type(t['uuid']).__name__}) | Title: {t.get('title', 'N/A')}")
                if t['uuid'] == self.exec_terminal_uuid:
                    self._debug("    ✓ EXACT MATCH!")

            # exec_terminal_uuid is already normalized at assignment
            return any(str(t['uuid']) == self.exec_terminal_uuid for t in terminals)
        except Exception as e:
            self._debug(f"Verification error: {e}")
            return False

    def _get_all_terminals_with_content(self) -> List[Dict]:
        """Get all terminals with their content for context capture.

        Returns list of dicts with uuid, title, and content for each terminal.
        Used by ReportMixin for evidence capture.
        """
        result = []
        try:
            terminals = self.plugin_dbus.get_all_terminals_metadata()
            for t in terminals:
                uuid = str(t.get('uuid', ''))
                title = t.get('title', 'unknown')
                try:
                    content = self.plugin_dbus.capture_terminal_content(uuid, -1)
                except Exception:
                    content = ''
                result.append({
                    'uuid': uuid,
                    'title': title,
                    'content': content
                })
        except Exception as e:
            self._debug(f"Failed to get terminals with content: {e}")
        return result

    def _recreate_exec_terminal(self) -> bool:
        """Recreate exec terminal if closed"""
        try:
            self.console.print("[yellow]Recreating Exec terminal...[/]")

            # Create new exec terminal by splitting from chat terminal
            # Use chat_terminal_uuid (stable) instead of get_focused_terminal()
            exec_uuid = self.dbus_service.vsplit(
                self.chat_terminal_uuid,
                dbus.Dictionary({'title': 'Assistant: Exec'}, signature='ss')
            )
            if str(exec_uuid).startswith('ERROR'):
                raise Exception(f"Failed to split terminal: {exec_uuid}")

            self.exec_terminal_uuid = self._normalize_uuid(exec_uuid)

            # Clear plugin cache to avoid stale data from old terminal
            try:
                self.plugin_dbus.clear_cache()
                self._debug("Plugin cache cleared after exec terminal recreation")
            except Exception as e:
                self._debug(f"Could not clear plugin cache: {e}")

            # Wait for shell prompt to render (prevents false TUI detection)
            # New terminals have minimal scrollback which triggers TUI heuristic
            max_wait = 2.0
            signal_timeout = 0.1  # Used for both signal wait and polling fallback
            start_time = time.time()

            # Use signal-based waiting if content change receiver is available
            use_signals = (hasattr(self, 'content_change_receiver') and
                          self.content_change_receiver and
                          self.content_change_receiver.is_running())
            if use_signals:
                self.plugin_dbus.subscribe_content_changes(self.exec_terminal_uuid)
                # Drain any pending signals from before subscription
                self.content_change_receiver.get_all_changes()
                self._debug("Using signal-based shell ready detection")

            try:
                while time.time() - start_time < max_wait:
                    try:
                        content = self.plugin_dbus.capture_terminal_content(
                            self.exec_terminal_uuid, -1
                        )
                        if content and PromptDetector.detect_prompt_at_end(content):
                            self._debug(f"Shell prompt detected after {time.time() - start_time:.2f}s")
                            break
                    except Exception:
                        pass

                    # Wait for content change signal or use polling fallback
                    if use_signals:
                        changed_uuid = self.content_change_receiver.get_change(timeout=signal_timeout)
                        if changed_uuid and changed_uuid != self.exec_terminal_uuid:
                            continue  # Signal for different terminal, keep waiting
                    else:
                        time.sleep(signal_timeout)
                else:
                    self._debug("Shell prompt wait timed out (continuing anyway)")
            finally:
                if use_signals:
                    self.plugin_dbus.unsubscribe_content_changes(self.exec_terminal_uuid)

            self.console.print(f"[green]✓[/] Exec terminal restored: {exec_uuid[:8]}...")
            return True

        except Exception as e:
            self.console.print(f"[red]Failed to recreate exec terminal: {e}[/]")
            return False

    def _capture_screenshot(self, terminal_uuid: str, unique_id: str = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Capture terminal screenshot and save to temp file.

        Args:
            terminal_uuid: UUID of terminal to capture
            unique_id: Optional unique identifier for filename (default: timestamp)

        Returns:
            Tuple of (temp_file_path, error_message). One will be None.
        """
        try:
            screenshot_data = self.plugin_dbus.capture_terminal_screenshot(terminal_uuid)

            if not screenshot_data or screenshot_data.startswith('ERROR'):
                error_msg = screenshot_data if screenshot_data else "No data returned"
                return None, error_msg

            image_bytes = base64.b64decode(screenshot_data)

            # Use mkstemp for atomic, secure temp file creation in dedicated directory
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.png',
                prefix='assistant_screenshot_',
                dir=str(self.screenshot_dir)
            )

            # Write image data
            with os.fdopen(temp_fd, 'wb') as f:
                f.write(image_bytes)

            self.screenshot_files.append(temp_path)
            return temp_path, None

        except Exception as e:
            return None, str(e)

    def wait_for_tui_render(self, terminal_uuid, max_wait=2.0, initial_content=None) -> bool:
        """
        Wait for TUI application to finish rendering by detecting content stability.

        First waits for content to CHANGE from initial state (TUI starting),
        then waits for stability (TUI finished rendering).

        Args:
            terminal_uuid: Terminal to monitor
            max_wait: Maximum wait time in seconds (default: 2.0)
            initial_content: Terminal content before command was sent (for change detection)

        Returns:
            True if content stabilized, False if timed out
        """
        start_time = time.time()
        signal_timeout = 0.15  # Used for both signal wait and polling fallback
        previous_content = None
        stable_count = 0
        content_changed = initial_content is None  # Skip change detection if no initial

        # Use signal-based waiting if content change receiver is available
        use_signals = (hasattr(self, 'content_change_receiver') and
                      self.content_change_receiver and
                      self.content_change_receiver.is_running())
        if use_signals:
            self.plugin_dbus.subscribe_content_changes(terminal_uuid)
            # Drain any pending signals from before subscription
            self.content_change_receiver.get_all_changes()
            self._debug("Using signal-based TUI render detection")

        try:
            while time.time() - start_time < max_wait:
                try:
                    current_content = self.plugin_dbus.capture_terminal_content(terminal_uuid, -1)

                    # First, wait for content to change from initial state
                    if not content_changed:
                        if current_content != initial_content:
                            content_changed = True
                            self._debug(f"TUI content changed after {time.time() - start_time:.2f}s")
                            previous_content = current_content

                        # Wait for content change signal or use polling fallback
                        if use_signals:
                            changed_uuid = self.content_change_receiver.get_change(timeout=signal_timeout)
                            if changed_uuid and changed_uuid != terminal_uuid:
                                continue  # Signal for different terminal
                        else:
                            time.sleep(signal_timeout)
                        continue

                    # Then check for stability
                    if current_content == previous_content:
                        stable_count += 1
                        if stable_count >= 2:  # Stable for 2 consecutive polls
                            self._debug(f"TUI render stabilized after {time.time() - start_time:.2f}s")
                            return True
                    else:
                        stable_count = 0
                        previous_content = current_content

                    # Wait for content change signal or use polling fallback
                    if use_signals:
                        changed_uuid = self.content_change_receiver.get_change(timeout=signal_timeout)
                        if changed_uuid and changed_uuid != terminal_uuid:
                            continue  # Signal for different terminal
                    else:
                        time.sleep(signal_timeout)
                except Exception as e:
                    self._debug(f"TUI render wait error: {e}")
                    if use_signals:
                        self.content_change_receiver.get_change(timeout=signal_timeout)
                    else:
                        time.sleep(signal_timeout)

            self._debug(f"TUI render wait timed out after {max_wait}s")
            return False  # Timeout - proceed anyway
        finally:
            if use_signals:
                self.plugin_dbus.unsubscribe_content_changes(terminal_uuid)

    def _ensure_exec_terminal(self) -> bool:
        """Verify exec terminal exists, recreate if needed.

        Returns:
            True if exec terminal is ready, False if recreation failed.
        """
        if not self._verify_exec_terminal():
            self.console.print("[yellow]Exec terminal not found[/]")
            if not self._recreate_exec_terminal():
                return False
        return True

    def _get_exec_terminal_cwd(self) -> str:
        """Get the current working directory of the exec terminal."""
        try:
            terminals = self.plugin_dbus.get_terminals_in_same_tab(self.chat_terminal_uuid)
            exec_term = next((t for t in terminals if str(t['uuid']) == self.exec_terminal_uuid), None)
            return exec_term['cwd'] if exec_term else "unknown"
        except Exception:
            return "unknown"

    def has_selection(self, terminal_uuid: str) -> bool:
        """Check if terminal has text selected.

        Args:
            terminal_uuid: UUID of terminal to check

        Returns:
            True if terminal has active text selection, False otherwise
        """
        try:
            return bool(self.plugin_dbus.has_selection(terminal_uuid))
        except Exception as e:
            self._debug(f"has_selection error: {e}")
            return False

    def get_selection(self, terminal_uuid: str) -> Optional[str]:
        """Get currently selected text in terminal.

        Args:
            terminal_uuid: UUID of terminal

        Returns:
            Selected text as string, None if no selection or error
        """
        try:
            result = str(self.plugin_dbus.get_selection(terminal_uuid))
            if result.startswith('ERROR:'):
                self._debug(f"get_selection error: {result}")
                return None
            return result if result else None
        except Exception as e:
            self._debug(f"get_selection error: {e}")
            return None

    def paste_from_clipboard(self, terminal_uuid: str) -> bool:
        """Paste clipboard content to terminal.

        Args:
            terminal_uuid: UUID of terminal to paste to

        Returns:
            True on success, False on error
        """
        try:
            return bool(self.plugin_dbus.paste_from_clipboard(terminal_uuid))
        except Exception as e:
            self._debug(f"paste_from_clipboard error: {e}")
            return False

    def get_foreground_process(self, terminal_uuid: str) -> Optional[Dict[str, str]]:
        """Get info about the foreground process in the terminal.

        Args:
            terminal_uuid: UUID of terminal

        Returns:
            Dict with process info (pid, name, cmdline) or None on error
        """
        try:
            result = self.plugin_dbus.get_foreground_process(terminal_uuid)
            # Convert dbus dict to regular Python dict
            if result:
                return {str(k): str(v) for k, v in result.items()}
            return None
        except Exception as e:
            self._debug(f"get_foreground_process error: {e}")
            return None

    def scroll_by_lines(self, terminal_uuid: str, lines: int) -> bool:
        """Scroll terminal by N lines (positive=down, negative=up).

        Args:
            terminal_uuid: UUID of terminal to scroll
            lines: Number of lines to scroll

        Returns:
            True on success, False on error
        """
        try:
            return bool(self.plugin_dbus.scroll_by_lines(terminal_uuid, lines))
        except Exception as e:
            self._debug(f"scroll_by_lines error: {e}")
            return False

    def get_scrollback_info(self, terminal_uuid: str) -> Optional[Dict[str, int]]:
        """Get scrollback buffer information for a terminal.

        Args:
            terminal_uuid: UUID of terminal

        Returns:
            Dict with scrollback info (total_lines, visible_lines,
            current_position, scrollback_lines) or None on error
        """
        try:
            result = self.plugin_dbus.get_scrollback_info(terminal_uuid)
            # Convert dbus dict to regular Python dict
            if result:
                return {str(k): int(v) for k, v in result.items()}
            return None
        except Exception as e:
            self._debug(f"get_scrollback_info error: {e}")
            return None

    def search_in_scrollback(self, terminal_uuid: str, pattern: str,
                             case_sensitive: bool = False) -> List[Dict]:
        """Search for regex/text pattern in terminal scrollback.

        Args:
            terminal_uuid: UUID of terminal to search
            pattern: Regex pattern to search for
            case_sensitive: If True, search is case-sensitive

        Returns:
            List of dicts with match info (line_number, text, start_col, end_col)
        """
        try:
            result = self.plugin_dbus.search_in_scrollback(
                terminal_uuid, pattern, case_sensitive
            )
            # Convert dbus array to regular Python list
            matches = []
            for match in result:
                matches.append({
                    'line_number': int(match.get('line_number', 0)),
                    'text': str(match.get('text', '')),
                    'start_col': int(match.get('start_col', 0)),
                    'end_col': int(match.get('end_col', 0))
                })
            return matches
        except Exception as e:
            self._debug(f"search_in_scrollback error: {e}")
            return []

    def subscribe_content_changes(self, terminal_uuid: str) -> bool:
        """Subscribe to content change notifications for a terminal.

        Args:
            terminal_uuid: UUID of terminal to watch

        Returns:
            True on success, False on error
        """
        try:
            result = str(self.plugin_dbus.subscribe_content_changes(terminal_uuid))
            if result.startswith('ERROR'):
                self._debug(f"subscribe_content_changes error: {result}")
                return False
            return True
        except Exception as e:
            self._debug(f"subscribe_content_changes error: {e}")
            return False

    def unsubscribe_content_changes(self, terminal_uuid: str) -> bool:
        """Unsubscribe from content change notifications for a terminal.

        Args:
            terminal_uuid: UUID of terminal to stop watching

        Returns:
            True on success, False on error
        """
        try:
            return bool(self.plugin_dbus.unsubscribe_content_changes(terminal_uuid))
        except Exception as e:
            self._debug(f"unsubscribe_content_changes error: {e}")
            return False

    def get_subscribed_terminals(self) -> List[str]:
        """Get list of terminal UUIDs currently subscribed for content changes.

        Returns:
            List of terminal UUID strings
        """
        try:
            result = self.plugin_dbus.get_subscribed_terminals()
            return [str(uuid) for uuid in result]
        except Exception as e:
            self._debug(f"get_subscribed_terminals error: {e}")
            return []


# =============================================================================
# D-Bus Signal Receiver (for event-driven monitoring)
# =============================================================================

import threading
import queue

# Plugin D-Bus constants (for signal reception)
PLUGIN_BUS_NAME = 'net.tenshu.Terminator2.Assistant'
PLUGIN_BUS_PATH = '/net/tenshu/Terminator2/Assistant'


class ContentChangeReceiver:
    """Receives D-Bus signals for terminal content changes.

    Runs a GLib mainloop in a dedicated thread to receive D-Bus signals.
    Signals are delivered to a thread-safe queue that can be consumed
    by the main thread or asyncio event loop.

    Usage:
        receiver = ContentChangeReceiver()
        receiver.start()

        # In main loop:
        try:
            terminal_uuid = receiver.get_change(timeout=0.1)
            if terminal_uuid:
                # Handle content change
                pass
        except queue.Empty:
            pass

        receiver.stop()
    """

    def __init__(self, debug_callback=None):
        """Initialize the receiver.

        Args:
            debug_callback: Optional callable for debug output
        """
        self._loop = None
        self._thread = None
        self._change_queue = queue.Queue()
        self._running = False
        self._debug = debug_callback or (lambda x: None)
        self._bus = None

    def start(self) -> bool:
        """Start the GLib mainloop thread for signal reception.

        Returns:
            True if started successfully, False on error
        """
        if self._running:
            return True

        try:
            # Import GLib here to avoid import errors if not available
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib

            def run_mainloop():
                try:
                    # Set up D-Bus with GLib mainloop
                    DBusGMainLoop(set_as_default=True)
                    self._loop = GLib.MainLoop()
                    self._bus = dbus.SessionBus()

                    # Add signal receiver for content_changed
                    self._bus.add_signal_receiver(
                        self._on_content_changed,
                        signal_name='content_changed',
                        dbus_interface=PLUGIN_BUS_NAME,
                        path=PLUGIN_BUS_PATH
                    )

                    self._debug("ContentChangeReceiver: mainloop started")
                    self._running = True
                    self._loop.run()
                except Exception as e:
                    self._debug(f"ContentChangeReceiver: mainloop error: {e}")
                finally:
                    self._running = False

            self._thread = threading.Thread(target=run_mainloop, daemon=True)
            self._thread.start()

            # Wait briefly for thread to start
            time.sleep(0.1)
            return self._running

        except ImportError as e:
            self._debug(f"ContentChangeReceiver: GLib not available: {e}")
            return False
        except Exception as e:
            self._debug(f"ContentChangeReceiver: start error: {e}")
            return False

    def stop(self):
        """Stop the GLib mainloop thread."""
        if self._loop:
            try:
                self._loop.quit()
            except Exception as e:
                self._debug(f"ContentChangeReceiver: stop error: {e}")
        self._running = False
        self._loop = None

    def _on_content_changed(self, terminal_uuid):
        """D-Bus signal handler - queues terminal UUID for processing."""
        try:
            self._change_queue.put_nowait(str(terminal_uuid))
        except queue.Full:
            pass  # Drop if queue is full

    def get_change(self, timeout: float = None) -> Optional[str]:
        """Get next terminal UUID that changed.

        Args:
            timeout: Max seconds to wait (None=block forever, 0=non-blocking)

        Returns:
            Terminal UUID string, or None if no change available

        Raises:
            queue.Empty: If timeout and no change available
        """
        try:
            return self._change_queue.get(block=(timeout is None or timeout > 0),
                                          timeout=timeout)
        except queue.Empty:
            return None

    def get_all_changes(self) -> List[str]:
        """Get all pending terminal UUIDs that changed (non-blocking).

        Returns:
            List of terminal UUID strings
        """
        changes = []
        while True:
            try:
                changes.append(self._change_queue.get_nowait())
            except queue.Empty:
                break
        return changes

    def is_running(self) -> bool:
        """Check if the receiver is running."""
        return self._running

    @property
    def queue(self) -> queue.Queue:
        """Direct access to the change queue for advanced usage."""
        return self._change_queue
