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
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, Gio, GLib, WebKit2, Pango  # noqa: E402

from llm_tools_core import (  # noqa: E402
    ensure_daemon,
    stream_events,
    gather_context,
    format_context_for_llm,
    capture_screenshot,
    extract_code_blocks,
    strip_markdown,
)

from .history import InputHistory  # noqa: E402


# Configuration paths
CONFIG_DIR = Path.home() / ".config" / "llm-guiassistant"
STATE_FILE = CONFIG_DIR / "state.json"
ASSETS_DIR = Path.home() / ".local" / "share" / "llm-guiassistant" / "js"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "conversation.html"

# Quick action button definitions: (label, prompt_prefix)
QUICK_ACTIONS = [
    ("Explain", "Explain this: "),
    ("Analyze", "Analyze this: "),
    ("Improve", "Improve this: "),
    ("Summarize", "Summarize: "),
]


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

    def _format_tool_status(self, tool_name: str, args: dict) -> str:
        """Format tool status message with optional args preview.

        Args:
            tool_name: Name of the tool being executed
            args: Tool arguments dict

        Returns:
            Formatted status message
        """
        # Friendly display names for tools
        display_names = {
            "execute_python": "Running Python",
            "suggest_command": "Preparing command",
            "sandboxed_shell": "Running shell",
            "search_google": "Searching",
            "context": "Getting context",
            "read_file": "Reading",
            "write_file": "Writing",
            "edit_file": "Editing",
            "web_fetch": "Fetching",
        }

        action = display_names.get(tool_name, f"Executing {tool_name}")

        # Add args preview for simple cases
        if tool_name == "search_google" and args.get("query"):
            query = args["query"]
            if len(query) > 30:
                query = query[:30] + "..."
            return f'{action}: "{query}"'
        elif tool_name in ("read_file", "write_file", "edit_file") and args.get("path"):
            path = args["path"]
            if len(path) > 30:
                path = "..." + path[-27:]
            return f'{action}: {path}'
        elif tool_name == "web_fetch" and args.get("url"):
            url = args["url"]
            if len(url) > 40:
                url = url[:40] + "..."
            return f'{action}: {url}'

        return f"{action}..."

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
                args = event.get("args", {})
                # Build status message with simple args preview when available
                message = self._format_tool_status(tool_name, args)
                # Finalize current message before showing tool status
                # This ensures tool status appears AFTER current text
                GLib.idle_add(self.on_event, {"type": "finalize_current"})
                GLib.idle_add(self.on_event, {
                    "type": "tool_status",
                    "message": message
                })
            elif event_type == "tool_done":
                # Generate new message_id so subsequent text creates new div
                # This ensures text after tool appears AFTER tool status
                self._message_id = str(time.time())
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

    def __init__(self, parent_window, relative_widget):
        super().__init__()
        self.parent_window = parent_window
        self.actions = []
        self.filtered_actions = []
        self.selected_index = 0

        # Popover must be relative to a widget inside the window, not the window itself
        self.set_relative_to(relative_widget)
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
        self._save_state_timeout_id = None  # Debounce window state saves

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

        # Action panel (Ctrl+K) - must be relative to a widget inside the window
        self.action_panel = ActionPanel(self, self.main_box)

        # Gather initial context
        self._gather_context()

        # Load models in background
        threading.Thread(target=self._load_models, daemon=True).start()

    def _build_ui(self):
        """Build the popup UI."""
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(self.main_box)

        # Header bar with model selector and new session button
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("LLM Assistant")

        # Model selector dropdown
        self.model_combo = Gtk.ComboBoxText()
        self.model_combo.set_tooltip_text("Select model")
        self.model_combo.append_text("Loading...")
        self.model_combo.set_active(0)
        self.model_combo.connect("changed", self._on_model_changed)
        header.pack_start(self.model_combo)

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
        self.main_box.pack_start(self.context_label, False, False, 0)

        # Attachment panel
        attachment_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        attachment_box.set_margin_start(8)
        attachment_box.set_margin_end(8)
        attachment_box.set_margin_top(2)
        attachment_box.set_margin_bottom(2)

        self.attachment_label = Gtk.Label()
        self.attachment_label.set_xalign(0)
        self.attachment_label.set_hexpand(True)
        attachment_box.pack_start(self.attachment_label, True, True, 0)

        # Selection preview label (for --with-selection)
        self.selection_label = Gtk.Label()
        self.selection_label.set_xalign(0)
        self.selection_label.set_line_wrap(True)
        self.selection_label.set_max_width_chars(60)
        self.selection_label.get_style_context().add_class("dim-label")

        # Add file button
        add_file_btn = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        add_file_btn.set_tooltip_text("Add file")
        add_file_btn.connect("clicked", self._on_add_file)
        attachment_box.pack_start(add_file_btn, False, False, 0)

        # Screenshot window button
        screenshot_win_btn = Gtk.Button.new_from_icon_name("camera-photo-symbolic", Gtk.IconSize.BUTTON)
        screenshot_win_btn.set_tooltip_text("Screenshot window")
        screenshot_win_btn.connect("clicked", lambda w: self._on_screenshot("window"))
        attachment_box.pack_start(screenshot_win_btn, False, False, 0)

        # Screenshot region button
        screenshot_region_btn = Gtk.Button.new_from_icon_name("edit-cut-symbolic", Gtk.IconSize.BUTTON)
        screenshot_region_btn.set_tooltip_text("Screenshot region")
        screenshot_region_btn.connect("clicked", lambda w: self._on_screenshot("region"))
        attachment_box.pack_start(screenshot_region_btn, False, False, 0)

        self.main_box.pack_start(attachment_box, False, False, 0)
        self.main_box.pack_start(self.selection_label, False, False, 0)

        # Quick actions toolbar
        quick_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        quick_box.set_margin_start(8)
        quick_box.set_margin_end(8)
        quick_box.set_margin_bottom(4)

        for label, prefix in QUICK_ACTIONS:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda w, p=prefix: self._on_quick_action(p))
            quick_box.pack_start(btn, False, False, 0)

        self.main_box.pack_start(quick_box, False, False, 0)

        # WebKit view for conversation
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.webview = WebKit2.WebView()
        self.webview.set_vexpand(True)

        # Configure WebKit settings
        settings = self.webview.get_settings()

        # Disable hardware acceleration to fix blank page in VMs/Docker
        # See: https://github.com/tauri-apps/tauri/issues/7927
        # See: https://github.com/reflex-frp/reflex-platform/issues/735
        settings.set_hardware_acceleration_policy(
            WebKit2.HardwareAccelerationPolicy.NEVER
        )

        # Enable file:// access for loading JS assets from ~/.local/share/
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)

        self._load_template()

        scrolled.add(self.webview)
        self.main_box.pack_start(scrolled, True, True, 0)

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

        self.main_box.pack_start(input_box, False, False, 0)

        # Bottom action buttons
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bottom_box.set_margin_start(8)
        bottom_box.set_margin_end(8)
        bottom_box.set_margin_bottom(4)

        copy_btn = Gtk.Button(label="Copy")
        copy_btn.set_tooltip_text("Copy last response")
        copy_btn.connect("clicked", self._on_copy_response)
        bottom_box.pack_start(copy_btn, False, False, 0)

        insert_btn = Gtk.Button(label="Insert")
        insert_btn.set_tooltip_text("Paste into original window (best-effort)")
        insert_btn.connect("clicked", self._on_insert_response)
        bottom_box.pack_start(insert_btn, False, False, 0)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bottom_box.pack_start(spacer, True, True, 0)

        # Screenshot buttons (duplicated for convenience)
        ss_win = Gtk.Button.new_from_icon_name("camera-photo-symbolic", Gtk.IconSize.BUTTON)
        ss_win.set_tooltip_text("Screenshot window")
        ss_win.connect("clicked", lambda w: self._on_screenshot("window"))
        bottom_box.pack_start(ss_win, False, False, 0)

        ss_region = Gtk.Button.new_from_icon_name("edit-cut-symbolic", Gtk.IconSize.BUTTON)
        ss_region.set_tooltip_text("Screenshot region")
        ss_region.connect("clicked", lambda w: self._on_screenshot("region"))
        bottom_box.pack_start(ss_region, False, False, 0)

        self.main_box.pack_start(bottom_box, False, False, 0)

        # Set up drag and drop
        self._setup_drag_drop()

    def _embed_js_assets(self, html: str) -> str:
        """Embed JavaScript assets inline to avoid file:// loading issues.

        WebKit in sandboxed/Docker environments may block file:// script loading.
        This reads the JS files and embeds them directly in the HTML.
        """
        marked_path = ASSETS_DIR / "marked.min.js"
        hljs_path = ASSETS_DIR / "highlight.min.js"

        try:
            if marked_path.exists() and hljs_path.exists():
                marked_js = marked_path.read_text()
                hljs_js = hljs_path.read_text()

                # Replace external script tags with inline scripts
                html = html.replace(
                    f'<script src="file://{Path.home()}/.local/share/llm-guiassistant/js/marked.min.js"></script>',
                    f'<script>{marked_js}</script>'
                )
                html = html.replace(
                    f'<script src="file://{Path.home()}/.local/share/llm-guiassistant/js/highlight.min.js"></script>',
                    f'<script>{hljs_js}</script>'
                )

                if self.debug:
                    print("[WebKit] Embedded JS assets inline")
        except Exception as e:
            if self.debug:
                print(f"[WebKit] Failed to embed JS assets: {e}")

        return html

    def _load_template(self):
        """Load the conversation HTML template."""
        # Enable WebKit developer tools in debug mode
        if self.debug:
            settings = self.webview.get_settings()
            settings.set_enable_developer_extras(True)

        # Connect load handlers for debugging
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.connect("load-failed", self._on_load_failed)

        if TEMPLATE_PATH.exists():
            html = TEMPLATE_PATH.read_text()
            # Expand $HOME in the template
            html = html.replace("$HOME", str(Path.home()))

            # Embed JavaScript inline to avoid file:// loading issues in sandboxed environments
            html = self._embed_js_assets(html)

            if self.debug:
                print(f"[WebKit] Loading HTML ({len(html)} bytes)")
                # Print first 200 chars to verify template loaded
                print(f"[WebKit] HTML start: {html[:200]}...")
                # Check if scripts were embedded (look for inline script content)
                if "marked.setOptions" in html and "function appendMessage" in html:
                    print("[WebKit] Template contains expected JavaScript functions")
                else:
                    print("[WebKit] WARNING: JavaScript functions may be missing!")

            # Write HTML to temp file and load via file:// URI
            # This works around load_html() issues in some WebKit versions
            self._temp_html = tempfile.NamedTemporaryFile(
                mode='w', suffix='.html', delete=False
            )
            self._temp_html.write(html)
            self._temp_html.close()

            if self.debug:
                print(f"[WebKit] Loading from temp file: {self._temp_html.name}")

            self.webview.load_uri(f"file://{self._temp_html.name}")
        else:
            # Fallback minimal template
            self.webview.load_html("""
                <html><body style="font-family: sans-serif; padding: 20px;">
                <p>Template not found. Please reinstall llm-guiassistant.</p>
                </body></html>
            """, "file://")

    def _on_load_changed(self, webview, load_event):
        """Handle WebView load state changes."""
        if self.debug:
            from gi.repository import WebKit2
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
        return False  # Let default error handling proceed

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

        # Show selection preview if --with-selection and selection exists
        selection = self.context.get("selection", "")
        if self.with_selection and selection:
            sel_len = len(selection)
            preview = selection[:50].replace("\n", " ")
            if len(selection) > 50:
                preview += "..."
            self.selection_label.set_text(f"Selected ({sel_len} chars): {preview}")
            self.selection_label.show()
        else:
            self.selection_label.hide()

    def _on_configure(self, widget, event):
        """Save window dimensions on resize (debounced to avoid excessive I/O)."""
        # Cancel any pending save
        if self._save_state_timeout_id is not None:
            GLib.source_remove(self._save_state_timeout_id)

        # Schedule save after 500ms of no resize activity
        self._save_state_timeout_id = GLib.timeout_add(
            500,
            self._do_save_state,
            event.width,
            event.height
        )
        return False

    def _do_save_state(self, width: int, height: int) -> bool:
        """Actually save window state (called after debounce delay)."""
        self._save_state_timeout_id = None
        save_window_state(width, height)
        return False  # Don't repeat

    def _on_delete(self, widget, event):
        """Handle window close - hide instead of destroy."""
        # Flush any pending state save
        if self._save_state_timeout_id is not None:
            GLib.source_remove(self._save_state_timeout_id)
            self._save_state_timeout_id = None
            # Save current size immediately
            alloc = self.get_allocation()
            save_window_state(alloc.width, alloc.height)
        self.hide()
        return True  # Prevent destruction

    def _on_key_press(self, widget, event):
        """Handle global key presses."""
        # Ctrl+V: Paste image from clipboard
        if event.keyval == Gdk.KEY_v and event.state & Gdk.ModifierType.CONTROL_MASK:
            if self._paste_image_from_clipboard():
                return True  # Consumed - was an image
            # Let GTK handle normal text paste

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
            self.attachment_label.set_text(f"📎 {count} attached")
        else:
            self.entry.set_placeholder_text("Ask anything...")
            self.attachment_label.set_text("")

    def _on_stream_event(self, event):
        """Handle streaming events from background thread."""
        event_type = event.get("type", "")

        if event_type == "text":
            content = event.get("content", "")
            message_id = event.get("message_id")
            self.last_response = content  # Track for action panel (Ctrl+K)
            self._run_js(f"appendMessage('assistant', {json.dumps(content)}, {json.dumps(message_id)})")

        elif event_type == "finalize_current":
            # Finalize current message so tool status appears after it
            self._run_js("finalizeMessage()")

        elif event_type == "tool_status":
            message = event.get("message", "")
            self._run_js(f"addToolStatus({json.dumps(message)})")

        elif event_type == "error":
            message = event.get("message", "Unknown error")
            self._run_js(f"addError({json.dumps(message)})")

        elif event_type == "done":
            self._run_js("finalizeMessage()")
            self._set_streaming(False)

    def _on_stop_clicked(self, widget):
        """Handle stop button click during streaming."""
        self.query.cancel()

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
            self.send_btn.connect("clicked", self._on_stop_clicked)
            self.entry.set_sensitive(False)
        else:
            # Restore send button
            self.send_btn.set_image(
                Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
            )
            self.send_btn.set_tooltip_text("Send (Enter)")
            # Disconnect stop handler and reconnect submit handler
            try:
                self.send_btn.disconnect_by_func(self._on_stop_clicked)
            except TypeError:
                pass  # Handler wasn't connected (shouldn't happen)
            self.send_btn.connect("clicked", self._on_submit)
            self.entry.set_sensitive(True)
            self.entry.grab_focus()

    # --- Model selector methods ---

    def _load_models(self):
        """Load available models in background thread."""
        try:
            result = subprocess.run(
                ["llm", "models"],
                capture_output=True,
                text=True,
                timeout=10
            )
            models = []
            for line in result.stdout.splitlines():
                # Parse lines like "OpenAI Chat: gpt-4.1-mini (aliases: 4.1-mini)"
                if ':' in line:
                    parts = line.split(':')
                    if len(parts) >= 2:
                        model_part = parts[1].strip().split()[0]  # First word after colon
                        models.append(model_part)
            GLib.idle_add(self._populate_model_combo, models)
        except Exception:
            pass  # Graceful degradation

    def _populate_model_combo(self, models: list):
        """Populate the model combo box (called from main thread)."""
        self.model_combo.remove_all()
        for model in models:
            self.model_combo.append_text(model)
        if models:
            self.model_combo.set_active(0)
        return False  # Remove from idle

    def _on_model_changed(self, combo):
        """Handle model selection change."""
        model = combo.get_active_text()
        if model and model != "Loading...":
            # Send /model command to daemon
            def send_model_cmd():
                try:
                    for _ in stream_events(f"/model {model}", mode="assistant", session_id=self.session_id):
                        pass  # Consume events
                except Exception:
                    pass
            threading.Thread(target=send_model_cmd, daemon=True).start()

    # --- Attachment methods ---

    def _on_add_file(self, widget):
        """Show file chooser dialog to add file attachment."""
        dialog = Gtk.FileChooserDialog(
            title="Add File",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        # Allow images and common file types
        filter_all = Gtk.FileFilter()
        filter_all.set_name("All Files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        filter_images = Gtk.FileFilter()
        filter_images.set_name("Images")
        filter_images.add_mime_type("image/*")
        dialog.add_filter(filter_images)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            if filepath and filepath not in self.attachments:
                self.attachments.append(filepath)
                self._update_attachment_indicator()
        dialog.destroy()

    def _on_screenshot(self, mode: str):
        """Initiate screenshot capture."""
        if mode == "region":
            # Hide window first for region capture
            self.hide()
            # Delay capture to let window hide
            GLib.timeout_add(200, self._do_screenshot, mode)
        else:
            self._do_screenshot(mode)

    def _do_screenshot(self, mode: str) -> bool:
        """Perform the actual screenshot capture (may be delayed)."""
        try:
            filepath = capture_screenshot(mode)
            if filepath:
                self.attachments.append(filepath)
                GLib.idle_add(self._update_attachment_indicator)
        except Exception as e:
            if self.debug:
                print(f"[Screenshot] Error: {e}")

        # Show window again if it was hidden
        if mode == "region":
            GLib.idle_add(self.show)
            GLib.idle_add(self.present)

        return False  # Remove from timeout

    # --- Quick action methods ---

    def _on_quick_action(self, prefix: str):
        """Handle quick action button click."""
        current_text = self.entry.get_text().strip()
        if current_text:
            self.entry.set_text(f"{prefix}{current_text}")
        else:
            self.entry.set_text(prefix)
        self.entry.grab_focus()
        self.entry.set_position(-1)  # Move cursor to end

    # --- Response action methods ---

    def _on_copy_response(self, widget):
        """Copy last response to clipboard (plain text)."""
        if not self.last_response:
            return

        # Strip markdown for plain text copy
        plain_text = strip_markdown(self.last_response)

        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(plain_text, -1)
        clipboard.store()

    def _on_insert_response(self, widget):
        """Insert response into original window (best-effort via xdotool)."""
        if not self.last_response:
            return

        # Get original window ID from context
        window_id = self.context.get("window_id")
        if not window_id:
            return

        # Copy response to clipboard
        plain_text = strip_markdown(self.last_response)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(plain_text, -1)
        clipboard.store()

        # Hide popup
        self.hide()

        # Activate original window and paste
        def do_insert():
            try:
                # Activate the original window
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", window_id],
                    timeout=2
                )
                # Small delay for window activation
                time.sleep(0.1)
                # Paste via Ctrl+V
                subprocess.run(
                    ["xdotool", "key", "ctrl+v"],
                    timeout=2
                )
            except Exception:
                pass

        # Run in background to not block GTK
        threading.Thread(target=do_insert, daemon=True).start()

    def _paste_image_from_clipboard(self) -> bool:
        """Check clipboard for image and add to attachments.

        Returns True if an image was found and added.
        """
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        image = clipboard.wait_for_image()

        if image is not None:
            # Save image to temp file
            fd, filepath = tempfile.mkstemp(suffix=".png", prefix="clipboard_")
            os.close(fd)
            image.savev(filepath, "png", [], [])

            if filepath not in self.attachments:
                self.attachments.append(filepath)
                self._update_attachment_indicator()

            return True

        return False

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
        # Explicitly show all widgets before presenting
        # Some WebKit rendering issues require explicit show_all()
        self.window.show_all()
        self.window.present()
        self.window.entry.grab_focus()

    def do_startup(self):
        """Handle application startup."""
        Gtk.Application.do_startup(self)
