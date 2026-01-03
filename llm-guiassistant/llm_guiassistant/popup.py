"""GTK popup window for llm-guiassistant (thin shell).

This module provides the main GTK application and window:
- Single-instance D-Bus activated GTK application
- Loads web UI from llm-assistant daemon's HTTP server
- Context gathering from X11/Wayland desktop
- Window state persistence
- Drag-drop support for files and images
"""

import json
import os
import tempfile
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
    gather_context,
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
    try:
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            return {**defaults, **state}
    except (json.JSONDecodeError, OSError):
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
        self.context = {}
        self.session_id = f"guiassistant:{os.getpid()}"
        self.web_port = get_web_port()
        self._save_state_timeout_id = None
        self._temp_files = []

        # Window setup
        state = load_window_state()
        self.set_default_size(state["width"], state["height"])
        self.set_position(Gtk.WindowPosition.MOUSE)
        self.connect("configure-event", self._on_configure)
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)

        # Build UI
        self._build_ui()

        # Gather initial context
        self._gather_context()

        # Ensure daemon is running
        ensure_daemon()

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

        # Configure WebKit settings
        settings = self.webview.get_settings()
        settings.set_hardware_acceleration_policy(
            WebKit2.HardwareAccelerationPolicy.NEVER
        )

        # Enable developer tools in debug mode
        if self.debug:
            settings.set_enable_developer_extras(True)

        # Load web UI from daemon
        self._load_web_ui()

        scrolled.add(self.webview)
        self.main_box.pack_start(scrolled, True, True, 0)

        # Set up drag and drop
        self._setup_drag_drop()

    def _load_web_ui(self):
        """Load the web UI from daemon's HTTP server."""
        # Connect load handlers for debugging
        if self.debug:
            self.webview.connect("load-changed", self._on_load_changed)
            self.webview.connect("load-failed", self._on_load_failed)

        # Build URL with session ID
        url = f"http://localhost:{self.web_port}/?session={self.session_id}"

        if self.debug:
            print(f"[WebKit] Loading {url}")

        self.webview.load_uri(url)

        # Post context to daemon after short delay (let page load)
        GLib.timeout_add(500, self._post_context)

    def _post_context(self) -> bool:
        """POST context to daemon's /context endpoint."""
        try:
            url = f"http://localhost:{self.web_port}/context"
            data = {
                "session": self.session_id,
                "context": self.context,
            }
            requests.post(url, json=data, timeout=2)
            if self.debug:
                print(f"[Context] Posted context to daemon")
        except Exception as e:
            if self.debug:
                print(f"[Context] Failed to post context: {e}")
        return False  # Don't repeat

    def _on_load_changed(self, webview, load_event):
        """Handle WebView load state changes."""
        event_names = {
            WebKit2.LoadEvent.STARTED: "STARTED",
            WebKit2.LoadEvent.REDIRECTED: "REDIRECTED",
            WebKit2.LoadEvent.COMMITTED: "COMMITTED",
            WebKit2.LoadEvent.FINISHED: "FINISHED",
        }
        print(f"[WebKit] Load {event_names.get(load_event, load_event)}")

    def _on_load_failed(self, webview, load_event, failing_uri, error):
        """Handle WebView load failures."""
        print(f"[WebKit ERROR] Failed to load {failing_uri}: {error.message}")

        # Show fallback content
        html = f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px; text-align: center;">
        <h2>Cannot connect to daemon</h2>
        <p>The llm-assistant daemon may not be running.</p>
        <p>Start it with: <code>llm-assistant --daemon</code></p>
        <p style="color: #666; font-size: 0.9em;">
            Expected URL: http://localhost:{self.web_port}/
        </p>
        </body>
        </html>
        """
        self.webview.load_html(html, None)
        return True

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

    def _gather_context(self):
        """Gather desktop context."""
        self.context = gather_context()
        if not self.with_selection:
            self.context['selection'] = None
            self.context['selection_truncated'] = False

    def _on_configure(self, widget, event):
        """Save window dimensions on resize (debounced)."""
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
        return False

    def _on_delete(self, widget, event):
        """Handle window close - hide instead of destroy."""
        if self._save_state_timeout_id is not None:
            GLib.source_remove(self._save_state_timeout_id)
            self._save_state_timeout_id = None
            alloc = self.get_allocation()
            save_window_state(alloc.width, alloc.height)

        self._cleanup_temp_files()
        self.hide()
        return True

    def _cleanup_temp_files(self):
        """Clean up temporary files created during session."""
        for filepath in self._temp_files:
            try:
                if os.path.exists(filepath):
                    os.unlink(filepath)
            except OSError:
                pass
        self._temp_files.clear()

    def _on_key_press(self, widget, event):
        """Handle global key presses."""
        # Escape: Close window
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True

        return False

    def _on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """Handle drag and drop - upload to daemon."""
        uris = data.get_uris()
        if uris:
            for uri in uris:
                try:
                    path = GLib.filename_from_uri(uri)[0]
                    if os.path.isfile(path):
                        self._upload_file(path)
                except Exception as e:
                    if self.debug:
                        print(f"[Drag] Error: {e}")
        Gtk.drag_finish(drag_context, True, False, time)

    def _upload_file(self, filepath: str):
        """Upload a file to the daemon's /upload endpoint."""
        try:
            url = f"http://localhost:{self.web_port}/upload"
            with open(filepath, 'rb') as f:
                files = {'file': (Path(filepath).name, f)}
                response = requests.post(url, files=files, timeout=30)
                if response.ok:
                    result = response.json()
                    # Notify web UI about the upload via JavaScript
                    temp_path = result.get('path', '')
                    if temp_path:
                        js = f"window.pendingImages = window.pendingImages || []; window.pendingImages.push({json.dumps(temp_path)});"
                        self.webview.run_javascript(js, None, None, None)
                        if self.debug:
                            print(f"[Upload] Uploaded: {temp_path}")
        except Exception as e:
            if self.debug:
                print(f"[Upload] Error: {e}")

    def refresh_context(self):
        """Refresh context and post to daemon."""
        self._gather_context()
        self._post_context()


class PopupApplication(Gtk.Application):
    """Single-instance GTK application for llm-guiassistant."""

    def __init__(self, with_selection: bool = False, debug: bool = False):
        super().__init__(
            application_id="com.llm.guiassistant",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.with_selection = with_selection
        self.debug = debug
        self.window: Optional[PopupWindow] = None

    def do_activate(self):
        """Handle application activation."""
        if not self.window:
            self.window = PopupWindow(
                self,
                with_selection=self.with_selection,
                debug=self.debug
            )
        self.window.show_all()
        self.window.present()

    def do_startup(self):
        """Handle application startup."""
        Gtk.Application.do_startup(self)

    def do_shutdown(self):
        """Handle application shutdown."""
        Gtk.Application.do_shutdown(self)
