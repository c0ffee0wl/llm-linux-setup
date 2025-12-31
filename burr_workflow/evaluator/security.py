"""
Security utilities for path validation and output sanitization.

Provides defense against:
- Path traversal attacks
- Command injection via output
- Control flow hijacking via reserved keys
"""

import re
from pathlib import Path
from typing import Any

from ..core.errors import PathTraversalError, SecurityError
from ..core.types import RESERVED_STATE_KEYS


class PathValidator:
    """Validates paths stay within allowed workspace.

    Applied at multiple points:
    1. Environment variable resolution (paths in env: block)
    2. Step execution (capture_mode paths)
    3. Script execution (script paths)
    """

    # Patterns that indicate a path value
    PATH_PATTERNS = [
        r"^\./",  # Starts with ./
        r"^/",  # Starts with /
        r"^~/",  # Starts with ~/
        r"\.\.",  # Contains ..
        r".*[/\\].*",  # Contains path separator
    ]

    # Environment variable names that typically contain paths
    PATH_ENV_NAMES = frozenset([
        "scan_dir", "output_dir", "temp_dir", "work_dir", "log_dir",
        "data_dir", "config_dir", "scripts_dir", "assets_dir",
        "dir", "path", "file", "folder", "directory",
    ])

    # Sensitive locations to block even within workspace
    SENSITIVE_PATTERNS = [
        ".git",
        ".env",
        "secrets",
        "__pycache__",
        ".ssh",
        ".gnupg",
        ".aws",
        ".config/gcloud",
    ]

    def __init__(
        self,
        workspace: Path,
        *,
        additional_allowed: set[Path] | None = None,
        allow_tmp: bool = True,
    ):
        """Initialize the path validator.

        Args:
            workspace: Primary workspace directory
            additional_allowed: Additional allowed root directories
            allow_tmp: Whether to allow /tmp access
        """
        self.workspace = workspace.resolve()
        self.allowed_roots: set[Path] = {self.workspace}

        if additional_allowed:
            self.allowed_roots.update(p.resolve() for p in additional_allowed)

        if allow_tmp:
            self.allowed_roots.add(Path("/tmp").resolve())

    def looks_like_path(self, name: str, value: str) -> bool:
        """Heuristic: does this value look like a file path?

        Args:
            name: Variable/field name
            value: The value to check

        Returns:
            True if value appears to be a path
        """
        if not isinstance(value, str):
            return False

        # Check if name suggests a path
        name_lower = name.lower()
        for path_name in self.PATH_ENV_NAMES:
            if path_name in name_lower:
                return True

        # Check if value looks like a path
        for pattern in self.PATH_PATTERNS:
            if re.match(pattern, value):
                return True

        return False

    def validate(
        self,
        path_str: str,
        *,
        operation: str = "access",
        check_exists: bool = False,
    ) -> Path:
        """Validate a path is within allowed roots.

        Args:
            path_str: The path to validate
            operation: "read", "write", or "access" for error messages
            check_exists: Whether to verify the path exists

        Returns:
            Resolved Path object if valid

        Raises:
            PathTraversalError: If path escapes allowed roots
            SecurityError: If path is in sensitive location
        """
        try:
            # Expand user home directory
            if path_str.startswith("~"):
                path = Path(path_str).expanduser()
            else:
                path = Path(path_str)

            # Make absolute relative to workspace if not already
            if not path.is_absolute():
                path = (self.workspace / path).resolve()
            else:
                path = path.resolve()

        except (ValueError, OSError) as e:
            raise SecurityError(
                f"Invalid path '{path_str}': {e}",
                context={"path": path_str, "operation": operation},
            ) from e

        # Check if path is within any allowed root
        is_allowed = False
        for allowed_root in self.allowed_roots:
            try:
                path.relative_to(allowed_root)
                is_allowed = True
                break
            except ValueError:
                continue

        if not is_allowed:
            raise PathTraversalError(
                path_str,
                allowed_base=str(self.workspace),
            )

        # Check for existence if required
        if check_exists and not path.exists():
            raise SecurityError(
                f"Path does not exist: '{path}'",
                context={"path": path_str, "operation": operation},
            )

        return path

    def validate_for_write(self, path_str: str) -> Path:
        """Validate a path for write operations.

        Additional checks for sensitive locations.

        Args:
            path_str: The path to validate

        Returns:
            Resolved Path object if valid

        Raises:
            SecurityError: If path is in sensitive location
        """
        path = self.validate(path_str, operation="write")

        # Block writes to sensitive locations
        path_parts = set(path.parts)
        for sensitive in self.SENSITIVE_PATTERNS:
            if sensitive in path_parts:
                raise SecurityError(
                    f"Cannot write to sensitive location: '{path}'",
                    context={"path": path_str, "sensitive_pattern": sensitive},
                )

        return path


def validate_path(
    path_str: str,
    workspace: Path,
    *,
    for_write: bool = False,
) -> Path:
    """Convenience function to validate a single path.

    Args:
        path_str: Path to validate
        workspace: Workspace directory
        for_write: Whether this is a write operation

    Returns:
        Validated Path object
    """
    validator = PathValidator(workspace)
    if for_write:
        return validator.validate_for_write(path_str)
    return validator.validate(path_str)


def safe_path_filter(
    value: str,
    workspace: str | None = None,
) -> str:
    """Jinja2 filter for path validation.

    Usage in workflow YAML:
        run: cat ${{ steps.extract.outputs.filename | safe_path }}

    Args:
        value: The path value to validate
        workspace: Optional workspace override

    Returns:
        The validated path string

    Raises:
        PathTraversalError: If path escapes workspace
    """
    if workspace:
        ws = Path(workspace)
    else:
        ws = Path.cwd()

    validated = validate_path(str(value), ws)
    return str(validated)


def sanitize_output(result: dict[str, Any]) -> dict[str, Any]:
    """Remove reserved keys from action output.

    Prevents control flow hijacking from user-generated output.
    Actions cannot set __next, __loop_break_requested, etc.

    Args:
        result: Raw action output

    Returns:
        Sanitized output with reserved keys removed
    """
    return {
        k: v for k, v in result.items()
        if k not in RESERVED_STATE_KEYS
    }


def validate_step_id(step_id: str) -> None:
    """Validate a step ID is safe to use.

    Args:
        step_id: The step ID to validate

    Raises:
        SecurityError: If step ID is reserved or invalid
    """
    # Reserved step IDs that could collide with internal nodes
    RESERVED_IDS = frozenset([
        "__cleanup__", "__end__", "__start__", "__error__",
        "__init", "__check", "__body", "__next",
        "__condition", "__handler",
        "loop", "inputs", "env", "steps", "workflow",
    ])

    # Reserved prefixes
    RESERVED_PREFIXES = ("__", "_internal_")

    # Max length
    MAX_LENGTH = 64

    if step_id.lower() in {r.lower() for r in RESERVED_IDS}:
        raise SecurityError(
            f"Step ID '{step_id}' is reserved",
            suggestion="Choose a different step ID",
        )

    for prefix in RESERVED_PREFIXES:
        if step_id.startswith(prefix):
            raise SecurityError(
                f"Step ID '{step_id}' cannot start with '{prefix}'",
                suggestion="Remove the prefix from the step ID",
            )

    if len(step_id) > MAX_LENGTH:
        raise SecurityError(
            f"Step ID '{step_id[:20]}...' exceeds max length ({MAX_LENGTH})",
            suggestion=f"Use a shorter step ID (max {MAX_LENGTH} characters)",
        )

    # Check for invalid characters
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", step_id):
        raise SecurityError(
            f"Step ID '{step_id}' contains invalid characters",
            suggestion="Use only letters, numbers, underscores, and hyphens. Must start with a letter.",
        )
