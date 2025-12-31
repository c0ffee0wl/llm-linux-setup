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
import sys


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
    return app.run(sys.argv[:1])  # Only pass program name to GTK


if __name__ == "__main__":
    sys.exit(main())
