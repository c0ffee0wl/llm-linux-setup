"""
CLI entry point for context command.

Usage:
    context          # Show last prompt block (default)
    context 5        # Show last 5 prompt blocks
    context all      # Show entire session
    context -a       # Show entire session
    context -e       # Output SESSION_LOG_FILE environment variable
"""

import subprocess
import sys

import click

from .core import (
    find_cast_file,
    convert_cast_to_text,
    extract_prompt_blocks,
    format_output,
)


@click.command()
@click.argument('count', default='1', required=False)
@click.option('-e', '--environment', is_flag=True,
              help='Output SESSION_LOG_FILE environment variable')
@click.option('-a', '--all', 'show_all', is_flag=True,
              help='Show entire history')
def main(count: str, environment: bool, show_all: bool):
    """Extract prompt blocks from terminal session."""

    # Find cast file
    cast_file = find_cast_file()

    # Handle -e/--environment flag
    if environment:
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
    want_all = show_all or count == 'all'

    if want_all:
        count_int = None  # Signal to show all
    else:
        # Validate count is a number
        try:
            count_int = int(count)
            # Convert negative to positive with a note
            if count_int < 0:
                count_int = abs(count_int)
                click.echo(
                    f"#c# context usage note: Using {count_int} (converted from negative value)",
                    err=True
                )
            # Still validate that it's not zero
            if count_int < 1:
                raise ValueError()
        except (ValueError, TypeError):
            raise click.BadParameter(
                'Please provide a positive number or "all"',
                param_hint="'COUNT'"
            )

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
    blocks = extract_prompt_blocks(text, count_int)

    # Display results
    print(format_output(blocks))


if __name__ == "__main__":
    main()
