# llm-guiassistant

GTK-based conversational popup for system-wide LLM access on Linux. Uses a hybrid HTTP/WebSocket architecture where the llm-assistant daemon serves the web UI and handles all communication.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    llm-assistant daemon                      │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ HTTP Server (localhost:8741) - aiohttp                  ││
│  │  GET /                    → conversation.html           ││
│  │  GET /static/*            → JS/CSS assets               ││
│  │  POST /upload             → image attachments           ││
│  │  POST /context            → receive context from GTK    ││
│  │  WS  /ws?session=xxx      → streaming + commands        ││
│  └─────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────┐│
│  │ Unix Socket (existing)    → CLI/terminal clients        ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
        ▲                              ▲
        │ WebSocket                    │ Unix Socket
┌───────┴─────────┐            ┌───────┴───────┐
│ llm-guiassistant│            │ CLI clients   │
│  (thin GTK)     │            │ espanso, etc  │
├─────────────────┤            └───────────────┘
│ Browser access  │
│ localhost:8741  │
└─────────────────┘
```

**Key design:** The GTK popup is a thin shell (~340 lines) that loads the web UI from the daemon. All conversation logic, streaming, and tool execution happens in the web UI via WebSocket.

## Components

### popup.py (~340 lines)
Thin GTK shell with single-instance D-Bus activation:
- `PopupApplication`: GTK Application with D-Bus activation for instant (<50ms) re-activation
- `PopupWindow`: Loads web UI from daemon via WebKit, gathers context, handles drag-drop
- No streaming logic - all handled by web UI via WebSocket

### history.py (~120 lines)
Shell-like input history with Up/Down arrow navigation:
- Persists to `~/.config/llm-guiassistant/input-history.json`
- 100 entry limit with deduplication
- Preserves draft when navigating

### Web UI (served by daemon)
Located in `llm-assistant/llm_assistant/static/`:
- `conversation.html` - Main HTML template
- `app.js` - WebSocket communication, streaming, action panel
- `style.css` - Light/dark theme, message styling, syntax highlighting

## Dependencies

### Python (installed via llm plugin mechanism)
- PyGObject (GTK3 bindings)
- requests (for context posting)
- llm-tools-core (shared utilities: context gathering)

### System (apt)
- gir1.2-webkit2-4.1 (WebKit2GTK for GTK3)
- x11-utils (xprop for window detection)
- xdotool, xclip (context gathering)
- maim, flameshot (screenshots via llm-tools-capture-screen)

### JavaScript (downloaded to llm-assistant static directory)
- marked.min.js v17.0.1 (Markdown parsing)
- highlight.min.js v11.11.1 (syntax highlighting)

## Hotkeys

Configured automatically via XFCE keyboard shortcuts (`xfconf-query`):

| Hotkey | Action |
|--------|--------|
| Super+^ | Open popup (German/European keyboards) |
| Super+Shift+^ | Open popup with current selection |
| Super+` | Open popup (US keyboards, backtick) |
| Super+Shift+` | Open popup with current selection |

### Manual Configuration (if needed)

```bash
# German keyboards (^ key)
xfconf-query -c xfce4-keyboard-shortcuts \
  -p "/commands/custom/<Super>dead_circumflex" \
  -n -t string -s "llm-guiassistant"

# US keyboards (` backtick key)
xfconf-query -c xfce4-keyboard-shortcuts \
  -p "/commands/custom/<Super>grave" \
  -n -t string -s "llm-guiassistant"
```

## Usage

```bash
# Open popup (also starts daemon if not running)
# Context: app, window, working directory (no selection)
llm-guiassistant

# Open with current X11 selection included as context
# Context: app, window, working directory, AND selected text
llm-guiassistant --with-selection

# Debug mode
llm-guiassistant --debug

# Direct browser access (no GTK popup needed)
# Open http://localhost:8741 in any browser
```

**Note:** Without `--with-selection`, the popup captures basic context (app, window title, working directory) but excludes any selected text. With `--with-selection`, the current X11 selection is captured at launch and included in the context sent with your query.

### Keyboard Shortcuts (in popup/browser)

| Key | Action |
|-----|--------|
| Ctrl+Enter | Send message |
| Ctrl+K | Open action panel (fuzzy-searchable) |
| ↑/↓ | Navigate input history |
| Escape | Close popup (GTK only) / Close action panel |

### Action Panel (Ctrl+K)

Raycast-style keyboard-first action panel with fuzzy search:

- **Copy response** (plain text, markdown stripped)
- **Copy markdown** (preserve formatting)
- **New session** (clear conversation)
- **Refresh context** (re-gather from focused window)

Type to filter actions, use ↑/↓ to navigate, Enter to execute.

### Features

1. **Context Gathering**: Automatically captures focused window info (app class, title, working directory)
2. **Context Refresh**: Re-gather context from currently focused window
3. **Image Attachments**: Drag & drop images onto the window
4. **Streaming Responses**: Real-time Markdown rendering during response streaming
5. **Session Persistence**: Conversations persist within session (identified by session ID)
6. **Browser Access**: Open http://localhost:8741 directly in any browser
7. **Action Panel (Ctrl+K)**: Keyboard-first quick actions with fuzzy search

## WebSocket Protocol

### Client → Server

```typescript
{type: "query", query: string, mode: "assistant"|"simple", context?: object, images?: string[]}
{type: "edit", messageIndex: number, newContent: string}
{type: "regenerate"}
{type: "branch", messageIndex: number}
{type: "stripMarkdown", text: string, requestId: string}
{type: "getHistory"}
{type: "command", command: "new"|"status"|"model", args?: string}
```

### Server → Client

```typescript
{type: "connected", sessionId: string, model: string}
{type: "text", content: string, messageId: string}  // content is ACCUMULATED (full text so far)
{type: "tool_start", tool: string, args: object}
{type: "tool_done", tool: string, result: string}
{type: "done"}
{type: "error", message: string, code?: string}
{type: "stripped", text: string, requestId: string}
{type: "history", messages: Array<{role: string, content: string}>}
```

## File Locations

| Path | Purpose |
|------|---------|
| `~/.config/llm-guiassistant/state.json` | Window dimensions |
| `~/.config/llm-guiassistant/input-history.json` | Input history |
| `llm-assistant/llm_assistant/static/` | Web UI assets (HTML, JS, CSS) |
| XFCE: `xfconf-query` | Keyboard shortcuts (stored in xfconf) |

## Port Configuration

The daemon's web server listens on port 8741 by default. Override via environment variable:

```bash
export LLM_GUI_PORT=9000
llm-assistant --daemon
```

## Limitations

- **X11 only for context**: Context gathering uses X11 tools (xdotool, xprop, xclip)
- **No voice input**: Use llm-assistant's voice mode in Terminator for voice queries
- **Single daemon**: One daemon serves all clients (GTK popup, browser, espanso, etc.)

## Troubleshooting

### Popup doesn't appear
1. Check if daemon is running: `pgrep -f "llm-assistant.*daemon"`
2. Start daemon manually: `llm-assistant --daemon`
3. Check web server: `curl http://localhost:8741/`

### Web UI shows "Cannot connect to daemon"
1. Ensure daemon is running with web server support
2. Check if aiohttp is installed: `pip show aiohttp`
3. Re-run install script: `./install-llm-tools.sh`

### Hotkeys don't work
1. Verify XFCE shortcuts are configured:
   ```bash
   xfconf-query -c xfce4-keyboard-shortcuts -l | grep guiassistant
   ```
2. Re-run install script to configure:
   ```bash
   ./install-llm-tools.sh
   ```
3. Or add manually via XFCE Settings → Keyboard → Application Shortcuts

### Context not captured
Verify X11 tools are installed:
```bash
which xdotool xclip xprop
```
