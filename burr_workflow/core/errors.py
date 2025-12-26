"""
Workflow error hierarchy.

All workflow-related exceptions inherit from WorkflowError,
providing a consistent interface for error handling.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class SourceLocation:
    """Precise source location for error reporting.

    Used with ruamel.yaml to track line/column info for
    user-friendly error messages.
    """
    file: Optional[Path]
    line: int  # 1-indexed for user display
    column: int

    def __str__(self) -> str:
        if self.file:
            return f"{self.file}:{self.line}:{self.column}"
        return f"line {self.line}, column {self.column}"


class WorkflowError(Exception):
    """Base exception for all workflow-related errors.

    Attributes:
        message: Human-readable error description
        suggestion: Optional hint for fixing the error
        location: Optional source location for YAML errors
        context: Optional dict with additional error context
    """

    def __init__(
        self,
        message: str,
        *,
        suggestion: Optional[str] = None,
        location: Optional[SourceLocation] = None,
        context: Optional[dict[str, Any]] = None,
    ):
        self.message = message
        self.suggestion = suggestion
        self.location = location
        self.context = context or {}
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format the full error message with location and suggestion."""
        parts = []

        if self.location:
            parts.append(f"{self.location}: ")

        parts.append(self.message)

        if self.suggestion:
            parts.append(f"\n  Hint: {self.suggestion}")

        return "".join(parts)


class WorkflowValidationError(WorkflowError):
    """Error during workflow YAML validation.

    Raised when the workflow YAML is syntactically valid but
    semantically incorrect (e.g., missing required fields,
    invalid references, type mismatches).
    """

    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        expected: Optional[str] = None,
        actual: Optional[str] = None,
        **kwargs: Any,
    ):
        self.field = field
        self.expected = expected
        self.actual = actual

        # Enhance message with field info
        if field and expected and actual:
            message = f"Field '{field}': expected {expected}, got {actual}"
        elif field:
            message = f"Field '{field}': {message}"

        super().__init__(message, **kwargs)


class WorkflowCompilationError(WorkflowError):
    """Error during workflow compilation to Burr graph.

    Raised when the workflow cannot be compiled into a valid
    Burr Application graph (e.g., cycle detection failures,
    invalid transitions, action resolution failures).
    """

    def __init__(
        self,
        message: str,
        *,
        step_id: Optional[str] = None,
        step_name: Optional[str] = None,
        **kwargs: Any,
    ):
        self.step_id = step_id
        self.step_name = step_name

        # Enhance message with step info
        if step_id:
            message = f"Step '{step_id}': {message}"

        super().__init__(message, **kwargs)


class WorkflowExecutionError(WorkflowError):
    """Error during workflow execution.

    Raised when a step fails during execution. Contains
    information about which step failed and the original
    exception.
    """

    def __init__(
        self,
        message: str,
        *,
        step_id: Optional[str] = None,
        step_name: Optional[str] = None,
        original_error: Optional[Exception] = None,
        **kwargs: Any,
    ):
        self.step_id = step_id
        self.step_name = step_name
        self.original_error = original_error

        # Enhance message with step info
        if step_id:
            message = f"Step '{step_id}' failed: {message}"

        super().__init__(message, **kwargs)


class WorkflowTimeoutError(WorkflowExecutionError):
    """Raised when a step or workflow exceeds its timeout."""

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: float,
        **kwargs: Any,
    ):
        self.timeout_seconds = timeout_seconds
        super().__init__(message, **kwargs)


class WorkflowInterruptedError(WorkflowError):
    """Raised when workflow is interrupted (e.g., SIGINT, user cancel).

    This is not necessarily an error condition - the workflow
    may be resumable from its last checkpoint.
    """

    def __init__(
        self,
        message: str = "Workflow interrupted",
        *,
        can_resume: bool = True,
        **kwargs: Any,
    ):
        self.can_resume = can_resume
        super().__init__(message, **kwargs)


class StepError(WorkflowExecutionError):
    """Error specific to a single step execution.

    Provides detailed information about what went wrong
    in a specific step for debugging.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        **kwargs: Any,
    ):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(message, **kwargs)


class ActionNotFoundError(WorkflowCompilationError):
    """Raised when an action type cannot be resolved.

    Occurs when a step references an action (via 'uses:')
    that is not registered in the ActionRegistry.
    """

    def __init__(
        self,
        action_type: str,
        *,
        available_actions: Optional[list[str]] = None,
        **kwargs: Any,
    ):
        self.action_type = action_type
        self.available_actions = available_actions

        message = f"Unknown action type: '{action_type}'"
        if available_actions:
            # Find similar action names for suggestions
            similar = [a for a in available_actions if action_type.split("/")[0] in a]
            if similar:
                kwargs.setdefault("suggestion", f"Did you mean: {', '.join(similar[:3])}?")

        super().__init__(message, **kwargs)


class ExpressionError(WorkflowError):
    """Error evaluating a Jinja2 expression.

    Raised when ${{ ... }} expression evaluation fails,
    with context about the expression and available variables.
    """

    def __init__(
        self,
        message: str,
        *,
        expression: str,
        available_vars: Optional[list[str]] = None,
        **kwargs: Any,
    ):
        self.expression = expression
        self.available_vars = available_vars

        message = f"Expression error in '${{{{ {expression} }}}}': {message}"
        super().__init__(message, **kwargs)


class SecurityError(WorkflowError):
    """Security-related error (e.g., path traversal, injection).

    Raised when a security check fails. These errors should
    not be caught and suppressed in production.
    """
    pass


class PathTraversalError(SecurityError):
    """Attempted path traversal attack detected."""

    def __init__(
        self,
        path: str,
        *,
        allowed_base: Optional[str] = None,
        **kwargs: Any,
    ):
        self.path = path
        self.allowed_base = allowed_base

        message = f"Path traversal detected: '{path}'"
        if allowed_base:
            message += f" (must be under '{allowed_base}')"

        super().__init__(message, **kwargs)
