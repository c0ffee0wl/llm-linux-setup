"""
Workflow compiler - YAML workflow definition to Burr Application.

This module compiles YAML workflow definitions into executable Burr
Applications by:
1. Validating the workflow using Pydantic schemas
2. Creating Burr actions from step configurations
3. Building graph transitions (including loop cycles)
4. Adding cleanup/finally nodes

Burr API Notes (verified from /tmp/burr source):
- SingleStepAction.run_and_update(state, **kwargs) -> tuple[dict, State]
- Burr State is immutable - use state.update(**kwargs) to create new state
- Transitions are (from, to) or (from, to, Condition)
- Condition.when(**kwargs) creates key=value state checks
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union, TYPE_CHECKING
import asyncio
import re

from burr.core import ApplicationBuilder, default
from burr.core.action import Action, Condition, SingleStepAction
from burr.core.state import State

from .errors import (
    WorkflowCompilationError,
    WorkflowValidationError,
    ActionNotFoundError,
)
from .types import ActionResult as CoreActionResult, RESERVED_STATE_KEYS
from .parser import WorkflowParser, SourceLocation
from .adapters import BurrActionAdapter
from .guardrails import GuardrailRouter
from ..evaluator.security import validate_step_id, SecurityError

if TYPE_CHECKING:
    from ..protocols import ExecutionContext, LLMClient
    from ..actions.base import BaseAction


@dataclass
class CompiledStep:
    """Represents a compiled step with its Burr action and transitions.

    This bridges our action classes to Burr's explicit transition model.
    """

    name: str
    action: "BurrActionAdapter"
    transitions: list[tuple[str, Optional[Condition]]] = field(default_factory=list)
    # Original step config for actions needing full step definition
    step_config: Optional[dict] = None


class NoOpAction(SingleStepAction):
    """A no-op action for terminal states and placeholders."""

    def __init__(self, name: str = "__noop"):
        super().__init__()
        # Store name internally but don't set _name (with_name() will set it)
        self._noop_name = name

    # NOTE: Do NOT override name property - let Burr's with_name() set _name

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return []

    def run_and_update(self, state: State, **run_kwargs) -> tuple[dict, State]:
        return {"outcome": "complete"}, state


class CleanupAction(SingleStepAction):
    """Executes finally blocks and performs cleanup."""

    def __init__(
        self,
        finally_steps: list[dict],
        action_registry: "ActionRegistry",
        exec_context: Optional["ExecutionContext"] = None,
    ):
        super().__init__()
        # NOTE: Do NOT set self._name - Burr's with_name() will set it
        self.finally_steps = finally_steps
        self.action_registry = action_registry
        self.exec_context = exec_context

    # NOTE: Do NOT override name property - inherited from Action, set by with_name()

    @property
    def reads(self) -> list[str]:
        return ["inputs", "env", "steps", "__workflow_exit", "__workflow_failed"]

    @property
    def writes(self) -> list[str]:
        return ["__cleanup_complete", "__cleanup_warnings", "__cleanup_errors"]

    def run_and_update(self, state: State, **run_kwargs) -> tuple[dict, State]:
        """Execute all finally steps in order."""
        warnings = []
        errors = []

        ctx = dict(state.get_all())

        for step in self.finally_steps:
            try:
                action = self._get_action_for_step(step)
                if action:
                    # Execute cleanup step (simplified - sync only for cleanup)
                    loop = asyncio.new_event_loop()
                    try:
                        result = loop.run_until_complete(
                            action.execute(step, ctx, self.exec_context)
                        )
                    finally:
                        loop.close()
                    if result.outcome != "success":
                        warnings.append(
                            f"Finally step {step.get('id', 'unknown')} "
                            f"completed with outcome: {result.outcome}"
                        )
            except Exception as e:
                errors.append(f"Finally step failed: {e}")

        new_state = state.update(
            __cleanup_complete=True,
            __cleanup_warnings=warnings,
            __cleanup_errors=errors,
        )

        return {"cleanup_complete": True}, new_state

    def _get_action_for_step(self, step: dict) -> Optional["BaseAction"]:
        """Get the appropriate action for a step."""
        if "run" in step:
            return self.action_registry.get("shell")
        elif "uses" in step:
            action_type = step["uses"]
            return self.action_registry.get(action_type)
        return None


class WorkflowCompiler:
    """Compiles YAML workflow definitions to Burr Applications.

    The compiler translates high-level workflow YAML into a Burr state
    machine graph, handling:
    - Step → Action translation
    - Conditional execution (if:)
    - Loop compilation as graph cycles
    - Error handling (on_failure:)
    - Finally/cleanup blocks

    Usage:
        from burr_workflow.core.compiler import WorkflowCompiler
        from burr_workflow.actions import get_default_registry

        compiler = WorkflowCompiler(
            action_registry=get_default_registry(),
            exec_context=my_context,
        )

        app = compiler.compile(workflow_dict)
        # or with validation:
        app = compiler.compile_and_validate(workflow_yaml_str)
    """

    def __init__(
        self,
        action_registry: Optional["ActionRegistry"] = None,
        exec_context: Optional["ExecutionContext"] = None,
        llm_client: Optional["LLMClient"] = None,
        parser: Optional[WorkflowParser] = None,
    ):
        """Initialize the compiler.

        Args:
            action_registry: Registry of available actions
            exec_context: Execution context for shell/prompts
            llm_client: LLM client for llm/* actions
            parser: WorkflowParser for source tracking (created if not provided)
        """
        if action_registry is None:
            from ..actions import get_default_registry
            action_registry = get_default_registry(llm_client=llm_client)

        self.action_registry = action_registry
        self.exec_context = exec_context
        self.llm_client = llm_client
        self.parser = parser or WorkflowParser()
        self._compiled_steps: list[CompiledStep] = []
        self._step_ids: list[str] = []
        self._step_counter = 0

        # Create a shared GuardrailRouter for all steps
        self.guardrail_router = GuardrailRouter(llm_client=llm_client)

    def compile(
        self,
        workflow: dict,
        initial_state: Optional[dict] = None,
        app_id: Optional[str] = None,
    ) -> "Application":
        """Compile a workflow dictionary to a Burr Application.

        Args:
            workflow: Parsed YAML workflow as dictionary
            initial_state: Initial state values (inputs, env)
            app_id: Optional application ID for tracking

        Returns:
            Burr Application ready for execution

        Raises:
            WorkflowCompilationError: If compilation fails
        """
        self._compiled_steps = []
        self._step_counter = 0

        # Validate structure
        self._validate_workflow_structure(workflow)

        # Extract main job steps
        main_job = workflow.get("jobs", {}).get("main", {})
        steps = main_job.get("steps", [])

        if not steps:
            raise WorkflowCompilationError("Workflow has no steps in 'main' job")

        # Pre-compute step IDs for correct transition resolution
        self._step_ids = [self._generate_step_id(step, idx) for idx, step in enumerate(steps)]

        # Phase 1: Compile all steps
        for idx, step in enumerate(steps):
            self._compile_step(step, idx, len(steps))

        # Phase 2: Add cleanup node for finally blocks
        finally_steps = (
            main_job.get("finally", []) +
            workflow.get("finally", [])
        )
        self._add_cleanup_node(finally_steps)

        # Phase 3: Build Burr Application
        return self._build_application(workflow, initial_state, app_id)

    def _validate_workflow_structure(self, workflow: dict) -> None:
        """Basic structural validation."""
        if "name" not in workflow:
            raise WorkflowValidationError("Workflow must have a 'name' field")

        if "jobs" not in workflow:
            raise WorkflowValidationError("Workflow must have a 'jobs' section")

        if "main" not in workflow.get("jobs", {}):
            raise WorkflowValidationError(
                "Workflow must have a 'main' job in 'jobs' section"
            )

    def _generate_step_id(self, step: dict, index: int) -> str:
        """Generate a unique step ID with validation.

        Args:
            step: Step configuration dict
            index: Step index for auto-generated IDs

        Returns:
            Valid step ID

        Raises:
            WorkflowCompilationError: If step ID is reserved or invalid
        """
        if step.get("id"):
            step_id = step["id"]
        elif step.get("name"):
            # Convert name to valid ID
            name = re.sub(r"[^a-zA-Z0-9_]", "_", step["name"].lower())
            # Ensure starts with letter
            if name and not name[0].isalpha():
                name = f"s{name}"
            step_id = f"{name}_{index}"
        else:
            step_id = f"step_{index}"

        # Validate step ID against reserved names
        try:
            validate_step_id(step_id)
        except SecurityError as e:
            # Add source location if available
            location = self.parser.get_location(step, "id") or self.parser.get_location(step)
            raise WorkflowCompilationError(
                f"Invalid step ID '{step_id}': {e.message}",
                location=location,
            )

        return step_id

    def _compile_step(self, step: dict, index: int, total: int) -> None:
        """Compile a single step into CompiledStep(s).

        A step may compile to multiple CompiledSteps if it has:
        - A loop: (init, check, body, advance, finalize)
        - A condition: (condition check, body)
        """
        step_id = self._generate_step_id(step, index)

        # Handle loops
        if "loop" in step:
            self._compile_loop(step, step_id, index, total)
            return

        # Handle conditional steps
        if "if" in step:
            self._compile_conditional(step, step_id, index, total)
            return

        # Regular step
        action = self._get_action_for_step(step, step_id)
        next_step = self._get_next_step_name(index, total)

        transitions = [(next_step, None)]  # Default transition

        # Add on_failure transition
        if "on_failure" in step:
            failure_condition = self._create_failure_condition()
            transitions.insert(0, (step["on_failure"], failure_condition))

        compiled = CompiledStep(
            name=step_id,
            action=action,
            transitions=transitions,
            step_config=step,
        )
        self._compiled_steps.append(compiled)

    def _compile_loop(
        self, step: dict, step_id: str, index: int, total: int
    ) -> None:
        """Compile a loop step into multiple graph nodes.

        Uses direct SingleStepAction subclasses for optimal Burr integration.
        These nodes bypass the adapter pattern for better performance and
        native state handling.

        Loop Graph Structure:
            [prev] → [loop_init] → [loop_check] → [loop_body] → [loop_advance]
                                       ↓                              ↓
                                  [loop_finalize] ← ← ← ← ← ← [break/complete]
                                       ↓
                                  [next_step]
        """
        from ..actions.loop_nodes import (
            IteratorInitNode,
            IteratorCheckNode,
            IteratorAdvanceNode,
            IteratorFinalizeNode,
        )

        loop_expr = step["loop"]
        loop_id = f"{step_id}_loop"
        next_step = self._get_next_step_name(index, total)

        # Extract loop configuration
        max_iterations = step.get("max_iterations", 10000)
        max_results = step.get("max_results", 100)
        max_errors = step.get("max_errors", 50)
        continue_on_error = step.get("continue_on_error", False)
        aggregate_results = step.get("aggregate_results", True)
        break_if = step.get("break_if")
        # Result storage: "memory" (default), "file" (scalable), or "none"
        result_storage = step.get("result_storage", "memory")

        # 1. Init node - direct SingleStepAction, no adapter
        init_node = IteratorInitNode(
            items_expr=loop_expr,
            step_id=loop_id,
            check_node=f"{loop_id}_check",
            on_done=next_step,  # Skip to next step if empty
            max_iterations=max_iterations,
            max_results=max_results,
            max_errors=max_errors,
            continue_on_error=continue_on_error,
            aggregate_results=aggregate_results,
            result_storage=result_storage,
        )
        self._compiled_steps.append(CompiledStep(
            name=f"{loop_id}_init",
            action=init_node,
            transitions=[
                # Route based on __next set by the node
                (next_step, Condition.when(__next=next_step)),
                (f"{loop_id}_check", Condition.when(__next=f"{loop_id}_check")),
                (f"{loop_id}_check", None),  # Default: go to check
            ],
            step_config=step,
        ))

        # 2. Check node - routes to body or finalize via __next
        # NOTE: break_if is handled in IteratorAdvanceNode, not here
        check_node = IteratorCheckNode(
            step_id=loop_id,
            body_node=f"{loop_id}_body",
            finalize_node=f"{loop_id}_finalize",
        )
        self._compiled_steps.append(CompiledStep(
            name=f"{loop_id}_check",
            action=check_node,
            transitions=[
                (f"{loop_id}_finalize", Condition.when(__next=f"{loop_id}_finalize")),
                (f"{loop_id}_body", Condition.when(__next=f"{loop_id}_body")),
                (f"{loop_id}_body", None),  # Default: continue loop
            ],
            step_config=step,
        ))

        # 3. Body action (the actual step without loop:)
        # Uses LoopBodyAdapter for exception handling with continue_on_error
        from .adapters import LoopBodyAdapter

        body_step = {k: v for k, v in step.items() if k != "loop"}
        base_action = self._get_base_action_for_step(body_step, f"{loop_id}_body")

        # Extract retry configuration for loop body if present
        body_retry_config = None
        if "retry" in body_step:
            retry = body_step["retry"]
            body_retry_config = {
                "max_attempts": retry.get("max_attempts", 3),
                "backoff_base": retry.get("delay", 1.0),  # Schema: delay
                "backoff_multiplier": retry.get("backoff", 2.0),  # Schema: backoff
                "backoff_max": retry.get("max_delay", 60.0),  # Schema: max_delay
                "retry_on": retry.get("retry_on"),  # Error types to retry
                "jitter": retry.get("jitter", True),
            }

        # Extract guardrails for loop body if present
        body_guardrails = body_step.get("guardrails")

        body_action = LoopBodyAdapter(
            base_action=base_action,
            step_id=f"{loop_id}_body",
            step_config=body_step,
            exec_context=self.exec_context,
            continue_on_error=continue_on_error,
            retry_config=body_retry_config,
            timeout=body_step.get("timeout"),
            guardrails=body_guardrails,
            guardrail_router=self.guardrail_router if body_guardrails else None,
        )
        self._compiled_steps.append(CompiledStep(
            name=f"{loop_id}_body",
            action=body_action,
            transitions=[(f"{loop_id}_advance", None)],
            step_config=body_step,
        ))

        # 4. Advance node - routes back to check via __next
        # break_if is evaluated here AFTER body execution (correct semantics)
        advance_node = IteratorAdvanceNode(
            step_id=loop_id,
            body_step_id=f"{loop_id}_body",
            check_node=f"{loop_id}_check",
            finalize_node=f"{loop_id}_finalize",
            break_if=break_if,
        )
        self._compiled_steps.append(CompiledStep(
            name=f"{loop_id}_advance",
            action=advance_node,
            transitions=[
                (f"{loop_id}_finalize", Condition.when(__next=f"{loop_id}_finalize")),
                (f"{loop_id}_check", Condition.when(__next=f"{loop_id}_check")),
                (f"{loop_id}_check", None),  # Default: cycle back
            ],
            step_config=step,
        ))

        # 5. Finalize node - direct SingleStepAction, no adapter
        # Pass on_done for state hygiene (__next routing)
        finalize_node = IteratorFinalizeNode(step_id=loop_id, on_done=next_step)
        self._compiled_steps.append(CompiledStep(
            name=f"{loop_id}_finalize",
            action=finalize_node,
            transitions=[
                # Route based on __next (now set by finalize node)
                (next_step, Condition.when(__next=next_step)),
                (next_step, None),  # Fallback default
            ],
            step_config=step,
        ))

    def _compile_conditional(
        self, step: dict, step_id: str, index: int, total: int
    ) -> None:
        """Compile a conditional step (if:).

        Creates a condition check that either:
        - Executes the step body if condition is true
        - Skips to next step if condition is false
        """
        from ..actions.base import ConditionAction

        condition_expr = step["if"]
        next_step = self._get_next_step_name(index, total)

        # Create condition check action
        condition_action = ConditionAction(condition_expr=condition_expr)
        condition_adapter = BurrActionAdapter(
            base_action=condition_action,
            step_id=f"{step_id}_cond",
            step_config=step,
            exec_context=self.exec_context,
        )

        # Condition check node
        self._compiled_steps.append(CompiledStep(
            name=f"{step_id}_cond",
            action=condition_adapter,
            transitions=[
                (step_id, self._create_condition_true()),
                (next_step, None),  # Skip if condition false
            ],
            step_config=step,
        ))

        # Body step (without if:)
        body_step = {k: v for k, v in step.items() if k != "if"}
        body_action = self._get_action_for_step(body_step, step_id)
        self._compiled_steps.append(CompiledStep(
            name=step_id,
            action=body_action,
            transitions=[(next_step, None)],
            step_config=body_step,
        ))

    def _get_base_action_for_step(
        self, step: dict, step_id: str
    ) -> "BaseAction":
        """Get the base action for a step without wrapping in adapter.

        Args:
            step: Step configuration dict
            step_id: Step identifier for error messages

        Returns:
            BaseAction instance

        Raises:
            ActionNotFoundError: If action type not in registry
            WorkflowCompilationError: If step has no run or uses field
        """
        action = None

        if "run" in step:
            action = self.action_registry.get("shell")
        elif "uses" in step:
            action_type = step["uses"]
            action = self.action_registry.get(action_type)
            if action is None:
                raise ActionNotFoundError(action_type)
        else:
            raise WorkflowCompilationError(
                f"Step {step_id} has no 'run' or 'uses' field"
            )

        # Handle both class and instance returns from registry
        if isinstance(action, type):
            action = action()

        return action

    def _get_action_for_step(
        self, step: dict, step_id: str
    ) -> BurrActionAdapter:
        """Get the appropriate action for a step and wrap it in BurrActionAdapter."""
        action = self._get_base_action_for_step(step, step_id)

        # Extract retry configuration if present
        retry_config = None
        if "retry" in step:
            retry = step["retry"]
            retry_config = {
                "max_attempts": retry.get("max_attempts", 3),
                "backoff_base": retry.get("delay", 1.0),  # Schema: delay
                "backoff_multiplier": retry.get("backoff", 2.0),  # Schema: backoff
                "backoff_max": retry.get("max_delay", 60.0),  # Schema: max_delay
                "retry_on": retry.get("retry_on"),  # Error types to retry
                "jitter": retry.get("jitter", True),
            }

        # Extract timeout if present
        timeout = step.get("timeout")

        # Extract guardrails if present
        guardrails = step.get("guardrails")

        return BurrActionAdapter(
            base_action=action,
            step_id=step_id,
            step_config=step,
            exec_context=self.exec_context,
            retry_config=retry_config,
            timeout=timeout,
            guardrails=guardrails,
            guardrail_router=self.guardrail_router if guardrails else None,
        )

    def _get_next_step_name(self, current_index: int, total: int) -> str:
        """Get the name of the next step in sequence."""
        if current_index + 1 >= total:
            return "__cleanup__"

        # Use pre-computed step IDs for correct transition resolution
        return self._step_ids[current_index + 1]

    def _add_cleanup_node(self, finally_steps: list[dict]) -> None:
        """Add the cleanup node for finally blocks."""
        cleanup_action = CleanupAction(
            finally_steps=finally_steps,
            action_registry=self.action_registry,
            exec_context=self.exec_context,
        )

        self._compiled_steps.append(CompiledStep(
            name="__cleanup__",
            action=cleanup_action,
            transitions=[("__end__", None)],
        ))

    def _create_failure_condition(self) -> Condition:
        """Create a condition for failure transitions."""
        return Condition.when(__step_outcome="failure")

    def _create_loop_complete_condition(self, finalize_node: str) -> Condition:
        """Create a condition for loop completion using __next routing.

        Args:
            finalize_node: The finalize node name to transition to

        Returns:
            Condition that checks if __next points to finalize
        """
        return Condition.when(__next=finalize_node)

    def _create_condition_true(self) -> Condition:
        """Create a condition for when if: evaluates to true."""
        return Condition.when(__condition_met=True)

    def _create_cleanup_priority_condition(self) -> Condition:
        """Create a condition for cleanup priority transitions.

        This condition triggers cleanup when:
        - __workflow_exit is True (explicit exit requested)
        - __workflow_failed is True (step failure without on_failure handler)

        These transitions are added FIRST for each step, so they take
        priority over default transitions in Burr's first-match evaluation.
        """
        def check_cleanup_needed(state: State) -> bool:
            return (
                state.get("__workflow_exit", False) or
                state.get("__workflow_failed", False)
            )

        return Condition(
            keys=["__workflow_exit", "__workflow_failed"],
            resolver=check_cleanup_needed,
            name="cleanup_priority",
        )

    def _build_application(
        self,
        workflow: dict,
        initial_state: Optional[dict],
        app_id: Optional[str],
    ) -> "Application":
        """Build the final Burr Application from compiled steps.

        IMPORTANT: Cleanup transitions are added FIRST to ensure they take
        priority over default transitions. Burr evaluates transitions in order,
        and the first matching condition wins.
        """
        builder = ApplicationBuilder()

        # Add all actions
        actions = {}
        for compiled in self._compiled_steps:
            actions[compiled.name] = compiled.action
        actions["__end__"] = NoOpAction("__end__")

        builder = builder.with_actions(**actions)

        # PRIORITY: Add cleanup transitions FIRST for all steps
        # This ensures workflow exit/failure always routes to cleanup
        cleanup_condition = self._create_cleanup_priority_condition()
        for compiled in self._compiled_steps:
            # Skip internal nodes and cleanup itself
            if compiled.name.startswith("__"):
                continue
            builder = builder.with_transitions(
                (compiled.name, "__cleanup__", cleanup_condition)
            )

        # Add regular transitions after cleanup transitions
        for compiled in self._compiled_steps:
            for target, condition in compiled.transitions:
                if condition is None:
                    builder = builder.with_transitions(
                        (compiled.name, target, default)
                    )
                else:
                    builder = builder.with_transitions(
                        (compiled.name, target, condition)
                    )

        # Set entry point
        first_step = self._compiled_steps[0].name if self._compiled_steps else "__end__"
        builder = builder.with_entrypoint(first_step)

        # Set initial state
        if initial_state:
            from burr.core.state import State
            # Merge with input definitions for runtime validation
            merged_state = {
                **initial_state,
                "__input_definitions": workflow.get("inputs", {}),
            }
            state = State(merged_state)
            builder = builder.with_state(state)
        else:
            # Default empty state structure with input definitions
            builder = builder.with_state(State({
                "inputs": workflow.get("inputs_values", {}),
                "env": workflow.get("env", {}),
                "steps": {},
                "__input_definitions": workflow.get("inputs", {}),
            }))

        # Set app ID if provided
        if app_id:
            builder = builder.with_identifiers(app_id=app_id)

        return builder.build()

    def compile_from_yaml(
        self,
        yaml_content: str,
        initial_state: Optional[dict] = None,
        app_id: Optional[str] = None,
    ) -> "Application":
        """Compile workflow from YAML string.

        Uses WorkflowParser with source tracking for precise error messages.

        Args:
            yaml_content: YAML workflow definition
            initial_state: Initial state values
            app_id: Optional application ID

        Returns:
            Burr Application
        """
        workflow = self.parser.parse_string(yaml_content)
        return self.compile(workflow, initial_state, app_id)

    def compile_with_validation(
        self,
        workflow: Union[dict, str],
        initial_state: Optional[dict] = None,
        app_id: Optional[str] = None,
    ) -> "Application":
        """Compile workflow with Pydantic validation.

        Args:
            workflow: Workflow dict or YAML string
            initial_state: Initial state values
            app_id: Optional application ID

        Returns:
            Burr Application

        Raises:
            WorkflowValidationError: If validation fails
        """
        from ..schemas import WorkflowDefinition

        # Parse YAML if string
        if isinstance(workflow, str):
            try:
                from ruamel.yaml import YAML
                yaml = YAML(typ="safe")
                workflow = yaml.load(workflow)
            except ImportError:
                import yaml as pyyaml
                workflow = pyyaml.safe_load(workflow)

        # Validate with Pydantic
        try:
            validated = WorkflowDefinition.model_validate(workflow)
            # Convert back to dict for compilation
            workflow_dict = validated.model_dump(by_alias=True)
        except Exception as e:
            raise WorkflowValidationError(f"Schema validation failed: {e}")

        return self.compile(workflow_dict, initial_state, app_id)


# Type import for return type hints
if TYPE_CHECKING:
    from burr.core import Application
    from ..actions.registry import ActionRegistry
