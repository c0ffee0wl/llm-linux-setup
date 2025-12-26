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

from burr_workflow.actions.base import AbstractAction, ActionResult

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
