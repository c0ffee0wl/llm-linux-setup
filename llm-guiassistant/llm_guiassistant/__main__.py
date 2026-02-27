#!/usr/bin/env python3
"""Entry point for llm-guiassistant.

This module provides the main() function that:
1. Parses command-line arguments
2. Creates a single-instance GTK application
3. Presents the popup window

Usage:
    python -m llm_guiassistant
    llm-guiassistant
    llm-guiassistant --with-selection
"""

import os
import sys

# Suppress EGL/Mesa warnings in VMs without 3D acceleration
# Must be set BEFORE importing GTK/WebKit
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
os.environ.setdefault("MESA_DEBUG", "silent")

# Fix blank page issue with WebKit2GTK hardware acceleration
# See: https://github.com/tauri-apps/tauri/issues/7927
# See: https://github.com/reflex-frp/reflex-platform/issues/735
# See: https://bugs.launchpad.net/ubuntu/+source/webkit2gtk/+bug/2023322
# See: https://bugs.webkit.org/show_bug.cgi?id=238244
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")


def main():
    """Main entry point for llm-guiassistant."""
    # Handle --help and --version before GTK takes over argv
    if '--help' in sys.argv or '-h' in sys.argv:
        print("usage: llm-guiassistant [--with-selection] [--debug] [--hidden] [--version]")
        print()
        print("GTK popup client for llm-assistant daemon")
        print()
        print("options:")
        print("  --with-selection  Capture current selection and include in context")
        print("  --debug           Enable debug output")
        print("  --hidden          Start hidden (for autostart, window shown on next activation)")
        print("  --version         Show version and exit")
        return 0

    if '--version' in sys.argv:
        print("llm-guiassistant 0.2.0")
        return 0

    # Parse flags locally for the primary instance's initial state.
    # These are also parsed in do_command_line for remote activation.
    with_selection = '--with-selection' in sys.argv
    debug = '--debug' in sys.argv
    hidden = '--hidden' in sys.argv

    # Import here to avoid slow startup for --help/--version
    from .popup import PopupApplication

    app = PopupApplication(
        with_selection=with_selection,
        debug=debug,
        hidden=hidden
    )
    try:
        # Pass full sys.argv so GTK forwards arguments to the primary
        # instance via D-Bus when a second instance is launched.
        # This is critical for --with-selection to work on re-activation.
        return app.run(sys.argv)
    except KeyboardInterrupt:
        # GTK's signal handler cleanup raises KeyboardInterrupt on Ctrl+C
        # This is expected behavior - exit cleanly
        return 0


if __name__ == "__main__":
    sys.exit(main())
