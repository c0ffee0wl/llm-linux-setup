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
            poll_interval = 0.1
            start_time = time.time()
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
                time.sleep(poll_interval)
            else:
                self._debug("Shell prompt wait timed out (continuing anyway)")

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
        poll_interval = 0.15
        previous_content = None
        stable_count = 0
        content_changed = initial_content is None  # Skip change detection if no initial

        while time.time() - start_time < max_wait:
            try:
                current_content = self.plugin_dbus.capture_terminal_content(terminal_uuid, -1)

                # First, wait for content to change from initial state
                if not content_changed:
                    if current_content != initial_content:
                        content_changed = True
                        self._debug(f"TUI content changed after {time.time() - start_time:.2f}s")
                        previous_content = current_content
                    time.sleep(poll_interval)
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

                time.sleep(poll_interval)
            except Exception as e:
                self._debug(f"TUI render wait error: {e}")
                time.sleep(poll_interval)

        self._debug(f"TUI render wait timed out after {max_wait}s")
        return False  # Timeout - proceed anyway

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
