"""
Guardrail system for output validation and control flow steering.

Guardrails validate step outputs and can route to different steps
based on validation results, supporting retry logic and pattern matching.
"""

import re
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocols import LLMClient


class GuardrailError(Exception):
    """Base exception for guardrail failures."""
    pass


class GuardrailAbort(GuardrailError):
    """Raised when guardrail validation fails and abort is requested."""
    pass


class GuardrailRetryExhausted(GuardrailError):
    """Raised when guardrail retry limit is exceeded."""
    pass


@dataclass
class ValidationResult:
    """Result of a guardrail validation."""
    passed: bool
    matched: bool = False  # For pattern-based guardrails
    error: Optional[str] = None
    details: Optional[dict[str, Any]] = None


class GuardrailRouter:
    """Apply guardrails and determine next step.

    Supports guardrail types:
    - regex: Match output against regex pattern
    - json_schema: Validate output against JSON schema
    - llm_judge: Use LLM to evaluate output quality
    - secrets_present: Detect potential secrets/credentials
    - pii: Detect personally identifiable information

    Routing actions:
    - on_fail: step_id - Route to step when validation fails
    - on_pass: step_id - Route to step when validation passes (default: next)
    - on_match: step_id - For pattern guardrails, route when pattern matches
    - retry: Re-run the step (with retry count tracking)
    """

    # Common secret patterns
    SECRET_PATTERNS = [
        r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\']?[\w-]{20,}',
        r'(?i)(secret|password|passwd|pwd)\s*[:=]\s*["\']?[^\s"\']{8,}',
        r'(?i)(token|bearer)\s*[:=]\s*["\']?[\w-]{20,}',
        r'(?i)aws[_-]?(access|secret)[_-]?key\s*[:=]\s*["\']?[\w/+=]{20,}',
        r'-----BEGIN (RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----',
        r'(?i)(ghp_|gho_|ghu_|ghs_|ghr_)[a-zA-Z0-9]{36}',  # GitHub tokens
        r'(?i)sk-[a-zA-Z0-9]{48}',  # OpenAI keys
        r'(?i)xox[baprs]-[\w-]+',  # Slack tokens
    ]

    # Common PII patterns
    PII_PATTERNS = [
        r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
        r'\b\d{16}\b',  # Credit card (basic)
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # Phone number
    ]

    def __init__(self, llm_client: Optional["LLMClient"] = None):
        """Initialize guardrail router.

        Args:
            llm_client: Optional LLM client for llm_judge guardrails
        """
        self.llm_client = llm_client
        self._retry_counts: dict[str, int] = {}

    def reset_retry_count(self, step_id: str) -> None:
        """Reset retry count for a step."""
        self._retry_counts.pop(step_id, None)

    def get_retry_count(self, step_id: str) -> int:
        """Get current retry count for a step."""
        return self._retry_counts.get(step_id, 0)

    async def validate_and_route(
        self,
        output: Any,
        guardrails: list[dict[str, Any]],
        context: dict[str, Any],
        step_id: str,
    ) -> str:
        """Validate output and return next step ID.

        Args:
            output: Step output to validate
            guardrails: List of guardrail configurations
            context: Workflow context (may be modified with retry info)
            step_id: Current step ID for retry tracking

        Returns:
            Next step ID: "next" for default, "__retry__" for retry,
            or specific step_id for routing

        Raises:
            GuardrailAbort: When validation fails with abort action
            GuardrailRetryExhausted: When retry limit exceeded
        """
        # Convert output to string for pattern matching
        output_str = self._to_string(output)

        for guard in guardrails:
            result = await self._validate(output_str, output, guard)

            guard_type = guard.get("type", "")

            # Pattern-based routing (regex, contains_string)
            if guard_type in ("regex", "contains_string", "regex_match"):
                if result.matched and "on_match" in guard:
                    return guard["on_match"]
                elif not result.matched and "on_no_match" in guard:
                    return guard["on_no_match"]

            # Standard pass/fail routing
            if not result.passed:
                action = guard.get("on_fail", "abort")

                if action == "retry":
                    return self._handle_retry(guard, step_id, context, result)
                elif action == "abort":
                    raise GuardrailAbort(
                        f"Guardrail '{guard_type}' failed for step '{step_id}': {result.error}"
                    )
                elif action == "continue":
                    # Log but continue
                    context["__guardrail_warning"] = result.error
                    continue
                else:
                    # Route to specified step_id
                    return action

            elif "on_pass" in guard:
                # Reset retry count on success
                self.reset_retry_count(step_id)
                return guard["on_pass"]

        # All guardrails passed
        self.reset_retry_count(step_id)
        return "next"

    def _handle_retry(
        self,
        guard: dict[str, Any],
        step_id: str,
        context: dict[str, Any],
        result: ValidationResult,
    ) -> str:
        """Handle retry logic with count tracking."""
        max_retries = guard.get("max_retries", 3)
        current_retries = self._retry_counts.get(step_id, 0)

        if current_retries >= max_retries:
            # Retry limit exhausted
            self._retry_counts[step_id] = 0  # Reset for next time
            exhausted_action = guard.get("on_retry_exhausted", "abort")

            if exhausted_action == "abort":
                raise GuardrailRetryExhausted(
                    f"Guardrail retry limit ({max_retries}) exhausted for step '{step_id}'. "
                    f"Last error: {result.error}"
                )
            elif exhausted_action == "skip":
                return "next"  # Skip this step, continue workflow
            else:
                return exhausted_action  # Route to specified step_id

        # Increment and retry
        self._retry_counts[step_id] = current_retries + 1
        context["__guardrail_retry_count"] = current_retries + 1
        context["__guardrail_error"] = result.error
        return "__retry__"

    async def _validate(
        self,
        output_str: str,
        output: Any,
        guard: dict[str, Any],
    ) -> ValidationResult:
        """Validate output against a single guardrail."""
        guard_type = guard.get("type", "")

        if guard_type == "regex" or guard_type == "regex_match":
            return self._validate_regex(output_str, guard)
        elif guard_type == "contains_string":
            return self._validate_contains(output_str, guard)
        elif guard_type == "json_schema":
            return self._validate_json_schema(output, guard)
        elif guard_type == "llm_judge":
            return await self._validate_llm_judge(output_str, guard)
        elif guard_type == "secrets_present":
            return self._validate_secrets(output_str)
        elif guard_type == "pii":
            return self._validate_pii(output_str)
        else:
            return ValidationResult(
                passed=True,
                error=f"Unknown guardrail type: {guard_type}"
            )

    def _validate_regex(
        self,
        output: str,
        guard: dict[str, Any],
    ) -> ValidationResult:
        """Validate against regex pattern."""
        pattern = guard.get("pattern", "")
        if not pattern:
            return ValidationResult(passed=True, error="No pattern specified")

        try:
            match = re.search(pattern, output)
            return ValidationResult(
                passed=True,  # Regex guardrails use on_match/on_no_match
                matched=match is not None,
                details={"pattern": pattern, "match": match.group() if match else None}
            )
        except re.error as e:
            return ValidationResult(
                passed=False,
                error=f"Invalid regex pattern: {e}"
            )

    def _validate_contains(
        self,
        output: str,
        guard: dict[str, Any],
    ) -> ValidationResult:
        """Check if output contains a string."""
        pattern = guard.get("pattern", "")
        if not pattern:
            return ValidationResult(passed=True, error="No pattern specified")

        matched = pattern in output
        return ValidationResult(
            passed=True,  # Contains guardrails use on_match/on_no_match
            matched=matched,
            details={"pattern": pattern, "found": matched}
        )

    def _validate_json_schema(
        self,
        output: Any,
        guard: dict[str, Any],
    ) -> ValidationResult:
        """Validate against JSON schema."""
        schema = guard.get("schema", {})
        if not schema:
            return ValidationResult(passed=True, error="No schema specified")

        try:
            import jsonschema
            jsonschema.validate(output, schema)
            return ValidationResult(passed=True)
        except ImportError:
            return ValidationResult(
                passed=True,
                error="jsonschema not installed, skipping validation"
            )
        except jsonschema.ValidationError as e:
            return ValidationResult(
                passed=False,
                error=f"Schema validation failed: {e.message}",
                details={"path": list(e.path), "schema_path": list(e.schema_path)}
            )

    async def _validate_llm_judge(
        self,
        output: str,
        guard: dict[str, Any],
    ) -> ValidationResult:
        """Use LLM to evaluate output quality."""
        if not self.llm_client:
            return ValidationResult(
                passed=True,
                error="No LLM client configured for llm_judge guardrail"
            )

        prompt = guard.get("prompt", "")
        if not prompt:
            return ValidationResult(passed=True, error="No prompt specified")

        try:
            full_prompt = f"""Evaluate the following output and determine if it passes the quality check.

OUTPUT TO EVALUATE:
{output[:5000]}  # Truncate for safety

EVALUATION CRITERIA:
{prompt}

Respond with ONLY "PASS" or "FAIL" followed by a brief reason."""

            response = await self.llm_client.complete(
                prompt=full_prompt,
                temperature=0.1,  # Low temperature for consistent judgment
            )

            passed = response.strip().upper().startswith("PASS")
            return ValidationResult(
                passed=passed,
                error=None if passed else response,
                details={"llm_response": response}
            )
        except Exception as e:
            return ValidationResult(
                passed=True,  # Don't fail on LLM errors
                error=f"LLM judge error: {e}"
            )

    def _validate_secrets(self, output: str) -> ValidationResult:
        """Detect potential secrets in output."""
        found_secrets = []

        for pattern in self.SECRET_PATTERNS:
            matches = re.findall(pattern, output)
            if matches:
                # Mask the actual values
                found_secrets.append({
                    "pattern": pattern[:30] + "...",
                    "count": len(matches)
                })

        if found_secrets:
            return ValidationResult(
                passed=False,
                error=f"Potential secrets detected: {len(found_secrets)} pattern(s) matched",
                details={"secrets_found": found_secrets}
            )

        return ValidationResult(passed=True)

    def _validate_pii(self, output: str) -> ValidationResult:
        """Detect personally identifiable information."""
        found_pii = []

        for pattern in self.PII_PATTERNS:
            matches = re.findall(pattern, output)
            if matches:
                found_pii.append({
                    "pattern_type": pattern[:20] + "...",
                    "count": len(matches)
                })

        if found_pii:
            return ValidationResult(
                passed=False,
                error=f"PII detected: {len(found_pii)} pattern(s) matched",
                details={"pii_found": found_pii}
            )

        return ValidationResult(passed=True)

    def _to_string(self, output: Any) -> str:
        """Convert output to string for pattern matching."""
        if isinstance(output, str):
            return output
        elif isinstance(output, dict):
            import json
            return json.dumps(output, indent=2)
        elif isinstance(output, (list, tuple)):
            import json
            return json.dumps(output, indent=2)
        else:
            return str(output)
