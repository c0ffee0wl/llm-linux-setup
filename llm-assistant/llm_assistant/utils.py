"""Utility functions for llm-assistant.

This module contains utility functions for:
- Markdown stripping for TTS and clipboard
- Language code validation
- XDG-compliant directory management
- Model context limit resolution
"""

import os
import re
from functools import wraps
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple


# =============================================================================
# Directory Configuration (XDG Base Directory Specification)
# =============================================================================

def get_config_dir() -> Path:
    """Get llm-assistant config directory using XDG spec.

    Returns: XDG_CONFIG_HOME/llm-assistant or ~/.config/llm-assistant
    """
    xdg_config = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config:
        base = Path(xdg_config)
    else:
        base = Path.home() / '.config'
    return base / 'llm-assistant'


def get_temp_dir() -> Path:
    """Get llm-assistant temp directory with user isolation.

    Returns: TMPDIR/llm-assistant/{uid} or /tmp/llm-assistant/{uid}
    """
    tmpdir = os.environ.get('TMPDIR') or os.environ.get('TMP') or os.environ.get('TEMP')
    if tmpdir:
        base = Path(tmpdir)
    else:
        base = Path('/tmp')
    return base / 'llm-assistant' / str(os.getuid())


def get_logs_db_path() -> Path:
    """Get path to llm-assistant's conversation database.

    Returns: XDG_CONFIG_HOME/llm-assistant/logs.db or ~/.config/llm-assistant/logs.db

    This is separate from llm CLI's logs.db to keep assistant conversations
    isolated from regular llm usage.
    """
    return get_config_dir() / 'logs.db'


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


class ConsoleHelper:
    """Consistent console output formatting.

    Provides static methods for common console output patterns.
    Uses Rich markup for styling.
    """

    @staticmethod
    def success(console, message: str) -> None:
        """Print success message with green checkmark."""
        console.print(f"[green]✓[/] {message}")

    @staticmethod
    def error(console, message: str) -> None:
        """Print error message with red X."""
        console.print(f"[red]✗[/] {message}")

    @staticmethod
    def warning(console, message: str) -> None:
        """Print warning message in yellow."""
        console.print(f"[yellow]{message}[/]")

    @staticmethod
    def warn_icon(console, message: str) -> None:
        """Print warning with ⚠ icon."""
        console.print(f"[yellow]⚠[/] {message}")

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
# Markdown Processing
# =============================================================================


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
# Model Context Limits
# =============================================================================


def get_model_context_limit(model_name: str) -> int:
    """Get the appropriate context limit for a model.

    Resolution order:
    1. Explicit model name in MODEL_CONTEXT_LIMITS (with azure/ prefix stripped)
    2. Provider prefix default from PROVIDER_DEFAULT_LIMITS
    3. DEFAULT_CONTEXT_LIMIT fallback

    Args:
        model_name: Full model name (e.g., "azure/gpt-4.1", "claude-3-opus")

    Returns:
        Context limit in tokens
    """
    # Import here to avoid circular imports
    from .config import MODEL_CONTEXT_LIMITS, PROVIDER_DEFAULT_LIMITS, DEFAULT_CONTEXT_LIMIT

    # Strip azure/ prefix for lookup (azure/gpt-4.1 -> gpt-4.1)
    lookup_name = model_name
    if model_name.startswith("azure/"):
        lookup_name = model_name[6:]  # Remove "azure/"

    # Check explicit model limit
    if lookup_name in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[lookup_name]

    # Check provider prefix defaults
    for prefix, limit in PROVIDER_DEFAULT_LIMITS.items():
        if model_name.startswith(prefix):
            return limit

    # Absolute fallback
    return DEFAULT_CONTEXT_LIMIT
