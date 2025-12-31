"""Input history management for llm-guiassistant.

Provides shell-like Up/Down navigation through previously typed inputs.
History is persisted to ~/.config/llm-guiassistant/input-history.json.
"""

import json
from pathlib import Path
from typing import List


class InputHistory:
    """Manages input history with shell-like navigation.

    Features:
    - Up/Down arrow navigation through history
    - Preserves current draft when navigating
    - Persists to disk (survives restarts)
    - Limits to MAX_HISTORY entries
    """

    MAX_HISTORY = 100
    HISTORY_FILE = Path.home() / ".config" / "llm-guiassistant" / "input-history.json"

    def __init__(self):
        self.history: List[str] = self._load()
        self.position: int = len(self.history)  # Start at end (new input position)
        self.draft: str = ""  # Preserve in-progress input when navigating

    def _load(self) -> List[str]:
        """Load history from disk, or return empty list."""
        try:
            if self.HISTORY_FILE.exists():
                data = json.loads(self.HISTORY_FILE.read_text())
                if isinstance(data, list):
                    return data[-self.MAX_HISTORY:]
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save(self):
        """Save history to disk."""
        try:
            self.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.HISTORY_FILE.write_text(json.dumps(self.history))
        except OSError:
            pass  # Best effort - don't crash on save failure

    def add(self, text: str):
        """Add input to history after submission.

        Deduplicates consecutive identical entries.
        Resets navigation position to end.
        """
        text = text.strip()
        if not text:
            return

        # Don't add duplicate of last entry
        if self.history and text == self.history[-1]:
            self.position = len(self.history)
            self.draft = ""
            return

        self.history.append(text)

        # Trim to max size
        if len(self.history) > self.MAX_HISTORY:
            self.history = self.history[-self.MAX_HISTORY:]

        self._save()

        # Reset position to end
        self.position = len(self.history)
        self.draft = ""

    def navigate(self, direction: int, current_text: str) -> str:
        """Navigate through history.

        Args:
            direction: -1 for up (older), +1 for down (newer)
            current_text: Current text in input field

        Returns:
            Text to display in input field
        """
        if not self.history:
            return current_text

        # Save draft when starting to navigate
        if self.position == len(self.history):
            self.draft = current_text

        new_pos = self.position + direction

        # Navigate up into history
        if 0 <= new_pos < len(self.history):
            self.position = new_pos
            return self.history[new_pos]

        # Navigate down past history to draft
        if new_pos >= len(self.history):
            self.position = len(self.history)
            return self.draft

        # At boundary, don't change
        return current_text

    def reset(self):
        """Reset navigation position to end (for new session)."""
        self.position = len(self.history)
        self.draft = ""

    def clear(self):
        """Clear all history."""
        self.history = []
        self.position = 0
        self.draft = ""
        self._save()
