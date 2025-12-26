"""
Control flow actions for workflow management.

Provides early exit, failure, and workflow control.
"""

from typing import Any, ClassVar, Optional, TYPE_CHECKING

from .base import AbstractAction, ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class ExitAction(AbstractAction):
    """Exit workflow early with success status.

    Usage:
        - uses: control/exit
          with:
            status: success
            message: "Completed early - no action needed"
            outputs:
              result: ${{ steps.analysis.outputs }}

    The workflow will transition to __cleanup__ to run
    finally blocks, then terminate.
    """

    action_type: ClassVar[str] = "control/exit"

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return ["__workflow_exit", "__exit_message", "__exit_outputs"]

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Exit the workflow.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult triggering workflow exit
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        status = with_config.get("status", "success")
        message = with_config.get("message", "")
        outputs = with_config.get("outputs", {})

        # Resolve outputs if they contain expressions
        if outputs:
            outputs = evaluator.resolve_all(outputs)

        return ActionResult(
            outputs={
                "__workflow_exit": True,
                "__exit_status": status,
                "__exit_message": message,
                "__exit_outputs": outputs,
            },
            outcome="success",
        )


class FailAction(AbstractAction):
    """Fail workflow immediately with error status.

    Usage:
        - uses: control/fail
          with:
            message: "Critical validation failed"
            error_code: "VALIDATION_ERROR"
            details:
              field: "email"
              reason: "Invalid format"

    The workflow will transition to __cleanup__ to run
    finally blocks, then terminate with failure status.
    """

    action_type: ClassVar[str] = "control/fail"

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return ["__workflow_failed", "__error_message", "__error_code", "__error_details"]

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Fail the workflow.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult triggering workflow failure
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        message = with_config.get("message", "Workflow failed")
        error_code = with_config.get("error_code", "WORKFLOW_FAILURE")
        details = with_config.get("details", {})

        # Resolve details if they contain expressions
        if details:
            details = evaluator.resolve_all(details)

        return ActionResult(
            outputs={
                "__workflow_failed": True,
                "__error_message": message,
                "__error_code": error_code,
                "__error_details": details,
            },
            outcome="failure",
            error=message,
            error_type=error_code,
        )


class BreakAction(AbstractAction):
    """Break out of a loop early.

    Usage:
        - uses: control/break
          with:
            reason: "Found target"
            result: ${{ loop.item }}

    Sets __loop_break_requested to trigger loop exit.
    """

    action_type: ClassVar[str] = "control/break"

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return ["__loop_break_requested", "__loop_break_reason", "__loop_break_item"]

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Break from the current loop.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult triggering loop break
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        reason = with_config.get("reason", "")
        result = with_config.get("result")

        if result is not None:
            result = evaluator.resolve_all(result)

        # Get current loop index
        loop_ctx = context.get("loop", {})
        current_index = loop_ctx.get("index", 0)

        return ActionResult(
            outputs={
                "__loop_break_requested": True,
                "__loop_break_reason": reason,
                "__loop_break_item": result,
                "__loop_break_index": current_index,
            },
            outcome="success",
        )


class ContinueAction(AbstractAction):
    """Skip to next loop iteration.

    Usage:
        - uses: control/continue
          with:
            reason: "Skipping invalid item"

    Note: In practice, this is typically handled by
    conditional execution (if:) rather than explicit continue.
    """

    action_type: ClassVar[str] = "control/continue"

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Continue to next loop iteration.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with skipped outcome
        """
        with_config = self._get_with_config(step_config)
        reason = with_config.get("reason", "Continuing to next iteration")

        return ActionResult(
            outputs={},
            outcome="skipped",
            next_hint="__loop_advance",
        )
