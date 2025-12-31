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
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, Gio, GLib, WebKit2, Pango

from llm_tools_core import (
    ensure_daemon,
    stream_events,
    gather_context,
    format_context_for_llm,
    get_session_type,
    capture_screenshot,
    extract_code_blocks,
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


class ActionPanel(Gtk.Popover):
    """Keyboard-first action panel (Ctrl+K) with fuzzy search.

    Provides Raycast-style quick actions for the current response:
    - Copy response (plain text)
    - Copy response (markdown)
    - Copy individual code blocks
    - Save to file
    - New session
    """

    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self.actions = []
        self.filtered_actions = []
        self.selected_index = 0

        self.set_relative_to(parent_window)
        self.set_position(Gtk.PositionType.TOP)
        self.set_modal(True)

        # Build UI
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)
        vbox.set_margin_top(8)
        vbox.set_margin_bottom(8)

        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Type to filter actions...")
        self.search_entry.connect("changed", self._on_search_changed)
        self.search_entry.connect("key-press-event", self._on_key_press)
        self.search_entry.set_width_chars(40)
        vbox.pack_start(self.search_entry, False, False, 0)

        # Scrolled list of actions
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)
        scrolled.set_max_content_height(300)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_row_activated)
        scrolled.add(self.listbox)
        vbox.pack_start(scrolled, True, True, 0)

        # Hint label
        hint = Gtk.Label()
        hint.set_markup("<small>↑↓ Navigate • Enter Execute • Esc Close</small>")
        hint.get_style_context().add_class("dim-label")
        vbox.pack_start(hint, False, False, 0)

        self.add(vbox)

    def show_actions(self, last_response: str):
        """Show the action panel with context-aware actions."""
        self.actions = self._build_actions(last_response)
        self.filtered_actions = self.actions.copy()
        self.selected_index = 0
        self.search_entry.set_text("")
        self._populate_list()
        self.show_all()
        self.search_entry.grab_focus()

    def _build_actions(self, response: str) -> List[Tuple[str, str, Callable]]:
        """Build list of available actions based on response content.

        Returns list of (icon, label, callback) tuples.
        """
        actions = []

        # Always available actions
        actions.append(("📋", "Copy response (plain text)", lambda: self._copy_text(response)))
        actions.append(("📝", "Copy response (markdown)", lambda: self._copy_markdown(response)))

        # Extract code blocks and add individual copy actions
        code_blocks = extract_code_blocks(response) if response else []
        for i, (lang, code) in enumerate(code_blocks[:5], 1):  # Limit to 5
            lang_display = lang if lang else "code"
            preview = code[:40].replace('\n', ' ') + "..." if len(code) > 40 else code.replace('\n', ' ')
            actions.append((
                "💻",
                f"Copy code block {i} ({lang_display}): {preview}",
                lambda c=code: self._copy_text(c)
            ))

        # File operations
        actions.append(("💾", "Save response to file...", lambda: self._save_to_file(response)))

        # Session actions
        actions.append(("🔄", "New session", self._new_session))
        actions.append(("📷", "Capture screenshot (window)", self._screenshot_window))
        actions.append(("✂️", "Capture screenshot (region)", self._screenshot_region))

        return actions

    def _populate_list(self):
        """Populate the listbox with filtered actions."""
        # Clear existing rows
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        # Add filtered actions
        for i, (icon, label, _) in enumerate(self.filtered_actions):
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(4)
            hbox.set_margin_end(4)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)

            icon_label = Gtk.Label(label=icon)
            hbox.pack_start(icon_label, False, False, 0)

            text_label = Gtk.Label(label=label)
            text_label.set_xalign(0)
            text_label.set_ellipsize(Pango.EllipsizeMode.END)
            hbox.pack_start(text_label, True, True, 0)

            row.add(hbox)
            self.listbox.add(row)

        self.listbox.show_all()

        # Select first row
        if self.filtered_actions:
            first_row = self.listbox.get_row_at_index(0)
            if first_row:
                self.listbox.select_row(first_row)

    def _on_search_changed(self, entry):
        """Filter actions based on search text."""
        query = entry.get_text().lower()
        if not query:
            self.filtered_actions = self.actions.copy()
        else:
            # Fuzzy match: all query chars must appear in order
            self.filtered_actions = []
            for action in self.actions:
                label = action[1].lower()
                if self._fuzzy_match(query, label):
                    self.filtered_actions.append(action)

        self.selected_index = 0
        self._populate_list()

    def _fuzzy_match(self, query: str, text: str) -> bool:
        """Check if query fuzzy-matches text (chars appear in order)."""
        query_idx = 0
        for char in text:
            if query_idx < len(query) and char == query[query_idx]:
                query_idx += 1
        return query_idx == len(query)

    def _on_key_press(self, widget, event):
        """Handle keyboard navigation."""
        if event.keyval == Gdk.KEY_Escape:
            self.popdown()
            return True

        elif event.keyval == Gdk.KEY_Return:
            if self.filtered_actions and self.selected_index < len(self.filtered_actions):
                _, _, callback = self.filtered_actions[self.selected_index]
                self.popdown()
                callback()
            return True

        elif event.keyval == Gdk.KEY_Up:
            if self.selected_index > 0:
                self.selected_index -= 1
                row = self.listbox.get_row_at_index(self.selected_index)
                if row:
                    self.listbox.select_row(row)
                    row.grab_focus()
                    self.search_entry.grab_focus()  # Keep focus on search
            return True

        elif event.keyval == Gdk.KEY_Down:
            if self.selected_index < len(self.filtered_actions) - 1:
                self.selected_index += 1
                row = self.listbox.get_row_at_index(self.selected_index)
                if row:
                    self.listbox.select_row(row)
                    row.grab_focus()
                    self.search_entry.grab_focus()  # Keep focus on search
            return True

        return False

    def _on_row_activated(self, listbox, row):
        """Execute action when row is clicked/activated."""
        index = row.get_index()
        if index < len(self.filtered_actions):
            _, _, callback = self.filtered_actions[index]
            self.popdown()
            callback()

    def _copy_text(self, text: str):
        """Copy text to clipboard (plain text)."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        clipboard.store()

    def _copy_markdown(self, text: str):
        """Copy text to clipboard (as markdown)."""
        self._copy_text(text)

    def _save_to_file(self, text: str):
        """Show save dialog and write response to file."""
        dialog = Gtk.FileChooserDialog(
            title="Save Response",
            parent=self.parent_window,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog.set_current_name("response.md")
        dialog.set_do_overwrite_confirmation(True)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            try:
                Path(filepath).write_text(text)
            except Exception:
                pass  # Silent fail for now
        dialog.destroy()

    def _new_session(self):
        """Trigger new session in parent window."""
        self.parent_window._on_new_session(None)

    def _screenshot_window(self):
        """Capture active window screenshot."""
        try:
            path = capture_screenshot(mode="window")
            if path:
                self.parent_window.attachments.append(Path(path))
                self.parent_window._update_attachment_indicator()
        except Exception:
            pass

    def _screenshot_region(self):
        """Capture region screenshot."""
        # Hide popup first so it's not in the screenshot
        self.parent_window.hide()
        GLib.timeout_add(200, self._do_region_screenshot)

    def _do_region_screenshot(self):
        """Delayed region screenshot capture."""
        try:
            path = capture_screenshot(mode="region")
            if path:
                self.parent_window.attachments.append(Path(path))
                self.parent_window._update_attachment_indicator()
        except Exception:
            pass
        self.parent_window.show()
        return False  # Don't repeat


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
        self.last_response = ""  # Track last response for action panel

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

        # Action panel (Ctrl+K)
        self.action_panel = ActionPanel(self)

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
        # Ctrl+K: Show action panel
        if event.keyval == Gdk.KEY_k and event.state & Gdk.ModifierType.CONTROL_MASK:
            self.action_panel.show_actions(self.last_response)
            return True

        # Escape: Stop streaming or close window
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
        # Clear state
        self.attachments.clear()
        self.last_response = ""

        # Clear conversation in WebView
        self._run_js("clearConversation()")

        # Send new session command to daemon (resets conversation)
        request = {"cmd": "new", "tid": self.session_id}
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
            self.last_response = content  # Track for action panel (Ctrl+K)
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
