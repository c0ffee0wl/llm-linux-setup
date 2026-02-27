"""Unified prompt detection for llm-tools.

This module provides shell prompt detection used by:
- llm-assistant (terminal prompt detection)
- llm-inlineassistant (daemon prompt detection)
- llm-tools-context (asciinema recording parsing)
- terminator plugin (VTE terminal detection)

It uses a hybrid detection approach:
1. Unicode markers (100% reliable for VTE terminals)
2. Regex fallback (SSH sessions, non-VTE terminals)
"""
import re
from typing import List, Tuple, Union


class PromptDetector:
    """Detect shell prompts in terminal output"""

    # Invisible Unicode markers for 100% reliable prompt detection in VTE terminals
    # These zero-width characters survive VTE's get_text_range_format(Vte.Format.TEXT)
    # Used by llm-assistant for local prompt detection (context tool uses regex-only)
    PROMPT_START_MARKER = '\u200B\u200D\u200B'  # ZWS+ZWJ+ZWS - before PS1
    INPUT_START_MARKER = '\u200D\u200B\u200D'   # ZWJ+ZWS+ZWJ - after PS1

    # Patterns to match shell prompts. More specific than just "ends with $"
    # to avoid false positives on output containing $ (like currency) or # (shell comments)
    PROMPT_PATTERNS = [
        # Dollar prompt: word char, path, or symbol before $
        # NO whitespace - avoids matching " $ " in prose like 'echo "Enter $ to continue"'
        re.compile(r'[~\w/\]):][$]\s*$'),
        re.compile(r'[~\w/\]):][$]\s+\S+'),
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
        # PowerShell prompts: PS C:\path> or PS /path> or PS>
        re.compile(r'^PS\s+[^>]*>\s*$'),
        re.compile(r'^PS\s+[^>]*>\s+\S+'),
        re.compile(r'^PS>\s*$'),
        re.compile(r'^PS>\s+\S+'),
        # PowerShell Remoting: [user@host]: PS path> or [user@host]: PS>
        re.compile(r'^\[[^\]]+\]:\s*PS\s+[^>]*>\s*$'),
        re.compile(r'^\[[^\]]+\]:\s*PS\s+[^>]*>\s+\S+'),
        re.compile(r'^\[[^\]]+\]:\s*PS>\s*$'),
        re.compile(r'^\[[^\]]+\]:\s*PS>\s+\S+'),
    ]

    # Kali/fancy two-line prompts (supports ┌/╭ and └/╰)
    # Note: \s* allows trailing whitespace which terminals often include
    KALI_HEADER = re.compile(r'^[┌╭]──.*[\])]\s*$')
    KALI_PROMPT_LINE = re.compile(r'^[└╰]─+(?:[$#]|PS>)')

    # Patterns for EMPTY prompts only (shell ready for input)
    # Used by detect_prompt_at_end() for completion detection
    # Unlike PROMPT_PATTERNS, these require prompt char at END of line
    EMPTY_PROMPT_PATTERNS = [
        # Dollar prompt: ends with $ (NO whitespace before - avoids " $ " in prose)
        re.compile(r'[~\w/\]):][$]\s*$'),
        re.compile(r'^[$]\s*$'),               # standalone $ at line start
        # Hash prompt (root): ends with # (path context required)
        re.compile(r'(?:/\w*|[~\])])[#]\s*$'),
        # Zsh: ends with % ❯ → ➜ (prompt char at end of line)
        re.compile(r'(?<!\d)[%❯→➜]\s*$'),
        # oh-my-zsh style: ➜  dirname [git:(branch)] [status] (prompt char at START)
        # Matches robbyrussell and similar themes where arrow comes first
        # Pattern: ➜  <dirname> [git:(<branch>)] [status-indicators]<whitespace-only>
        # Status indicators: ✗✔✘✓ (dirty/clean), *+? (changes), ⚡!↑↓ (ahead/behind), ●○◐ (stash)
        re.compile(r'^[❯→➜]\s+[\w./-]+(?:\s+git:\([^)]+\))?(?:\s*[✗✔✘✓*+?⚡!↑↓●○◐])*\s*$'),
        # user@host: prompt char at END
        re.compile(r'^\S+@\S+\s*[$#%]\s*$'),
        # PowerShell: PS C:\path> or PS /path> or PS>
        re.compile(r'^PS\s+[^>]*>\s*$'),
        re.compile(r'^PS>\s*$'),
        # PowerShell Remoting: [user@host]: PS path> or [user@host]: PS>
        re.compile(r'^\[[^\]]+\]:\s*PS\s+[^>]*>\s*$'),
        re.compile(r'^\[[^\]]+\]:\s*PS>\s*$'),
    ]
    # Kali prompt: allow trailing whitespace and control chars (cursor, etc.)
    KALI_EMPTY_PROMPT_LINE = re.compile(r'^[└╰]─+(?:[$#]|PS>)[\s\x00-\x1f]*$')

    @classmethod
    def is_prompt_line(cls, line: str) -> bool:
        """Check if a single line matches a prompt pattern"""
        if not line.strip():
            return False
        # Strip tag metadata and Unicode markers before regex matching
        # They break ^ anchored patterns (e.g., Kali prompt ^[┌╭])
        line = cls.strip_tag_metadata(line)
        line = line.replace(cls.PROMPT_START_MARKER, '').replace(cls.INPUT_START_MARKER, '')
        # Check standard patterns (includes PowerShell)
        if any(p.search(line) for p in cls.PROMPT_PATTERNS):
            return True
        # Check Kali two-line prompt (second line, includes PS>)
        if cls.KALI_PROMPT_LINE.search(line):
            return True
        return False

    @classmethod
    def has_unicode_markers(cls, text: str) -> bool:
        """Check if text contains Unicode prompt markers (VTE terminals only)

        Returns True only if INPUT_START_MARKER is present, since that's what
        we use for marker-based detection. If only PROMPT_START is present
        (edge case), we should fall through to pure regex instead.
        """
        return cls.INPUT_START_MARKER in text

    # Tag character range for invisible metadata (legacy support)
    # Unicode Tags block (U+E0000-E007F) - no longer encoded but may exist in old data
    TAG_CHAR_BASE = 0xE0000
    TAG_CHAR_END = 0xE007F

    @classmethod
    def strip_tag_metadata(cls, text: str) -> str:
        """Remove tag characters from text for clean display/regex matching."""
        return ''.join(c for c in text if not (cls.TAG_CHAR_BASE <= ord(c) <= cls.TAG_CHAR_END))

    @classmethod
    def _detect_prompt_regex(cls, text: str, debug: bool = False) -> bool:
        """
        Internal: Check if text ends with an empty prompt using regex patterns.
        This is the original detection logic, extracted for hybrid detection.
        """
        if not text or not text.strip():
            if debug:
                print("[PromptDetector] Empty or whitespace-only text (regex)")
            return False

        # Strip tag metadata and Unicode markers before regex matching
        # They break ^ anchored patterns (e.g., Kali prompt ^[┌╭])
        text = cls.strip_tag_metadata(text)
        text = text.replace(cls.PROMPT_START_MARKER, '').replace(cls.INPUT_START_MARKER, '')

        lines = text.strip().split('\n')
        last = lines[-1]

        if debug:
            print(f"[PromptDetector] Regex checking {len(lines)} lines")
            print(f"[PromptDetector] Last line: {last!r}")

        # Check empty prompt patterns (ready for input, no command after)
        for i, p in enumerate(cls.EMPTY_PROMPT_PATTERNS):
            if p.search(last):
                if debug:
                    print(f"[PromptDetector] Matched EMPTY_PROMPT_PATTERNS[{i}]")
                return True

        # Check Kali two-line prompt (must be empty, includes PS>)
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
            print("[PromptDetector] No regex pattern matched")
        return False

    @classmethod
    def detect_prompt_at_end_with_method(cls, text: str, debug: bool = False) -> Tuple[bool, str]:
        """
        Check if text ends with an EMPTY shell prompt (ready for input).
        Returns both the detection result and the method used.

        Uses hybrid detection:
        1. Priority 1: Unicode markers (100% reliable for VTE terminals)
        2. Priority 2: Regex fallback (SSH sessions, non-VTE terminals)

        Unlike is_prompt_line(), this does NOT match prompts with commands
        after them. Used for completion detection to distinguish between:
        - "$ command" (executing) - does NOT match
        - "$ " (ready for input) - MATCHES

        Args:
            text: Terminal output text
            debug: If True, print diagnostic info about pattern matching

        Returns:
            Tuple of (detected: bool, method: str) where method is:
            - "marker" if detected via Unicode markers
            - "regex" if detected via regex patterns
            - "" if not detected
        """
        if not text or not text.strip():
            if debug:
                print("[PromptDetector] Empty or whitespace-only text")
            return (False, "")

        # Priority 1: Unicode markers (100% reliable for VTE local prompts)
        if cls.has_unicode_markers(text):
            if debug:
                print("[PromptDetector] Unicode markers detected, using marker-based detection")

            # Find last INPUT_START_MARKER (marks where user types after prompt)
            last_input_pos = text.rfind(cls.INPUT_START_MARKER)
            if last_input_pos != -1:
                # Check what comes after the marker
                after = text[last_input_pos + len(cls.INPUT_START_MARKER):]
                if debug:
                    print(f"[PromptDetector] Text after INPUT_START_MARKER: {after!r}")

                if not after.strip():
                    if debug:
                        print("[PromptDetector] Marker + nothing after = ready for input (100%)")
                    return (True, "marker")  # Marker + nothing = ready for input (100% reliable)
                else:
                    # Text after marker - could be SSH session with remote prompt
                    # Use regex to detect if the text after marker ends with a prompt
                    if debug:
                        print("[PromptDetector] Text after marker, checking with regex fallback")
                    if cls._detect_prompt_regex(after, debug):
                        return (True, "regex")
                    return (False, "")

        # Priority 2: Regex fallback (SSH sessions, non-VTE terminals, no markers)
        if debug:
            print("[PromptDetector] No Unicode markers, using regex fallback")
        if cls._detect_prompt_regex(text, debug):
            return (True, "regex")
        return (False, "")

    @classmethod
    def detect_prompt_at_end(cls, text: str, debug: bool = False) -> bool:
        """
        Check if text ends with an EMPTY shell prompt (ready for input).

        Uses hybrid detection:
        1. Priority 1: Unicode markers (100% reliable for VTE terminals)
        2. Priority 2: Regex fallback (SSH sessions, non-VTE terminals)

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
        detected, _ = cls.detect_prompt_at_end_with_method(text, debug)
        return detected

    @classmethod
    def find_all_prompts(cls, text_or_lines: Union[str, List[str]]) -> List[Tuple[int, str]]:
        """
        Find all prompt lines in text.
        Used by context tool to split recording into command blocks.

        Uses marker-priority detection: when INPUT_START_MARKER is present in the text,
        ONLY lines containing that marker are considered as prompts. This eliminates
        false positives from command output that happens to match prompt regex patterns.

        Args:
            text_or_lines: Either a text string or a list of lines

        Returns:
            List of (line_number, line_content) tuples for lines that are prompts.
        """
        if isinstance(text_or_lines, str):
            lines = text_or_lines.split('\n')
        else:
            lines = list(text_or_lines)

        # Check if markers are present (for marker-priority detection)
        full_text = '\n'.join(lines)
        has_markers = cls.has_unicode_markers(full_text)

        prompt_lines = []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            # When markers are present, ONLY accept lines with INPUT_START_MARKER
            # This eliminates false positives from command output
            if has_markers and cls.INPUT_START_MARKER not in line:
                continue
            if cls.is_prompt_line(line):
                prompt_lines.append((i, line))

        # Adjust for Kali two-line prompts (include header line)
        adjusted = []
        for line_num, line_content in prompt_lines:
            if line_num > 0:
                # Strip tag metadata and Unicode markers before KALI_HEADER check
                prev_line = cls.strip_tag_metadata(lines[line_num - 1])
                prev_line = prev_line.replace(cls.PROMPT_START_MARKER, '').replace(cls.INPUT_START_MARKER, '')
                if cls.KALI_HEADER.search(prev_line):
                    adjusted.append((line_num - 1, lines[line_num - 1]))
                    continue
            adjusted.append((line_num, line_content))

        return adjusted
