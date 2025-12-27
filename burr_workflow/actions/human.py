"""
Human input actions for interactive workflows.

This module provides actions that suspend workflow execution
to await human input, enabling interactive approval flows.

Two action types:
- human/input: Free-form input (text, multiline, file, editor)
- human/decide: Constrained input (confirm, single/multi choice)
"""

import os
import subprocess
import tempfile
from typing import Any, Optional, TYPE_CHECKING

from .base import BaseAction
from ..core.types import ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class HumanInputAction(BaseAction):
    """
    Action for free-form human input.

    Uses Burr's suspension mechanism via __suspend_* state keys.
    The executor checks for suspension and returns control to caller.

    YAML usage:
        # Simple text input
        - id: get_description
          uses: human/input
          with:
            prompt: "Describe what you observed"
            input_type: text  # default

        # Multi-line input
        - id: get_notes
          uses: human/input
          with:
            prompt: "Paste the log output"
            input_type: multiline

        # File selection
        - id: get_file
          uses: human/input
          with:
            prompt: "Select the config file"
            input_type: file

        # Editor (opens $EDITOR)
        - id: edit_config
          uses: human/input
          with:
            prompt: "Edit the configuration"
            input_type: editor
            initial_content: ${{ steps.generate.outputs.config }}

    Input types:
        - text: Single line text input (default)
        - multiline: Multi-line text area
        - file: File path selection
        - editor: Opens $EDITOR with temp file, returns edited content

    Outputs:
        - value: The user's input
        - input_type: The type of input collected
        - resumed: True when resuming after suspension

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
        """Execute human input - suspends workflow for input."""
        with_config = step_config.get("with", {})
        step_id = step_config.get("id", "human_input")

        prompt = with_config.get("prompt", "Please provide input:")
        input_type = with_config.get("input_type", "text")
        timeout = with_config.get("timeout")
        default = with_config.get("default")
        initial_content = with_config.get("initial_content", "")

        # Validate input_type
        valid_types = {"text", "multiline", "file", "editor"}
        if input_type not in valid_types:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid input_type '{input_type}'. Must be one of: {valid_types}",
                error_type="ValidationError",
            )

        # Check if we're resuming with input
        resume_data = context.get("__resume_data", {})
        if step_id in resume_data:
            user_input = resume_data[step_id]

            # For file type, verify file exists
            if input_type == "file":
                expanded_path = os.path.expanduser(str(user_input))
                if not os.path.isfile(expanded_path):
                    return ActionResult(
                        outcome="failure",
                        outputs={"received": user_input},
                        error=f"File not found: {user_input}",
                        error_type="FileNotFoundError",
                    )
                user_input = expanded_path

            if exec_context:
                exec_context.log(
                    "info",
                    f"Human input received for '{step_id}'"
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
                "__suspend_timeout": timeout,
                "__suspend_default": default,
                "__suspend_initial_content": initial_content,
                # User-visible output
                "awaiting_input": True,
                "prompt": prompt,
                "input_type": input_type,
            }
        )


class HumanDecideAction(BaseAction):
    """
    Action for constrained human decisions.

    Supports yes/no confirmation or selection from choices (single or multi).

    YAML usage:
        # Yes/no confirmation (omit choices for confirm mode)
        - id: approve
          uses: human/decide
          with:
            prompt: "Proceed with the scan?"

        # Single selection from choices
        - id: select_target
          uses: human/decide
          with:
            prompt: "Select the target host"
            choices:
              - host1.example.com
              - host2.example.com
              - host3.example.com

        # Multi-selection
        - id: select_targets
          uses: human/decide
          with:
            prompt: "Select targets to scan"
            choices:
              - host1.example.com
              - host2.example.com
              - host3.example.com
            multi: true

    Parameters:
        - prompt: The question to ask
        - choices: List of options (omit for yes/no confirm)
        - multi: Allow multiple selections (default: false)
        - timeout: Optional timeout in seconds
        - default: Default value if timeout expires

    Outputs:
        - value: Boolean for confirm, string for single choice, list for multi
        - input_type: "confirm" or "choice"
        - multi: True if multi-select was enabled
        - choices: The available choices (if any)
        - resumed: True when resuming after suspension
    """

    reads = ["inputs", "env", "steps", "__resume_data"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Execute human decision - suspends workflow for choice."""
        with_config = step_config.get("with", {})
        step_id = step_config.get("id", "human_decide")

        prompt = with_config.get("prompt", "Please confirm:")
        choices = with_config.get("choices", [])
        multi = with_config.get("multi", False)
        timeout = with_config.get("timeout")
        default = with_config.get("default")

        # Determine input type based on choices
        input_type = "choice" if choices else "confirm"

        # Check if we're resuming with input
        resume_data = context.get("__resume_data", {})
        if step_id in resume_data:
            user_input = resume_data[step_id]

            # Validate and normalize based on type
            if input_type == "confirm":
                # Normalize to boolean
                if isinstance(user_input, str):
                    user_input = user_input.lower() in ("yes", "y", "true", "1")
                else:
                    user_input = bool(user_input)

            elif input_type == "choice":
                if multi:
                    # Expect list for multi-select
                    if not isinstance(user_input, list):
                        user_input = [user_input]
                    # Validate all choices
                    invalid = [c for c in user_input if c not in choices]
                    if invalid:
                        return ActionResult(
                            outcome="failure",
                            outputs={"received": user_input, "invalid": invalid, "valid_choices": choices},
                            error=f"Invalid choices: {invalid}. Must be from: {choices}",
                            error_type="ValidationError",
                        )
                else:
                    # Single choice validation
                    if user_input not in choices:
                        return ActionResult(
                            outcome="failure",
                            outputs={"received": user_input, "valid_choices": choices},
                            error=f"Invalid choice '{user_input}'. Must be one of: {choices}",
                            error_type="ValidationError",
                        )

            if exec_context:
                exec_context.log(
                    "info",
                    f"Human decision received for '{step_id}': {user_input}"
                )

            return ActionResult(
                outcome="success",
                outputs={
                    "value": user_input,
                    "input_type": input_type,
                    "multi": multi,
                    "choices": choices if choices else None,
                    "resumed": True,
                }
            )

        # First execution - suspend workflow for decision
        if exec_context:
            exec_context.log(
                "info",
                f"Suspending workflow for human decision: {prompt}"
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
                "__suspend_multi": multi,
                "__suspend_timeout": timeout,
                "__suspend_default": default,
                # User-visible output
                "awaiting_input": True,
                "prompt": prompt,
                "input_type": input_type,
                "choices": choices if choices else None,
                "multi": multi,
            }
        )
