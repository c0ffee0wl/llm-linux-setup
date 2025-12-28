"""
llm-tools-context - Extract terminal history from asciinema session recordings.

This package provides both a CLI tool and an LLM plugin for retrieving
recent shell commands and their outputs from asciinema recordings.

CLI Usage:
    context          # Show last prompt block (default)
    context 5        # Show last 5 prompt blocks
    context all      # Show entire session
    context -a       # Show entire session
    context -e       # Output SESSION_LOG_FILE environment variable

Library Usage:
    from llm_tools_context import get_command_blocks, get_context

    blocks = get_command_blocks(n_commands=3)  # Returns list of prompt blocks
    context_str = get_context(n_commands=3)    # Returns formatted context string

LLM Tool Usage:
    llm --tool context "what did I just run?"
"""

from .core import (
    get_command_blocks,
    get_context,
    get_session_log_file,
)

__all__ = [
    'get_command_blocks',
    'get_context',
    'get_session_log_file',
]

__version__ = "0.2.0"
