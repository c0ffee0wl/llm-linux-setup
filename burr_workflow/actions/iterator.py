"""
Iterator actions for loop handling.

These internal actions are used by the compiler to implement
loop constructs as Burr graph cycles.

Loop Graph Structure:
    [before_loop] → [loop_init] → [loop_check] → [loop_body] → [loop_advance] → [loop_check]
                                       ↓                              ↓
                                  [loop_finalize] ← ← ← ← ← ← [break/complete]
                                       ↓
                                  [after_loop]
"""

from typing import Any, ClassVar, Optional, TYPE_CHECKING

from .base import AbstractAction, ActionResult
from ..core.types import LoopContext

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class IteratorInitAction(AbstractAction):
    """Initialize loop iteration.

    Evaluates the loop expression and sets up initial loop state.
    Called once at the start of a loop.
    """

    action_type: ClassVar[str] = "__iterator/init"

    def __init__(
        self,
        loop_id: str,
        loop_expr: str,
        max_iterations: int = 10000,
        max_results: int = 100,
        max_errors: int = 50,
        continue_on_error: bool = False,
        aggregate_results: bool = True,
    ):
        """Initialize the iterator action.

        Args:
            loop_id: Unique identifier for this loop
            loop_expr: Expression to evaluate for items
            max_iterations: Maximum iterations allowed
            max_results: Maximum results to keep in state
            max_errors: Maximum errors before failing
            continue_on_error: Whether to continue on iteration failure
            aggregate_results: Whether to store results
        """
        self.loop_id = loop_id
        self.loop_expr = loop_expr
        self.max_iterations = max_iterations
        self.max_results = max_results
        self.max_errors = max_errors
        self.continue_on_error = continue_on_error
        self.aggregate_results = aggregate_results

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return [
            "loop", "__loop_stack", "__loop_depth",
            "__loop_results", "__loop_errors",
            "__loop_iteration_count", "__loop_success_count",
        ]

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Initialize the loop.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with loop initialization
        """
        from ..evaluator import ContextEvaluator

        evaluator = ContextEvaluator(context)

        # Evaluate loop expression to get items
        items = evaluator.resolve(self.loop_expr)

        # Ensure items is a list
        if items is None:
            items = []
        elif not isinstance(items, (list, tuple)):
            items = list(items) if hasattr(items, "__iter__") else [items]

        items = list(items)
        total = len(items)

        # Handle empty list
        if total == 0:
            return ActionResult(
                outputs={
                    "loop": None,
                    "__loop_stack": context.get("__loop_stack", []),
                    "__loop_depth": context.get("__loop_depth", 0),
                    "__loop_results": [],
                    "__loop_errors": [],
                    "__loop_iteration_count": 0,
                    "__loop_success_count": 0,
                    "__loop_empty": True,
                },
                outcome="success",
            )

        # Create loop context for first item
        loop_ctx = LoopContext(
            items=items,
            item=items[0],
            index=1,  # 1-based (Jinja2 standard)
            index0=0,  # 0-based
            total=total,
            first=True,
            last=(total == 1),
            revindex=total,
            revindex0=total - 1,
            output=None,
            parent=context.get("loop"),  # Nested loop support
            _LoopContext__loop_id=self.loop_id,
            _LoopContext__ancestor_ids=context.get("__loop_stack", []) + [self.loop_id],
        )

        # Push to loop stack for nesting
        loop_stack = context.get("__loop_stack", []) + [self.loop_id]
        loop_depth = context.get("__loop_depth", 0) + 1

        return ActionResult(
            outputs={
                "loop": loop_ctx.to_dict(),
                "__loop_stack": loop_stack,
                "__loop_depth": loop_depth,
                "__loop_results": [],
                "__loop_errors": [],
                "__loop_iteration_count": 0,
                "__loop_success_count": 0,
                "__loop_empty": False,
                "__loop_items": items,
                "__loop_config": {
                    "max_iterations": self.max_iterations,
                    "max_results": self.max_results,
                    "max_errors": self.max_errors,
                    "continue_on_error": self.continue_on_error,
                    "aggregate_results": self.aggregate_results,
                },
            },
            outcome="success",
        )


class IteratorCheckAction(AbstractAction):
    """Check if loop should continue.

    Evaluates continuation condition, break_if, and limits.
    Sets next_hint to route to body or finalize node.
    """

    action_type: ClassVar[str] = "__iterator/check"

    def __init__(
        self,
        loop_id: str,
        break_if: Optional[str] = None,
        body_node: Optional[str] = None,
        finalize_node: Optional[str] = None,
    ):
        """Initialize the check action.

        Args:
            loop_id: Loop identifier
            break_if: Optional break condition expression
            body_node: Name of body node (defaults to {loop_id}_body)
            finalize_node: Name of finalize node (defaults to {loop_id}_finalize)
        """
        self.loop_id = loop_id
        self.break_if = break_if
        self.body_node = body_node or f"{loop_id}_body"
        self.finalize_node = finalize_node or f"{loop_id}_finalize"

    @property
    def reads(self) -> list[str]:
        return ["loop", "__loop_iteration_count", "__loop_break_requested"]

    @property
    def writes(self) -> list[str]:
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Check loop continuation.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with continue/break decision
        """
        from ..evaluator import ContextEvaluator

        loop_data = context.get("loop")

        # Empty loop - skip directly to finalize
        if context.get("__loop_empty"):
            return ActionResult(
                outputs={"__loop_continue": False, "__loop_reason": "empty"},
                outcome="success",
                next_hint=self.finalize_node,
            )

        if not loop_data:
            return ActionResult(
                outputs={"__loop_continue": False, "__loop_reason": "no_loop"},
                outcome="success",
                next_hint=self.finalize_node,
            )

        loop_ctx = LoopContext.from_dict(loop_data)
        iteration_count = context.get("__loop_iteration_count", 0)
        config = context.get("__loop_config", {})

        # Check explicit break request
        if context.get("__loop_break_requested"):
            return ActionResult(
                outputs={"__loop_continue": False, "__loop_reason": "break_requested"},
                outcome="success",
                next_hint=self.finalize_node,
            )

        # Check max iterations
        max_iterations = config.get("max_iterations", 10000)
        if iteration_count >= max_iterations:
            if exec_context:
                exec_context.log(
                    "warning",
                    f"Loop '{self.loop_id}' hit max iterations ({max_iterations})",
                )
            return ActionResult(
                outputs={"__loop_continue": False, "__loop_reason": "max_iterations"},
                outcome="success",
                next_hint=self.finalize_node,
            )

        # Check max errors
        max_errors = config.get("max_errors", 50)
        error_count = len(context.get("__loop_errors", []))
        if error_count >= max_errors:
            return ActionResult(
                outputs={"__loop_continue": False, "__loop_reason": "max_errors"},
                outcome="success",
                next_hint=self.finalize_node,
            )

        # Check if we're past the last item
        if loop_ctx.index0 >= loop_ctx.total:
            return ActionResult(
                outputs={"__loop_continue": False, "__loop_reason": "complete"},
                outcome="success",
                next_hint=self.finalize_node,
            )

        # Check break_if condition
        if self.break_if:
            evaluator = ContextEvaluator(context)
            should_break = evaluator.evaluate_condition(self.break_if)
            if should_break:
                return ActionResult(
                    outputs={
                        "__loop_continue": False,
                        "__loop_reason": "break_if",
                        "__loop_break_item": loop_ctx.item,
                        "__loop_break_index": loop_ctx.index0,
                    },
                    outcome="success",
                    next_hint=self.finalize_node,
                )

        # Continue loop
        return ActionResult(
            outputs={"__loop_continue": True},
            outcome="success",
            next_hint=self.body_node,
        )


class IteratorAdvanceAction(AbstractAction):
    """Advance to next loop iteration.

    Updates loop context and stores iteration result.
    Uses sliding window for results (max_results) and caps errors (max_errors).
    """

    action_type: ClassVar[str] = "__iterator/advance"

    def __init__(
        self,
        loop_id: str,
        body_step_id: str,
        check_node: Optional[str] = None,
        finalize_node: Optional[str] = None,
    ):
        """Initialize the advance action.

        Args:
            loop_id: Loop identifier
            body_step_id: ID of the body step (to get its output)
            check_node: Name of check node (defaults to {loop_id}_check)
            finalize_node: Name of finalize node (defaults to {loop_id}_finalize)
        """
        self.loop_id = loop_id
        self.body_step_id = body_step_id
        self.check_node = check_node or f"{loop_id}_check"
        self.finalize_node = finalize_node or f"{loop_id}_finalize"

    @property
    def reads(self) -> list[str]:
        return ["loop", "__loop_results", "__loop_errors", "__loop_iteration_count"]

    @property
    def writes(self) -> list[str]:
        return ["loop", "__loop_results", "__loop_errors", "__loop_iteration_count", "__loop_success_count"]

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Advance to next iteration.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with updated loop state
        """
        loop_data = context.get("loop")
        if not loop_data:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="No active loop",
            )

        loop_ctx = LoopContext.from_dict(loop_data)
        items = context.get("__loop_items", loop_ctx.items)
        config = context.get("__loop_config", {})

        # Get body step result
        body_result = context.get("steps", {}).get(self.body_step_id, {})
        body_outcome = body_result.get("outcome", "success")
        body_outputs = body_result.get("outputs", {})

        # Update counters
        iteration_count = context.get("__loop_iteration_count", 0) + 1
        success_count = context.get("__loop_success_count", 0)
        results = list(context.get("__loop_results", []))
        errors = list(context.get("__loop_errors", []))

        if body_outcome == "success":
            success_count += 1
            if config.get("aggregate_results", True):
                # Add result with sliding window
                max_results = config.get("max_results", 100)
                results.append({
                    "index": loop_ctx.index0,
                    "item": loop_ctx.item,
                    "outputs": body_outputs,
                })
                if len(results) > max_results:
                    results = results[-max_results:]
        else:
            # Record error
            errors.append({
                "index": loop_ctx.index0,
                "item": loop_ctx.item,
                "error": body_result.get("error"),
                "error_type": body_result.get("error_type"),
            })

            # Check if we should continue
            if not config.get("continue_on_error", False):
                return ActionResult(
                    outputs={
                        "__loop_results": results,
                        "__loop_errors": errors,
                        "__loop_iteration_count": iteration_count,
                        "__loop_success_count": success_count,
                        "__loop_failed": True,
                    },
                    outcome="failure",
                    error=f"Loop iteration {loop_ctx.index0} failed",
                    next_hint=self.finalize_node,
                )

        # Advance to next item
        next_index0 = loop_ctx.index0 + 1

        if next_index0 >= len(items):
            # No more items - will be caught by check
            return ActionResult(
                outputs={
                    "loop": loop_data,  # Keep current for final check
                    "__loop_results": results,
                    "__loop_errors": errors,
                    "__loop_iteration_count": iteration_count,
                    "__loop_success_count": success_count,
                },
                outcome="success",
                next_hint=self.check_node,
            )

        # Update loop context for next item
        new_loop_ctx = LoopContext(
            items=items,
            item=items[next_index0],
            index=next_index0 + 1,
            index0=next_index0,
            total=len(items),
            first=False,
            last=(next_index0 == len(items) - 1),
            revindex=len(items) - next_index0,
            revindex0=len(items) - next_index0 - 1,
            output=body_outputs if body_outcome == "success" else None,
            parent=loop_ctx.parent,
            _LoopContext__loop_id=loop_ctx._LoopContext__loop_id,
            _LoopContext__ancestor_ids=loop_ctx._LoopContext__ancestor_ids,
        )

        return ActionResult(
            outputs={
                "loop": new_loop_ctx.to_dict(),
                "__loop_results": results,
                "__loop_errors": errors,
                "__loop_iteration_count": iteration_count,
                "__loop_success_count": success_count,
            },
            outcome="success",
            next_hint=self.check_node,
        )


class IteratorFinalizeAction(AbstractAction):
    """Finalize loop and restore parent context.

    Called when loop completes (normally or via break).
    """

    action_type: ClassVar[str] = "__iterator/finalize"

    def __init__(self, loop_id: str):
        """Initialize the finalize action.

        Args:
            loop_id: Loop identifier
        """
        self.loop_id = loop_id

    @property
    def reads(self) -> list[str]:
        return ["loop", "__loop_stack", "__loop_results", "__loop_errors"]

    @property
    def writes(self) -> list[str]:
        return ["loop", "__loop_stack", "__loop_depth"]

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Finalize the loop.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with loop results
        """
        loop_data = context.get("loop")
        results = context.get("__loop_results", [])
        errors = context.get("__loop_errors", [])
        iteration_count = context.get("__loop_iteration_count", 0)
        success_count = context.get("__loop_success_count", 0)

        # Restore parent loop context if nested
        parent_loop = None
        if loop_data:
            loop_ctx = LoopContext.from_dict(loop_data)
            parent_loop = loop_ctx.parent

        # Pop from loop stack
        loop_stack = context.get("__loop_stack", [])
        if loop_stack and loop_stack[-1] == self.loop_id:
            loop_stack = loop_stack[:-1]

        loop_depth = max(0, context.get("__loop_depth", 1) - 1)

        # Determine overall outcome
        if context.get("__loop_failed"):
            outcome = "failure"
        elif errors:
            outcome = "partial"
        else:
            outcome = "success"

        return ActionResult(
            outputs={
                "loop": parent_loop.to_dict() if parent_loop else None,
                "__loop_stack": loop_stack,
                "__loop_depth": loop_depth,
                # Make results accessible to subsequent steps
                f"{self.loop_id}_results": results,
                f"{self.loop_id}_errors": errors,
                f"{self.loop_id}_count": iteration_count,
                f"{self.loop_id}_success_count": success_count,
            },
            outcome=outcome,
        )
