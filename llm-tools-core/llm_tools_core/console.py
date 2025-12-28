"""Console output formatting helpers.

This module provides consistent console output formatting used by:
- llm-assistant (terminal UI output)
- llm-inlineassistant (daemon output)

Note: This module does NOT import rich. The ConsoleHelper class receives
a rich.Console instance as a parameter, keeping this package dependency-free.
"""


class ConsoleHelper:
    """Consistent console output formatting.

    Provides static methods for common console output patterns.
    Uses Rich markup for styling.

    Note: All methods receive a console parameter - the rich.Console
    instance is provided by the caller, not imported here. This keeps
    llm-tools-core free of external dependencies.
    """

    @staticmethod
    def success(console, message: str) -> None:
        """Print success message with green checkmark."""
        console.print(f"[green]\u2713[/] {message}")

    @staticmethod
    def error(console, message: str) -> None:
        """Print error message with red X."""
        console.print(f"[red]\u2717[/] {message}")

    @staticmethod
    def warning(console, message: str) -> None:
        """Print warning message in yellow."""
        console.print(f"[yellow]{message}[/]")

    @staticmethod
    def warn_icon(console, message: str) -> None:
        """Print warning with warning icon."""
        console.print(f"[yellow]\u26a0[/] {message}")

    @staticmethod
    def info(console, message: str) -> None:
        """Print info message in cyan."""
        console.print(f"[cyan]{message}[/]")

    @staticmethod
    def dim(console, message: str) -> None:
        """Print dim/muted message."""
        console.print(f"[dim]{message}[/]")

    @staticmethod
    def enabled(console, message: str) -> None:
        """Print enabled/activated message in bold green."""
        console.print(f"[bold green]{message}[/bold green]")

    @staticmethod
    def disabled(console, message: str) -> None:
        """Print disabled/deactivated message in bold yellow."""
        console.print(f"[bold yellow]{message}[/bold yellow]")

    @staticmethod
    def bold(console, message: str) -> None:
        """Print bold message."""
        console.print(f"[bold]{message}[/bold]")
