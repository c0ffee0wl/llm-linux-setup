"""Utility functions for llm-assistant.

This module contains utility functions for:
- Markdown stripping for TTS and clipboard
- Language code validation
"""

import re
from typing import Optional


# Regex to remove fenced code blocks (```...```) only, preserves inline code
_CODE_BLOCK_RE = re.compile(r'```[\s\S]*?```', re.MULTILINE)
# Pattern to match complete code blocks (for preserving code during markdown stripping)
_CODE_BLOCK_PATTERN = re.compile(r'```\w*\n(.*?)```', re.DOTALL)


# Markdown stripping for TTS (removes formatting before speech synthesis)
try:
    from strip_markdown import strip_markdown as _strip_markdown

    def strip_markdown_for_tts(text: str) -> str:
        """Strip markdown formatting for TTS. Removes code blocks entirely."""
        text = _CODE_BLOCK_RE.sub('', text)
        return _strip_markdown(text)
except ImportError:
    def strip_markdown_for_tts(text: str) -> str:
        """Fallback: just remove code blocks."""
        return _CODE_BLOCK_RE.sub('', text)


def strip_markdown_for_clipboard(text: str) -> str:
    """Strip markdown but fully preserve code block content."""
    # Extract code blocks and replace with null-byte placeholders
    code_blocks = []

    def save_code(match):
        code_blocks.append(match.group(1))
        return f'\x00CODE{len(code_blocks)-1}\x00'

    text_with_placeholders = _CODE_BLOCK_PATTERN.sub(save_code, text)

    # Strip markdown from non-code parts only
    try:
        stripped = _strip_markdown(text_with_placeholders)
    except NameError:
        stripped = text_with_placeholders

    # Restore code blocks (content only, fences removed)
    for i, code in enumerate(code_blocks):
        stripped = stripped.replace(f'\x00CODE{i}\x00', code.rstrip('\n'))

    return stripped


def validate_language_code(code: str) -> Optional[str]:
    """Validate ISO 639-1 language code and return full English name.

    Uses iso639-lang library for comprehensive validation.
    Accepts lowercase input (Pythonic). Returns None if invalid.

    Examples:
        validate_language_code("de")  # Returns "German"
        validate_language_code("EN")  # Returns "English"
        validate_language_code("xx")  # Returns None
    """
    try:
        from iso639 import Lang
        from iso639.exceptions import InvalidLanguageValue
        lang = Lang(code.lower().strip())
        return lang.name  # Returns English name, e.g., "German"
    except InvalidLanguageValue:
        return None
    except ImportError:
        # Fallback if iso639-lang not installed
        return code.lower().strip() if len(code) == 2 else None
