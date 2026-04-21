"""
Core context extraction functionality.

Extracts prompt blocks (prompt + command + output) from asciinema session recordings.
Each block contains everything from one prompt to the next, preserving the exact
terminal formatting including multi-line commands.
"""

import glob
import os
import re
import subprocess
from typing import List, Optional

# Import shared prompt detection module
try:
    from llm_tools_core import PromptDetector
except ImportError:
    try:
        from llm_tools.prompt_detection import PromptDetector  # legacy fallback
    except ImportError:
        # This shouldn't happen if installed correctly
        raise ImportError(
            "Could not import PromptDetector. "
            "Please install llm-tools-core: pip install llm-tools-core"
        )

# Public API
__all__ = ['get_command_blocks', 'get_context', 'get_session_log_file', 'parse_count']

# Argument values that request "all history" for the context count parser.
_ALL_COUNT_TOKENS = frozenset({'all', '-a', '--all'})


def parse_count(spec: str) -> Optional[int]:
    """Parse a count spec shared by the CLI and the LLM plugin.

    Returns None for "all" / "-a" / "--all", or a positive int for a digit
    string. Raises ValueError for anything else.
    """
    token = spec.strip().lower()
    if token in _ALL_COUNT_TOKENS:
        return None
    try:
        n = int(token)
    except ValueError as exc:
        raise ValueError(f"Invalid count '{spec}': expected positive integer or 'all'") from exc
    if n < 1:
        raise ValueError(f"Invalid count '{spec}': must be >= 1")
    return n

# Compiled regex for filtering AI-related commands
# Matches commands starting with: @, context, llm, llm-inlineassistant, aichat, llm-assistant, or wut
FILTERED_COMMANDS = re.compile(
    r'^(@|context|llm|llm-inlineassistant|aichat|llm-assistant|wut)(\s|$)',
    re.IGNORECASE
)

# Shared prompt patterns used by both should_exclude_block and the tail-trim
# logic in extract_prompt_blocks.
# Unix-style prompt char followed by command: $ # % ❯ → ➜
COMMAND_PROMPT_RE = re.compile(r'[$#%❯→➜]\s+(.+)')
# PowerShell prompt: "PS path> cmd" or "[user@host]: PS path> cmd"
POWERSHELL_PROMPT_RE = re.compile(r'(?:^\[[^\]]+\]:\s*)?PS[^>]*>\s+(.+)')
# Same two, specialized to detect an empty `context` invocation with no output
CONTEXT_COMMAND_RE = re.compile(r'[$#%❯→➜]\s+context(\s|$)')
PS_CONTEXT_COMMAND_RE = re.compile(r'(?:^\[[^\]]+\]:\s*)?PS[^>]*>\s+context(\s|$)')


def _strip_markers(line: str) -> str:
    """Remove tag metadata and VTE zero-width markers from a prompt line."""
    return (PromptDetector.strip_tag_metadata(line)
            .replace(PromptDetector.PROMPT_START_MARKER, '')
            .replace(PromptDetector.INPUT_START_MARKER, ''))


def _is_kali_two_line(lines: List[str]) -> bool:
    """True if `lines` begins with a Kali two-line prompt decorative header."""
    return len(lines) >= 2 and bool(PromptDetector.KALI_HEADER.search(lines[0]))


def _is_bare_context_invocation(lines: List[str]) -> bool:
    """True if `lines` is just a `context` prompt with no output yet.

    Strips markers first — they otherwise break \\s+ between prompt and command.
    """
    stripped = [_strip_markers(line) for line in lines]
    if len(stripped) == 1:
        return bool(CONTEXT_COMMAND_RE.search(stripped[0])
                    or PS_CONTEXT_COMMAND_RE.search(stripped[0]))
    if _is_kali_two_line(stripped):
        return bool(CONTEXT_COMMAND_RE.search(stripped[1]))
    return False


def find_cast_file() -> Optional[str]:
    """Find the current session's cast file."""
    # First, check environment variable
    if "SESSION_LOG_FILE" in os.environ:
        cast_file = os.environ["SESSION_LOG_FILE"]
        if os.path.exists(cast_file):
            return cast_file

    # Fall back to most recent .cast in the log directory
    log_dir = os.environ.get("SESSION_LOG_DIR", "/tmp/session_logs/asciinema")
    cast_files = glob.glob(os.path.join(log_dir, "*.cast"))
    if not cast_files:
        return None
    return max(cast_files, key=os.path.getmtime)


def convert_cast_to_text(cast_file: str) -> str:
    """Convert .cast file to readable text using asciinema."""
    # Stream to stdout to avoid a temp-file round trip.
    # --output-format txt is explicit because the .txt filename trick doesn't
    # apply to '-' (stdout would default to asciicast-v3 otherwise).
    result = subprocess.run(
        ["asciinema", "convert", "--output-format", "txt", cast_file, "-"],
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8", errors="replace")


def should_exclude_block(block: str) -> bool:
    """
    Check if a prompt block should be excluded from output.

    Excludes blocks where the command starts with:
    - @ (llm-inlineassistant queries)
    - context (with space or end of string)
    - llm (with space)
    - llm-inlineassistant (with space or end of string)
    - aichat (with space or end of string)
    - llm-assistant (with space or end of string)
    - wut (with space or end of string)

    Args:
        block: Text block containing prompt + command + output

    Returns:
        True if block should be excluded, False otherwise
    """
    if not block:
        return False

    # Split into non-empty lines
    lines = [line for line in block.split('\n') if line.strip()]

    if not lines:
        return False

    # Strip Unicode prompt markers and tag metadata for pattern matching
    # (consistent with PromptDetector.is_prompt_line and find_all_prompts)
    lines = [_strip_markers(line) for line in lines]

    # Kali two-line prompts put the command on the second line
    command_line_idx = 1 if _is_kali_two_line(lines) else 0
    if command_line_idx >= len(lines):
        return False

    command_line = lines[command_line_idx]

    match = COMMAND_PROMPT_RE.search(command_line) \
        or POWERSHELL_PROMPT_RE.search(command_line)
    if not match:
        # No command found (just a prompt), don't exclude
        return False

    # Get the command and strip leading/trailing whitespace
    command = match.group(1).strip()

    # Check if command matches filtered patterns (case-insensitive)
    return bool(FILTERED_COMMANDS.match(command))


def extract_prompt_blocks(text: str, count: Optional[int] = 1) -> List[str]:
    """
    Extract prompt blocks (prompt + command + output) from the converted text.

    Args:
        text: Converted asciinema text
        count: Number of recent prompt blocks to extract (None for all)

    Returns:
        List of text blocks, each containing everything from one prompt to the next
    """
    lines = text.split('\n')
    # Filter out lines from previous context command outputs
    lines = [line for line in lines if not line.lstrip().startswith('#c#')]
    # Pass lines directly (no need to rejoin into text)
    prompt_lines = PromptDetector.find_all_prompts(lines)

    if not prompt_lines:
        return []

    blocks = []

    # Process each prompt line - extract everything from this prompt to the next
    for i, (line_num, prompt_line) in enumerate(prompt_lines):
        # Get all lines from this prompt to the next prompt (or end of text)
        start_line = line_num
        if i + 1 < len(prompt_lines):
            end_line = prompt_lines[i + 1][0]
        else:
            end_line = len(lines)

        block_lines = lines[start_line:end_line]
        block = '\n'.join(block_lines).rstrip()

        # Only append if this block should not be excluded (filter AI commands)
        if not should_exclude_block(block):
            blocks.append(block)

    # Drop the last block if it's (a) empty, (b) just a prompt ready for input,
    # or (c) a bare `context` invocation echoing the user's own pending prompt.
    if blocks:
        last_block = blocks[-1]
        last_block_lines = [line for line in last_block.split('\n') if line.strip()]
        if (not last_block_lines
                or PromptDetector.detect_prompt_at_end(last_block)
                or _is_bare_context_invocation(last_block_lines)):
            blocks = blocks[:-1]

    # Return requested number of blocks
    if count is None:
        return blocks
    else:
        return blocks[-count:]


def format_output(blocks: List[str]) -> str:
    """Format prompt blocks for display."""
    if not blocks:
        return "#c# No prompts found in session recording."

    result = []
    for block in blocks:
        # Prefix each line of the block
        for line in block.split('\n'):
            result.append(f"#c# {line}")
        result.append("#c# ")  # Blank line separator between blocks

    return '\n'.join(result)


def get_session_log_file() -> Optional[str]:
    """
    Get the path to the current session's asciinema recording.

    Returns:
        Path to the .cast file, or None if not found.

    Example:
        >>> from llm_tools_context import get_session_log_file
        >>> log_file = get_session_log_file()
        >>> if log_file:
        ...     print(f"Recording: {log_file}")
    """
    return find_cast_file()


def get_command_blocks(n_commands: Optional[int] = 3, session_log: Optional[str] = None) -> List[str]:
    """
    Extract the last N command blocks from the current asciinema recording.

    Each block contains a prompt line, the command, and its output
    (everything from one prompt to the next).

    Args:
        n_commands: Number of recent prompt blocks to extract.
                   Use None for all blocks.
        session_log: Path to session log file. If provided, used directly
                    instead of checking SESSION_LOG_FILE env var.

    Returns:
        List of prompt block strings. Empty list if no session found
        or no prompts detected.

    Raises:
        FileNotFoundError: If asciinema command is not installed.
        subprocess.CalledProcessError: If cast file conversion fails.

    Example:
        >>> from llm_tools_context import get_command_blocks
        >>> blocks = get_command_blocks(3)
        >>> for block in blocks:
        ...     print(block)
        ...     print("---")
    """
    cast_file = session_log or find_cast_file()
    if not cast_file:
        return []

    text = convert_cast_to_text(cast_file)
    return extract_prompt_blocks(text, n_commands)


def get_context(n_commands: Optional[int] = 3, raw: bool = False) -> str:
    """
    Get formatted context string from the current asciinema recording.

    This is the main entry point for library usage. Returns the last N
    command blocks formatted for injection into AI prompts.

    Args:
        n_commands: Number of recent prompt blocks to include.
                   Use None for all blocks.
        raw: If True, return blocks joined without #c# prefix formatting.
             If False (default), return with #c# prefix on each line.

    Returns:
        Formatted context string. Returns empty string if no session found.

    Example:
        >>> from llm_tools_context import get_context
        >>> context_str = get_context(3)
        >>> print(context_str)

        # For raw output (no #c# prefix):
        >>> raw_context = get_context(3, raw=True)
    """
    blocks = get_command_blocks(n_commands)

    if not blocks:
        return "" if raw else "#c# No prompts found in session recording."

    if raw:
        return '\n\n'.join(blocks)
    else:
        return format_output(blocks)
