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
    # to avoid false positives on output containing $ (like currency) or # (shell comments)
    PROMPT_PATTERNS = [
        # Dollar prompt: word char, path, or symbol before $
        re.compile(r'[~\w/\])\s:][$]\s*$'),
        re.compile(r'[~\w/\])\s:][$]\s+\S+'),
        # Standalone $ at line start (minimal prompt)
        re.compile(r'^[$]\s*$'),
        re.compile(r'^[$]\s+\S+'),
        # Hash prompt (root): require path context (/word) OR explicit symbols (~, ], ))
        # NO whitespace before # - avoids matching "command # comment"
        re.compile(r'(?:/\w*|[~\])])[#]\s*$'),
        re.compile(r'(?:/\w*|[~\])])[#]\s+\S+'),
        # Zsh markers (not after digit to avoid $100)
        re.compile(r'(?<!\d)[%❯→➜]\s*$'),
        re.compile(r'(?<!\d)[%❯→➜]\s+\S+'),
        # user@host pattern - require prompt char at/near end (avoid matching email addresses)
        re.compile(r'^\S+@\S+\s*[$#%]\s*$'),
    ]

    # Kali/fancy two-line prompts (supports ┌/╭ and └/╰)
    # Note: \s* allows trailing whitespace which terminals often include
    KALI_HEADER = re.compile(r'^[┌╭]──.*[\])]\s*$')
    KALI_PROMPT_LINE = re.compile(r'^[└╰]─+[$#]')

    # Patterns for EMPTY prompts only (shell ready for input)
    # Used by detect_prompt_at_end() for completion detection
    # Unlike PROMPT_PATTERNS, these require prompt char at END of line
    EMPTY_PROMPT_PATTERNS = [
        # Dollar prompt: ends with $
        re.compile(r'[~\w/\])\s:][$]\s*$'),
        re.compile(r'^[$]\s*$'),               # standalone $ at line start
        # Hash prompt (root): ends with # (path context required)
        re.compile(r'(?:/\w*|[~\])])[#]\s*$'),
        # Zsh: ends with % ❯ → ➜
        re.compile(r'(?<!\d)[%❯→➜]\s*$'),
        # user@host: prompt char at END
        re.compile(r'^\S+@\S+\s*[$#%]\s*$'),
    ]
    # Kali prompt: allow trailing whitespace and control chars (cursor, etc.)
    KALI_EMPTY_PROMPT_LINE = re.compile(r'^[└╰]─+[$#][\s\x00-\x1f]*$')

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
    def detect_prompt_at_end(cls, text: str, debug: bool = False) -> bool:
        """
        Check if text ends with an EMPTY shell prompt (ready for input).

        Unlike is_prompt_line(), this does NOT match prompts with commands
        after them. Used for completion detection to distinguish between:
        - "$ command" (executing) - does NOT match
        - "$ " (ready for input) - MATCHES

        Args:
            text: Terminal output text
            debug: If True, print diagnostic info about pattern matching

        Returns:
            True if text ends with an empty prompt ready for input
        """
        if not text or not text.strip():
            if debug:
                print("[PromptDetector] Empty or whitespace-only text")
            return False

        lines = text.strip().split('\n')
        last = lines[-1]

        if debug:
            print(f"[PromptDetector] Checking {len(lines)} lines")
            print(f"[PromptDetector] Last line: {last!r}")
            if len(lines) >= 2:
                print(f"[PromptDetector] Prev line: {lines[-2]!r}")

        # Check empty prompt patterns (ready for input, no command after)
        for i, p in enumerate(cls.EMPTY_PROMPT_PATTERNS):
            if p.search(last):
                if debug:
                    print(f"[PromptDetector] Matched EMPTY_PROMPT_PATTERNS[{i}]")
                return True

        # Check Kali two-line prompt (must be empty)
        if len(lines) >= 2:
            prev = lines[-2]
            header_match = cls.KALI_HEADER.search(prev)
            prompt_match = cls.KALI_EMPTY_PROMPT_LINE.search(last)
            if debug:
                print(f"[PromptDetector] Kali header match: {header_match is not None}")
                print(f"[PromptDetector] Kali prompt match: {prompt_match is not None}")
            if header_match and prompt_match:
                return True

        if debug:
            print("[PromptDetector] No pattern matched")
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
