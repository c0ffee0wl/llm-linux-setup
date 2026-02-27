"""GTK popup window for llm-guiassistant (thin shell).

This module provides the main GTK application and window:
- Single-instance D-Bus activated GTK application
- Loads web UI from llm-assistant daemon's HTTP server
- Window state persistence
- Drag-drop support for files and images

Note: Context gathering (X11/Wayland desktop) is handled by the daemon
directly on each query, not by this thin GTK shell.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

import gi
import requests

gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, Gio, GLib, WebKit2  # noqa: E402

from llm_tools_core import (  # noqa: E402
    ensure_daemon,
)


# Configuration
CONFIG_DIR = Path.home() / ".config" / "llm-guiassistant"
STATE_FILE = CONFIG_DIR / "state.json"
DEFAULT_WEB_PORT = 8741


def get_web_port() -> int:
    """Get web UI port from environment or default."""
    return int(os.environ.get('LLM_GUI_PORT', DEFAULT_WEB_PORT))


def load_window_state() -> dict:
    """Load saved window dimensions, or return defaults."""
    defaults = {"width": 600, "height": 700}
    min_size = {"width": 300, "height": 400}
    try:
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            result = {**defaults, **state}
            # Enforce minimum dimensions to prevent unusable windows
            result["width"] = max(result["width"], min_size["width"])
            result["height"] = max(result["height"], min_size["height"])
            return result
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return defaults


def save_window_state(width: int, height: int):
    """Save window dimensions for next session."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"width": width, "height": height}))
    except OSError:
        pass


class PopupWindow(Gtk.ApplicationWindow):
    """Main popup window - thin shell loading web UI from daemon."""

    def __init__(self, app, with_selection: bool = False, debug: bool = False):
        super().__init__(application=app, title="LLM GUI Assistant")

        self.debug = debug
        self.with_selection = with_selection
        # Include timestamp in session ID to avoid collision on rapid crash/restart
        # Encode with_selection in prefix so daemon knows whether to include X11 selection
        prefix = "guiassistant-sel" if with_selection else "guiassistant"
        self.session_id = f"{prefix}:{os.getpid()}-{int(time.time() * 1000)}"
        self.web_port = get_web_port()
        self._save_state_timeout_id = None
        self._initializing = True  # Prevent state save during initialization
        self._load_retry_count = 0  # Initialize before _load_web_ui() to avoid race

        # Window setup - store state for later use
        self._saved_state = load_window_state()
        self.set_default_size(self._saved_state["width"], self._saved_state["height"])
        self.set_position(Gtk.WindowPosition.MOUSE)
        self.connect("configure-event", self._on_configure)
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)

        # Build UI
        self._build_ui()

        # Ensure daemon is running in background thread
        # to avoid blocking GTK main thread
        threading.Thread(target=self._ensure_daemon_background, daemon=True).start()

    def _build_ui(self):
        """Build the popup UI with WebKit view."""
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.main_box)

        # Header bar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("LLM GUI Assistant")
        self.set_titlebar(header)

        # WebKit view for web UI
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.webview = WebKit2.WebView()
        self.webview.set_vexpand(True)

        # Register JavaScript → Python message handler for window control
        user_content_manager = self.webview.get_user_content_manager()
        user_content_manager.register_script_message_handler('windowControl')
        user_content_manager.connect(
            'script-message-received::windowControl',
            self._on_window_control_message
        )

        # Configure WebKit settings
        settings = self.webview.get_settings()
        settings.set_hardware_acceleration_policy(
            WebKit2.HardwareAccelerationPolicy.NEVER
        )

        # Disable caching to ensure fresh JS/CSS on each load
        context = self.webview.get_context()
        context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)

        # Enable developer tools in debug mode
        if self.debug:
            settings.set_enable_developer_extras(True)

        # Open external links in default browser
        self.webview.connect("decide-policy", self._on_decide_policy)

        # Load web UI from daemon
        self._load_web_ui()

        scrolled.add(self.webview)
        self.main_box.pack_start(scrolled, True, True, 0)

        # Set up drag and drop
        self._setup_drag_drop()

    def _load_web_ui(self, is_retry: bool = False):
        """Load the web UI from daemon's HTTP server.

        Args:
            is_retry: True if this is a retry after daemon restart (don't reset counter)
        """
        # Connect handlers (only once per webview instance)
        if not getattr(self.webview, '_handlers_connected', False):
            self.webview.connect("load-failed", self._on_load_failed)
            self.webview.connect("load-changed", self._on_load_changed_internal)
            self.webview._handlers_connected = True

        # Reset retry count on fresh load (not on retry)
        if not is_retry:
            self._load_retry_count = 0

        # Build URL with session ID
        url = f"http://localhost:{self.web_port}/?session={self.session_id}"

        if self.debug:
            print(f"[WebKit] Loading {url}")

        self.webview.load_uri(url)

        # Note: Context is now captured directly by daemon on each query
        # (see web_ui_server.py _handle_query for guiassistant sessions)

    def _on_load_changed_internal(self, webview, load_event):
        """Handle WebView load state changes - reset retry counter on success."""
        # Debug logging
        if self.debug:
            event_names = {
                WebKit2.LoadEvent.STARTED: "STARTED",
                WebKit2.LoadEvent.REDIRECTED: "REDIRECTED",
                WebKit2.LoadEvent.COMMITTED: "COMMITTED",
                WebKit2.LoadEvent.FINISHED: "FINISHED",
            }
            print(f"[WebKit] Load {event_names.get(load_event, load_event)}")

        # Reset retry counter on successful page commit (server responded).
        # Use COMMITTED rather than FINISHED because FINISHED also fires
        # after load-failed, which would defeat the retry limit.
        if load_event == WebKit2.LoadEvent.COMMITTED:
            self._load_retry_count = 0

    def _on_load_failed(self, webview, load_event, failing_uri, error):
        """Handle WebView load failures - try to restart daemon and retry."""
        if self.debug:
            print(f"[WebKit ERROR] Failed to load {failing_uri}: {error.message}")

        # Track retry attempts to avoid infinite loops
        self._load_retry_count += 1

        if self._load_retry_count <= 2:
            if self.debug:
                print(f"[WebKit] Attempting to restart daemon (attempt {self._load_retry_count})")

            # Try to restart daemon in background thread to avoid blocking GTK.
            # Use a weak reference to avoid calling methods on a destroyed window.
            import weakref
            weak_self = weakref.ref(self)

            def _try_restart():
                try:
                    if ensure_daemon():
                        def _schedule_retry():
                            obj = weak_self()
                            if obj is not None:
                                GLib.timeout_add(500, obj._retry_load_web_ui)
                            return False
                        GLib.idle_add(_schedule_retry)
                except Exception as e:
                    obj = weak_self()
                    if obj is not None and obj.debug:
                        print(f"[Daemon] Restart failed: {e}", flush=True)

            threading.Thread(target=_try_restart, daemon=True).start()
            return True

        # Show fallback content after retries exhausted
        html = f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px; text-align: center;">
        <h2>Cannot connect to daemon</h2>
        <p>The llm-assistant daemon could not be started.</p>
        <p>Try starting it manually: <code>llm-assistant --daemon</code></p>
        <p style="color: #666; font-size: 0.9em;">
            Expected URL: http://localhost:{self.web_port}/
        </p>
        </body>
        </html>
        """
        self.webview.load_html(html, None)
        return True

    def _retry_load_web_ui(self):
        """Retry loading web UI after daemon restart."""
        if self.debug:
            print("[WebKit] Retrying web UI load...")
        self._load_web_ui(is_retry=True)
        return False  # Don't repeat

    def _on_decide_policy(self, webview, decision, decision_type):
        """Handle navigation decisions - open external links in default browser."""
        # Handle new window requests (target="_blank" links)
        if decision_type == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            nav_action = decision.get_navigation_action()
            request = nav_action.get_request()
            uri = request.get_uri()

            if uri and (uri.startswith("http://") or uri.startswith("https://")):
                if self.debug:
                    print(f"[WebKit] Opening new window link in browser: {uri}")
                Gio.AppInfo.launch_default_for_uri(uri, None)
            decision.ignore()
            return True

        # Handle regular navigation
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            request = nav_action.get_request()
            uri = request.get_uri()

            # Allow navigation to our own daemon server
            if uri and (uri.startswith(f"http://localhost:{self.web_port}") or
                        uri.startswith(f"http://127.0.0.1:{self.web_port}")):
                decision.use()
                return False

            # Open external links in default browser
            if uri and (uri.startswith("http://") or uri.startswith("https://")):
                if self.debug:
                    print(f"[WebKit] Opening in browser: {uri}")
                Gio.AppInfo.launch_default_for_uri(uri, None)
                decision.ignore()
                return True

        decision.use()
        return False

    def _setup_drag_drop(self):
        """Set up drag and drop for files and images."""
        self.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [],
            Gdk.DragAction.COPY
        )
        self.drag_dest_add_uri_targets()
        self.drag_dest_add_image_targets()
        self.connect("drag-data-received", self._on_drag_data_received)

    def _ensure_daemon_background(self):
        """Ensure daemon is running (called from background thread)."""
        try:
            ensure_daemon()
        except Exception as e:
            if self.debug:
                print(f"[Daemon] Failed to start: {e}")

    def _on_configure(self, widget, event):
        """Save window dimensions on resize (debounced)."""
        # Skip state save during initialization (hidden mode)
        if self._initializing:
            return False

        if self._save_state_timeout_id is not None:
            GLib.source_remove(self._save_state_timeout_id)

        self._save_state_timeout_id = GLib.timeout_add(
            500,
            self._do_save_state,
            event.width,
            event.height
        )
        return False

    def _do_save_state(self, width: int, height: int) -> bool:
        """Actually save window state."""
        self._save_state_timeout_id = None
        save_window_state(width, height)
        # Update cached state so subsequent shows use current size
        self._saved_state = {"width": width, "height": height}
        return False

    def _on_delete(self, widget, event):
        """Handle window close - hide instead of destroy."""
        self._save_current_state()
        self.hide()
        return True

    def _save_current_state(self):
        """Save current window state immediately (cancels pending debounced save)."""
        if self._initializing:
            return  # Don't save during initialization
        if self._save_state_timeout_id is not None:
            GLib.source_remove(self._save_state_timeout_id)
            self._save_state_timeout_id = None

        alloc = self.get_allocation()
        save_window_state(alloc.width, alloc.height)
        # Update cached state so subsequent shows use current size
        self._saved_state = {"width": alloc.width, "height": alloc.height}

    def _on_key_press(self, widget, event):
        """Handle global key presses."""
        # Escape: Close window (same behavior as window close button)
        if event.keyval == Gdk.KEY_Escape:
            self._save_current_state()
            self.hide()
            return True

        return False

    def _on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """Handle drag and drop - upload to daemon."""
        uris = data.get_uris()
        file_paths = []
        if uris:
            for uri in uris:
                try:
                    path = GLib.filename_from_uri(uri)[0]
                    if os.path.isfile(path):
                        file_paths.append(path)
                except (GLib.Error, Exception) as e:
                    if self.debug:
                        print(f"[Drag] Error: {e}")

        if not file_paths:
            Gtk.drag_finish(drag_context, False, False, time)
            return

        # Upload files in background; report actual success via GLib.idle_add
        def _do_uploads():
            any_success = False
            for filepath in file_paths:
                if self._upload_file_sync(filepath):
                    any_success = True
            GLib.idle_add(Gtk.drag_finish, drag_context, any_success, False, time)

        threading.Thread(target=_do_uploads, daemon=True).start()

    def _upload_file_sync(self, filepath: str) -> bool:
        """Upload a file to the daemon's /upload endpoint (synchronous).

        Must be called from a background thread, not the GTK main thread.

        Returns:
            True if upload succeeded, False otherwise.
        """
        try:
            url = f"http://localhost:{self.web_port}/upload"
            with open(filepath, 'rb') as f:
                files = {'file': (Path(filepath).name, f)}
                response = requests.post(url, files=files, timeout=30)
                if response.ok:
                    try:
                        result = response.json()
                    except (json.JSONDecodeError, ValueError):
                        if self.debug:
                            print(f"[Upload] Invalid JSON response for {filepath}")
                        return False
                    # Notify web UI about the upload via JavaScript (must run on GTK thread)
                    temp_path = result.get('path', '')
                    if temp_path:
                        def _notify_ui():
                            js = f"""
                                window.pendingImages = window.pendingImages || [];
                                window.pendingImages.push({json.dumps(temp_path)});
                                if (typeof attachmentPanel !== 'undefined') {{
                                    attachmentPanel.add({json.dumps(temp_path)}, 'upload');
                                }}
                            """
                            self.webview.run_javascript(js, None, None)
                            return False
                        GLib.idle_add(_notify_ui)
                        if self.debug:
                            print(f"[Upload] Uploaded: {temp_path}")
                    return True
                return False
        except Exception as e:
            if self.debug:
                print(f"[Upload] Error: {e}")
            return False

    def _on_window_control_message(self, user_content_manager, js_result):
        """Handle window control messages from JavaScript."""
        try:
            data = json.loads(js_result.get_js_value().to_json(0))
            action = data.get('action')

            if action == 'minimize':
                self.iconify()
            elif action == 'restore':
                self.deiconify()
                self.present()
        except Exception as e:
            if self.debug:
                print(f"[WebKit] Window control error: {e}")


class PopupApplication(Gtk.Application):
    """Single-instance GTK application for llm-guiassistant."""

    def __init__(self, with_selection: bool = False, debug: bool = False, hidden: bool = False):
        super().__init__(
            application_id="com.llm.guiassistant",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        self.with_selection = with_selection
        self.debug = debug
        self.start_hidden = hidden
        self.window: Optional[PopupWindow] = None

    def do_command_line(self, command_line):
        """Handle command-line activation (supports remote instance activation).

        Parses arguments from second instances so --with-selection works
        when the application is already running.
        """
        args = command_line.get_arguments()
        if '--with-selection' in args:
            self.with_selection = True
            if self.window:
                self.window.with_selection = True
                # Re-generate session ID with selection prefix so daemon
                # knows to include X11 selection in context.
                # Also reload the web UI so the new session ID takes effect.
                self.window.session_id = (
                    f"guiassistant-sel:{os.getpid()}-{int(time.time() * 1000)}"
                )
                self.window._load_web_ui()
        self.do_activate()
        return 0

    def _ensure_daemon_background(self):
        """Start daemon in background thread (called from do_activate)."""
        try:
            ensure_daemon()
        except Exception as e:
            if self.debug:
                print(f"[Daemon] Background start failed: {e}", flush=True)

    def do_activate(self):
        """Handle application activation."""
        if not self.window:
            self.window = PopupWindow(
                self,
                with_selection=self.with_selection,
                debug=self.debug
            )

        if self.start_hidden:
            # First activation in hidden mode:
            # Show the window minimized so WebKit fully initializes,
            # then immediately hide it
            self.window.show_all()
            self.window.iconify()  # Minimize to taskbar
            GLib.idle_add(self._finish_hidden_init)  # Hide after event loop processes
            self.start_hidden = False  # Subsequent activations show normally
        else:
            # Enable state saving now that initialization is complete
            self.window._initializing = False
            # Restore saved window size before showing
            # (set_default_size only works before first show)
            self.window.resize(
                self.window._saved_state["width"],
                self.window._saved_state["height"]
            )
            # Ensure daemon is still running in background (may have died while hidden)
            threading.Thread(target=self._ensure_daemon_background, daemon=True).start()
            self.window.show_all()
            self.window.present()

    def _finish_hidden_init(self):
        """Complete hidden initialization - hide window and enable state saving."""
        self.window.hide()
        self.window._initializing = False
        return False  # Don't repeat

    def do_startup(self):
        """Handle application startup."""
        Gtk.Application.do_startup(self)

    def do_shutdown(self):
        """Handle application shutdown."""
        Gtk.Application.do_shutdown(self)
