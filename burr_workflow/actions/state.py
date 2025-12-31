"""
State action for setting workflow variables.

Allows explicit state manipulation without running commands.
"""

from typing import TYPE_CHECKING, Any, ClassVar, Optional

from .base import AbstractAction, ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class StateSetAction(AbstractAction):
    """Set state variables explicitly.

    Usage:
        - uses: state/set
          with:
            variables:
              count: ${{ steps.scan.outputs.total }}
              status: "completed"
              items: ${{ steps.list.outputs.items | first }}
    """

    action_type: ClassVar[str] = "state/set"

    @property
    def reads(self) -> list[str]:
        # Reads from context to resolve expressions like ${{ steps.scan.outputs.total }}
        return ["steps", "inputs", "env"]

    @property
    def writes(self) -> list[str]:
        # Writes resolved variables to step outputs
        return ["steps"]

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Set state variables.

        Args:
            step_config: Step configuration with variables
            context: Workflow context
            exec_context: Execution context for logging

        Returns:
            ActionResult with set variables as outputs
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        variables = with_config.get("variables", {})

        if not variables:
            return ActionResult(
                outputs={},
                outcome="success",
            )

        # Resolve all variable values
        resolved = evaluator.resolve_all(variables)

        return ActionResult(
            outputs=resolved,
            outcome="success",
        )


class StateAppendAction(AbstractAction):
    """Append to list variables.

    Usage:
        - uses: state/append
          with:
            target: findings
            value:
              host: ${{ loop.item }}
              status: vulnerable
    """

    action_type: ClassVar[str] = "state/append"

    @property
    def reads(self) -> list[str]:
        # Reads from context to resolve value expressions and get current list
        return ["steps", "inputs", "env"]

    @property
    def writes(self) -> list[str]:
        # Writes appended list to step outputs
        return ["steps"]

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Append to a list variable.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with updated list
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        target = with_config.get("target")
        value = with_config.get("value")

        if not target:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="'target' is required for state/append",
            )

        # Resolve value
        resolved_value = evaluator.resolve_all(value) if value else None

        # Get current list from context
        current = context.get("steps", {}).get(target, {}).get("outputs", [])
        if not isinstance(current, list):
            current = []

        # Append and return
        new_list = current + [resolved_value]

        return ActionResult(
            outputs={target: new_list},
            outcome="success",
        )
