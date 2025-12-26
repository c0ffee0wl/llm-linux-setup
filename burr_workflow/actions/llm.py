"""
LLM actions for AI-powered workflow steps.

These actions use the LLMClient protocol, allowing any LLM
backend to be used (OpenAI, Anthropic, local models, etc.).
"""

from typing import Any, ClassVar, Optional, TYPE_CHECKING

from .base import AbstractAction, ActionResult

if TYPE_CHECKING:
    from ..protocols import LLMClient, ExecutionContext


class LLMExtractAction(AbstractAction):
    """Extract structured data from text using LLM.

    Usage:
        - uses: llm/extract
          with:
            input: ${{ steps.scan.outputs.raw_output }}
            schema:
              type: object
              properties:
                hosts: { type: array, items: { type: string } }
                ports: { type: array, items: { type: integer } }
              required: [hosts, ports]
            prompt: "Extract all discovered hosts and open ports"
    """

    action_type: ClassVar[str] = "llm/extract"

    def __init__(self, llm_client: "LLMClient"):
        """Initialize with LLM client.

        Args:
            llm_client: LLM client for completions
        """
        self.llm_client = llm_client

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
        """Extract structured data from input.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with extracted data
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        # Get input and resolve expressions
        input_text = with_config.get("input", with_config.get("content", ""))
        input_text = evaluator.resolve(input_text)

        schema = with_config.get("schema", {})
        user_prompt = with_config.get("prompt", "Extract the relevant information")

        if not input_text:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="No input provided for extraction",
            )

        # Build extraction prompt
        full_prompt = f"""Analyze the following text and extract information according to the schema.

TEXT:
{input_text}

INSTRUCTIONS:
{user_prompt}

Respond with valid JSON matching the schema. No additional text."""

        system_prompt = """You are a precise data extraction assistant.
Extract exactly what is requested, in valid JSON format.
If information is not present, use null or empty arrays as appropriate."""

        try:
            result = await self.llm_client.complete_json(
                prompt=full_prompt,
                schema=schema,
                system=system_prompt,
            )
            return ActionResult(outputs=result, outcome="success")
        except Exception as e:
            return ActionResult(
                outputs={},
                outcome="failure",
                error=f"LLM extraction failed: {e}",
                error_type="LLMError",
            )


class LLMDecideAction(AbstractAction):
    """Make a decision from predefined choices using LLM.

    Usage:
        - uses: llm/decide
          with:
            context: ${{ steps.analysis.outputs }}
            choices:
              - continue_scan
              - escalate_to_human
              - abort_workflow
            prompt: "Based on the analysis, what should we do next?"
    """

    action_type: ClassVar[str] = "llm/decide"

    def __init__(self, llm_client: "LLMClient"):
        """Initialize with LLM client.

        Args:
            llm_client: LLM client for completions
        """
        self.llm_client = llm_client

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
        """Make a decision from choices.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with decision
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        # Get inputs
        input_context = with_config.get("context", "")
        input_context = evaluator.resolve_all(input_context)

        choices = with_config.get("choices", [])
        user_prompt = with_config.get("prompt", "Make a decision")

        if not choices:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="No choices provided for llm/decide",
            )

        # Build decision prompt
        choices_str = "\n".join(f"- {c}" for c in choices)

        # Convert context to string if dict
        if isinstance(input_context, dict):
            import json
            input_context = json.dumps(input_context, indent=2)

        full_prompt = f"""Based on the following context, make a decision.

CONTEXT:
{input_context}

QUESTION:
{user_prompt}

VALID CHOICES (respond with exactly one):
{choices_str}

Respond with ONLY the choice text, nothing else."""

        try:
            choice = await self.llm_client.complete_choice(
                prompt=full_prompt,
                choices=choices,
            )
            return ActionResult(
                outputs={"decision": choice, "choices": choices},
                outcome="success",
            )
        except Exception as e:
            return ActionResult(
                outputs={},
                outcome="failure",
                error=f"LLM decision failed: {e}",
                error_type="LLMError",
            )


class LLMGenerateAction(AbstractAction):
    """Generate free-form text using LLM.

    Usage:
        - uses: llm/generate
          with:
            prompt: "Summarize the following security findings"
            context: ${{ steps.analysis.outputs }}
            temperature: 0.7
            max_tokens: 500
    """

    action_type: ClassVar[str] = "llm/generate"

    def __init__(self, llm_client: "LLMClient"):
        """Initialize with LLM client.

        Args:
            llm_client: LLM client for completions
        """
        self.llm_client = llm_client

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
        """Generate text response.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with generated text
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        # Get inputs
        prompt = with_config.get("prompt", "")
        prompt = evaluator.resolve(prompt)

        input_context = with_config.get("context", "")
        if input_context:
            input_context = evaluator.resolve_all(input_context)
            if isinstance(input_context, dict):
                import json
                input_context = json.dumps(input_context, indent=2)

        system_prompt = with_config.get("system", None)
        temperature = with_config.get("temperature", 0.7)
        max_tokens = with_config.get("max_tokens", None)

        if not prompt:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="No prompt provided for llm/generate",
            )

        # Build full prompt
        if input_context:
            full_prompt = f"""{prompt}

CONTEXT:
{input_context}"""
        else:
            full_prompt = prompt

        try:
            response = await self.llm_client.complete(
                prompt=full_prompt,
                system=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return ActionResult(
                outputs={"text": response, "response": response},
                outcome="success",
            )
        except Exception as e:
            return ActionResult(
                outputs={},
                outcome="failure",
                error=f"LLM generation failed: {e}",
                error_type="LLMError",
            )


class LLMAnalyzeAction(AbstractAction):
    """Analyze content with optional structured output.

    Alias for llm/generate with analysis-focused defaults.

    Usage:
        - uses: llm/analyze
          with:
            content: ${{ steps.scan.outputs.raw_output }}
            prompt: "Identify potential security vulnerabilities"
            output_format: bullet_points
    """

    action_type: ClassVar[str] = "llm/analyze"

    def __init__(self, llm_client: "LLMClient"):
        """Initialize with LLM client.

        Args:
            llm_client: LLM client for completions
        """
        self.llm_client = llm_client

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
        """Analyze content.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with analysis
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        # Get inputs
        content = with_config.get("content", with_config.get("input", ""))
        content = evaluator.resolve(content)

        prompt = with_config.get("prompt", "Analyze the following content")
        output_format = with_config.get("output_format", "prose")

        if not content:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="No content provided for analysis",
            )

        # Build format instruction
        format_instructions = {
            "prose": "Provide your analysis in clear paragraphs.",
            "bullet_points": "Present your findings as bullet points.",
            "numbered": "Present your findings as a numbered list.",
            "json": "Return your analysis as a JSON object with relevant fields.",
        }

        format_instruction = format_instructions.get(output_format, format_instructions["prose"])

        full_prompt = f"""{prompt}

CONTENT TO ANALYZE:
{content}

{format_instruction}"""

        system_prompt = """You are an expert analyst. Provide clear, actionable insights.
Be thorough but concise. Focus on the most important findings."""

        try:
            response = await self.llm_client.complete(
                prompt=full_prompt,
                system=system_prompt,
                temperature=0.5,  # Lower for more focused analysis
            )

            outputs: dict[str, Any] = {"analysis": response}

            # Try to parse as JSON if format requested
            if output_format == "json":
                try:
                    import json
                    outputs["parsed"] = json.loads(response)
                except json.JSONDecodeError:
                    pass  # Keep as string

            return ActionResult(outputs=outputs, outcome="success")

        except Exception as e:
            return ActionResult(
                outputs={},
                outcome="failure",
                error=f"LLM analysis failed: {e}",
                error_type="LLMError",
            )
