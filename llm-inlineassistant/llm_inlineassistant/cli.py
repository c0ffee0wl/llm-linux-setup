"""CLI entry point for llm-shell.

Provides the main() function for the llm-shell command.
"""

from .client import main

__all__ = ['main']

if __name__ == "__main__":
    main()
