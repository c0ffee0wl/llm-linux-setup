"""
Secure expression evaluator using Jinja2.

Uses NativeEnvironment to preserve Python types (lists, dicts, ints)
with sandboxed security to prevent template injection attacks.
"""

import json
import re
import shlex
from datetime import datetime
from typing import Any, Optional

from jinja2 import ChainableUndefined, Undefined
from jinja2.nativetypes import NativeEnvironment
from jinja2.sandbox import ImmutableSandboxedEnvironment

from ..core.errors import ExpressionError, SecurityError
from .filters import SAFE_FILTERS


class SecureNativeEnvironment(NativeEnvironment):
    """Native environment with explicit sandbox security.

    Uses composition instead of multiple inheritance to avoid MRO issues.
    This approach ensures all security-sensitive methods delegate to
    the sandbox regardless of Jinja2 version changes.

    Security Features:
        - Blocks access to dangerous attributes (__class__, __mro__, etc.)
        - Prevents arbitrary function calls
        - Blocks dangerous operations (getattr on sensitive objects)
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Composition: use sandbox for security checks
        self._sandbox = ImmutableSandboxedEnvironment()

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        """Delegate to sandbox's security check."""
        return self._sandbox.is_safe_attribute(obj, attr, value)

    def is_safe_callable(self, obj: Any) -> bool:
        """Delegate to sandbox's security check."""
        return self._sandbox.is_safe_callable(obj)

    def call_binop(
        self, context: Any, operator: str, left: Any, right: Any
    ) -> Any:
        """Delegate to sandbox's binary operation check."""
        return self._sandbox.call_binop(context, operator, left, right)

    def call_unop(self, context: Any, operator: str, arg: Any) -> Any:
        """Delegate to sandbox's unary operation check."""
        return self._sandbox.call_unop(context, operator, arg)

    def getitem(self, obj: Any, argument: Any) -> Any:
        """Use sandbox's getitem with security checks."""
        return self._sandbox.getitem(obj, argument)

    def getattr(self, obj: Any, attribute: str) -> Any:
        """Use sandbox's getattr with security checks."""
        return self._sandbox.getattr(obj, attribute)

    def call(
        self, __context: Any, __obj: Any, *args: Any, **kwargs: Any
    ) -> Any:
        """Use sandbox's call with security checks."""
        return self._sandbox.call(__context, __obj, *args, **kwargs)


class ContextEvaluator:
    """Secure expression evaluator that treats inputs as data, not templates.

    Security Model:
        - SandboxedEnvironment blocks dangerous attribute access
        - Dangerous pattern check as defense-in-depth
        - Whitelist-only filters (no arbitrary function calls)
        - No globals except safe utility functions
        - Inputs are data, never re-evaluated as templates

    Type Preservation:
        Uses NativeEnvironment to preserve Python types:
        - Lists remain lists, not "['a', 'b']"
        - Integers remain integers, not "42"
        - Booleans remain booleans, not "True"
    """

    # Expression pattern: ${{ expression }}
    EXPR_PATTERN = re.compile(r"\$\{\{\s*(.+?)\s*\}\}", re.DOTALL)

    # Whitelisted filters (no arbitrary function calls)
    # Includes all filters from SAFE_FILTERS plus built-in evaluator filters
    ALLOWED_FILTERS = frozenset([
        # Built-in collection/string/type filters
        "length", "keys", "values", "first", "last", "join",
        "default", "shell_quote", "safe_path", "lower", "upper",
        "trim", "split", "sort", "unique", "int", "float", "string",
        # GitHub Actions compatible functions
        "contains", "startsWith", "endsWith", "format", "toJSON", "fromJSON",
        # Filters from SAFE_FILTERS (filters.py)
        "safe_filename", "regex_replace", "regex_match", "lines",
        "base64_encode", "base64_decode", "url_encode", "url_decode",
        "extract_domain", "extract_ip",
        # Network validation filters
        "is_valid_ip", "is_private_ip", "in_cidr",
        # Filesystem filters
        "file_exists",
        # List operations
        "in_list",
    ])

    # Dangerous patterns to block (defense-in-depth)
    DANGEROUS_PATTERNS = [
        "__",  # Dunder attributes
        "import",  # Module import
        "eval",  # Code evaluation
        "exec",  # Code execution
        "open(",  # File operations
        "os.",  # OS module
        "sys.",  # Sys module
        "subprocess",  # Process execution
        "builtins",  # Python builtins
        "globals",  # Global access
        "locals",  # Local access
    ]

    def __init__(
        self,
        ctx: Optional[dict[str, Any]] = None,
        *,
        workspace: Optional[str] = None,
    ):
        """Initialize the evaluator.

        Args:
            ctx: Initial context dictionary
            workspace: Workspace path for path validation
        """
        self.ctx = ctx or {}
        self.workspace = workspace

        # Use hybrid SecureNativeEnvironment
        self.env = SecureNativeEnvironment()

        # Configure undefined behavior for graceful None handling
        self.env.undefined = ChainableUndefined

        # Clear all built-in filters, add only safe ones
        self.env.filters = {}
        self._setup_filters()

        # Configure safe globals
        self.env.globals = {
            "now": lambda: datetime.now(),  # ${{ now() }} for timestamps
            "true": True,
            "false": False,
            "none": None,
        }

    def _setup_filters(self) -> None:
        """Set up whitelisted filters."""
        # Collection filters
        self.env.filters["length"] = len
        self.env.filters["keys"] = lambda d: list(d.keys()) if isinstance(d, dict) else []
        self.env.filters["values"] = lambda d: list(d.values()) if isinstance(d, dict) else []
        self.env.filters["first"] = lambda x: x[0] if x else None
        self.env.filters["last"] = lambda x: x[-1] if x else None
        self.env.filters["join"] = lambda x, sep=",": sep.join(str(i) for i in x) if x else ""
        self.env.filters["sort"] = lambda x: sorted(x) if x else []
        self.env.filters["unique"] = lambda x: list(dict.fromkeys(x)) if x else []

        # Default value filter (handles None and Undefined)
        def default_filter(value: Any, default_value: Any = "") -> Any:
            if value is None or isinstance(value, Undefined):
                return default_value
            return value
        self.env.filters["default"] = default_filter

        # String filters
        self.env.filters["lower"] = lambda x: str(x).lower() if x else ""
        self.env.filters["upper"] = lambda x: str(x).upper() if x else ""
        self.env.filters["trim"] = lambda x: str(x).strip() if x else ""
        self.env.filters["split"] = lambda x, sep=None: str(x).split(sep) if x else []

        # Type conversion filters
        self.env.filters["int"] = lambda x: int(x) if x else 0
        self.env.filters["float"] = lambda x: float(x) if x else 0.0
        self.env.filters["string"] = str

        # CRITICAL: Shell injection prevention
        self.env.filters["shell_quote"] = shlex.quote

        # Path safety filter (implemented in security.py)
        from .security import safe_path_filter
        self.env.filters["safe_path"] = safe_path_filter

        # GitHub Actions compatible functions
        self.env.filters["contains"] = lambda haystack, needle: (
            needle in haystack if isinstance(haystack, (str, list, tuple)) else False
        )
        self.env.filters["startsWith"] = lambda s, prefix: (
            str(s).startswith(str(prefix)) if s is not None else False
        )
        self.env.filters["endsWith"] = lambda s, suffix: (
            str(s).endswith(str(suffix)) if s is not None else False
        )
        self.env.filters["format"] = self._gha_format
        self.env.filters["toJSON"] = lambda v: json.dumps(v)
        self.env.filters["fromJSON"] = lambda s: json.loads(s) if isinstance(s, str) else s

        # Register all filters from SAFE_FILTERS (filters.py)
        for name, fn in SAFE_FILTERS.items():
            self.env.filters[name] = fn

    def update_context(self, updates: dict[str, Any]) -> None:
        """Update the evaluation context.

        Args:
            updates: Dictionary of values to add/update
        """
        self.ctx.update(updates)

    def set_context(self, ctx: dict[str, Any]) -> None:
        """Replace the entire evaluation context.

        Args:
            ctx: New context dictionary
        """
        self.ctx = ctx

    def _gha_format(self, fmt: str, *args: Any) -> str:
        """GitHub Actions format() function.

        Replaces {0}, {1}, etc. with positional arguments.

        Args:
            fmt: Format string with {0}, {1}, etc. placeholders
            *args: Values to substitute

        Returns:
            Formatted string
        """
        result = str(fmt)
        for i, arg in enumerate(args):
            result = result.replace("{" + str(i) + "}", str(arg))
        return result

    def _check_dangerous_patterns(self, expr: str) -> None:
        """Check for dangerous patterns in expression.

        Args:
            expr: The expression to check

        Raises:
            SecurityError: If dangerous pattern detected
        """
        expr_lower = expr.lower()
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in expr_lower:
                raise SecurityError(
                    f"Blocked dangerous pattern '{pattern}' in expression",
                    context={"expression": expr},
                )

    def resolve(
        self,
        expr: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Resolve a ${{ expr }} expression safely.

        Args:
            expr: Expression to evaluate (with or without ${{ }})
            context: Optional context override (merged with self.ctx)

        Returns:
            Evaluated value, preserving Python types

        Raises:
            SecurityError: If expression contains dangerous patterns
            ExpressionError: If expression evaluation fails
        """
        # Handle non-expression strings
        if not isinstance(expr, str):
            return expr

        # Check if this is a template expression
        if not expr.startswith("${{"):
            # Check for embedded expressions
            if "${{" not in expr:
                return expr
            # Handle string with embedded expressions
            return self._resolve_embedded(expr, context)

        # Extract inner expression
        inner = expr[3:-2].strip()

        # Security check
        self._check_dangerous_patterns(inner)

        # Build evaluation context
        eval_ctx = {**self.ctx}
        if context:
            eval_ctx.update(context)

        try:
            # Render the template
            template = self.env.from_string(f"{{{{ {inner} }}}}")
            result = template.render(**eval_ctx)

            # NativeEnvironment returns actual Python objects
            return result

        except Exception as e:
            raise ExpressionError(
                str(e),
                expression=inner,
                available_vars=list(eval_ctx.keys()),
            )

    def _resolve_embedded(
        self,
        text: str,
        context: Optional[dict[str, Any]] = None,
    ) -> str:
        """Resolve embedded expressions in a string.

        Handles strings like: "Hello ${{ name }}, you have ${{ count }} items"

        Args:
            text: String with embedded expressions
            context: Optional context override

        Returns:
            String with expressions resolved
        """
        def replace_expr(match: re.Match) -> str:
            inner = match.group(1).strip()
            self._check_dangerous_patterns(inner)

            eval_ctx = {**self.ctx}
            if context:
                eval_ctx.update(context)

            try:
                template = self.env.from_string(f"{{{{ {inner} }}}}")
                result = template.render(**eval_ctx)
                return str(result) if result is not None else ""
            except Exception:
                # Return empty string for failed embedded expressions
                return ""

        return self.EXPR_PATTERN.sub(replace_expr, text)

    def resolve_all(
        self,
        data: Any,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Recursively resolve expressions in a data structure.

        Args:
            data: Data structure (dict, list, str, etc.)
            context: Optional context override

        Returns:
            Data with all expressions resolved
        """
        if isinstance(data, str):
            return self.resolve(data, context=context)
        elif isinstance(data, dict):
            return {
                k: self.resolve_all(v, context=context)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [self.resolve_all(item, context=context) for item in data]
        else:
            return data

    def evaluate_condition(
        self,
        expr: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Evaluate an expression as a boolean condition.

        Args:
            expr: Expression to evaluate
            context: Optional context override

        Returns:
            Boolean result
        """
        result = self.resolve(expr, context=context)

        # Handle various falsy values
        if result is None or isinstance(result, Undefined):
            return False
        if isinstance(result, str):
            return result.lower() not in ("", "false", "0", "no", "none")
        if isinstance(result, (list, dict)):
            return len(result) > 0

        return bool(result)
