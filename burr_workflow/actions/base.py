"""
Base action protocol and result dataclass.

All workflow actions implement the BaseAction protocol,
enabling a unified execution model.
"""

from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


@dataclass
class ActionResult:
    """Result of action execution.

    This is the standardized result format that all actions
    return, enabling consistent state updates and control flow.

    Attributes:
        outputs: Dictionary of output values
        outcome: Execution outcome ("success", "failure", "skipped", "suspended")
        error: Error message if failed
        error_type: Error classification for on_failure routing
        next_hint: Optional hint for transition routing
    """
    outputs: dict[str, Any] = field(default_factory=dict)
    outcome: str = "success"
    error: Optional[str] = None
    error_type: Optional[str] = None
    next_hint: Optional[str] = None

    def is_success(self) -> bool:
        """Check if action succeeded."""
        return self.outcome == "success"

    def is_failure(self) -> bool:
        """Check if action failed."""
        return self.outcome == "failure"

    def is_skipped(self) -> bool:
        """Check if action was skipped."""
        return self.outcome == "skipped"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for state storage."""
        result = {
            "outputs": self.outputs,
            "outcome": self.outcome,
        }
        if self.error:
            result["error"] = self.error
        if self.error_type:
            result["error_type"] = self.error_type
        return result


@runtime_checkable
class BaseAction(Protocol):
    """Protocol that all workflow actions must implement.

    Actions are the core execution units in a workflow. Each action:
    - Reads from the workflow context (inputs, env, steps)
    - Performs some operation (shell command, HTTP request, LLM call, etc.)
    - Returns an ActionResult with outputs

    The `reads` and `writes` properties inform Burr about state dependencies,
    enabling proper state tracking and persistence.
    """

    # Class-level type hint for action type
    action_type: ClassVar[str]

    @property
    def reads(self) -> list[str]:
        """State keys this action reads from.

        Returns:
            List of state key names
        """
        ...

    @property
    def writes(self) -> list[str]:
        """State keys this action writes to.

        Returns:
            List of state key names
        """
        ...

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Execute the action.

        Args:
            step_config: The step definition from YAML (includes 'with' config)
            context: Current workflow context (inputs, env, steps, loop)
            exec_context: Execution environment for shell/prompts/logging

        Returns:
            ActionResult with outputs and outcome
        """
        ...


class AbstractAction:
    """Base class for action implementations.

    Provides common functionality for actions. Subclasses should
    override `action_type`, `reads`, `writes`, and `execute`.
    """

    action_type: ClassVar[str] = "abstract"

    @property
    def reads(self) -> list[str]:
        """Default: reads nothing from state."""
        return []

    @property
    def writes(self) -> list[str]:
        """Default: writes nothing to state."""
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Execute the action. Override in subclasses."""
        raise NotImplementedError(
            f"Action {self.__class__.__name__} must implement execute()"
        )

    def _get_with_config(self, step_config: dict[str, Any]) -> dict[str, Any]:
        """Extract 'with' configuration from step config."""
        return step_config.get("with", {})

    def _get_step_id(self, step_config: dict[str, Any]) -> str:
        """Get step ID from config, with fallback."""
        return step_config.get("id", step_config.get("name", "unnamed"))


class NoOpAction(AbstractAction):
    """Action that does nothing (used for placeholder nodes)."""

    action_type: ClassVar[str] = "noop"

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        return ActionResult(outputs={}, outcome="success")


class ConditionAction(AbstractAction):
    """Action that evaluates a condition and returns skipped if false.

    Used internally by the compiler for 'if:' handling.
    """

    action_type: ClassVar[str] = "condition"

    def __init__(self, condition_expr: str):
        """Initialize with condition expression.

        Args:
            condition_expr: The condition to evaluate
        """
        self.condition_expr = condition_expr

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        from ..evaluator import ContextEvaluator

        evaluator = ContextEvaluator(context)
        result = evaluator.evaluate_condition(self.condition_expr)

        if result:
            return ActionResult(
                outputs={"__condition_met": True},
                outcome="success",
            )
        else:
            return ActionResult(
                outputs={"__condition_met": False},
                outcome="skipped",
            )
