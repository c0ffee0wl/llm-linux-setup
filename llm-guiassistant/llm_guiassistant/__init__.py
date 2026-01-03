"""llm-guiassistant - GTK-based conversational popup for llm-assistant daemon.

A system-wide LLM access popup that:
- Connects to the existing llm-assistant daemon
- Provides global hotkey activation via XFCE keyboard shortcuts
- Captures X11 desktop context (selection, window info)
- Renders Markdown responses with syntax highlighting via WebKit2GTK
- Supports screenshots, drag-drop files/images, and smart action buttons

Usage:
    llm-guiassistant              # Open popup
    llm-guiassistant --with-selection  # Open with current selection
"""

__version__ = "0.2.0"
