"""
Parse actions for extracting structured data.

This module provides actions for parsing JSON and extracting data via regex.
"""

import json
import re
from typing import Any, Optional, TYPE_CHECKING

from .base import BaseAction
from ..core.types import ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class ParseJSONAction(BaseAction):
    """
    Parse JSON and extract data using queries.

    Uses jmespath-style queries if jmespath is installed,
    otherwise falls back to simple dot-notation paths.

    YAML usage:
        - id: extract
          uses: parse/json
          with:
            input: ${{ steps.api.outputs.body }}
            queries:
              hosts: ".results[].hostname"
              count: ".results | length"
            defaults:
              hosts: []

    Parameters:
        - input: JSON string to parse (required)
        - queries: Dict of name -> query mappings
        - defaults: Default values if query returns null

    Outputs:
        - Each query name becomes an output key
        - _parsed: The full parsed JSON object
    """

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Parse JSON and run queries."""
        with_config = step_config.get("with", {})

        input_text = with_config.get("input", "")
        queries = with_config.get("queries", {})
        defaults = with_config.get("defaults", {})

        if not input_text:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'input' provided for parse/json",
                error_type="ValidationError",
            )

        # Parse JSON
        try:
            if isinstance(input_text, str):
                data = json.loads(input_text)
            else:
                # Already parsed (e.g., from HTTP action)
                data = input_text
        except json.JSONDecodeError as e:
            return ActionResult(
                outcome="failure",
                outputs={"raw": input_text[:200] if isinstance(input_text, str) else str(input_text)[:200]},
                error=f"Invalid JSON: {e}",
                error_type="JSONDecodeError",
            )

        outputs: dict[str, Any] = {"_parsed": data}

        # Try to use jmespath if available
        try:
            import jmespath
            use_jmespath = True
        except ImportError:
            use_jmespath = False
            if exec_context:
                exec_context.log("debug", "jmespath not installed, using simple path extraction")

        # Run queries
        for name, query in queries.items():
            try:
                if use_jmespath:
                    result = jmespath.search(query, data)
                else:
                    result = self._simple_path_extract(query, data)

                # Apply default if result is None
                if result is None and name in defaults:
                    result = defaults[name]

                outputs[name] = result

            except Exception as e:
                # Query failed, use default or None
                if name in defaults:
                    outputs[name] = defaults[name]
                else:
                    outputs[name] = None
                if exec_context:
                    exec_context.log("warning", f"Query '{name}' failed: {e}")

        return ActionResult(outcome="success", outputs=outputs)

    def _simple_path_extract(self, query: str, data: Any) -> Any:
        """Simple dot-notation path extraction fallback.

        Supports:
            - .field - access field
            - .field.nested - nested access
            - .[0] - array index

        Does NOT support jmespath features like filters, pipes, functions.
        """
        # Remove leading dot
        query = query.lstrip(".")

        if not query:
            return data

        current = data

        # Split on dots, but handle array indices
        parts = re.split(r'\.(?![^\[]*\])', query)

        for part in parts:
            if not part:
                continue

            # Handle array index: field[0] or just [0]
            match = re.match(r'(\w+)?\[(\d+)\]$', part)
            if match:
                field, index = match.groups()
                if field and isinstance(current, dict):
                    current = current.get(field)
                if isinstance(current, (list, tuple)):
                    idx = int(index)
                    current = current[idx] if idx < len(current) else None
                else:
                    current = None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                current = None

            if current is None:
                break

        return current


class ParseRegexAction(BaseAction):
    """
    Extract data using regex patterns.

    YAML usage:
        # Find all IPs
        - id: all_ips
          uses: parse/regex
          with:
            input: ${{ steps.scan.outputs.stdout }}
            pattern: '\\d+\\.\\d+\\.\\d+\\.\\d+'
            mode: all

        # Extract credentials with named groups
        - id: creds
          uses: parse/regex
          with:
            input: ${{ steps.dump.outputs.stdout }}
            pattern: '(?P<user>\\w+):(?P<hash>[a-f0-9]{32})'
            mode: all

    Parameters:
        - input: Text to search (required)
        - pattern: Regex pattern (required)
        - mode: first (single match) or all (all matches, default)

    Outputs (depends on pattern and mode):
        - No groups, mode=first: match (string or null)
        - No groups, mode=all: matches (list of strings)
        - Named groups, mode=first: match (dict or null)
        - Named groups, mode=all: matches (list of dicts)
    """

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Extract data using regex."""
        with_config = step_config.get("with", {})

        input_text = with_config.get("input", "")
        pattern = with_config.get("pattern", "")
        mode = with_config.get("mode", "all")

        if not input_text:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'input' provided for parse/regex",
                error_type="ValidationError",
            )

        if not pattern:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'pattern' provided for parse/regex",
                error_type="ValidationError",
            )

        # Validate mode
        valid_modes = {"first", "all"}
        if mode not in valid_modes:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid mode '{mode}'. Must be one of: {valid_modes}",
                error_type="ValidationError",
            )

        # Compile regex
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid regex pattern: {e}",
                error_type="RegexError",
            )

        # Ensure input is string
        input_text = str(input_text)

        # Check for named groups
        has_named_groups = bool(regex.groupindex)

        if mode == "first":
            match = regex.search(input_text)
            if match:
                if has_named_groups:
                    result = match.groupdict()
                else:
                    result = match.group(0)
            else:
                result = None

            return ActionResult(
                outcome="success",
                outputs={"match": result}
            )

        else:  # mode == "all"
            matches = list(regex.finditer(input_text))

            if has_named_groups:
                results = [m.groupdict() for m in matches]
            else:
                results = [m.group(0) for m in matches]

            return ActionResult(
                outcome="success",
                outputs={"matches": results, "count": len(results)}
            )
