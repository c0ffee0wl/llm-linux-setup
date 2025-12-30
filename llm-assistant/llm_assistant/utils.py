"""Utility functions for llm-assistant.

This module contains utility functions for:
- Language code validation
- XDG-compliant directory management
- Model context limit resolution

Markdown stripping functions (strip_markdown, strip_markdown_for_tts)
are imported from llm_tools_core and re-exported here for backward compatibility.
"""

import os
from functools import wraps
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

# Import shared utilities from llm-tools-core
from llm_tools_core import ConsoleHelper
from llm_tools_core import get_config_dir as _core_get_config_dir
from llm_tools_core import get_temp_dir as _core_get_temp_dir
from llm_tools_core import get_logs_db_path as _core_get_logs_db_path
from llm_tools_core import (
    strip_markdown,
    strip_markdown_for_tts,
)


# =============================================================================
# Directory Configuration (XDG Base Directory Specification)
# =============================================================================

_APP_NAME = "llm-assistant"


def get_config_dir() -> Path:
    """Get llm-assistant config directory using XDG spec.

    Returns: XDG_CONFIG_HOME/llm-assistant or ~/.config/llm-assistant
    """
    return _core_get_config_dir(_APP_NAME)


def get_temp_dir() -> Path:
    """Get llm-assistant temp directory with user isolation.

    Returns: TMPDIR/llm-assistant/{uid} or /tmp/llm-assistant/{uid}
    """
    return _core_get_temp_dir(_APP_NAME)


def get_suggest_path() -> Path:
    """Get path for suggested command file.

    Returns: TMPDIR/llm-assistant/{uid}/suggest

    Used by suggest_command tool to pass commands to the shell,
    which places them on the user's prompt for editing/execution.
    """
    return get_temp_dir() / "suggest"


def write_suggested_command(command: str) -> None:
    """Write a suggested command for the shell to pick up.

    Args:
        command: The command to suggest (placed on user's prompt)
    """
    get_suggest_path().write_text(command)


def get_logs_db_path() -> Path:
    """Get path to llm-assistant's conversation database.

    Returns: XDG_CONFIG_HOME/llm-assistant/logs.db or ~/.config/llm-assistant/logs.db

    This is separate from llm CLI's logs.db to keep assistant conversations
    isolated from regular llm usage.
    """
    return _core_get_logs_db_path(_APP_NAME)


def logs_on() -> bool:
    """Check if logging is enabled (respects llm's global logs-off setting).

    Returns False if llm's logs-off file exists, True otherwise.
    This ensures llm-assistant respects `llm logs off` command.
    """
    import llm
    return not (llm.user_dir() / "logs-off").exists()


# =============================================================================
# Command Parsing Helpers
# =============================================================================


def parse_command(args: str) -> Tuple[str, str]:
    """Parse slash command args into (subcommand, remaining).

    Examples:
        parse_command("load foo")    # Returns ("load", "foo")
        parse_command("list")        # Returns ("list", "")
        parse_command("")            # Returns ("", "")
        parse_command("  ")          # Returns ("", "")
    """
    parts = args.strip().split(maxsplit=1)
    if not parts or parts[0] == "":
        return ("", "")
    return (parts[0], parts[1] if len(parts) > 1 else "")


def parse_comma_list(text: str) -> List[str]:
    """Parse comma-separated list with trimming.

    Examples:
        parse_comma_list("a,b,c")      # Returns ["a", "b", "c"]
        parse_comma_list("a, b , c")   # Returns ["a", "b", "c"]
        parse_comma_list("")           # Returns []
    """
    return [n.strip() for n in text.split(",") if n.strip()]


# =============================================================================
# Plugin Availability Check
# =============================================================================


def check_import(module_name: str) -> bool:
    """Check if a module is importable.

    Args:
        module_name: Fully qualified module name (e.g., "llm_tools_rag")

    Returns:
        True if module can be imported, False otherwise.
    """
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


# =============================================================================
# Console Output Helpers
# =============================================================================

# ConsoleHelper is imported from llm_tools_core above and re-exported for
# backward compatibility with existing imports: from .utils import ConsoleHelper


def render_grouped_list(
    console,
    title: str,
    loaded: dict,
    available: dict,
    format_item: Callable[[str, Any], str],
    empty_msg: str = "None found"
) -> None:
    """Render grouped list with Loaded/Available sections.

    Args:
        console: Rich Console instance
        title: Section title (e.g., "Knowledge Bases")
        loaded: Dict of loaded items {name: data}
        available: Dict of all available items {name: data}
        format_item: Callable(name, data) -> display string
        empty_msg: Message when both dicts are empty
    """
    console.print()
    ConsoleHelper.bold(console, title)

    if loaded:
        console.print()
        console.print("[green]Loaded:[/]")
        for name in sorted(loaded.keys()):
            console.print(f"  • {format_item(name, loaded[name])}")

    unloaded = {k: v for k, v in available.items() if k not in loaded}
    if unloaded:
        console.print()
        ConsoleHelper.dim(console, "Available:")
        for name in sorted(unloaded.keys()):
            console.print(f"  • {format_item(name, unloaded[name])}")

    if not loaded and not available:
        console.print()
        ConsoleHelper.dim(console, empty_msg)


# =============================================================================
# Exception Handling Decorator
# =============================================================================


def safe_operation(error_prefix: str = "Operation failed", return_on_error: Any = False):
    """Decorator for consistent exception handling.

    Use on methods that have a simple try/except pattern and a uniform
    error message format. Not suitable for complex error handling.

    Args:
        error_prefix: Prefix for error message (e.g., "Failed to load KB")
        return_on_error: Value to return when exception occurs

    Example:
        @safe_operation("Failed to load KB")
        def _load_kb(self, name: str) -> bool:
            content = Path(name).read_text()
            ...
            return True
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                if hasattr(self, 'console'):
                    self.console.print(f"[red]{error_prefix}: {e}[/]")
                return return_on_error
        return wrapper
    return decorator


# =============================================================================
# Markdown Processing (imported from llm-tools-core)
# =============================================================================
# strip_markdown and strip_markdown_for_tts are imported above
# from llm_tools_core and re-exported here for backward compatibility.


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


# =============================================================================
# Pure Utility Functions
# =============================================================================


def process_exists(pid: int) -> bool:
    """Check if a process with the given PID exists.

    Args:
        pid: Process ID to check

    Returns:
        True if process exists, False otherwise
    """
    try:
        os.kill(pid, 0)  # Signal 0 = check existence without killing
        return True
    except (OSError, ProcessLookupError):
        return False


def md_table_escape(value: str) -> str:
    """Escape a string for use in markdown table cells."""
    if not value:
        return value
    # Escape pipe characters which break table structure
    return str(value).replace('|', '\\|')


def yaml_escape(value) -> str:
    """Escape a value for safe YAML output."""
    if value is None:
        return '""'
    if isinstance(value, bool):
        return 'true' if value else 'false'  # YAML boolean literals
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    # Empty string must be quoted to avoid being parsed as null
    if not s:
        return '""'
    # Quote if contains special chars or looks like YAML syntax
    if any(c in s for c in [':', '#', '"', "'", '\n', '{', '}', '[', ']']) or s.startswith(('-', '!', '&', '*')):
        # Use double quotes and escape internal quotes/newlines
        s = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
        return f'"{s}"'
    return s


def is_watch_response_dismissive(response_text: str) -> bool:
    """Determine if response indicates no action needed."""
    if not response_text:
        return True
    normalized = response_text.strip().lower().rstrip('.')
    dismissive_exact = {
        'ok', 'okay', 'k', 'no comment', 'no issues', 'nothing to report',
        'nothing new', 'all good', 'looks good', 'no action needed',
        'no changes', 'nothing notable', 'nothing unusual', 'all normal',
    }
    if normalized in dismissive_exact:
        return True
    # Short positive responses (1-3 words) are likely dismissive
    words = normalized.split()
    if len(words) <= 3:
        positive = {'ok', 'okay', 'good', 'fine', 'normal', 'clear', 'stable'}
        if any(word in positive for word in words):
            return True
    return False


# =============================================================================
# Model Context Limits (imported from llm-tools-core)
# =============================================================================

# Re-export from llm_tools_core for backward compatibility
from llm_tools_core import get_model_context_limit
