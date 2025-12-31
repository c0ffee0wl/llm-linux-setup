# llm-guiassistant

GTK-based conversational popup for system-wide LLM access on Linux. Connects to the existing llm-assistant daemon for streaming AI responses with tool execution.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│         llm-assistant daemon (already running)      │
│  Unix Socket: /tmp/llm-assistant-{UID}/daemon.sock  │
└─────────────────────────────────────────────────────┘
                         ▲
                         │ JSON request + NDJSON response
                         │
                ┌────────────────┐
                │ llm-guiassistant│
                │   (GTK popup)   │
                └────────────────┘
```

**Key design: No new daemon.** llm-guiassistant is just another client of the existing llm-assistant daemon, using Unix sockets and the same NDJSON streaming protocol.

## Components

### popup.py (~400 lines)
Main GTK application with single-instance D-Bus activation:
- `PopupApplication`: GTK Application with D-Bus activation for instant (<50ms) re-activation
- `PopupWindow`: Main window with WebKit conversation view, input field, context panel
- `StreamingQuery`: Background thread for streaming daemon responses via `GLib.idle_add()`

### history.py (~80 lines)
Shell-like input history with Up/Down arrow navigation:
- Persists to `~/.config/llm-guiassistant/input-history.json`
- 100 entry limit with deduplication
- Preserves draft when navigating

### templates/conversation.html (~150 lines)
WebKit template for rich Markdown rendering:
- Uses marked.js + highlight.js for Markdown and syntax highlighting
- Light/dark theme via `@media (prefers-color-scheme: dark)`
- Copy buttons on code blocks
- Auto-scroll during streaming

## Dependencies

### Python (installed via llm plugin mechanism)
- PyGObject (GTK3 bindings)
- llm-tools-core (shared utilities: `stream_events`, context gathering)

### System (apt)
- gir1.2-webkit2-4.1 (WebKit2GTK for GTK3)
- x11-utils (xprop for window detection)
- xdotool, xclip, maim (context gathering and screenshots)

### JavaScript (downloaded to ~/.local/share/llm-guiassistant/js/)
- marked.min.js v15.0.7 (Markdown parsing)
- highlight.min.js v11.11.1 (syntax highlighting)

## Hotkeys

Configured via swhkd (`~/.config/swhkd/swhkdrc`):

| Hotkey | Action |
|--------|--------|
| Super+Shift+A | Open popup (no selection) |
| Super+Shift+S | Open popup with current selection |

### swhkd Setup

swhkd requires user to be in the `input` group:
```bash
sudo usermod -aG input $USER
# Log out and back in for group change to take effect
```

## Usage

```bash
# Open popup (also starts daemon if not running)
llm-guiassistant

# Open with current X11 selection included as context
llm-guiassistant --with-selection

# Debug mode
llm-guiassistant --debug
```

### Keyboard Shortcuts (in popup)

| Key | Action |
|-----|--------|
| Ctrl+K | Open action panel (fuzzy-searchable) |
| ↑/↓ | Navigate input history |
| Enter | Send message |
| Escape | Stop generation / Close popup |

### Action Panel (Ctrl+K)

Raycast-style keyboard-first action panel with fuzzy search:

- **Copy response** (plain text or markdown)
- **Copy code blocks** (individually, with language preview)
- **Save to file** (file chooser dialog)
- **New session** (clear conversation)
- **Capture screenshot** (window or region)

Type to filter actions, use ↑/↓ to navigate, Enter to execute.

### Features

1. **Context Gathering**: Automatically captures focused window info (app class, title, working directory)
2. **Image Attachments**: Drag & drop images or paste from clipboard
3. **Streaming Responses**: Real-time Markdown rendering during response streaming
4. **Session Persistence**: Conversations persist within popup session (uses daemon's session management)
5. **New Session Button**: Clear conversation and context (prevents target contamination in security work)
6. **Action Panel (Ctrl+K)**: Keyboard-first quick actions with fuzzy search

## Protocol

Uses the llm-assistant daemon protocol with image attachment extension:

**Request:**
```json
{
  "cmd": "query",
  "tid": "guiassistant:12345",
  "q": "Explain this code",
  "mode": "assistant",
  "images": ["/tmp/screenshot.png"]
}
```

**Response (NDJSON stream):**
```json
{"type": "text", "content": "Here's..."}
{"type": "tool_start", "tool": "Python", "args": {...}}
{"type": "tool_done", "tool": "Python", "result": "..."}
{"type": "done"}
```

## File Locations

| Path | Purpose |
|------|---------|
| `~/.config/llm-guiassistant/state.json` | Window dimensions |
| `~/.config/llm-guiassistant/input-history.json` | Input history |
| `~/.local/share/llm-guiassistant/js/` | JavaScript assets |
| `~/.config/swhkd/swhkdrc` | Hotkey configuration |

## Limitations (v1)

- **X11 only**: Wayland support planned for v2 (requires wl-paste, wlr-randr, etc.)
- **No voice input**: Use llm-assistant's voice mode in Terminator for voice queries
- **No AT-SPI**: Context gathering uses X11 tools, not AT-SPI accessibility APIs

## Troubleshooting

### Popup doesn't appear
1. Check if daemon is running: `pgrep -f "llm-assistant.*daemon"`
2. Start daemon manually: `llm-assistant --daemon`

### Hotkeys don't work
1. Verify swhkd is running: `pgrep swhkd`
2. Check group membership: `groups | grep input`
3. Start swhkd: `swhkd &`

### JavaScript assets missing
Re-run install script to download:
```bash
./install-llm-tools.sh
```

### Context not captured
Verify X11 tools are installed:
```bash
which xdotool xclip xprop
```
