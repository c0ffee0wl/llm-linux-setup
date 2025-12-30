"""Helper utilities for ulauncher-llm.

Provides text formatting, tool display names, and utility functions.
"""

import re

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk


def strip_markdown_for_copy(text: str) -> str:
    """Strip markdown formatting for plain text copy.

    Args:
        text: Markdown-formatted text

    Returns:
        Plain text with markdown formatting removed
    """
    if not text:
        return ""

    # Remove code fences and their content markers (preserve the code inside)
    text = re.sub(r'```\w*\n(.*?)\n```', r'\1', text, flags=re.DOTALL)

    # Remove inline code backticks
    text = re.sub(r'`([^`]+)`', r'\1', text)

    # Remove bold
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)

    # Remove italic
    text = re.sub(r'\*([^*]+)\*', r'\1', text)

    # Remove headers (lines starting with #)
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)

    # Remove bullet points (convert to plain dashes)
    text = re.sub(r'^[\*\-]\s+', '- ', text, flags=re.MULTILINE)

    return text.strip()


def truncate_query(query: str, max_len: int = 60) -> str:
    """Truncate query for display in description.

    Args:
        query: User's query text
        max_len: Maximum display length

    Returns:
        Truncated query with ellipsis if needed
    """
    if len(query) > max_len:
        return query[:max_len - 3] + '...'
    return query


def extract_code_blocks(text: str) -> list:
    """Extract fenced code blocks from markdown text.

    Args:
        text: Markdown-formatted text with potential code blocks

    Returns:
        List of code block contents (without fence markers)
    """
    if not text:
        return []

    # Match fenced code blocks: ```[lang]\n...\n```
    pattern = r'```(?:\w*\n)?(.*?)```'
    matches = re.findall(pattern, text, re.DOTALL)
    return [m.strip() for m in matches if m.strip()]


# --- Line wrapping functions (inspired by ulauncher-gemini-direct) ---

def _clean_markdown(text: str) -> str:
    """Remove markdown formatting for display."""
    # Remove bold/italic markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    # Convert bullets to •
    text = re.sub(r'^[\*\-]\s+', '• ', text, flags=re.MULTILINE)
    # Remove code fences
    text = re.sub(r'```\w*\n?', '', text)
    # Remove inline code backticks
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove headers
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    return text.strip()


def _adjust_width_for_script(text: str, max_width: int, wide_script_factor: float = 0.5) -> int:
    """Adjust width for wide scripts (CJK, Greek, Cyrillic).

    Wide characters visually take more space, so we reduce the
    character count to maintain visual consistency.

    Args:
        text: Text to analyze
        max_width: Base maximum width
        wide_script_factor: Minimum factor for wide scripts (0.2-1.0, default 0.5)
    """
    # Sample first 10 non-space characters
    sample = [c for c in text if not c.isspace()][:10]
    if not sample:
        return max_width

    # Count non-Latin characters (codepoint > 0x024F)
    wide_count = sum(1 for c in sample if ord(c) > 0x024F)

    if wide_count > 2:
        # Apply width factor (reduces width for wide scripts)
        # Clamp wide_script_factor between 0.2 and 1.0
        min_factor = max(0.2, min(1.0, wide_script_factor))
        factor = max(min_factor, 1.0 - (wide_count / len(sample)) * (1.0 - min_factor))
        return int(max_width * factor)

    return max_width


def _wrap_by_words(text: str, max_width: int) -> str:
    """Wrap text at word boundaries."""
    lines = []
    for paragraph in text.split('\n'):
        if not paragraph.strip():
            lines.append('')
            continue

        words = paragraph.split()
        current_line = []
        current_len = 0

        for word in words:
            word_len = len(word)
            if current_len + word_len + (1 if current_line else 0) <= max_width:
                current_line.append(word)
                current_len += word_len + (1 if len(current_line) > 1 else 0)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
                current_len = word_len

        if current_line:
            lines.append(' '.join(current_line))

    return '\n'.join(lines)


def _wrap_by_chars(text: str, max_width: int) -> str:
    """Wrap text character-by-character (for languages without spaces)."""
    lines = []
    for i in range(0, len(text), max_width):
        lines.append(text[i:i + max_width])
    return '\n'.join(lines)


def format_for_display(text: str, max_width: int = 43, wide_script_factor: float = 0.5) -> str:
    """Format text for Ulauncher display with intelligent line wrapping.

    Inspired by ulauncher-gemini-direct's text formatting approach.

    Args:
        text: Response text (may contain markdown)
        max_width: Maximum line width (default 43 for Ulauncher)
        wide_script_factor: Width multiplier for wide scripts (0.2-1.0, default 0.5)

    Returns:
        Wrapped text suitable for display
    """
    if not text:
        return "Response"

    # Clean markdown formatting
    text = _clean_markdown(text)

    # Check for wide scripts (CJK, Greek, Cyrillic)
    adjusted_width = _adjust_width_for_script(text, max_width, wide_script_factor)

    # Wrap text based on whether it has spaces
    if ' ' in text:
        return _wrap_by_words(text, adjusted_width)
    else:
        return _wrap_by_chars(text, adjusted_width)


def get_clipboard_text() -> str:
    """Get current clipboard text content.

    Returns:
        Clipboard text, or empty string if unavailable
    """
    try:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        return text if text else ""
    except Exception:
        return ""
