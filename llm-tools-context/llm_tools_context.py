import llm
import subprocess
import sys


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
    args = ["context"]

    # Validate and sanitize input to prevent shell injection
    if input and input.strip():
        input_clean = input.strip()

        # Only allow "all", "-a", "--all", or positive integers
        if input_clean.lower() in ["all", "-a", "--all"]:
            args.append(input_clean.lower())
        elif input_clean.isdigit() and int(input_clean) > 0:
            args.append(input_clean)
        else:
            return f"Error: Invalid input '{input_clean}'. Must be 'all', '-a', '--all', or a positive integer."

    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error running context command: {e.stderr}"
    except FileNotFoundError:
        return "Error: 'context' command not found in PATH"


@llm.hookimpl
def register_tools(register):
    register(context)
