"""UI components for llm-assistant.

This module provides:
- Confirm: Extended confirmation prompt accepting 'yes'/'no'
- Spinner: Animated spinner for long-running operations
"""

from rich.console import Console
from rich.prompt import Confirm as _RichConfirm, InvalidResponse
from rich.status import Status


class Confirm(_RichConfirm):
    """Extended Confirm that accepts 'yes'/'no' in addition to 'y'/'n'."""

    def process_response(self, value: str) -> bool:
        value = value.strip().lower()
        if value in ("y", "yes"):
            return True
        elif value in ("n", "no"):
            return False
        else:
            raise InvalidResponse(self.validate_error_message)


# Module-level console for spinner (avoids passing console instance everywhere)
_spinner_console = Console()


class Spinner:
    """Animated spinner for long-running operations (context manager or manual control).

    Uses Rich's Status component for proper integration with Rich console output.
    This prevents issues where raw stdout writes interfere with Rich's rendering.
    """

    CHARS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "", console: Console = None):
        self.message = message
        self._console = console or _spinner_console
        self._status = None
        self._idx = 0

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def start(self):
        """Start the spinner animation using Rich Status."""
        self._status = Status(f"[cyan]{self.message}[/]", console=self._console, spinner="dots", spinner_style="cyan")
        self._status.start()

    def stop(self):
        """Stop the spinner."""
        if self._status:
            self._status.stop()
            self._status = None

    def clear(self):
        """Clear the spinner (stop is sufficient with Rich Status)."""
        self.stop()

    def update(self, message: str):
        """Update the spinner message."""
        self.message = message
        if self._status:
            self._status.update(f"[cyan]{message}[/]")

    def tick(self, message: str = None):
        """Manual tick for polling loops - update spinner once without threading.

        For Rich-based spinner, this just updates the message since Rich handles animation.
        """
        if message is not None:
            self.message = message
        if self._status:
            self._status.update(f"[cyan]{self.message}[/]")
        else:
            # Fallback for when used outside context manager
            spinner = self.CHARS[self._idx % len(self.CHARS)]
            self._console.print(f"[cyan]{spinner} {self.message}[/]", end="\r")
            self._idx += 1
