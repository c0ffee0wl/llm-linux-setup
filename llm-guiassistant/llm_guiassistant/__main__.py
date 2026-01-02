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

import argparse
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
    parser = argparse.ArgumentParser(
        description="GTK popup client for llm-assistant daemon"
    )
    parser.add_argument(
        "--with-selection",
        action="store_true",
        help="Capture current selection and include in context"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output"
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0"
    )

    args = parser.parse_args()

    # Import here to avoid slow startup for --help/--version
    from .popup import PopupApplication

    app = PopupApplication(
        with_selection=args.with_selection,
        debug=args.debug
    )
    try:
        return app.run(sys.argv[:1])  # Only pass program name to GTK
    except KeyboardInterrupt:
        # GTK's signal handler cleanup raises KeyboardInterrupt on Ctrl+C
        # This is expected behavior - exit cleanly
        return 0


if __name__ == "__main__":
    sys.exit(main())
