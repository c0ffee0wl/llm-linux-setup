"""
LLM plugin for context tool.

Registers the context tool with Simon Willison's llm CLI,
allowing AI to query recent terminal history.
"""

import llm

from .core import get_context, parse_count


def context(input: str) -> str:
    """
    Get terminal session history including commands and their outputs.

    Retrieves recent terminal activity from the current asciinema recording session.
    Useful for understanding what commands were run and what output they produced.
    Each line in the output is prefixed with '#c#' to distinguish context from other content.

    Args:
        input: Controls how much history to retrieve:
               - "" (empty): Last command and output only
               - number (e.g., "5"): Last N commands and their outputs
               - "all": Entire session history

    Returns:
        Session history with commands and outputs, each line prefixed with '#c#'.
        Returns error message if context command not found or session not active.
    """
    # Parse count spec. Empty input defaults to last-command-only (1).
    if input and input.strip():
        try:
            n_commands = parse_count(input)
        except ValueError as exc:
            return f"Error: {exc}. Must be 'all', '-a', '--all', or a positive integer."
    else:
        n_commands = 1

    try:
        return get_context(n_commands=n_commands, raw=False)
    except Exception as e:
        return f"Error retrieving context: {e}"


@llm.hookimpl
def register_tools(register):
    """Register the context tool with llm."""
    register(context)
