"""GTK popup window for llm-guiassistant.

This module provides the main GTK application and window:
- Single-instance D-Bus activated GTK application
- WebKit2GTK for rich Markdown rendering
- Context gathering from X11/Wayland desktop
- Streaming responses from llm-assistant daemon
- Drag-drop support for files and images
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, Gio, GLib, WebKit2

from llm_tools_core import (
    ensure_daemon,
    stream_events,
    gather_context,
    format_context_for_llm,
    get_session_type,
    capture_screenshot,
)

from .history import InputHistory


# Configuration paths
CONFIG_DIR = Path.home() / ".config" / "llm-guiassistant"
STATE_FILE = CONFIG_DIR / "state.json"
ASSETS_DIR = Path.home() / ".local" / "share" / "llm-guiassistant" / "js"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "conversation.html"


def load_window_state() -> dict:
    """Load saved window dimensions, or return defaults."""
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {"width": 500, "height": 600}


def save_window_state(width: int, height: int):
    """Save window dimensions for next session."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"width": width, "height": height}))


class StreamingQuery:
    """Background thread for streaming daemon responses."""

    def __init__(self, on_event: Callable[[dict], None]):
        self.on_event = on_event
        self.accumulated_text = ""
        self.cancelled = False
        self._message_id = None

    def start(self, request: dict):
        """Start streaming query in background thread."""
        self.accumulated_text = ""
        self.cancelled = False
        self._message_id = str(time.time())

        thread = threading.Thread(target=self._worker, args=(request,), daemon=True)
        thread.start()

    def cancel(self):
        """Cancel the current streaming query."""
        self.cancelled = True

    def _worker(self, request: dict):
        """Background worker that streams events."""
        ensure_daemon()

        for event in stream_events(request):
            if self.cancelled:
                break

            event_type = event.get("type", "")

            if event_type == "text":
                # Accumulate text on Python side for stable Markdown rendering
                self.accumulated_text += event.get("content", "")
                # Schedule UI update with full accumulated text
                GLib.idle_add(self.on_event, {
                    "type": "text",
                    "content": self.accumulated_text,
                    "message_id": self._message_id
                })
            elif event_type == "tool_start":
                tool_name = event.get("tool", "unknown")
                GLib.idle_add(self.on_event, {
                    "type": "tool_status",
                    "message": f"Executing {tool_name}..."
                })
            elif event_type == "tool_done":
                pass  # Tool completion handled by next text event
            elif event_type == "error":
                GLib.idle_add(self.on_event, {
                    "type": "error",
                    "message": event.get("message", "Unknown error")
                })
            elif event_type == "done":
                GLib.idle_add(self.on_event, {"type": "done"})
                break


class PopupWindow(Gtk.ApplicationWindow):
    """Main popup window with WebKit conversation view."""

    def __init__(self, app, with_selection: bool = False, debug: bool = False):
        super().__init__(application=app, title="LLM Assistant")

        self.debug = debug
        self.with_selection = with_selection
        self.context = {}
        self.attachments = []
        self.streaming = False
        self.session_id = f"guiassistant:{os.getpid()}"

        # Input history for shell-like navigation
        self.history = InputHistory()

        # Streaming query handler
        self.query = StreamingQuery(self._on_stream_event)

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

    def _build_ui(self):
        """Build the popup UI."""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        # Header bar with model selector and new session button
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("LLM Assistant")

        # New session button
        new_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        new_btn.set_tooltip_text("New Session")
        new_btn.connect("clicked", self._on_new_session)
        header.pack_start(new_btn)

        self.set_titlebar(header)

        # Context panel (collapsible)
        self.context_label = Gtk.Label()
        self.context_label.set_line_wrap(True)
        self.context_label.set_xalign(0)
        self.context_label.set_margin_start(8)
        self.context_label.set_margin_end(8)
        self.context_label.set_margin_top(4)
        self.context_label.set_margin_bottom(4)
        self.context_label.get_style_context().add_class("dim-label")
        vbox.pack_start(self.context_label, False, False, 0)

        # WebKit view for conversation
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.webview = WebKit2.WebView()
        self.webview.set_vexpand(True)

        # Enable file:// access for loading JS assets from ~/.local/share/
        settings = self.webview.get_settings()
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)

        self._load_template()

        scrolled.add(self.webview)
        vbox.pack_start(scrolled, True, True, 0)

        # Input area
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        input_box.set_margin_start(8)
        input_box.set_margin_end(8)
        input_box.set_margin_top(4)
        input_box.set_margin_bottom(8)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Ask anything...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_submit)
        self.entry.connect("key-press-event", self._on_entry_key_press)
        input_box.pack_start(self.entry, True, True, 0)

        # Send/Stop button
        self.send_btn = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        self.send_btn.set_tooltip_text("Send (Enter)")
        self.send_btn.connect("clicked", self._on_submit)
        input_box.pack_start(self.send_btn, False, False, 0)

        vbox.pack_start(input_box, False, False, 0)

        # Set up drag and drop
        self._setup_drag_drop()

    def _load_template(self):
        """Load the conversation HTML template."""
        if TEMPLATE_PATH.exists():
            html = TEMPLATE_PATH.read_text()
            # Expand $HOME in the template
            html = html.replace("$HOME", str(Path.home()))
            self.webview.load_html(html, f"file://{TEMPLATE_PATH.parent}/")
        else:
            # Fallback minimal template
            self.webview.load_html("""
                <html><body style="font-family: sans-serif; padding: 20px;">
                <p>Template not found. Please reinstall llm-guiassistant.</p>
                </body></html>
            """, "file://")

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

        # Update context label
        parts = []
        if self.context.get("app_class"):
            parts.append(self.context["app_class"])
        if self.context.get("window_title"):
            title = self.context["window_title"]
            if len(title) > 40:
                title = title[:40] + "..."
            parts.append(title)

        if parts:
            self.context_label.set_text(" - ".join(parts))
            self.context_label.show()
        else:
            self.context_label.hide()

    def _on_configure(self, widget, event):
        """Save window dimensions on resize."""
        save_window_state(event.width, event.height)
        return False

    def _on_delete(self, widget, event):
        """Handle window close - hide instead of destroy."""
        self.hide()
        return True  # Prevent destruction

    def _on_key_press(self, widget, event):
        """Handle global key presses."""
        if event.keyval == Gdk.KEY_Escape:
            if self.streaming:
                self.query.cancel()
                self._set_streaming(False)
            else:
                self.hide()
            return True
        return False

    def _on_entry_key_press(self, widget, event):
        """Handle key presses in the entry field."""
        if event.keyval == Gdk.KEY_Up:
            text = self.history.navigate(-1, self.entry.get_text())
            self.entry.set_text(text)
            self.entry.set_position(-1)  # Move cursor to end
            return True
        elif event.keyval == Gdk.KEY_Down:
            text = self.history.navigate(+1, self.entry.get_text())
            self.entry.set_text(text)
            self.entry.set_position(-1)
            return True
        return False

    def _on_submit(self, widget):
        """Handle submit button or Enter key."""
        if self.streaming:
            return

        query = self.entry.get_text().strip()
        if not query:
            return

        # Add to history
        self.history.add(query)

        # Clear entry
        self.entry.set_text("")

        # Build full query with context
        context_text = format_context_for_llm(self.context)
        if context_text:
            full_query = f"{context_text}\n\n{query}"
        else:
            full_query = query

        # Show user message in WebView
        self._run_js(f"appendMessage('user', {json.dumps(query)}, null)")

        # Build request
        request = {
            "cmd": "query",
            "tid": self.session_id,
            "q": full_query,
            "mode": "assistant"
        }

        # Add image attachments
        if self.attachments:
            request["images"] = [str(p) for p in self.attachments]
            self.attachments.clear()

        # Start streaming
        self._set_streaming(True)
        self.query.start(request)

    def _on_new_session(self, widget):
        """Handle new session button click."""
        # Clear attachments
        self.attachments.clear()

        # Clear conversation in WebView
        self._run_js("clearConversation()")

        # Send clear command to daemon
        request = {"cmd": "clear", "tid": self.session_id}
        for _ in stream_events(request):
            pass  # Just consume the response

        # Re-gather context
        self._gather_context()

    def _on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """Handle drag and drop data."""
        uris = data.get_uris()
        if uris:
            for uri in uris:
                try:
                    path = GLib.filename_from_uri(uri)[0]
                    if os.path.isfile(path):
                        # Check if it's an image
                        ext = Path(path).suffix.lower()
                        if ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']:
                            self.attachments.append(Path(path))
                            self._update_attachment_indicator()
                except Exception:
                    pass
        Gtk.drag_finish(drag_context, True, False, time)

    def _update_attachment_indicator(self):
        """Update the attachment count indicator."""
        count = len(self.attachments)
        if count > 0:
            self.entry.set_placeholder_text(f"Ask anything... ({count} attachment{'s' if count > 1 else ''})")
        else:
            self.entry.set_placeholder_text("Ask anything...")

    def _on_stream_event(self, event):
        """Handle streaming events from background thread."""
        event_type = event.get("type", "")

        if event_type == "text":
            content = event.get("content", "")
            message_id = event.get("message_id")
            self._run_js(f"appendMessage('assistant', {json.dumps(content)}, {json.dumps(message_id)})")

        elif event_type == "tool_status":
            message = event.get("message", "")
            self._run_js(f"addToolStatus({json.dumps(message)})")

        elif event_type == "error":
            message = event.get("message", "Unknown error")
            self._run_js(f"addError({json.dumps(message)})")

        elif event_type == "done":
            self._run_js("finalizeMessage()")
            self._set_streaming(False)

    def _set_streaming(self, streaming: bool):
        """Update UI for streaming state."""
        self.streaming = streaming

        if streaming:
            # Change button to stop icon
            self.send_btn.set_image(
                Gtk.Image.new_from_icon_name("process-stop-symbolic", Gtk.IconSize.BUTTON)
            )
            self.send_btn.set_tooltip_text("Stop (Escape)")
            self.send_btn.disconnect_by_func(self._on_submit)
            self.send_btn.connect("clicked", lambda w: self.query.cancel())
            self.entry.set_sensitive(False)
        else:
            # Restore send button
            self.send_btn.set_image(
                Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
            )
            self.send_btn.set_tooltip_text("Send (Enter)")
            # Reconnect submit handler
            try:
                self.send_btn.disconnect_by_func(lambda w: self.query.cancel())
            except Exception:
                pass
            self.send_btn.connect("clicked", self._on_submit)
            self.entry.set_sensitive(True)
            self.entry.grab_focus()

    def _run_js(self, script: str):
        """Run JavaScript in the WebView."""
        self.webview.run_javascript(script, None, None, None)


class PopupApplication(Gtk.Application):
    """Single-instance GTK application for llm-guiassistant."""

    def __init__(self, with_selection: bool = False, debug: bool = False):
        super().__init__(
            application_id="com.llm.guiassistant",
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.with_selection = with_selection
        self.debug = debug
        self.window = None

    def do_activate(self):
        """Handle application activation."""
        if not self.window:
            self.window = PopupWindow(
                self,
                with_selection=self.with_selection,
                debug=self.debug
            )
        self.window.present()
        self.window.entry.grab_focus()

    def do_startup(self):
        """Handle application startup."""
        Gtk.Application.do_startup(self)
