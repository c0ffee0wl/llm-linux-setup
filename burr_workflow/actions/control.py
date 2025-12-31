"""
Control flow actions for workflow management.

Provides early exit, failure, wait, and workflow control.
"""

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar, Optional

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
        with_config.get("reason", "Continuing to next iteration")

        return ActionResult(
            outputs={},
            outcome="skipped",
            next_hint="__loop_advance",
        )


class WaitAction(AbstractAction):
    """Wait for a duration or until a condition is met.

    Usage:
        # Simple duration wait
        - uses: control/wait
          with:
            duration: 30  # seconds

        # Wait until condition with polling
        - uses: control/wait
          with:
            until: ${{ steps.check.outputs.ready == true }}
            interval: 5      # poll every 5 seconds
            timeout: 300     # fail after 5 minutes

    Parameters:
        - duration: Wait for this many seconds (simple wait)
        - until: Expression to evaluate repeatedly (conditional wait)
        - interval: Poll interval in seconds (default: 5, for 'until' mode)
        - timeout: Maximum wait time in seconds (default: 300, for 'until' mode)

    Outputs:
        - waited: Number of seconds waited
        - condition_met: True if 'until' condition was met (only for until mode)
    """

    action_type: ClassVar[str] = "control/wait"

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
        """Wait for duration or until condition.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with wait information
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)

        duration = with_config.get("duration")
        until_expr = with_config.get("until")
        interval = with_config.get("interval", 5)
        timeout = with_config.get("timeout", 300)

        # Validate: must have either duration or until
        if duration is None and until_expr is None:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="Must specify either 'duration' or 'until' for control/wait",
                error_type="ValidationError",
            )

        # Simple duration wait
        if duration is not None:
            try:
                wait_seconds = float(duration)
                if wait_seconds < 0:
                    return ActionResult(
                        outcome="failure",
                        outputs={},
                        error=f"Duration must be non-negative: {duration}",
                        error_type="ValidationError",
                    )

                if exec_context:
                    exec_context.log("info", f"Waiting for {wait_seconds} seconds...")

                await asyncio.sleep(wait_seconds)

                return ActionResult(
                    outcome="success",
                    outputs={"waited": wait_seconds}
                )
            except (ValueError, TypeError):
                return ActionResult(
                    outcome="failure",
                    outputs={},
                    error=f"Invalid duration value: {duration}",
                    error_type="ValidationError",
                )

        # Conditional wait with polling
        evaluator = ContextEvaluator(context)
        elapsed = 0.0

        if exec_context:
            exec_context.log("info", f"Waiting until condition is met (timeout: {timeout}s)...")

        while elapsed < timeout:
            # Evaluate condition
            try:
                result = evaluator.evaluate_condition(until_expr)
                if result:
                    if exec_context:
                        exec_context.log("info", f"Condition met after {elapsed:.1f} seconds")
                    return ActionResult(
                        outcome="success",
                        outputs={"waited": elapsed, "condition_met": True}
                    )
            except Exception as e:
                if exec_context:
                    exec_context.log("warning", f"Condition evaluation error: {e}")
                # Continue polling on evaluation error

            # Wait for interval
            await asyncio.sleep(interval)
            elapsed += interval

        # Timeout reached
        return ActionResult(
            outcome="failure",
            outputs={"waited": elapsed, "condition_met": False},
            error=f"Condition not met within {timeout} seconds",
            error_type="TimeoutError",
        )
