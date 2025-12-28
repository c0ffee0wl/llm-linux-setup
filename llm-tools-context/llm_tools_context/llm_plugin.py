"""
LLM plugin for context tool.

Registers the context tool with Simon Willison's llm CLI,
allowing AI to query recent terminal history.
"""

import llm

from .core import get_context


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
    # Parse and validate input
    n_commands = 1
    all_history = False

    if input and input.strip():
        input_clean = input.strip()

        # Only allow "all", "-a", "--all", or positive integers
        if input_clean.lower() in ["all", "-a", "--all"]:
            all_history = True
        elif input_clean.isdigit() and int(input_clean) > 0:
            n_commands = int(input_clean)
        else:
            return f"Error: Invalid input '{input_clean}'. Must be 'all', '-a', '--all', or a positive integer."

    # Call the core function directly
    try:
        if all_history:
            # get_context with large number for "all"
            return get_context(n_commands=9999, raw=False)
        else:
            return get_context(n_commands=n_commands, raw=False)
    except Exception as e:
        return f"Error retrieving context: {e}"


@llm.hookimpl
def register_tools(register):
    """Register the context tool with llm."""
    register(context)
