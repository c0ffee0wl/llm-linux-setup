"""CLI entry point for llm-inlineassistant.

Provides the main() function for the llm-inlineassistant command.
"""

from .client import main

__all__ = ['main']

if __name__ == "__main__":
    main()
