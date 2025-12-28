"""Context capture for llm-inlineassistant.

Captures terminal context from asciinema recordings and applies
block-level hashing for deduplication.

Uses direct import from llm_tools_context when available, with
subprocess fallback.
"""

import subprocess
from typing import List, Set, Tuple

from llm_tools_core import filter_new_blocks

# Try to import from llm_tools_context package
_get_command_blocks_func = None

try:
    from llm_tools_context import get_command_blocks as _pkg_get_command_blocks
    _get_command_blocks_func = _pkg_get_command_blocks
except ImportError:
    pass


def _get_command_blocks_subprocess(n_commands: int = 3) -> List[str]:
    """Fallback: get command blocks via subprocess."""
    try:
        result = subprocess.run(
            ['context', str(n_commands)],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return []

        # Parse the context output (lines prefixed with #c#)
        output = result.stdout
        if not output.strip():
            return []

        # Split into blocks (separated by blank lines)
        blocks = []
        current_block = []

        for line in output.split('\n'):
            # Remove #c# prefix
            if line.startswith('#c# '):
                content = line[4:]
            elif line.startswith('#c#'):
                content = line[3:]
            else:
                continue

            if content.strip():
                current_block.append(content)
            else:
                # Blank line = block separator
                if current_block:
                    blocks.append('\n'.join(current_block))
                    current_block = []

        # Don't forget the last block
        if current_block:
            blocks.append('\n'.join(current_block))

        return blocks

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return []


def get_command_blocks(n_commands: int = 3) -> List[str]:
    """
    Get command blocks from asciinema recording.

    Uses direct import when available, falls back to subprocess.

    Args:
        n_commands: Number of recent command blocks to retrieve

    Returns:
        List of command block strings. Empty list if capture fails.
    """
    if _get_command_blocks_func is not None:
        try:
            return _get_command_blocks_func(n_commands=n_commands)
        except Exception:
            pass

    # Fallback to subprocess
    return _get_command_blocks_subprocess(n_commands)


def capture_shell_context(prev_hashes: Set[str]) -> Tuple[str, Set[str]]:
    """
    Capture context from asciinema with block-level deduplication.

    Args:
        prev_hashes: Set of block hashes from previous capture

    Returns:
        Tuple of (context_string, new_hashes):
        - context_string: New blocks only, or "[Content unchanged]" if no new blocks
        - new_hashes: Updated hash set for next comparison
    """
    blocks = get_command_blocks(n_commands=3)

    if not blocks:
        return "", prev_hashes

    # Apply shared hashing logic from llm-assistant
    new_blocks, current_hashes = filter_new_blocks(blocks, prev_hashes)

    if not new_blocks:
        return "[Content unchanged]", current_hashes

    # Format for system prompt injection
    context = '\n'.join(new_blocks)
    return context, current_hashes


def format_context_for_prompt(context: str) -> str:
    """
    Format captured context for injection into system prompt.

    Wraps context in XML-style tags consistent with llm-assistant.

    Args:
        context: Raw context string

    Returns:
        Formatted context with terminal_context tags
    """
    if not context or context == "[Content unchanged]":
        return f"<terminal_context>{context}</terminal_context>"

    return f"""<terminal_context>
{context}
</terminal_context>"""
