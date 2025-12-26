"""
LLM client bridge for llm-assistant integration.

This module implements the LLMClient protocol from burr_workflow,
bridging workflow LLM actions to llm-assistant's model capabilities.
"""

from typing import Any, Optional, TYPE_CHECKING
import json
import re

if TYPE_CHECKING:
    from llm import Conversation, Model


class LLMSchemaValidationError(Exception):
    """Raised when LLM output doesn't match expected schema."""
    pass


class LLMChoiceError(Exception):
    """Raised when LLM doesn't select a valid choice."""
    pass


class AssistantLLMClient:
    """
    LLMClient implementation for llm-assistant.
    
    Uses the session's model which handles:
    - Context window management
    - Token counting
    - API key configuration
    - Model selection (GPT-4, Claude, Gemini, etc.)
    
    Example:
        llm_client = AssistantLLMClient(
            model=session.model,
            conversation=session.conversation,
        )
        
        compiler = WorkflowCompiler(llm_client=llm_client)
    """
    
    def __init__(
        self,
        model: "Model",
        conversation: Optional["Conversation"] = None,
        default_temperature: float = 0.7,
    ):
        """Initialize the LLM client.
        
        Args:
            model: llm.Model instance
            conversation: Optional conversation for context
            default_temperature: Default sampling temperature
        """
        self.model = model
        self.conversation = conversation
        self.default_temperature = default_temperature
    
    async def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate text completion.
        
        Args:
            prompt: The user prompt
            system: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Returns:
            Generated text response
        """
        import asyncio
        
        temp = temperature if temperature is not None else self.default_temperature
        
        # Build options
        options = {}
        if max_tokens:
            options["max_tokens"] = max_tokens
        
        # Note: llm library uses sync API, so we run in executor
        def _sync_complete():
            # Use direct prompt method
            response = self.model.prompt(
                prompt,
                system=system,
                temperature=temp,
            )
            return response.text()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_complete)
    
    async def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        system: Optional[str] = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Generate and validate JSON output.
        
        Args:
            prompt: The user prompt describing what to extract
            schema: JSON Schema defining expected output structure
            system: Optional system prompt
            max_retries: Retry count for validation failures
            
        Returns:
            Parsed and validated JSON object
            
        Raises:
            LLMSchemaValidationError: If output doesn't match schema after retries
        """
        # Build JSON-focused system prompt
        json_system = (system or "") + """

IMPORTANT: Respond with valid JSON only. No markdown code blocks, no explanation.
The response must be a valid JSON object that can be parsed with json.loads()."""
        
        # Include schema in prompt for guidance
        full_prompt = f"""{prompt}

Expected output format (JSON Schema):
{json.dumps(schema, indent=2)}

Respond with JSON matching this schema."""
        
        for attempt in range(max_retries):
            response = await self.complete(
                prompt=full_prompt if attempt == 0 else prompt,
                system=json_system,
                temperature=0.2,  # Lower temp for structured output
            )
            
            try:
                # Parse JSON
                text = self._extract_json(response)
                parsed = json.loads(text)
                
                # Validate against schema if jsonschema available
                try:
                    import jsonschema
                    jsonschema.validate(parsed, schema)
                except ImportError:
                    pass  # Skip validation if jsonschema not installed
                except jsonschema.ValidationError as e:
                    if attempt == max_retries - 1:
                        raise LLMSchemaValidationError(
                            f"Schema validation failed: {e.message}"
                        )
                    # Retry with error feedback
                    prompt = f"""Previous response was invalid: {e.message}

Please fix and try again. {prompt}"""
                    continue
                
                return parsed
                
            except json.JSONDecodeError as e:
                if attempt == max_retries - 1:
                    raise LLMSchemaValidationError(
                        f"Failed to parse JSON after {max_retries} attempts: {e}"
                    )
                # Retry with error feedback
                prompt = f"""Previous response was not valid JSON: {e}

Please respond with valid JSON only. {prompt}"""
        
        # Should not reach here
        raise LLMSchemaValidationError("Failed to get valid JSON response")
    
    async def complete_choice(
        self,
        prompt: str,
        choices: list[str],
        system: Optional[str] = None,
    ) -> str:
        """Select from predefined choices.
        
        Args:
            prompt: The decision prompt
            choices: Valid choices to select from
            system: Optional system prompt
            
        Returns:
            Selected choice (guaranteed to be in choices list)
            
        Raises:
            LLMChoiceError: If LLM returns invalid choice after retries
        """
        choices_str = ", ".join(f'"{c}"' for c in choices)
        
        choice_system = (system or "") + f"""

IMPORTANT: You must respond with exactly one of these choices: {choices_str}
Respond with ONLY the choice text, nothing else. No explanation, no punctuation."""
        
        for attempt in range(3):
            response = await self.complete(
                prompt=prompt,
                system=choice_system,
                temperature=0.1,  # Very low temp for determinism
            )
            
            # Normalize and match
            response_clean = response.strip().strip('"\'').lower()
            
            # Exact match
            for choice in choices:
                if choice.lower() == response_clean:
                    return choice
            
            # Partial match (response contains a choice)
            for choice in choices:
                if choice.lower() in response_clean:
                    return choice
            
            # Try to find any choice word in response
            response_words = set(response_clean.split())
            for choice in choices:
                if choice.lower() in response_words:
                    return choice
        
        raise LLMChoiceError(
            f"LLM did not select a valid choice from {choices}. Got: {response}"
        )
    
    def _extract_json(self, text: str) -> str:
        """Extract JSON from potentially markdown-wrapped response."""
        text = text.strip()
        
        # Handle markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            # Skip opening fence
            start = 1
            if lines[0].startswith("```json"):
                start = 1
            elif lines[0] == "```":
                start = 1
            
            # Find closing fence
            end = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == "```":
                    end = i
                    break
            
            text = "\n".join(lines[start:end])
        
        # Try to find JSON object or array
        # Match outermost braces/brackets
        brace_match = re.search(r'\{[\s\S]*\}', text)
        if brace_match:
            return brace_match.group()
        
        bracket_match = re.search(r'\[[\s\S]*\]', text)
        if bracket_match:
            return bracket_match.group()
        
        return text.strip()
