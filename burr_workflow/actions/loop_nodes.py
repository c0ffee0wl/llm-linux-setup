"""
Burr-native loop nodes using direct SingleStepAction inheritance.

These nodes bypass the adapter pattern for optimal Burr integration:
- Direct State manipulation (no mutable context)
- Proper __next routing for transitions
- Native Burr reads/writes tracking
- RESERVED_STATE_KEYS protection at the Burr level
- Scalable result storage (memory, file, or none)

NOTE: SingleStepAction is an Internal Burr API
----------------------------------------------
The Burr source code header for SingleStepAction states:
  "Note this is not user-facing, as the internal API is meant to change."

However, the API has been stable from 0.20.x through 0.40.x (20 versions).
The run_and_update() signature and is_async() behavior are unchanged.

Best practices for maintaining compatibility:
1. Pin the burr version strictly in pyproject.toml
2. Test thoroughly after any Burr version upgrade
3. Monitor Burr release notes for SingleStepAction changes

Result Storage Modes:
--------------------
- "memory": Store results in-state list (default, limited by max_results)
- "file": Stream results to JSONL file (scalable for >10K items)
- "none": Don't store results (fire-and-forget, lowest memory)

Usage:
    # In WorkflowCompiler._compile_loop():
    nodes = {
        f"{loop_id}_init": IteratorInitNode(
            items_expr="${{ targets }}",
            step_id=loop_id,
            check_node=f"{loop_id}_check",
            on_done=next_step,
            result_storage="file",  # For large loops
        ),
        f"{loop_id}_check": IteratorCheckNode(...),
        f"{loop_id}_advance": IteratorAdvanceNode(...),
        f"{loop_id}_finalize": IteratorFinalizeNode(
            step_id=loop_id,
            on_done=next_step,  # For state hygiene
        ),
    }
    builder.with_actions(**nodes)
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from burr.core.action import SingleStepAction
from burr.core.state import State

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class IteratorInitNode(SingleStepAction):
    """
    Burr-compatible loop initialization node.

    Responsibilities:
    1. Resolve the items expression to a list
    2. Initialize loop state (index, item, total, etc.)
    3. Handle empty list short-circuit
    4. Handle nested loop stack management

    Transitions (via __next in state):
    - Empty list → on_done (skip loop entirely)
    - Non-empty list → check_node (start iteration)
    """

    def __init__(
        self,
        items_expr: str,
        step_id: str,
        check_node: str,
        on_done: str,
        max_iterations: int = 10000,
        max_results: int = 100,
        max_errors: int = 50,
        continue_on_error: bool = False,
        aggregate_results: bool = True,
        result_storage: str = "memory",
        result_file_dir: str | None = None,
    ):
        """Initialize the loop init node.

        Args:
            items_expr: Expression to evaluate for items (e.g., "${{ targets }}")
            step_id: Unique identifier for this loop
            check_node: Name of the check node to transition to
            on_done: Name of the node to transition to when loop is skipped/done
            max_iterations: Maximum iterations allowed (safety limit)
            max_results: Maximum results to keep in memory (ignored for file storage)
            max_errors: Maximum errors before failing
            continue_on_error: Whether to continue on iteration failure
            aggregate_results: Whether to store results
            result_storage: Where to store results: "memory", "file", or "none"
            result_file_dir: Directory for JSONL file (default: system temp)
        """
        super().__init__()
        # DO NOT set self._name - let ApplicationBuilder handle it via with_name()
        self.items_expr = items_expr
        self.step_id = step_id
        self.check_node = check_node
        self.on_done = on_done
        self.max_iterations = max_iterations
        self.max_results = max_results
        self.max_errors = max_errors
        self.continue_on_error = continue_on_error
        self.aggregate_results = aggregate_results
        self.result_storage = result_storage
        self.result_file_dir = result_file_dir

    @property
    def reads(self) -> list[str]:
        return ["loop", "__loop_stack", "__loop_depth", "steps", "inputs", "env"]

    @property
    def writes(self) -> list[str]:
        return [
            "loop", "__loop_stack", "__loop_depth",
            "__loop_results", "__loop_errors",
            "__loop_iteration_count", "__loop_success_count",
            "__loop_config", "__loop_items", "__loop_empty",
            "__loop_results_file",  # JSONL file path for file storage
            "__next",
        ]

    def run_and_update(self, state: State, **kwargs) -> tuple[dict, State]:
        """Initialize the loop.

        Returns:
            Tuple of (result dict, new State)
        """
        # Resolve items expression
        from ..evaluator import ContextEvaluator

        # Build context from state for expression evaluation
        ctx = {
            "inputs": state.get("inputs") or {},
            "env": state.get("env") or {},
            "steps": state.get("steps") or {},
            "loop": state.get("loop"),
        }
        evaluator = ContextEvaluator(ctx)
        items = evaluator.resolve(self.items_expr)

        # Normalize to list
        if items is None:
            items = []
        elif not isinstance(items, (list, tuple)):
            items = list(items) if hasattr(items, "__iter__") else [items]
        items = list(items)

        current_depth = state.get("__loop_depth", 0)
        current_stack = list(state.get("__loop_stack") or [])

        # Handle empty list
        if len(items) == 0:
            new_state = state.update(
                loop=None,
                __loop_stack=current_stack,
                __loop_depth=current_depth,
                __loop_results=[],
                __loop_errors=[],
                __loop_iteration_count=0,
                __loop_success_count=0,
                __loop_empty=True,
                __next=self.on_done,  # Skip to done
            )
            return {"next": self.on_done, "reason": "empty"}, new_state

        # Build initial loop state
        loop_state = {
            "__loop_id": self.step_id,
            "__ancestor_ids": current_stack + [self.step_id],
            "items": items,
            "item": items[0],
            "index": 1,      # 1-based (Jinja2 standard)
            "index0": 0,     # 0-based (Jinja2 standard)
            "total": len(items),
            "first": True,
            "last": len(items) == 1,
            "revindex": len(items),      # 1-based from end
            "revindex0": len(items) - 1, # 0-based from end
            "output": None,
            "parent": state.get("loop"),  # For nested loop access
        }

        # Store loop config for check/advance nodes
        loop_config = {
            "max_iterations": self.max_iterations,
            "max_results": self.max_results,
            "max_errors": self.max_errors,
            "continue_on_error": self.continue_on_error,
            "aggregate_results": self.aggregate_results,
            "result_storage": self.result_storage,
        }

        # Initialize result storage based on mode
        results_file = None
        if self.result_storage == "file" and self.aggregate_results:
            # Create JSONL file for streaming results
            file_dir = self.result_file_dir or tempfile.gettempdir()

            # Security: Validate file_dir to prevent path traversal attacks
            # Resolve to absolute path and validate it doesn't escape allowed directories
            try:
                resolved_dir = Path(file_dir).resolve()
                allowed_dirs = {
                    Path(tempfile.gettempdir()).resolve(),
                    Path.home().resolve(),
                    Path.cwd().resolve(),
                }
                is_allowed = any(
                    resolved_dir == allowed or
                    any(p == allowed for p in resolved_dir.parents)
                    for allowed in allowed_dirs
                )
                if not is_allowed:
                    raise ValueError(
                        f"result_file_dir must be within temp dir, home, or cwd. "
                        f"Got: {file_dir}"
                    )
            except (OSError, ValueError) as e:
                logger.error(f"Invalid result_file_dir '{file_dir}': {e}")
                raise ValueError(f"Invalid result_file_dir: {e}") from e

            os.makedirs(file_dir, exist_ok=True)

            # Build filename and validate final path stays within intended directory
            filename = f"loop_results_{self.step_id}_{os.getpid()}.jsonl"
            results_file = os.path.realpath(os.path.join(file_dir, filename))

            # Ensure the resolved path is still within the intended directory
            if not results_file.startswith(os.path.realpath(file_dir) + os.sep):
                raise ValueError(
                    "Path traversal detected: result file would escape directory"
                )

            # Create empty file (will be appended to during iteration)
            with open(results_file, "w"):
                pass  # Empty file

        new_state = state.update(
            loop=loop_state,
            __loop_stack=current_stack + [self.step_id],
            __loop_depth=current_depth + 1,
            __loop_results=[],  # Empty for file mode, used for memory mode
            __loop_errors=[],
            __loop_iteration_count=0,
            __loop_success_count=0,
            __loop_config=loop_config,
            __loop_items=items,
            __loop_empty=False,
            __loop_results_file=results_file,  # None for memory/none modes
            __next=self.check_node,  # Go to check
        )

        return {"next": self.check_node, "reason": "initialized"}, new_state


class IteratorCheckNode(SingleStepAction):
    """
    Burr-compatible loop condition check node.

    Responsibilities:
    1. Check if more iterations remain
    2. Check for explicit break request (set by IteratorAdvanceNode after break_if)
    3. Check max_iterations safety limit
    4. Check max_errors limit

    NOTE: break_if is evaluated in IteratorAdvanceNode AFTER the body executes,
    not here. This ensures correct semantics: "I just processed item N, got
    output X, should I stop?" The check node only handles explicit break requests.

    Transitions (via __next in state):
    - More iterations → body_node
    - No more iterations → finalize_node
    - Max iterations/errors exceeded → finalize_node
    - Break requested → finalize_node
    """

    def __init__(
        self,
        step_id: str,
        body_node: str,
        finalize_node: str,
    ):
        """Initialize the check node.

        Args:
            step_id: Loop identifier
            body_node: Name of body node to execute
            finalize_node: Name of finalize node for completion
        """
        super().__init__()
        self.step_id = step_id
        self.body_node = body_node
        self.finalize_node = finalize_node

    @property
    def reads(self) -> list[str]:
        return [
            "loop", "__loop_iteration_count", "__loop_break_requested",
            "__loop_config", "__loop_errors", "__loop_empty",
            "inputs", "env", "steps",
        ]

    @property
    def writes(self) -> list[str]:
        return ["__next", "__loop_reason"]

    def run_and_update(self, state: State, **kwargs) -> tuple[dict, State]:
        """Check if loop should continue."""
        # Empty loop - skip directly to finalize
        if state.get("__loop_empty"):
            new_state = state.update(
                __next=self.finalize_node,
                __loop_reason="empty",
            )
            return {"next": self.finalize_node, "reason": "empty"}, new_state

        loop = state.get("loop")
        if loop is None:
            new_state = state.update(
                __next=self.finalize_node,
                __loop_reason="no_loop",
            )
            return {"next": self.finalize_node, "reason": "no_loop"}, new_state

        config = state.get("__loop_config") or {}
        iteration_count = state.get("__loop_iteration_count", 0)

        # Check for explicit break request
        if state.get("__loop_break_requested"):
            new_state = state.update(
                __next=self.finalize_node,
                __loop_reason="break_requested",
            )
            return {"next": self.finalize_node, "reason": "break_requested"}, new_state

        # Check max iterations
        max_iterations = config.get("max_iterations", 10000)
        if iteration_count >= max_iterations:
            new_state = state.update(
                __next=self.finalize_node,
                __loop_reason="max_iterations",
            )
            return {"next": self.finalize_node, "reason": "max_iterations"}, new_state

        # Check max errors
        max_errors = config.get("max_errors", 50)
        error_count = len(state.get("__loop_errors") or [])
        if error_count >= max_errors:
            new_state = state.update(
                __next=self.finalize_node,
                __loop_reason="max_errors",
            )
            return {"next": self.finalize_node, "reason": "max_errors"}, new_state

        # Check if we're past the last item
        if loop.get("index0", 0) >= loop.get("total", 0):
            new_state = state.update(
                __next=self.finalize_node,
                __loop_reason="complete",
            )
            return {"next": self.finalize_node, "reason": "completed"}, new_state

        # NOTE: break_if is evaluated in IteratorAdvanceNode AFTER the body
        # executes, not here. This ensures correct semantics where loop.output
        # contains the current iteration's result when break_if is evaluated.

        # Continue to body
        new_state = state.update(__next=self.body_node)
        return {"next": self.body_node, "reason": "continue"}, new_state


class IteratorAdvanceNode(SingleStepAction):
    """
    Burr-compatible loop advancement node.

    Responsibilities:
    1. Get body result and update loop.output
    2. Evaluate break_if condition (with correct context: current item + output)
    3. Aggregate results (memory, file, or none)
    4. Increment loop counters and advance to next item
    5. Track iteration count for safety

    break_if Semantics:
    ------------------
    The break_if condition is evaluated HERE (not in CheckNode) because:
    - We just executed the body for item N
    - loop.output now contains the result from item N
    - The question is: "Given what I just got, should I stop?"

    If evaluated in CheckNode, loop.output would be from item N-1 (wrong).

    Transitions (via __next in state):
    - break_if triggered → finalize_node
    - On failure without continue_on_error → finalize_node
    - More items remain → check_node
    """

    def __init__(
        self,
        step_id: str,
        body_step_id: str,
        check_node: str,
        finalize_node: str,
        break_if: str | None = None,
    ):
        """Initialize the advance node.

        Args:
            step_id: Loop identifier
            body_step_id: ID of the body step (to get its output)
            check_node: Name of check node to cycle back to
            finalize_node: Name of finalize node for early exit
            break_if: Optional break condition expression (evaluated post-body)
        """
        super().__init__()
        self.step_id = step_id
        self.body_step_id = body_step_id
        self.check_node = check_node
        self.finalize_node = finalize_node
        self.break_if = break_if

    @property
    def reads(self) -> list[str]:
        return [
            "loop", "__loop_items", "__loop_results", "__loop_errors",
            "__loop_iteration_count", "__loop_success_count", "__loop_config",
            "__loop_results_file",  # JSONL file path for file storage
            "steps",
        ]

    @property
    def writes(self) -> list[str]:
        return [
            "loop", "__loop_results", "__loop_errors",
            "__loop_iteration_count", "__loop_success_count",
            "__loop_failed", "__loop_break_requested", "__loop_reason",
            "__loop_break_item", "__loop_break_index", "__next",
        ]

    def run_and_update(self, state: State, **kwargs) -> tuple[dict, State]:
        """Advance to next iteration."""
        loop = state.get("loop")
        if loop is None:
            new_state = state.update(__next=self.finalize_node)
            return {"next": self.finalize_node, "error": "no_loop"}, new_state

        items = state.get("__loop_items") or loop.get("items", [])
        config = state.get("__loop_config") or {}

        # Get body step result
        steps = state.get("steps") or {}
        body_result = steps.get(self.body_step_id, {})
        body_outcome = body_result.get("outcome", "success")
        body_outputs = body_result.get("outputs", {})

        # Update counters
        iteration_count = (state.get("__loop_iteration_count") or 0) + 1
        success_count = state.get("__loop_success_count") or 0
        results = list(state.get("__loop_results") or [])
        errors = list(state.get("__loop_errors") or [])

        if body_outcome == "success":
            success_count += 1
            if config.get("aggregate_results", True):
                result_storage = config.get("result_storage", "memory")
                result_entry = {
                    "index": loop.get("index0"),
                    "item": loop.get("item"),
                    "outputs": body_outputs,
                }

                if result_storage == "file":
                    # Append to JSONL file (scalable for large loops)
                    results_file = state.get("__loop_results_file")
                    if results_file:
                        with open(results_file, "a") as f:
                            f.write(json.dumps(result_entry) + "\n")
                    # Keep in-memory list empty for file mode
                elif result_storage == "memory":
                    # Add result with sliding window (original behavior)
                    max_results = config.get("max_results", 100)
                    results.append(result_entry)
                    if len(results) > max_results:
                        results = results[-max_results:]
                # result_storage == "none": don't store anything
        else:
            # Record error
            errors.append({
                "index": loop.get("index0"),
                "item": loop.get("item"),
                "error": body_result.get("error"),
                "error_type": body_result.get("error_type"),
            })

            # Check if we should continue
            if not config.get("continue_on_error", False):
                new_state = state.update(
                    __loop_results=results,
                    __loop_errors=errors,
                    __loop_iteration_count=iteration_count,
                    __loop_success_count=success_count,
                    __loop_failed=True,
                    __next=self.finalize_node,
                )
                return {
                    "next": self.finalize_node,
                    "error": f"Loop iteration {loop.get('index0')} failed",
                }, new_state

        # Evaluate break_if condition AFTER body execution
        # At this point:
        # - loop.item is still the current item we just processed
        # - body_outputs contains the result from processing this item
        # This is the correct semantics: "I just processed item N, got result X, should I stop?"
        if self.break_if and body_outcome == "success":
            from ..evaluator import ContextEvaluator

            # Build context with current item and its output
            # We temporarily set loop.output to the current result for evaluation
            loop_with_output = {
                **loop,
                "output": body_outputs,
            }
            ctx = {
                "inputs": state.get("inputs") or {},
                "env": state.get("env") or {},
                "steps": state.get("steps") or {},
                "loop": loop_with_output,
            }
            evaluator = ContextEvaluator(ctx)
            if evaluator.evaluate_condition(self.break_if):
                # Break condition met - finalize with current results
                new_state = state.update(
                    __loop_results=results,
                    __loop_errors=errors,
                    __loop_iteration_count=iteration_count,
                    __loop_success_count=success_count,
                    __loop_break_requested=True,
                    __loop_reason="break_if",
                    __loop_break_item=loop.get("item"),
                    __loop_break_index=loop.get("index0"),
                    __next=self.finalize_node,
                )
                return {
                    "next": self.finalize_node,
                    "reason": "break_if",
                    "break_item": loop.get("item"),
                    "break_index": loop.get("index0"),
                }, new_state

        # Advance to next item
        next_index0 = loop.get("index0", 0) + 1

        if next_index0 >= len(items):
            # No more items - will be caught by check
            new_state = state.update(
                __loop_results=results,
                __loop_errors=errors,
                __loop_iteration_count=iteration_count,
                __loop_success_count=success_count,
                __next=self.check_node,
            )
            return {"next": self.check_node, "reason": "end_of_items"}, new_state

        # Update loop state for next item
        total = len(items)
        new_loop = {
            **loop,
            "item": items[next_index0],
            "index": next_index0 + 1,
            "index0": next_index0,
            "first": False,
            "last": next_index0 == total - 1,
            "revindex": total - next_index0,
            "revindex0": total - next_index0 - 1,
            "output": body_outputs if body_outcome == "success" else None,
        }

        new_state = state.update(
            loop=new_loop,
            __loop_results=results,
            __loop_errors=errors,
            __loop_iteration_count=iteration_count,
            __loop_success_count=success_count,
            __next=self.check_node,
        )
        return {"next": self.check_node, "reason": "advanced"}, new_state


class IteratorFinalizeNode(SingleStepAction):
    """
    Burr-compatible loop finalization node.

    Responsibilities:
    1. Pop from loop stack
    2. Restore parent loop context if nested
    3. Store final results in step outputs
    4. Determine overall loop outcome
    5. Set __next for proper state hygiene

    Transitions (via __next in state):
    - Always transitions to on_done (the next step after the loop)
    """

    def __init__(self, step_id: str, on_done: str):
        """Initialize the finalize node.

        Args:
            step_id: Loop identifier
            on_done: Name of the node to transition to after loop completes
        """
        super().__init__()
        self.step_id = step_id
        self.on_done = on_done

    @property
    def reads(self) -> list[str]:
        return [
            "loop", "__loop_stack", "__loop_depth",
            "__loop_results", "__loop_errors",
            "__loop_iteration_count", "__loop_success_count",
            "__loop_failed", "__loop_reason",
            "__loop_results_file", "__loop_config",
            "__loop_break_requested", "__loop_break_item", "__loop_break_index",
        ]

    @property
    def writes(self) -> list[str]:
        return ["loop", "__loop_stack", "__loop_depth", "steps", "__next"]

    def run_and_update(self, state: State, **kwargs) -> tuple[dict, State]:
        """Finalize the loop."""
        loop = state.get("loop")
        results = state.get("__loop_results") or []
        errors = state.get("__loop_errors") or []
        iteration_count = state.get("__loop_iteration_count") or 0
        success_count = state.get("__loop_success_count") or 0
        reason = state.get("__loop_reason", "complete")
        results_file = state.get("__loop_results_file")
        config = state.get("__loop_config") or {}
        result_storage = config.get("result_storage", "memory")

        # Get break state for outputs
        break_requested = state.get("__loop_break_requested", False)
        break_item = state.get("__loop_break_item")
        break_index = state.get("__loop_break_index")

        # Restore parent loop context if nested
        parent_loop = loop.get("parent") if loop else None

        # Pop from loop stack
        loop_stack = list(state.get("__loop_stack") or [])
        if loop_stack and loop_stack[-1] == self.step_id:
            loop_stack = loop_stack[:-1]

        loop_depth = max(0, (state.get("__loop_depth") or 1) - 1)

        # Determine overall outcome
        if state.get("__loop_failed"):
            outcome = "failure"
        elif errors:
            outcome = "partial"
        else:
            outcome = "success"

        # Build outputs based on result_storage mode
        outputs = {
            "errors": errors,
            "count": iteration_count,
            "iterations": iteration_count,  # Alias for documentation compatibility
            "success_count": success_count,
            "succeeded": success_count,  # Alias for documentation compatibility
            "reason": reason,
            "result_storage": result_storage,
            # Break information for early exit detection
            "break_early": break_requested,
            "break_item": break_item,
            "break_index": break_index,
        }

        if result_storage == "file" and results_file:
            # For file mode, provide the file path instead of in-memory results
            outputs["results_file"] = results_file
            outputs["results"] = []  # Empty list for compatibility
        else:
            # Memory or none mode: include in-memory results
            outputs["results"] = results

        # Store results in steps for subsequent access
        steps = dict(state.get("steps") or {})
        steps[self.step_id] = {
            "outcome": outcome,
            "outputs": outputs,
        }

        new_state = state.update(
            loop=parent_loop,
            __loop_stack=loop_stack,
            __loop_depth=loop_depth,
            steps=steps,
            __next=self.on_done,  # State hygiene: set routing for next step
        )

        # Build return dict
        result_dict = {
            "outcome": outcome,
            "errors_count": len(errors),
        }
        if result_storage == "file" and results_file:
            result_dict["results_file"] = results_file
            result_dict["results_count"] = success_count  # Count from tracking
        else:
            result_dict["results_count"] = len(results)

        return result_dict, new_state
