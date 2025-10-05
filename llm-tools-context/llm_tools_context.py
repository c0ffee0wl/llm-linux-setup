import llm
import subprocess
import sys


def context(input: str) -> str:
    """
    Execute the context command to get terminal history including commands and their outputs.

    Args:
        input: Number of recent entries to show, empty for last entry, or "all" for entire history

    Returns:
        Terminal history from the 'context' command, including input and output (commands ran and their outputs). 
        Each line of the history (the output of the 'context' command) is prefixed with #c#
    """
    args = ["context"]
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
