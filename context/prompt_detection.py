"""Unified prompt detection for llm-tools

This module provides shell prompt detection used by both the context tool
and llm-sidechat. It's installed to Python user site-packages (llm_tools/)
and can be imported as: from llm_tools.prompt_detection import PromptDetector
"""
import re
from typing import List, Tuple, Union


class PromptDetector:
    """Detect shell prompts in terminal output"""

    # Patterns to match shell prompts. More specific than just "ends with $"
    # to avoid false positives on output containing $ (like currency)
    PROMPT_PATTERNS = [
        # Path-like character before $ or # (covers ~$, path$, ]$, )$, :$)
        re.compile(r'[~\w/\])\s:][$#]\s*$'),
        # $ or # followed by command (user typing)
        re.compile(r'[~\w/\])\s:][$#]\s+\S+'),
        # Zsh markers (not after digit to avoid $100)
        re.compile(r'(?<!\d)[%❯→➜]\s*$'),
        re.compile(r'(?<!\d)[%❯→➜]\s+\S+'),
        # user@host pattern
        re.compile(r'^\S+@\S+.*[$#%]'),
    ]

    # Kali/fancy two-line prompts (supports ┌/╭ and └/╰)
    # Note: \s* allows trailing whitespace which terminals often include
    KALI_HEADER = re.compile(r'^[┌╭]──.*[\])]\s*$')
    KALI_PROMPT_LINE = re.compile(r'^[└╰]─+[$#]')

    # Patterns for EMPTY prompts only (shell ready for input)
    # Used by detect_prompt_at_end() for completion detection
    # Unlike PROMPT_PATTERNS, these require prompt char at END of line
    EMPTY_PROMPT_PATTERNS = [
        re.compile(r'[~\w/\])\s:][$#]\s*$'),   # bash/sh: ends with $ or #
        re.compile(r'(?<!\d)[%❯→➜]\s*$'),      # zsh: ends with % ❯ → ➜
        re.compile(r'^\S+@\S+.*[$#%]\s*$'),    # user@host: ends with prompt char
    ]
    KALI_EMPTY_PROMPT_LINE = re.compile(r'^[└╰]─+[$#]\s*$')

    @classmethod
    def is_prompt_line(cls, line: str) -> bool:
        """Check if a single line matches a prompt pattern"""
        if not line.strip():
            return False
        # Check standard patterns
        if any(p.search(line) for p in cls.PROMPT_PATTERNS):
            return True
        # Check Kali two-line prompt (second line)
        if cls.KALI_PROMPT_LINE.search(line):
            return True
        return False

    @classmethod
    def detect_prompt_at_end(cls, text: str) -> bool:
        """
        Check if text ends with an EMPTY shell prompt (ready for input).

        Unlike is_prompt_line(), this does NOT match prompts with commands
        after them. Used for completion detection to distinguish between:
        - "$ command" (executing) - does NOT match
        - "$ " (ready for input) - MATCHES

        Args:
            text: Terminal output text

        Returns:
            True if text ends with an empty prompt ready for input
        """
        if not text or not text.strip():
            return False

        lines = text.strip().split('\n')
        last = lines[-1]

        # Check empty prompt patterns (ready for input, no command after)
        if any(p.search(last) for p in cls.EMPTY_PROMPT_PATTERNS):
            return True

        # Check Kali two-line prompt (must be empty)
        if len(lines) >= 2:
            prev = lines[-2]
            if cls.KALI_HEADER.search(prev) and cls.KALI_EMPTY_PROMPT_LINE.search(last):
                return True

        return False

    @classmethod
    def find_all_prompts(cls, text_or_lines: Union[str, List[str]]) -> List[Tuple[int, str]]:
        """
        Find all prompt lines in text.
        Used by context tool to split recording into command blocks.

        Args:
            text_or_lines: Either a text string or a list of lines

        Returns:
            List of (line_number, line_content) tuples for lines that are prompts.
        """
        if isinstance(text_or_lines, str):
            lines = text_or_lines.split('\n')
        else:
            lines = list(text_or_lines)

        prompt_lines = []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            if cls.is_prompt_line(line):
                prompt_lines.append((i, line))

        # Adjust for Kali two-line prompts (include header line)
        adjusted = []
        for line_num, line_content in prompt_lines:
            if line_num > 0 and cls.KALI_HEADER.search(lines[line_num - 1]):
                adjusted.append((line_num - 1, lines[line_num - 1]))
            else:
                adjusted.append((line_num, line_content))

        return adjusted
