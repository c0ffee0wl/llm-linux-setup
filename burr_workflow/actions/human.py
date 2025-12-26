"""
Human input action for interactive workflows.

This module provides an action that suspends workflow execution
to await human input, enabling interactive approval flows.
"""

from typing import Any, Optional, TYPE_CHECKING

from .base import BaseAction
from ..core.types import ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class HumanInputAction(BaseAction):
    """
    Action that suspends workflow execution to await human input.

    Uses Burr's suspension mechanism via __suspend_* state keys.
    The executor checks for suspension and returns control to caller.

    YAML usage:
        - id: get_approval
          uses: human/input
          with:
            prompt: "Do you approve this action?"
            input_type: "confirm"  # text, confirm, choice
            choices: ["yes", "no"]
            timeout: 300  # seconds, optional

    Input types:
        - text: Free-form text input
        - confirm: Yes/no confirmation (returns boolean)
        - choice: Selection from provided choices

    Resume flow:
        1. Action returns outcome="suspended" with suspension metadata
        2. Executor halts and returns control to caller
        3. Caller collects input and provides it via __resume_data[step_id]
        4. On resume, action reads input and returns success
    """

    reads = ["inputs", "env", "steps", "__resume_data"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Execute human input - suspends workflow for input.

        Args:
            step_config: Step configuration from YAML
            context: Current workflow context (state as dict)
            exec_context: Optional execution context for logging

        Returns:
            ActionResult with either:
            - outcome="suspended" and suspension metadata (first call)
            - outcome="success" with user input (after resume)
        """
        with_config = step_config.get("with", {})
        step_id = step_config.get("id", "human_input")

        prompt = with_config.get("prompt", "Please provide input:")
        input_type = with_config.get("input_type", "text")
        choices = with_config.get("choices", [])
        timeout = with_config.get("timeout")
        default = with_config.get("default")

        # Validate input_type
        valid_types = {"text", "confirm", "choice"}
        if input_type not in valid_types:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid input_type '{input_type}'. Must be one of: {valid_types}",
                error_type="ValidationError",
            )

        # Validate choices for choice type
        if input_type == "choice" and not choices:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="input_type='choice' requires non-empty 'choices' list",
                error_type="ValidationError",
            )

        # Check if we're resuming with input
        resume_data = context.get("__resume_data", {})
        if step_id in resume_data:
            user_input = resume_data[step_id]

            # Validate input for choice type
            if input_type == "choice" and user_input not in choices:
                return ActionResult(
                    outcome="failure",
                    outputs={"received": user_input, "valid_choices": choices},
                    error=f"Invalid choice '{user_input}'. Must be one of: {choices}",
                    error_type="ValidationError",
                )

            # Normalize confirm type to boolean
            if input_type == "confirm":
                if isinstance(user_input, str):
                    user_input = user_input.lower() in ("yes", "y", "true", "1")

            if exec_context:
                exec_context.log(
                    "info",
                    f"Human input received for '{step_id}': {user_input}"
                )

            return ActionResult(
                outcome="success",
                outputs={
                    "value": user_input,
                    "input_type": input_type,
                    "resumed": True,
                }
            )

        # First execution - suspend workflow for input
        if exec_context:
            exec_context.log(
                "info",
                f"Suspending workflow for human input: {prompt}"
            )

        return ActionResult(
            outcome="suspended",
            outputs={
                # Suspension metadata for executor
                "__suspend_for_input": True,
                "__suspend_step_id": step_id,
                "__suspend_prompt": prompt,
                "__suspend_input_type": input_type,
                "__suspend_choices": choices,
                "__suspend_timeout": timeout,
                "__suspend_default": default,
                # User-visible output
                "awaiting_input": True,
                "prompt": prompt,
                "input_type": input_type,
            }
        )
