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


class LLMInstructAction(AbstractAction):
    """LLM instruction action with optional feedback collection.

    Two modes of operation:

    1. Simple mode (no await_feedback):
        Just applies an instruction to input and returns the LLM response.

    2. Airgapped mode (await_feedback: true):
        Generates instructions, suspends workflow for user feedback,
        and optionally analyzes the feedback with LLM.

    Usage (simple):
        - uses: llm/instruct
          with:
            instruction: "Summarize the following text in 3 bullet points"
            input: ${{ steps.fetch.outputs.content }}

    Usage (airgapped with feedback):
        - uses: llm/instruct
          with:
            prompt: |
              Generate instructions for running Mimikatz on the target.
              Target: ${{ inputs.target_host }}
            await_feedback: true
            feedback_type: multiline  # text, multiline, file_path, json
            analyze_feedback: true    # Parse feedback with LLM

    Outputs (simple mode):
        - response: The LLM's response text
        - model: The model used

    Outputs (airgapped mode):
        - instructions: Generated instructions
        - feedback: User-provided feedback (after resume)
        - feedback_analysis: Parsed feedback (if analyze_feedback=true)
    """

    action_type: ClassVar[str] = "llm/instruct"

    def __init__(self, llm_client: "LLMClient"):
        """Initialize with LLM client.

        Args:
            llm_client: LLM client for completions
        """
        self.llm_client = llm_client

    @property
    def reads(self) -> list[str]:
        return ["__resume_data"]

    @property
    def writes(self) -> list[str]:
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Execute instruction, optionally with feedback collection.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with LLM response or suspension for feedback
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)
        step_id = step_config.get("id", "llm_instruct")

        # Check for airgapped mode
        await_feedback = with_config.get("await_feedback", False)

        if await_feedback:
            return await self._execute_airgapped(
                step_config, with_config, context, evaluator, step_id, exec_context
            )
        else:
            return await self._execute_simple(
                with_config, evaluator, exec_context
            )

    async def _execute_simple(
        self,
        with_config: dict[str, Any],
        evaluator: "ContextEvaluator",
        exec_context: Optional["ExecutionContext"],
    ) -> ActionResult:
        """Simple mode: apply instruction to input."""
        # Get inputs
        instruction = with_config.get("instruction", "")
        instruction = evaluator.resolve(instruction)

        input_text = with_config.get("input", "")
        input_text = evaluator.resolve(input_text)

        model = with_config.get("model")

        if not instruction:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="No instruction provided for llm/instruct",
                error_type="ValidationError",
            )

        # Build prompt - instruction first, then input (if any)
        if input_text:
            full_prompt = f"""{instruction}

INPUT:
{input_text}"""
        else:
            full_prompt = instruction

        try:
            response = await self.llm_client.complete(
                prompt=full_prompt,
                model=model,
            )
            return ActionResult(
                outputs={
                    "response": response,
                    "model": model or "default",
                },
                outcome="success",
            )
        except Exception as e:
            return ActionResult(
                outputs={},
                outcome="failure",
                error=f"LLM instruction failed: {e}",
                error_type="LLMError",
            )

    async def _execute_airgapped(
        self,
        step_config: dict[str, Any],
        with_config: dict[str, Any],
        context: dict[str, Any],
        evaluator: "ContextEvaluator",
        step_id: str,
        exec_context: Optional["ExecutionContext"],
    ) -> ActionResult:
        """Airgapped mode: generate instructions, wait for feedback."""
        # Check if we're resuming with feedback
        resume_data = context.get("__resume_data", {})

        if step_id in resume_data:
            # Phase 2: Process feedback
            return await self._process_feedback(
                with_config, resume_data[step_id], evaluator, exec_context
            )
        else:
            # Phase 1: Generate instructions and suspend
            return await self._generate_and_suspend(
                with_config, evaluator, step_id, exec_context
            )

    async def _generate_and_suspend(
        self,
        with_config: dict[str, Any],
        evaluator: "ContextEvaluator",
        step_id: str,
        exec_context: Optional["ExecutionContext"],
    ) -> ActionResult:
        """Generate instructions and suspend for feedback."""
        prompt = with_config.get("prompt", "")
        prompt = evaluator.resolve(prompt)

        if not prompt:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="No prompt provided for llm/instruct in airgapped mode",
                error_type="ValidationError",
            )

        feedback_type = with_config.get("feedback_type", "text")
        valid_types = {"text", "multiline", "file_path", "json"}
        if feedback_type not in valid_types:
            return ActionResult(
                outputs={},
                outcome="failure",
                error=f"Invalid feedback_type '{feedback_type}'. Must be one of: {valid_types}",
                error_type="ValidationError",
            )

        model = with_config.get("model")

        # Generate instructions via LLM
        system_prompt = """You are an expert at generating clear, step-by-step instructions.
Generate precise instructions that can be followed by a human operator.
Be specific about commands, paths, and expected outputs."""

        try:
            instructions = await self.llm_client.complete(
                prompt=prompt,
                system=system_prompt,
                model=model,
            )
        except Exception as e:
            return ActionResult(
                outputs={},
                outcome="failure",
                error=f"Failed to generate instructions: {e}",
                error_type="LLMError",
            )

        if exec_context:
            exec_context.log(
                "info",
                f"Generated instructions for '{step_id}', awaiting feedback"
            )

        # Build feedback prompt based on type
        feedback_prompts = {
            "text": "Please provide the result or output:",
            "multiline": "Please paste the output (multi-line supported):",
            "file_path": "Please provide the path to the output file:",
            "json": "Please provide the result as JSON:",
        }

        # Suspend for user feedback
        return ActionResult(
            outcome="suspended",
            outputs={
                # Suspension metadata for executor
                "__suspend_for_input": True,
                "__suspend_step_id": step_id,
                "__suspend_prompt": f"Instructions generated:\n\n{instructions}\n\n{feedback_prompts[feedback_type]}",
                "__suspend_input_type": "multiline" if feedback_type in ("multiline", "json") else "text",
                "__suspend_feedback_type": feedback_type,
                # Store instructions for later use
                "__instruct_instructions": instructions,
                "__instruct_analyze": with_config.get("analyze_feedback", False),
                # User-visible output
                "awaiting_feedback": True,
                "instructions": instructions,
                "feedback_type": feedback_type,
            }
        )

    async def _process_feedback(
        self,
        with_config: dict[str, Any],
        feedback: Any,
        evaluator: "ContextEvaluator",
        exec_context: Optional["ExecutionContext"],
    ) -> ActionResult:
        """Process user feedback, optionally analyzing with LLM."""
        # Get stored values from context (passed via resume)
        analyze_feedback = with_config.get("analyze_feedback", False)
        feedback_type = with_config.get("feedback_type", "text")
        model = with_config.get("model")

        # Handle file_path type - read the file
        if feedback_type == "file_path" and isinstance(feedback, str):
            import os
            try:
                with open(os.path.expanduser(feedback), "r") as f:
                    feedback_content = f.read()
            except Exception as e:
                return ActionResult(
                    outputs={"feedback": feedback, "error": str(e)},
                    outcome="failure",
                    error=f"Failed to read file at path: {e}",
                    error_type="FileError",
                )
        else:
            feedback_content = feedback

        # Handle JSON type - parse it
        parsed_json = None
        if feedback_type == "json" and isinstance(feedback_content, str):
            import json
            try:
                parsed_json = json.loads(feedback_content)
            except json.JSONDecodeError as e:
                return ActionResult(
                    outputs={"feedback": feedback_content, "parse_error": str(e)},
                    outcome="failure",
                    error=f"Invalid JSON in feedback: {e}",
                    error_type="ValidationError",
                )

        outputs: dict[str, Any] = {
            "feedback": feedback_content,
            "feedback_type": feedback_type,
        }

        if parsed_json is not None:
            outputs["parsed_json"] = parsed_json

        # Optionally analyze feedback with LLM
        if analyze_feedback:
            try:
                analysis_prompt = f"""Analyze the following feedback/output and extract key findings:

FEEDBACK:
{feedback_content}

Provide a structured analysis identifying:
1. Success/failure status
2. Key data points or findings
3. Any errors or issues
4. Recommended next steps"""

                analysis = await self.llm_client.complete(
                    prompt=analysis_prompt,
                    model=model,
                )
                outputs["feedback_analysis"] = analysis

                if exec_context:
                    exec_context.log("info", "Feedback analyzed successfully")

            except Exception as e:
                outputs["analysis_error"] = str(e)
                if exec_context:
                    exec_context.log("warning", f"Failed to analyze feedback: {e}")

        return ActionResult(
            outputs=outputs,
            outcome="success",
        )
