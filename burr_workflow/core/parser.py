"""
YAML parser with source location tracking for precise error messages.

Uses ruamel.yaml in round-trip mode to preserve:
- Line/column numbers for every node (via .lc property)
- Comments and formatting for round-trip editing
- Original structure for validation error reporting

This parser is MANDATORY for the workflow engine - do NOT use PyYAML.
"""

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


@dataclass(frozen=True)
class SourceLocation:
    """Source location for error reporting.

    Provides precise file:line:column references for validation errors.
    """

    file: Path | None = None
    line: int = 1
    column: int = 1

    def __str__(self) -> str:
        if self.file:
            return f"{self.file}:{self.line}:{self.column}"
        return f"line {self.line}, column {self.column}"


class WorkflowParser:
    """
    YAML parser with source location tracking for precise error messages.

    Uses ruamel.yaml in round-trip mode to preserve:
    - Line/column numbers for every node (via .lc property)
    - Comments and formatting for round-trip editing
    - Original structure for validation error reporting

    Example:
        parser = WorkflowParser()
        workflow, path = parser.parse_file(Path("workflow.yaml"))

        # Get location for error reporting
        for step in workflow["jobs"]["main"]["steps"]:
            if "timeout" in step:
                timeout = step["timeout"]
                if not isinstance(timeout, (int, float)):
                    loc = parser.get_location(step, "timeout")
                    raise WorkflowValidationError(
                        f"'timeout' must be a number, got {type(timeout).__name__}",
                        location=loc
                    )
    """

    def __init__(self):
        """Initialize the parser with round-trip YAML settings."""
        self.yaml = YAML()
        self.yaml.preserve_quotes = True  # Preserve string quoting style
        self._current_file: Path | None = None

    def parse_file(self, path: str | Path) -> tuple[dict, Path]:
        """Parse workflow file with source tracking.

        Args:
            path: Path to the workflow YAML file

        Returns:
            Tuple of (parsed workflow dict, resolved file path)

        Raises:
            FileNotFoundError: If file doesn't exist
            ruamel.yaml.YAMLError: If YAML is malformed
        """
        path = Path(path).resolve()
        self._current_file = path

        with open(path) as f:
            data = self.yaml.load(f)

        # Attach file path for error reporting
        self._attach_source_file(data, path)
        return data, path

    def parse_string(self, content: str, source_name: str = "<string>") -> dict:
        """Parse workflow string with source tracking.

        Args:
            content: YAML content as string
            source_name: Name for error messages (e.g., "<stdin>")

        Returns:
            Parsed workflow dict
        """
        self._current_file = None
        data = self.yaml.load(StringIO(content))
        return data

    def get_location(
        self, node: Any, key: str | None = None
    ) -> SourceLocation | None:
        """
        Get source location for a node or key within a node.

        Args:
            node: A ruamel.yaml node (dict, list, or scalar)
            key: Optional key name to get location of within a mapping

        Returns:
            SourceLocation if available, None otherwise

        Usage:
            loc = parser.get_location(step)  # Location of the step mapping
            loc = parser.get_location(step, 'timeout')  # Location of 'timeout' key
        """
        if not hasattr(node, "lc"):
            return None

        lc = node.lc
        file_path = getattr(lc, "file_name", None)

        if key is not None and hasattr(lc, "key"):
            # Get location of specific key
            try:
                line, col = lc.key(key)
                return SourceLocation(file=file_path, line=line + 1, column=col)
            except KeyError:
                pass

        # Get location of node itself
        return SourceLocation(
            file=file_path,
            line=lc.line + 1,  # Convert to 1-indexed
            column=lc.col,
        )

    def get_value_location(
        self, node: Any, key: str
    ) -> SourceLocation | None:
        """Get source location for a value (not key) within a mapping.

        Args:
            node: A ruamel.yaml mapping node
            key: Key name whose value location to get

        Returns:
            SourceLocation if available, None otherwise
        """
        if not hasattr(node, "lc"):
            return None

        lc = node.lc
        file_path = getattr(lc, "file_name", None)

        if hasattr(lc, "value"):
            try:
                line, col = lc.value(key)
                return SourceLocation(file=file_path, line=line + 1, column=col)
            except KeyError:
                pass

        return None

    def _attach_source_file(self, node: Any, file_path: Path) -> None:
        """Recursively attach file path to all nodes for error reporting.

        Args:
            node: Node to attach file path to
            file_path: Path to attach
        """
        if hasattr(node, "lc"):
            node.lc.file_name = file_path

        if isinstance(node, dict):
            for value in node.values():
                self._attach_source_file(value, file_path)
        elif isinstance(node, list):
            for item in node:
                self._attach_source_file(item, file_path)

    def format_error(
        self,
        message: str,
        node: Any = None,
        key: str | None = None,
    ) -> str:
        """Format an error message with source location.

        Args:
            message: The error message
            node: Optional node to get location from
            key: Optional key within node

        Returns:
            Formatted error string with location prefix
        """
        location = None
        if node is not None:
            location = self.get_location(node, key)

        if location:
            return f"{location}: {message}"
        return message


# Convenience function for simple parsing
def parse_workflow(source: str | Path) -> tuple[dict, Path | None]:
    """Parse a workflow from file or string.

    Args:
        source: Either a file path or YAML string

    Returns:
        Tuple of (workflow dict, file path if from file)
    """
    parser = WorkflowParser()

    if isinstance(source, Path) or (isinstance(source, str) and Path(source).exists()):
        return parser.parse_file(source)
    else:
        return parser.parse_string(source), None
