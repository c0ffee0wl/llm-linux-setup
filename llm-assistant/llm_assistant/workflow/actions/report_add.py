"""
Report add action for pentest finding management.

This action bridges the workflow engine to the session's ReportMixin,
enabling automated finding creation within workflow execution.

Usage in YAML:
    - id: add_sqli_finding
      name: Record SQL injection finding
      uses: report/add
      with:
        note: "SQL injection in login form - ${{ steps.exploit.outputs.payload }}"
      register: finding

    # Access result via ${{ steps.add_sqli_finding.outputs.finding_id }}
"""

from typing import Any, ClassVar, Optional, TYPE_CHECKING

# Lazy import pattern - burr_workflow imports are deferred
# to allow llm-assistant to work without burr_workflow installed
try:
    from burr_workflow.actions.base import AbstractAction, ActionResult
except ImportError:
    # Provide stub base class when burr_workflow not installed
    # This allows the module to be imported without error
    class AbstractAction:  # type: ignore[no-redef]
        """Stub base class when burr_workflow is not installed."""
        action_type: ClassVar[str] = ""

        @property
        def reads(self) -> list[str]:
            return []

        @property
        def writes(self) -> list[str]:
            return []

    class ActionResult:  # type: ignore[no-redef]
        """Stub ActionResult when burr_workflow is not installed."""
        def __init__(self, outputs=None, outcome=None, error=None, error_type=None):
            self.outputs = outputs or {}
            self.outcome = outcome
            self.error = error
            self.error_type = error_type

if TYPE_CHECKING:
    from burr_workflow.protocols import ExecutionContext


class ReportAddAction(AbstractAction):
    """
    Action for adding pentest findings.

    Bridges to the session's ReportMixin._report_add() method for
    creating findings with LLM-assisted analysis.

    Step Configuration:
        note: str - Quick note describing the finding (required)
        severity: int - Optional severity override (1-9, OWASP scale)

    Outputs:
        finding_id: str - Generated finding ID (e.g., "F001")
        title: str - LLM-generated title
        severity: int - Assigned severity (1-9)
        success: bool - Whether finding was added successfully

    Requirements:
        - ReportMixin must be available on the session
        - A findings project must be initialized (/report init)

    Example:
        - uses: report/add
          with:
            note: "XSS in search parameter - <script>alert(1)</script> works"
    """

    action_type: ClassVar[str] = "report/add"

    @classmethod
    def validate_requirements(cls, exec_context: "ExecutionContext") -> tuple[bool, str]:
        """Validate that requirements are met for this action.

        This can be called during workflow compilation to provide early
        warning about missing dependencies.

        Args:
            exec_context: The execution context to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if exec_context is None:
            return False, "No execution context available"

        session = cls._get_session_from_context(exec_context)
        if session is None:
            return False, "Cannot access session from execution context"

        if not hasattr(session, "_report_add"):
            return False, "Session does not have ReportMixin (report functionality not available)"

        return True, ""

    @staticmethod
    def _get_session_from_context(exec_context: "ExecutionContext") -> Optional[Any]:
        """Extract session reference from execution context.

        Tries multiple access patterns for robustness:
        1. Direct _session attribute
        2. session property/attribute
        3. Bound method introspection via _prompt_fn

        Args:
            exec_context: Execution context

        Returns:
            Session object or None if not found
        """
        # Try direct attribute first (preferred)
        session = getattr(exec_context, "_session", None)
        if session is not None:
            return session

        # Try session property
        session = getattr(exec_context, "session", None)
        if session is not None:
            return session

        # Fall back to bound method introspection
        prompt_fn = getattr(exec_context, "_prompt_fn", None)
        if prompt_fn and hasattr(prompt_fn, "__self__"):
            return prompt_fn.__self__

        return None

    @property
    def reads(self) -> list[str]:
        return ["inputs", "env", "steps"]

    @property
    def writes(self) -> list[str]:
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Execute report add action.

        Args:
            step_config: Step configuration with note
            context: Workflow context
            exec_context: Execution context with session reference

        Returns:
            ActionResult with finding details
        """
        # Extract configuration
        with_config = step_config.get("with", {})
        note = with_config.get("note", "")

        if not note:
            return ActionResult(
                outputs={"success": False},
                outcome="failure",
                error="Missing required 'note' in step configuration",
                error_type="configuration_error",
            )

        # Resolve any template expressions in note
        from burr_workflow.evaluator import ContextEvaluator
        evaluator = ContextEvaluator(context)
        note = evaluator.resolve(note) if "${{" in note else note

        # Validate requirements using the class method
        is_valid, error_msg = self.validate_requirements(exec_context)
        if not is_valid:
            error_type = "missing_context" if "context" in error_msg else \
                         "missing_session" if "session" in error_msg else \
                         "unsupported_operation"
            return ActionResult(
                outputs={"success": False},
                outcome="failure",
                error=error_msg,
                error_type=error_type,
            )

        # Get session reference using the robust helper
        session = self._get_session_from_context(exec_context)

        # Check for active project
        if not getattr(session, "findings_project", None):
            return ActionResult(
                outputs={"success": False},
                outcome="failure",
                error="No findings project initialized. Use /report init <project> <lang> first",
                error_type="no_project",
            )

        try:
            # Get optional severity override from config
            severity_override = with_config.get("severity")
            if severity_override is not None:
                try:
                    severity_override = int(severity_override)
                    if not 1 <= severity_override <= 9:
                        severity_override = None
                except (ValueError, TypeError):
                    severity_override = None

            # Call the async add_finding method which returns full finding details
            # This is the proper async method that returns finding_id, title, severity
            result = await session.add_finding(
                note=note,
                severity_override=severity_override,
            )

            if result.get("success"):
                return ActionResult(
                    outputs={
                        "success": True,
                        "finding_id": result.get("finding_id", "unknown"),
                        "title": result.get("title", note[:60]),
                        "severity": result.get("severity", 5),
                        "note": note,
                    },
                    outcome="success",
                )
            else:
                return ActionResult(
                    outputs={"success": False},
                    outcome="failure",
                    error=result.get("error", "Failed to add finding"),
                    error_type="add_failed",
                )

        except Exception as e:
            return ActionResult(
                outputs={"success": False},
                outcome="failure",
                error=str(e),
                error_type=type(e).__name__,
            )
