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
import copy
import random
import time
import traceback
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, Optional

from burr.core.action import SingleStepAction
from burr.core.state import State

from ..actions.base import ActionResult
from .types import RESERVED_STATE_KEYS

# Import new guard system (with graceful degradation)
try:
    from ..guard import LLM_GUARD_AVAILABLE, GuardError, GuardScanner
except ImportError:
    LLM_GUARD_AVAILABLE = False
    GuardScanner = None  # type: ignore[misc, assignment]
    GuardError = Exception  # type: ignore[misc, assignment]

if TYPE_CHECKING:
    from ..actions.base import BaseAction
    from ..protocols import ExecutionContext
    from ..schemas.models import GuardrailsConfig, LLMDefaultsConfig

# Default values when neither workflow nor step specifies
DEFAULT_ON_FAIL = "abort"
DEFAULT_MAX_RETRIES = 2


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
        retry_config: dict | None = None,
        timeout: float | None = None,
        guardrails_config: Optional["GuardrailsConfig"] = None,
        guard_scanner: Optional["GuardScanner"] = None,
        workflow_llm_config: Optional["LLMDefaultsConfig"] = None,
    ):
        """Initialize the Burr action adapter.

        Args:
            base_action: The action implementing BaseAction protocol
            step_id: Unique identifier for this step
            step_config: Original YAML step configuration
            exec_context: Execution context for shell/prompts/logging
            retry_config: Optional retry configuration dict with keys:
                - max_attempts: Number of attempts (default 3)
                - backoff_base: Base delay in seconds (default 1.0)
                - backoff_max: Maximum delay (default 60.0)
                - jitter: Add randomness to backoff (default True)
            timeout: Optional per-step timeout in seconds
            guardrails_config: Optional GuardrailsConfig for LLM Guard scanning
            guard_scanner: Optional GuardScanner instance for LLM Guard
            workflow_llm_config: Optional workflow-level LLM configuration
        """
        super().__init__()
        self.base_action = base_action
        self.step_id = step_id
        self.step_config = step_config
        self.exec_context = exec_context
        self.retry_config = retry_config
        self.timeout = timeout
        # LLM Guard integration
        self.guardrails_config: GuardrailsConfig | None = guardrails_config
        self.guard_scanner: GuardScanner | None = guard_scanner
        # Workflow-level LLM configuration
        self.workflow_llm_config: LLMDefaultsConfig | None = workflow_llm_config
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
    ) -> tuple[dict, State] | Coroutine[Any, Any, tuple[dict, State]]:
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
        """Execute async action with optional timeout and retry support."""
        if self.timeout:
            try:
                return await asyncio.wait_for(
                    self._async_execute_with_retry(state, **run_kwargs),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                result = ActionResult(
                    outcome="failure",
                    outputs={},
                    error=f"Step '{self.step_id}' timed out after {self.timeout}s",
                    error_type="TimeoutError",
                )
                return self._apply_result_to_state(state, result)
        else:
            return await self._async_execute_with_retry(state, **run_kwargs)

    async def _async_execute_with_retry(
        self, state: State, **run_kwargs
    ) -> tuple[dict, State]:
        """Execute async action with retry support and guardrails."""
        # Deep copy to prevent action mutations from leaking to Burr state
        ctx = copy.deepcopy(dict(state.get_all()))

        # Retry configuration
        max_attempts = 1
        backoff_base = 1.0
        backoff_multiplier = 2.0
        backoff_max = 60.0
        jitter = True
        retry_on = None  # None = default retryable errors

        if self.retry_config:
            max_attempts = self.retry_config.get("max_attempts", 3)
            backoff_base = self.retry_config.get("backoff_base", 1.0)
            backoff_multiplier = self.retry_config.get("backoff_multiplier", 2.0)
            backoff_max = self.retry_config.get("backoff_max", 60.0)
            jitter = self.retry_config.get("jitter", True)
            retry_on = self.retry_config.get("retry_on")  # User-specified error types

        last_error = None
        last_result = None

        for attempt in range(max_attempts):
            try:
                # NEW: Input guardrails (LLM Guard) - scan BEFORE execution
                step_config_for_execution = self.step_config
                if self.guardrails_config and self.guard_scanner:
                    try:
                        step_config_for_execution, should_continue = await self._scan_input_guardrails(
                            self.step_config, ctx
                        )
                        if not should_continue:
                            result = ActionResult(
                                outputs={},
                                outcome="skipped",
                                error="Input guardrail blocked",
                            )
                            return self._apply_result_to_state(state, result)
                    except GuardError as e:
                        result = ActionResult(
                            outputs={},
                            outcome="failure",
                            error=str(e),
                            error_type="GuardError",
                        )
                        return self._apply_result_to_state(state, result)

                # Merge workflow-level LLM config for llm/* actions
                action_type = step_config_for_execution.get("uses", "")
                if action_type.startswith("llm/") and self.workflow_llm_config:
                    step_config_for_execution = self._apply_llm_config(
                        step_config_for_execution, action_type
                    )

                # Track execution time
                start_time = time.monotonic()
                result = await self.base_action.execute(
                    step_config=step_config_for_execution,
                    context=ctx,
                    exec_context=self.exec_context,
                )
                # Set duration if not already set by action
                if result.duration_ms is None:
                    result.duration_ms = (time.monotonic() - start_time) * 1000
                last_result = result

                # Success or non-failure outcome - apply guardrails if configured
                if result.outcome != "failure":
                    # NEW: Output guardrails (LLM Guard) - scan AFTER execution
                    if self.guardrails_config and self.guard_scanner:
                        try:
                            result = await self._scan_output_guardrails(result, ctx)
                        except GuardError as e:
                            result = ActionResult(
                                outputs={},
                                outcome="failure",
                                error=str(e),
                                error_type="GuardError",
                            )
                            return self._apply_result_to_state(state, result)

                        # Propagate guard state from context to result outputs
                        # This ensures vault state persists across steps for anonymize→deanonymize flow
                        guard_state_keys = ("__guard_vault", "__guard_input_content", "__guard_scan_results")
                        for key in guard_state_keys:
                            if key in ctx:
                                result.outputs[key] = ctx[key]

                    output, new_state = self._apply_result_to_state(state, result)
                    return output, new_state

                # Failure but no retry config - return immediately
                if not self.retry_config:
                    return self._apply_result_to_state(state, result)

                # Check if error is retryable
                if not self._is_retryable_error(result, retry_on):
                    return self._apply_result_to_state(state, result)

                last_error = result.error

            except Exception as e:
                last_error = str(e)
                if attempt == max_attempts - 1:
                    raise

            # Backoff before retry (if not last attempt)
            if attempt < max_attempts - 1:
                delay = min(backoff_base * (backoff_multiplier ** attempt), backoff_max)
                if jitter:
                    delay *= (0.5 + random.random())
                await asyncio.sleep(delay)

                if self.exec_context:
                    self.exec_context.log(
                        "warning",
                        f"Retrying '{self.step_id}' (attempt {attempt + 2}/{max_attempts})"
                    )

        # All retries exhausted - return last result or create failure
        if last_result:
            return self._apply_result_to_state(state, last_result)

        result = ActionResult(
            outcome="failure",
            outputs={},
            error=f"Max retries ({max_attempts}) exceeded. Last error: {last_error}",
            error_type="RetryExhaustedError",
        )
        return self._apply_result_to_state(state, result)

    def _is_retryable_error(
        self,
        result: ActionResult,
        retry_on: list[str] | None = None,
    ) -> bool:
        """Check if error should trigger retry.

        Args:
            result: The action result with error information
            retry_on: User-specified error types to retry on (None = use defaults)

        Returns:
            True if the error is retryable
        """
        if retry_on is not None:
            # User specified which errors to retry
            return result.error_type in retry_on

        # Default retryable error types (network/transient issues)
        default_retryable = {
            "TimeoutError",
            "ConnectionError",
            "HTTPError",
            "ConnectionRefusedError",
            "ConnectionResetError",
            "OSError",
            "timeout",  # Common string representation
            "connection_error",
            # HTTP retryable errors (429 Too Many Requests, 5xx Server Errors)
            "HTTPRetryableError",  # 429, 502, 503, 504
            "HTTPServerError",     # 5xx
        }
        return result.error_type in default_retryable

    def _sync_execute_and_update(
        self, state: State, **run_kwargs
    ) -> tuple[dict, State]:
        """Execute sync action with retry support (timeout not supported for sync)."""
        # Deep copy to prevent action mutations from leaking to Burr state
        ctx = copy.deepcopy(dict(state.get_all()))

        # Retry configuration
        max_attempts = 1
        backoff_base = 1.0
        backoff_multiplier = 2.0
        backoff_max = 60.0
        jitter = True
        retry_on = None  # None = default retryable errors

        if self.retry_config:
            max_attempts = self.retry_config.get("max_attempts", 3)
            backoff_base = self.retry_config.get("backoff_base", 1.0)
            backoff_multiplier = self.retry_config.get("backoff_multiplier", 2.0)
            backoff_max = self.retry_config.get("backoff_max", 60.0)
            jitter = self.retry_config.get("jitter", True)
            retry_on = self.retry_config.get("retry_on")  # User-specified error types

        last_error = None
        last_result = None

        for attempt in range(max_attempts):
            try:
                # Track execution time
                start_time = time.monotonic()
                result = self.base_action.execute(
                    step_config=self.step_config,
                    context=ctx,
                    exec_context=self.exec_context,
                )

                # Handle if action accidentally returned a coroutine in sync context
                if asyncio.iscoroutine(result):
                    # Check if we're already in an event loop
                    try:
                        running_loop = asyncio.get_running_loop()
                    except RuntimeError:
                        running_loop = None

                    if running_loop is not None:
                        # Already in async context - this is a programming error
                        result.close()  # Clean up the coroutine
                        raise RuntimeError(
                            f"Action {self.name} returned a coroutine in sync context "
                            "while an event loop is running. The action should be marked "
                            "as async or the execute() method should be synchronous."
                        )

                    # No running loop - use asyncio.run() (Python 3.7+)
                    result = asyncio.run(result)

                # Set duration if not already set by action
                if result.duration_ms is None:
                    result.duration_ms = (time.monotonic() - start_time) * 1000
                last_result = result

                # Success or non-failure outcome - return immediately
                if result.outcome != "failure":
                    return self._apply_result_to_state(state, result)

                # Failure but no retry config - return immediately
                if not self.retry_config:
                    return self._apply_result_to_state(state, result)

                # Check if error is retryable
                if not self._is_retryable_error(result, retry_on):
                    return self._apply_result_to_state(state, result)

                last_error = result.error

            except Exception as e:
                last_error = str(e)
                if attempt == max_attempts - 1:
                    raise

            # Backoff before retry (if not last attempt)
            if attempt < max_attempts - 1:
                delay = min(backoff_base * (backoff_multiplier ** attempt), backoff_max)
                if jitter:
                    delay *= (0.5 + random.random())
                time.sleep(delay)

                if self.exec_context:
                    self.exec_context.log(
                        "warning",
                        f"Retrying '{self.step_id}' (attempt {attempt + 2}/{max_attempts})"
                    )

        # All retries exhausted - return last result or create failure
        if last_result:
            return self._apply_result_to_state(state, last_result)

        result = ActionResult(
            outcome="failure",
            outputs={},
            error=f"Max retries ({max_attempts}) exceeded. Last error: {last_error}",
            error_type="RetryExhaustedError",
        )
        return self._apply_result_to_state(state, result)

    def _apply_result_to_state(
        self, state: State, result: ActionResult
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
        if result.duration_ms is not None:
            step_result["duration_ms"] = result.duration_ms

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
            # Suspension control (for human input)
            "__suspend_for_input", "__suspend_step_id", "__suspend_prompt",
            "__suspend_input_type", "__suspend_choices", "__suspend_timeout",
            "__suspend_default", "__suspend_feedback_type",
            # Guard state (LLM Guard integration) - persisted for anonymize/deanonymize flow
            "__guard_vault", "__guard_input_content", "__guard_scan_results", "__guard_warning",
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

    # =========================================================================
    # LLM Configuration Methods
    # =========================================================================

    def _apply_llm_config(self, step_config: dict, action_type: str) -> dict:
        """Apply workflow-level LLM config to step config.

        Merges workflow-level LLM defaults with step-level config,
        where step-level takes precedence.

        Args:
            step_config: Original step configuration.
            action_type: Action type string (e.g., "llm/extract").

        Returns:
            Modified step configuration with merged LLM settings.
        """
        from .llm_config import resolve_llm_config

        with_config = step_config.get("with", {})
        merged = resolve_llm_config(with_config, action_type, self.workflow_llm_config)

        # Only modify if there are changes
        if not merged:
            return step_config

        # Create new config with merged LLM settings
        new_config = copy.deepcopy(step_config)
        new_with = new_config.setdefault("with", {})

        # Apply merged values (only if not already in step config)
        for key in ("model", "temperature", "max_tokens"):
            if key in merged and key not in with_config:
                new_with[key] = merged[key]

        return new_config

    # =========================================================================
    # LLM Guard Integration Methods
    # =========================================================================

    async def _scan_input_guardrails(
        self,
        step_config: dict,
        context: dict,
    ) -> tuple[dict, bool]:
        """Scan input before step execution using LLM Guard.

        Args:
            step_config: Step configuration to scan.
            context: Workflow context (for vault state).

        Returns:
            Tuple of (modified step_config with sanitized values, should_continue).

        Raises:
            GuardError: If guardrail fails with abort action.
        """
        if not self.guardrails_config or not self.guardrails_config.input:
            return step_config, True

        # Restore vault state from previous step (for anonymize→deanonymize flow)
        if "__guard_vault" in context and hasattr(self.guard_scanner, '_vault_manager'):
            self.guard_scanner._vault_manager.restore(context["__guard_vault"])

        input_content = self._extract_scannable_input(step_config, context)
        if not input_content:
            return step_config, True

        scan_result = self.guard_scanner.scan_input(input_content, self.guardrails_config.input)

        # Store for output relevance check
        context["__guard_input_content"] = input_content
        context["__guard_scan_results"] = {"input": scan_result.details}

        if not scan_result.passed:
            on_fail = self.guardrails_config.on_fail or DEFAULT_ON_FAIL
            if on_fail == "abort":
                raise GuardError(f"Input guardrail failed: {scan_result.failed_scanners}")
            elif on_fail == "continue":
                context["__guard_warning"] = scan_result.failed_scanners
                return step_config, True
            # retry handled at adapter level

        # Persist vault state after input scanning (anonymize populates the vault)
        if hasattr(self.guard_scanner, '_vault_manager'):
            context["__guard_vault"] = self.guard_scanner._vault_manager.serialize()

        # Apply sanitized input back to step_config
        return self._apply_sanitized_input(step_config, scan_result.sanitized), True

    async def _scan_output_guardrails(
        self,
        result: ActionResult,
        context: dict,
    ) -> ActionResult:
        """Scan output after step execution using LLM Guard.

        Args:
            result: Action result to scan.
            context: Workflow context (for vault state).

        Returns:
            Modified ActionResult with sanitized output.

        Raises:
            GuardError: If guardrail fails with abort action.
        """
        config = self.guardrails_config
        if not config or not config.output or result.outcome != "success":
            return result

        output_content = self._extract_scannable_output(result)
        input_content = context.get("__guard_input_content", "")

        scan_result = self.guard_scanner.scan_output(
            input_content, output_content, config.output
        )

        context.setdefault("__guard_scan_results", {})["output"] = scan_result.details

        if not scan_result.passed:
            on_fail = config.on_fail or DEFAULT_ON_FAIL
            if on_fail == "abort":
                raise GuardError(f"Output guardrail failed: {scan_result.failed_scanners}")
            elif on_fail == "continue":
                context["__guard_warning"] = scan_result.failed_scanners
                return result
            # retry handled at adapter level

        # Persist vault state after output scanning (for deanonymize flow)
        if hasattr(self.guard_scanner, '_vault_manager'):
            context["__guard_vault"] = self.guard_scanner._vault_manager.serialize()

        # Apply sanitized output
        return self._apply_sanitized_output(result, scan_result.sanitized)

    def _extract_scannable_input(self, step_config: dict, context: dict) -> str:
        """Extract input content for scanning based on step type.

        Args:
            step_config: Step configuration.
            context: Workflow context.

        Returns:
            String content to scan.
        """
        import json

        # LLM actions: use prompt/content field
        if step_config.get("uses", "").startswith("llm/"):
            with_config = step_config.get("with", {})
            return with_config.get("prompt") or with_config.get("content") or ""

        # Shell/script: use run command
        if "run" in step_config:
            run_val = step_config["run"]
            if isinstance(run_val, list):
                return " ".join(run_val)
            return run_val

        # HTTP: use URL + body
        if step_config.get("uses") == "http/request":
            with_config = step_config.get("with", {})
            parts = [with_config.get("url", "")]
            if with_config.get("body"):
                parts.append(str(with_config["body"]))
            if with_config.get("json"):
                parts.append(json.dumps(with_config["json"]))
            return "\n".join(parts)

        return ""

    def _extract_scannable_output(self, result: ActionResult) -> str:
        """Extract output content for scanning based on result.

        Args:
            result: Action result.

        Returns:
            String content to scan.
        """
        import json

        outputs = result.outputs

        # LLM actions: response/result/text
        for key in ("response", "result", "text", "content"):
            if key in outputs and isinstance(outputs[key], str):
                return outputs[key]

        # Shell: stdout
        if "stdout" in outputs:
            return outputs["stdout"]

        # HTTP: text or json
        if "text" in outputs:
            return outputs["text"]
        if "json" in outputs:
            return json.dumps(outputs["json"])

        # Fallback: stringify all outputs
        return json.dumps(outputs)

    def _apply_sanitized_input(self, step_config: dict, sanitized: str) -> dict:
        """Apply sanitized input back to step config.

        Replaces the original input content with sanitized version.

        Args:
            step_config: Original step configuration.
            sanitized: Sanitized input string.

        Returns:
            Modified step configuration.
        """
        config = copy.deepcopy(step_config)

        # LLM actions: update prompt/content field
        if config.get("uses", "").startswith("llm/"):
            with_config = config.setdefault("with", {})
            if "prompt" in with_config:
                with_config["prompt"] = sanitized
            elif "content" in with_config:
                with_config["content"] = sanitized

        # Shell/script: update run command (be careful with arrays)
        elif "run" in config:
            if isinstance(config["run"], str):
                config["run"] = sanitized
            # For array syntax, sanitization is tricky - skip to avoid breaking args

        return config

    def _apply_sanitized_output(self, result: ActionResult, sanitized: str) -> ActionResult:
        """Apply sanitized output to result.

        Replaces the original output content with sanitized version.

        Args:
            result: Original action result.
            sanitized: Sanitized output string.

        Returns:
            Modified ActionResult.
        """
        outputs = dict(result.outputs)

        # LLM actions: response/result/text
        for key in ("response", "result", "text", "content"):
            if key in outputs and isinstance(outputs[key], str):
                outputs[key] = sanitized
                break
        else:
            # Shell: stdout
            if "stdout" in outputs:
                outputs["stdout"] = sanitized
            # HTTP: text
            elif "text" in outputs:
                outputs["text"] = sanitized

        return ActionResult(
            outputs=outputs,
            outcome=result.outcome,
            error=result.error,
            error_type=result.error_type,
            duration_ms=result.duration_ms,
        )

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
        retry_config: dict | None = None,
        timeout: float | None = None,
        guardrails_config: Optional["GuardrailsConfig"] = None,
        guard_scanner: Optional["GuardScanner"] = None,
        workflow_llm_config: Optional["LLMDefaultsConfig"] = None,
    ):
        """Initialize the loop body adapter.

        Args:
            base_action: The action implementing BaseAction protocol
            step_id: Unique identifier for this step
            step_config: Original YAML step configuration
            exec_context: Execution context for shell/prompts/logging
            continue_on_error: Whether to catch exceptions and continue loop
            retry_config: Optional retry configuration (passed to parent)
            timeout: Optional per-step timeout (passed to parent)
            guardrails_config: Optional GuardrailsConfig for LLM Guard (passed to parent)
            guard_scanner: Optional GuardScanner instance (passed to parent)
            workflow_llm_config: Optional workflow-level LLM config (passed to parent)
        """
        super().__init__(
            base_action=base_action,
            step_id=step_id,
            step_config=step_config,
            exec_context=exec_context,
            retry_config=retry_config,
            timeout=timeout,
            guardrails_config=guardrails_config,
            guard_scanner=guard_scanner,
            workflow_llm_config=workflow_llm_config,
        )
        self.continue_on_error = continue_on_error

    def run_and_update(
        self, state: State, **run_kwargs
    ) -> tuple[dict, State] | Coroutine[Any, Any, tuple[dict, State]]:
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
