"""
Burr action adapters for workflow execution.

This module provides adapter classes that bridge workflow actions to Burr's
execution model. All adapters live here to avoid circular imports.

Classes:
    BurrActionAdapter: Base adapter bridging BaseAction to SingleStepAction
    LoopBodyAdapter: Specialized adapter with exception handling for loops

Important: Burr's async execution model
---------------------------------------
When is_async() returns True, Burr calls `await action.run_and_update(...)`.
This means run_and_update() MUST return a coroutine that can be awaited.

The pattern (from Burr's SingleStepStreamingAction):
    def run_and_update(self, state, **kwargs) -> Union[tuple, Coroutine]:
        if self.is_async():
            return self._async_run(state, **kwargs)  # Returns coroutine
        return self._sync_run(state, **kwargs)  # Returns tuple

Burr does NOT call a separate arun_and_update() method - it only uses
run_and_update() and expects it to return appropriately based on is_async().
"""

import asyncio
import traceback
from typing import Any, Coroutine, Optional, TYPE_CHECKING, Union

from burr.core.action import SingleStepAction
from burr.core.state import State

from .types import ActionResult as CoreActionResult, RESERVED_STATE_KEYS

if TYPE_CHECKING:
    from ..protocols import ExecutionContext
    from ..actions.base import BaseAction


class BurrActionAdapter(SingleStepAction):
    """Bridges the BaseAction protocol to Burr's SingleStepAction interface.

    This adapter allows workflow actions written with the simpler `execute()` API
    to integrate seamlessly with Burr's state machine execution model.

    Key Responsibilities:
    1. Convert Burr's immutable State to a mutable dict for action execution
    2. Execute the wrapped BaseAction with the dict context
    3. Convert ActionResult outputs back to Burr State updates
    4. Return coroutine for async actions, tuple for sync actions

    Important: run_and_update() returns different types based on is_async():
    - is_async() == True: Returns a coroutine (Burr awaits it)
    - is_async() == False: Returns a tuple directly
    """

    def __init__(
        self,
        base_action: "BaseAction",
        step_id: str,
        step_config: dict,
        exec_context: Optional["ExecutionContext"] = None,
    ):
        """Initialize the Burr action adapter.

        Args:
            base_action: The action implementing BaseAction protocol
            step_id: Unique identifier for this step
            step_config: Original YAML step configuration
            exec_context: Execution context for shell/prompts/logging
        """
        super().__init__()
        self.base_action = base_action
        self.step_id = step_id
        self.step_config = step_config
        self.exec_context = exec_context
        # NOTE: Do NOT set self._name here! Burr's ApplicationBuilder calls
        # with_name() which raises ValueError if _name is already set.
        # The name property falls back to step_id when _name is None.

    @property
    def reads(self) -> list[str]:
        """State keys this action reads."""
        # Include standard workflow context keys
        base_reads = ["inputs", "env", "steps", "loop"]
        # Add action-specific reads
        action_reads = list(getattr(self.base_action, "reads", []))
        return list(set(base_reads + action_reads))

    @property
    def writes(self) -> list[str]:
        """State keys this action writes."""
        # Always writes to steps.{step_id}
        base_writes = ["steps"]
        # Add action-specific writes
        action_writes = list(getattr(self.base_action, "writes", []))
        return list(set(base_writes + action_writes))

    def run_and_update(
        self, state: State, **run_kwargs
    ) -> Union[tuple[dict, State], Coroutine[Any, Any, tuple[dict, State]]]:
        """Execute the wrapped action and return (result_dict, new_state).

        IMPORTANT: This method returns different types based on the action:
        - For async actions: Returns a coroutine (Burr awaits it)
        - For sync actions: Returns a tuple directly

        This matches Burr's expectation from SingleStepStreamingAction.
        When is_async() is True, Burr calls `await action.run_and_update(...)`.
        """
        if self.is_async():
            # Return coroutine for Burr to await
            return self._async_execute_and_update(state, **run_kwargs)
        else:
            # Return tuple directly for sync execution
            return self._sync_execute_and_update(state, **run_kwargs)

    async def _async_execute_and_update(
        self, state: State, **run_kwargs
    ) -> tuple[dict, State]:
        """Execute async action and apply result to state."""
        # Convert State to mutable context dict
        ctx = dict(state.get_all())

        # Execute the action
        result = await self.base_action.execute(
            step_config=self.step_config,
            context=ctx,
            exec_context=self.exec_context,
        )

        return self._apply_result_to_state(state, result)

    def _sync_execute_and_update(
        self, state: State, **run_kwargs
    ) -> tuple[dict, State]:
        """Execute sync action and apply result to state."""
        ctx = dict(state.get_all())

        # For sync actions that may return coroutines (defensive)
        result = self.base_action.execute(
            step_config=self.step_config,
            context=ctx,
            exec_context=self.exec_context,
        )

        # Handle if it accidentally returned a coroutine
        if asyncio.iscoroutine(result):
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(result)
            finally:
                loop.close()

        return self._apply_result_to_state(state, result)

    def _apply_result_to_state(
        self, state: State, result: CoreActionResult
    ) -> tuple[dict, State]:
        """Apply ActionResult to Burr State, creating a new immutable state.

        State Structure:
            steps.{step_id}: {outcome, outputs, error}
            Internal __* keys at top level

        SECURITY: Reserved state keys are stripped from user action outputs
        to prevent control flow hijacking (e.g., shell outputting __next).
        """
        # Normalize outcome to string if it's an enum
        outcome = result.outcome
        if hasattr(outcome, "value"):
            outcome = outcome.value

        # SECURITY: Sanitize outputs - remove reserved keys from user output
        # This prevents malicious/accidental control flow hijacking
        sanitized_outputs = {
            k: v for k, v in result.outputs.items()
            if k not in RESERVED_STATE_KEYS
        }

        # Build step result with sanitized outputs
        step_result = {
            "outcome": outcome,
            "outputs": sanitized_outputs,
        }
        if result.error:
            step_result["error"] = result.error
        if result.error_type:
            step_result["error_type"] = result.error_type

        # Get current steps dict immutably
        current_steps = dict(state.get("steps") or {})
        current_steps[self.step_id] = step_result

        # Apply base update
        new_state = state.update(steps=current_steps)

        # Apply internal control keys from outputs to top-level state
        # These are set by internal actions (iterators, conditions), not user actions
        # They are in RESERVED_STATE_KEYS so they're stripped from sanitized_outputs,
        # but we need them in state for routing transitions.
        internal_control_keys = {
            # Loop control
            "loop", "__loop_stack", "__loop_depth", "__loop_results",
            "__loop_errors", "__loop_iteration_count", "__loop_success_count",
            "__loop_empty", "__loop_items", "__loop_config", "__loop_continue",
            "__loop_reason", "__loop_break_requested", "__loop_break_item",
            "__loop_break_index", "__loop_failed",
            # Condition control
            "__condition_met",
        }
        for key in internal_control_keys:
            if key in result.outputs:
                new_state = new_state.update(**{key: result.outputs[key]})

        # Add routing via __next for Condition.when() transitions
        # Actions can set next_hint in result to override default
        next_hint = result.outputs.get("next_hint")
        new_state = new_state.update(
            __step_outcome=outcome,
            __step_error=result.error is not None,
        )
        if next_hint:
            new_state = new_state.update(__next=next_hint)

        return sanitized_outputs, new_state

    def is_async(self) -> bool:
        """Check if the wrapped action is async."""
        return asyncio.iscoroutinefunction(self.base_action.execute)


class LoopBodyAdapter(BurrActionAdapter):
    """
    Specialized adapter for loop body actions with exception handling.

    This adapter wraps action execution in try/except to support continue_on_error.
    When an exception occurs:
    - If continue_on_error=True: Returns failure result, allowing loop to continue
    - If continue_on_error=False: Re-raises exception to crash the workflow

    This is critical because:
    - BurrActionAdapter does NOT catch exceptions
    - Without this, any exception in a loop body crashes the entire workflow
    - The continue_on_error logic in IteratorAdvanceNode never gets a chance to run

    Note: Like BurrActionAdapter, run_and_update() returns different types:
    - For async actions: Returns a coroutine (with exception handling)
    - For sync actions: Returns a tuple directly (with exception handling)

    Usage:
        # In WorkflowCompiler._compile_loop():
        body_action = LoopBodyAdapter(
            base_action=action,
            step_id=f"{loop_id}_body",
            step_config=body_step,
            exec_context=self.exec_context,
            continue_on_error=step.get("continue_on_error", False),
        )
    """

    def __init__(
        self,
        base_action: "BaseAction",
        step_id: str,
        step_config: dict,
        exec_context: Optional["ExecutionContext"] = None,
        continue_on_error: bool = False,
    ):
        """Initialize the loop body adapter.

        Args:
            base_action: The action implementing BaseAction protocol
            step_id: Unique identifier for this step
            step_config: Original YAML step configuration
            exec_context: Execution context for shell/prompts/logging
            continue_on_error: Whether to catch exceptions and continue loop
        """
        super().__init__(
            base_action=base_action,
            step_id=step_id,
            step_config=step_config,
            exec_context=exec_context,
        )
        self.continue_on_error = continue_on_error

    def run_and_update(
        self, state: State, **run_kwargs
    ) -> Union[tuple[dict, State], Coroutine[Any, Any, tuple[dict, State]]]:
        """Execute with exception handling for continue_on_error support.

        Returns different types based on action type (matching parent pattern):
        - For async actions: Returns a coroutine (with exception handling)
        - For sync actions: Returns a tuple directly (with exception handling)
        """
        if self.is_async():
            # Return async wrapper coroutine with exception handling
            return self._async_run_with_exception_handling(state, **run_kwargs)
        else:
            # Sync execution with exception handling
            try:
                return self._sync_execute_and_update(state, **run_kwargs)
            except Exception as e:
                return self._handle_exception(state, e)

    async def _async_run_with_exception_handling(
        self, state: State, **run_kwargs
    ) -> tuple[dict, State]:
        """Async wrapper that handles exceptions for continue_on_error."""
        try:
            return await self._async_execute_and_update(state, **run_kwargs)
        except Exception as e:
            return self._handle_exception(state, e)

    def _handle_exception(
        self, state: State, exc: Exception
    ) -> tuple[dict, State]:
        """Handle exception based on continue_on_error setting.

        Args:
            state: Current Burr state
            exc: The caught exception

        Returns:
            Tuple of (result_dict, new_state) with failure outcome

        Raises:
            Exception: Re-raised if continue_on_error is False
        """
        if not self.continue_on_error:
            # Re-raise to crash workflow (default behavior)
            raise exc

        # Log the error if we have exec_context
        if self.exec_context:
            self.exec_context.log(
                "warning",
                f"Loop body '{self.step_id}' failed: {exc}. Continuing due to continue_on_error=True"
            )

        # Build failure result
        error_type = type(exc).__name__
        error_msg = str(exc)
        error_trace = traceback.format_exc()

        # Build step result with failure outcome
        step_result = {
            "outcome": "failure",
            "outputs": {},
            "error": error_msg,
            "error_type": error_type,
            "error_traceback": error_trace,
        }

        # Update steps in state
        current_steps = dict(state.get("steps") or {})
        current_steps[self.step_id] = step_result

        # Apply to state - IteratorAdvanceNode will read this and handle continue_on_error
        new_state = state.update(
            steps=current_steps,
            __step_outcome="failure",
            __step_error=True,
        )

        return {"outcome": "failure", "error": error_msg}, new_state
