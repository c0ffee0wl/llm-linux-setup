import importlib.machinery
import importlib.util
import os
import subprocess

import llm

# Try to import context module directly for better performance
_get_context_func = None


def _load_context_module(script_path: str):
    """Load context module from a script file (handles files without .py extension)."""
    # Use SourceFileLoader explicitly for files without .py extension
    loader = importlib.machinery.SourceFileLoader("context", script_path)
    spec = importlib.util.spec_from_loader("context", loader)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _try_import_context():
    """Try to import context module from various locations."""
    global _get_context_func

    # Try ~/.local/bin/context (when installed)
    local_bin_context = os.path.expanduser('~/.local/bin/context')
    if os.path.isfile(local_bin_context):
        try:
            module = _load_context_module(local_bin_context)
            if module and hasattr(module, 'get_context'):
                _get_context_func = module.get_context
                return
        except Exception:
            pass


_try_import_context()


def _get_context_subprocess(n_commands: int = 1, all_history: bool = False) -> str:
    """Fallback: get context via subprocess."""
    args = ["context"]
    if all_history:
        args.append("all")
    elif n_commands > 1:
        args.append(str(n_commands))

    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error running context command: {e.stderr}"
    except FileNotFoundError:
        return "Error: 'context' command not found in PATH"


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

    # Try direct import first, fall back to subprocess
    if _get_context_func is not None:
        try:
            if all_history:
                # get_context with large number for "all"
                return _get_context_func(n_commands=9999, raw=False)
            else:
                return _get_context_func(n_commands=n_commands, raw=False)
        except Exception:
            pass

    # Fallback to subprocess
    return _get_context_subprocess(n_commands, all_history)


@llm.hookimpl
def register_tools(register):
    register(context)
