"""
Human input action for workflow user prompts.

This action bridges the workflow engine to the ExecutionContext.prompt_user()
method, enabling interactive user input within workflow execution.

Usage in YAML:
    - id: get_target
      name: Ask for target
      uses: human/input
      with:
        prompt: "Enter target IP or hostname"
        options: ["192.168.1.1", "localhost", "custom"]
        default: "localhost"
      register: target

    # Access result via ${{ steps.get_target.outputs.response }}
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


class HumanInputAction(AbstractAction):
    """
    Action for interactive user input.

    Bridges to ExecutionContext.prompt_user() for rich console prompts
    with optional choices and defaults.

    Step Configuration:
        prompt: str - The prompt message to display
        options: list[str] - Optional list of choices (numbered menu)
        default: str - Default value if user presses Enter

    Outputs:
        response: str - User's response (or selected option)
        is_default: bool - True if user accepted the default

    Example:
        - uses: human/input
          with:
            prompt: "Select environment"
            options: ["development", "staging", "production"]
            default: "development"
    """

    action_type: ClassVar[str] = "human/input"

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
        """Execute human input prompt.

        Args:
            step_config: Step configuration with prompt/options/default
            context: Workflow context
            exec_context: Execution context with prompt_user method

        Returns:
            ActionResult with user response
        """
        # Extract configuration
        with_config = step_config.get("with", {})
        prompt = with_config.get("prompt", "Enter input")
        options = with_config.get("options")
        default = with_config.get("default")

        # Resolve any template expressions in prompt
        from burr_workflow.evaluator import ContextEvaluator
        evaluator = ContextEvaluator(context)
        prompt = evaluator.resolve(prompt) if "${{" in prompt else prompt

        # Resolve options if they contain expressions
        if options:
            resolved_options = []
            for opt in options:
                if isinstance(opt, str) and "${{" in opt:
                    resolved_options.append(evaluator.resolve(opt))
                else:
                    resolved_options.append(opt)
            options = resolved_options

        # Resolve default
        if default and isinstance(default, str) and "${{" in default:
            default = evaluator.resolve(default)

        # Check for exec_context
        if exec_context is None:
            return ActionResult(
                outputs={"response": default or "", "is_default": True},
                outcome="failure",
                error="No execution context available for user prompts",
                error_type="missing_context",
            )

        # Check for prompt_user capability
        if not hasattr(exec_context, "prompt_user"):
            return ActionResult(
                outputs={"response": default or "", "is_default": True},
                outcome="failure",
                error="Execution context does not support user prompts",
                error_type="unsupported_operation",
            )

        try:
            # Call the prompt method
            response = await exec_context.prompt_user(
                prompt=prompt,
                options=options,
                default=default,
            )

            is_default = response == default if default else False

            return ActionResult(
                outputs={
                    "response": response,
                    "is_default": is_default,
                    "prompt": prompt,
                },
                outcome="success",
            )

        except KeyboardInterrupt:
            return ActionResult(
                outputs={"response": "", "is_default": False},
                outcome="failure",
                error="User cancelled input",
                error_type="user_cancelled",
            )
        except Exception as e:
            return ActionResult(
                outputs={"response": default or "", "is_default": True},
                outcome="failure",
                error=str(e),
                error_type=type(e).__name__,
            )


class HumanDecideAction(AbstractAction):
    """
    Action for constrained human decisions (confirm/choice).

    Bridges to ExecutionContext.prompt_user() for rich console prompts
    with predefined choices or yes/no confirmation.

    Step Configuration:
        prompt: str - The decision prompt
        choices: list[str] - Optional list of choices (if omitted, yes/no confirm)
        multi: bool - Allow multiple selections (default: false)
        default: str|bool - Default value if user presses Enter

    Outputs:
        value: bool|str|list[str] - User's decision/selection
        input_type: str - "confirm" or "choice"
        multi: bool - True if multi-select was enabled
        choices: list[str] - The available choices (if any)

    Example:
        # Yes/no confirmation
        - uses: human/decide
          with:
            prompt: "Proceed with the scan?"

        # Single choice from options
        - uses: human/decide
          with:
            prompt: "Select target"
            choices: ["192.168.1.1", "10.0.0.1", "localhost"]
    """

    action_type: ClassVar[str] = "human/decide"

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
        """Execute human decision prompt.

        Args:
            step_config: Step configuration with prompt/choices/multi
            context: Workflow context
            exec_context: Execution context with prompt_user method

        Returns:
            ActionResult with user decision
        """
        # Extract configuration
        with_config = step_config.get("with", {})
        prompt = with_config.get("prompt", "Please confirm:")
        choices = with_config.get("choices", [])
        multi = with_config.get("multi", False)
        default = with_config.get("default")

        # Resolve any template expressions in prompt
        from burr_workflow.evaluator import ContextEvaluator
        evaluator = ContextEvaluator(context)
        prompt = evaluator.resolve(prompt) if "${{" in prompt else prompt

        # Resolve choices if they contain expressions
        if choices:
            resolved_choices = []
            for choice in choices:
                if isinstance(choice, str) and "${{" in choice:
                    resolved_choices.append(evaluator.resolve(choice))
                else:
                    resolved_choices.append(choice)
            choices = resolved_choices

        # Determine input type
        input_type = "choice" if choices else "confirm"

        # Check for exec_context
        if exec_context is None:
            default_value = default if default is not None else (False if input_type == "confirm" else "")
            return ActionResult(
                outputs={"value": default_value, "input_type": input_type},
                outcome="failure",
                error="No execution context available for user prompts",
                error_type="missing_context",
            )

        # Check for prompt_user capability
        if not hasattr(exec_context, "prompt_user"):
            default_value = default if default is not None else (False if input_type == "confirm" else "")
            return ActionResult(
                outputs={"value": default_value, "input_type": input_type},
                outcome="failure",
                error="Execution context does not support user prompts",
                error_type="unsupported_operation",
            )

        try:
            if input_type == "confirm":
                # Build confirm prompt with yes/no options
                confirm_prompt = f"{prompt} (y/n)"
                response = await exec_context.prompt_user(
                    prompt=confirm_prompt,
                    options=["yes", "no"],
                    default="yes" if default else "no",
                )
                # Normalize to boolean
                value = response.lower() in ("yes", "y", "true", "1")
            else:
                # Choice selection
                if multi:
                    # Multi-select: prompt user to select multiple
                    # For simplicity, prompt for comma-separated input
                    options_str = ", ".join(choices)
                    multi_prompt = f"{prompt}\nOptions: {options_str}\nEnter comma-separated selections:"
                    response = await exec_context.prompt_user(
                        prompt=multi_prompt,
                        default=default if isinstance(default, str) else "",
                    )
                    # Parse comma-separated response
                    selected = [s.strip() for s in response.split(",") if s.strip()]
                    # Validate selections
                    invalid = [s for s in selected if s not in choices]
                    if invalid:
                        return ActionResult(
                            outputs={
                                "value": selected,
                                "invalid": invalid,
                                "choices": choices,
                                "input_type": "choice",
                                "multi": True,
                            },
                            outcome="failure",
                            error=f"Invalid choices: {invalid}. Must be from: {choices}",
                            error_type="ValidationError",
                        )
                    value = selected
                else:
                    # Single choice: use options menu
                    response = await exec_context.prompt_user(
                        prompt=prompt,
                        options=choices,
                        default=default,
                    )
                    # Validate selection
                    if response not in choices:
                        return ActionResult(
                            outputs={
                                "value": response,
                                "choices": choices,
                                "input_type": "choice",
                                "multi": False,
                            },
                            outcome="failure",
                            error=f"Invalid choice '{response}'. Must be one of: {choices}",
                            error_type="ValidationError",
                        )
                    value = response

            return ActionResult(
                outputs={
                    "value": value,
                    "input_type": input_type,
                    "multi": multi if input_type == "choice" else False,
                    "choices": choices if choices else None,
                    "prompt": prompt,
                },
                outcome="success",
            )

        except KeyboardInterrupt:
            return ActionResult(
                outputs={"value": None, "input_type": input_type},
                outcome="failure",
                error="User cancelled decision",
                error_type="user_cancelled",
            )
        except Exception as e:
            default_value = default if default is not None else (False if input_type == "confirm" else "")
            return ActionResult(
                outputs={"value": default_value, "input_type": input_type},
                outcome="failure",
                error=str(e),
                error_type=type(e).__name__,
            )
