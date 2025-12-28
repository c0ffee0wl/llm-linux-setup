"""
CLI entry point for context command.

Usage:
    context          # Show last prompt block (default)
    context 5        # Show last 5 prompt blocks
    context all      # Show entire session
    context -a       # Show entire session
    context -e       # Output SESSION_LOG_FILE environment variable
"""

import argparse
import subprocess
import sys

from .core import (
    find_cast_file,
    convert_cast_to_text,
    extract_prompt_blocks,
    format_output,
)


def main():
    """Main CLI entry point."""
    # Parse arguments
    parser = argparse.ArgumentParser(
        description='Extract prompt blocks from terminal session'
    )
    parser.add_argument(
        'count', nargs='?', default=1,
        help='Number of recent prompt blocks to show or "all" for entire history (default: 1)'
    )
    parser.add_argument(
        '-e', '--environment', action='store_true',
        help='Output SESSION_LOG_FILE environment variable'
    )
    parser.add_argument(
        '-a', '--all', action='store_true',
        help='Show entire history'
    )

    args = parser.parse_args()

    # Find cast file
    cast_file = find_cast_file()

    # Handle -e/--environment flag
    if args.environment:
        if cast_file:
            export_cmd = f"export SESSION_LOG_FILE='{cast_file}'"
            print(export_cmd)

            # Try to copy to clipboard using xsel (both PRIMARY and CLIPBOARD)
            try:
                # Copy to PRIMARY selection (middle-click paste)
                subprocess.run(
                    ['xsel', '--primary', '--input'],
                    input=export_cmd.encode(),
                    check=True,
                    capture_output=True
                )
                # Copy to CLIPBOARD selection (Ctrl+V paste)
                subprocess.run(
                    ['xsel', '--clipboard', '--input'],
                    input=export_cmd.encode(),
                    check=True,
                    capture_output=True
                )
                print("# Command copied to clipboard", file=sys.stderr)
            except (subprocess.CalledProcessError, FileNotFoundError):
                # xsel not available or failed - silently continue
                pass
        else:
            print("# No asciinema session recording found", file=sys.stderr)
            sys.exit(1)
        return

    # Determine if we want all history
    show_all = args.all or args.count == 'all'

    if show_all:
        count = None  # Signal to show all
    else:
        # Validate count is a number
        try:
            count = int(args.count)
            # Convert negative to positive with a note
            if count < 0:
                count = abs(count)
                print(
                    f"#c# context usage note: Using {count} (converted from negative value)",
                    file=sys.stderr
                )
            # Still validate that it's not zero
            if count < 1:
                raise ValueError()
        except (ValueError, TypeError):
            print("Error: Please provide a positive number or 'all'", file=sys.stderr)
            parser.print_help(sys.stderr)
            sys.exit(1)

    if not cast_file:
        print("Error: No asciinema session recording found.", file=sys.stderr)
        print("Make sure you're in a shell with asciinema recording enabled.", file=sys.stderr)
        sys.exit(1)

    # Convert to text
    try:
        text = convert_cast_to_text(cast_file)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to convert cast file: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: asciinema command not found. Is it installed?", file=sys.stderr)
        sys.exit(1)

    # Extract prompt blocks
    blocks = extract_prompt_blocks(text, count)

    # Display results
    print(format_output(blocks))


if __name__ == "__main__":
    main()
