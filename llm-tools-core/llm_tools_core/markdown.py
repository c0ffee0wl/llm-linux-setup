"""Markdown processing utilities.

This module provides markdown stripping and extraction functions used by:
- llm-assistant (TTS speech synthesis, clipboard copy)
- llm-inlineassistant (clipboard copy)
- llm-guiassistant (smart actions on code blocks)

The strip_markdown library is optional - functions degrade gracefully
to regex-based fallbacks when not installed.
"""
import re
from typing import List, Tuple

# Regex to remove fenced code blocks entirely
_CODE_BLOCK_RE = re.compile(r'```[\s\S]*?```', re.MULTILINE)

# Pattern to match complete code blocks and capture content (for preservation)
_CODE_BLOCK_PATTERN = re.compile(r'```\w*\n(.*?)```', re.DOTALL)

# Fallback regex patterns for when strip_markdown library is not available
# Order matters: more specific patterns (images) must come before general ones (links)
_FALLBACK_PATTERNS = [
    (re.compile(r'`([^`]+)`'), r'\1'),                    # Inline code
    (re.compile(r'\*\*([^*]+)\*\*'), r'\1'),              # Bold **text**
    (re.compile(r'__([^_]+)__'), r'\1'),                  # Bold __text__
    (re.compile(r'\*([^*]+)\*'), r'\1'),                  # Italic *text*
    (re.compile(r'_([^_]+)_'), r'\1'),                    # Italic _text_
    (re.compile(r'~~([^~]+)~~'), r'\1'),                  # Strikethrough
    (re.compile(r'^\s*#{1,6}\s*', re.MULTILINE), ''),     # Headers
    (re.compile(r'^\s*>\s*', re.MULTILINE), ''),          # Blockquotes
    (re.compile(r'^\s*[-*+]\s+', re.MULTILINE), ''),      # Unordered lists
    (re.compile(r'^\s*\d+\.\s+', re.MULTILINE), ''),      # Ordered lists
    (re.compile(r'!\[([^\]]*)\]\([^)]+\)'), r'\1'),       # Images ![alt](url) - before links!
    (re.compile(r'\[([^\]]+)\]\([^)]+\)'), r'\1'),        # Links [text](url)
    (re.compile(r'^\s*[-*_]{3,}\s*$', re.MULTILINE), ''), # Horizontal rules
]


def _strip_inline_markdown(text: str) -> str:
    """Strip inline markdown formatting (not code blocks).

    Used internally after code blocks have been handled.
    """
    for pattern, replacement in _FALLBACK_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# Try to import the strip_markdown library for better parsing
try:
    from strip_markdown import strip_markdown as _lib_strip_markdown
    _HAS_STRIP_MARKDOWN = True
except ImportError:
    _HAS_STRIP_MARKDOWN = False
    _lib_strip_markdown = None


def strip_markdown(text: str, preserve_code_blocks: bool = True) -> str:
    """Strip markdown formatting from text.

    Args:
        text: Markdown-formatted text
        preserve_code_blocks: If True (default), code block content is preserved
                             (fences and language identifier removed).
                             If False, code blocks are removed entirely
                             (useful for TTS where code shouldn't be spoken).

    Returns:
        Plain text with markdown formatting removed

    Examples:
        >>> strip_markdown("**bold** and `code`")
        'bold and code'

        >>> strip_markdown("```python\\nprint('hi')\\n```", preserve_code_blocks=True)
        "print('hi')"

        >>> strip_markdown("```python\\nprint('hi')\\n```", preserve_code_blocks=False)
        ''
    """
    if not preserve_code_blocks:
        # Remove code blocks entirely (TTS use case)
        text = _CODE_BLOCK_RE.sub('', text)
        if _HAS_STRIP_MARKDOWN:
            return _lib_strip_markdown(text)
        return _strip_inline_markdown(text)

    # Preserve code blocks using placeholder technique
    # This works correctly even with the library (which leaks language identifiers)
    code_blocks: List[str] = []

    def save_code(match):
        code_blocks.append(match.group(1))
        return f'\x00CODE{len(code_blocks)-1}\x00'

    text_with_placeholders = _CODE_BLOCK_PATTERN.sub(save_code, text)

    # Strip markdown from non-code parts
    if _HAS_STRIP_MARKDOWN:
        stripped = _lib_strip_markdown(text_with_placeholders)
    else:
        stripped = _strip_inline_markdown(text_with_placeholders)

    # Restore code blocks (content only, fences removed)
    for i, code in enumerate(code_blocks):
        stripped = stripped.replace(f'\x00CODE{i}\x00', code.rstrip('\n'))

    return stripped


# Convenience alias for TTS (different default behavior)
def strip_markdown_for_tts(text: str) -> str:
    """Strip markdown for TTS. Removes code blocks entirely.

    Alias for strip_markdown(text, preserve_code_blocks=False).
    """
    return strip_markdown(text, preserve_code_blocks=False)


# Pattern to extract code blocks with language identifier
_CODE_BLOCK_EXTRACT_RE = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)


def extract_code_blocks(text: str) -> List[Tuple[str, str]]:
    """Extract fenced code blocks from markdown text.

    Returns a list of (language, code) tuples. The language is an empty
    string if no language identifier was specified.

    Used by llm-guiassistant for smart action buttons on code blocks.

    Args:
        text: Markdown-formatted text

    Returns:
        List of (language, code) tuples

    Examples:
        >>> extract_code_blocks("```python\\nprint('hi')\\n```")
        [('python', "print('hi')")]

        >>> extract_code_blocks("```\\nplain code\\n```")
        [('', 'plain code')]

        >>> extract_code_blocks("no code here")
        []

        >>> text = '''Here's Python:
        ... ```python
        ... def hello():
        ...     print("hi")
        ... ```
        ... And bash:
        ... ```bash
        ... echo hello
        ... ```
        ... '''
        >>> blocks = extract_code_blocks(text)
        >>> len(blocks)
        2
        >>> blocks[0][0]
        'python'
        >>> blocks[1][0]
        'bash'
    """
    matches = _CODE_BLOCK_EXTRACT_RE.findall(text)
    return [(lang, code.rstrip('\n')) for lang, code in matches]
