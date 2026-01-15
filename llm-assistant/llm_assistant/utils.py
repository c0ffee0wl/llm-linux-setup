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
from typing import Any, Callable, List, Optional, Tuple, Type, TypeVar, Union, overload

from pydantic import BaseModel, ValidationError

T = TypeVar('T', bound=BaseModel)

# Import shared utilities from llm-tools-core
from llm_tools_core import ConsoleHelper
from llm_tools_core import get_config_dir as _core_get_config_dir
from llm_tools_core import get_temp_dir as _core_get_temp_dir
from llm_tools_core import get_logs_db_path as _core_get_logs_db_path
from llm_tools_core import strip_markdown, strip_markdown_for_tts  # noqa: F401 (re-export)
from llm_tools_core import get_model_context_limit  # noqa: F401 (re-export)


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


# Note: suggest_command uses llm_tools_core.daemon.write_suggested_command
# which writes to /tmp/llm-assistant-{uid}/suggest (daemon socket dir)


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
# Schema Response Parsing
# =============================================================================


def _extract_json(response_text: str) -> Optional[dict]:
    """Extract JSON dict from response text, handling markdown code blocks.

    Tries multiple strategies:
    1. Direct JSON parse
    2. Extract from markdown code blocks (```json ... ``` or ``` ... ```)
    3. Find raw JSON object in surrounding text

    Args:
        response_text: Raw response text from model

    Returns:
        Parsed dict if successful, None if extraction fails
    """
    import json
    import re

    # Strategy 1: Direct JSON parse
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from markdown code blocks ```json ... ``` or ``` ... ```
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find JSON object in text using JSONDecoder (handles nested braces)
    decoder = json.JSONDecoder()
    # Try each '{' position to find valid JSON
    for i, char in enumerate(response_text):
        if char == '{':
            try:
                obj, _ = decoder.raw_decode(response_text, i)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue

    return None


@overload
def parse_schema_response(response_text: str, schema_class: Type[T]) -> Optional[T]: ...

@overload
def parse_schema_response(response_text: str, schema_class: None = None) -> Optional[dict]: ...

def parse_schema_response(
    response_text: str,
    schema_class: Optional[Type[T]] = None
) -> Union[Optional[T], Optional[dict]]:
    """Parse and validate JSON from a schema response.

    Handles markdown code blocks that models sometimes wrap JSON in.
    When schema_class is provided, returns a validated Pydantic model instance.

    Args:
        response_text: Raw response text from model
        schema_class: Optional Pydantic model class for validation

    Returns:
        - If schema_class provided: Validated model instance or None
        - If schema_class is None: Parsed dict or None

    Examples:
        # Get typed model instance
        result = parse_schema_response(text, SafetySchema)
        if result:
            print(result.safe)  # Type-safe attribute access

        # Get raw dict (backward compatibility)
        result = parse_schema_response(text)
        if result:
            print(result.get('safe'))
    """
    data = _extract_json(response_text)
    if data is None:
        return None

    if schema_class is not None:
        try:
            return schema_class.model_validate(data)
        except ValidationError:
            return None

    return data


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


def is_handy_running() -> bool:
    """Check if Handy STT application is running.

    Handy provides OS-level speech-to-text input. When running, we should
    skip importing sounddevice/PortAudio to avoid resource conflicts and
    unnecessary microphone access.

    Returns:
        True if Handy is running, False otherwise
    """
    import subprocess
    try:
        result = subprocess.run(["pgrep", "-xi", "handy"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


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
# Judge Model Selection
# =============================================================================

def get_judge_model_id(model_id: str) -> Optional[str]:
    """Get appropriate lightweight model for security judge based on current model's provider.

    The judge model is used for safety evaluation in auto mode. Using a lighter model
    reduces cost and latency while maintaining sufficient capability for safety checks.

    Args:
        model_id: The current model's ID (e.g., 'gemini-2.5-pro', 'vertex/gemini-2.5-pro', 'azure/gpt-4.1')

    Returns:
        Model ID for judge, or None if no lighter alternative available
    """
    model_id_lower = model_id.lower()

    # Vertex Gemini → vertex/gemini-2.5-flash-lite
    if model_id_lower.startswith('vertex/'):
        return 'vertex/gemini-2.5-flash-lite'

    # Regular Gemini → gemini-2.5-flash-lite
    if model_id_lower.startswith('gemini'):
        return 'gemini-2.5-flash-lite'

    # Azure/OpenAI → azure/gpt-4.1-mini
    if model_id_lower.startswith(('azure/', 'gpt-', 'o1-', 'o3-', 'o4-')):
        return 'azure/gpt-4.1-mini'

    return None  # No lighter alternative known


def get_judge_model(current_model):
    """Get a lightweight model for security judge operations.

    Attempts to find a cheaper/faster model from the same provider for safety
    evaluation. Falls back to current model if no alternative is available or
    if the alternative doesn't support schema (required for structured output).

    Args:
        current_model: The current llm model object

    Returns:
        A model suitable for judge operations (lighter model if available, else current)
    """
    import llm

    judge_model_id = get_judge_model_id(current_model.model_id)

    if judge_model_id:
        try:
            judge_model = llm.get_model(judge_model_id)
            # Verify schema support (required for structured safety evaluation)
            if getattr(judge_model, 'supports_schema', False):
                return judge_model
        except Exception:
            pass  # Fall through to return current model

    return current_model


# =============================================================================
# Model Context Limits (imported from llm-tools-core)
# =============================================================================

# Re-export from llm_tools_core for backward compatibility
