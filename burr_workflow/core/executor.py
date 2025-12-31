"""
Workflow executor - High-level workflow execution API.

The WorkflowExecutor provides a higher-level abstraction over Burr's
Application execution, handling:
- Persistence and resume from checkpoints
- Suspension for human input
- Progress tracking and callbacks
- Graceful interruption (Ctrl+C handling)
- Timeout management

Usage:
    from burr_workflow import WorkflowCompiler, WorkflowExecutor
    
    compiler = WorkflowCompiler()
    app = compiler.compile(workflow_dict)
    
    executor = WorkflowExecutor()
    result = await executor.run(app, inputs={"target": "example.com"})
"""

import asyncio
import logging
import signal
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from burr.core.state import State

from .errors import (
    WorkflowExecutionError,
)
from .hooks import StepTimingHook

if TYPE_CHECKING:
    from burr.core import Application

    from ..protocols import AuditLogger, ExecutionContext, OutputHandler

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    """Status of workflow execution."""
    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"  # Waiting for input
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    TIMEOUT = "timeout"


@dataclass
class StepProgress:
    """Progress information for a single step."""
    step_id: str
    step_name: str | None
    status: str  # "pending", "running", "completed", "failed", "skipped"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    outcome: str | None = None
    error: str | None = None


@dataclass
class ExecutionProgress:
    """Overall execution progress."""
    workflow_name: str
    status: ExecutionStatus
    workflow_version: str | None = None
    current_step: str | None = None
    steps_completed: int = 0
    steps_total: int = 0
    started_at: datetime | None = None
    step_history: list[StepProgress] = field(default_factory=list)
    
    @property
    def percent_complete(self) -> float:
        """Percentage of completion (0.0 to 100.0)."""
        if self.steps_total == 0:
            return 0.0
        return (self.steps_completed / self.steps_total) * 100.0


@dataclass
class SuspensionRequest:
    """Request for workflow suspension (e.g., human input needed)."""
    step_id: str
    suspension_type: str  # "input", "approval", "review"
    prompt: str
    options: list[str] | None = None
    default: str | None = None
    timeout: int | None = None
    # For resume: store state and app reference
    suspended_state: dict[str, Any] | None = None
    app: Optional["Application"] = None


@dataclass
class ExecutionResult:
    """Result of workflow execution."""
    status: ExecutionStatus
    final_state: dict[str, Any]
    outputs: dict[str, Any]
    progress: ExecutionProgress
    error: str | None = None
    suspension: SuspensionRequest | None = None
    
    @property
    def success(self) -> bool:
        """Check if execution completed successfully."""
        return self.status == ExecutionStatus.COMPLETED
    
    @property
    def failed(self) -> bool:
        """Check if execution failed."""
        return self.status == ExecutionStatus.FAILED
    
    @property
    def suspended(self) -> bool:
        """Check if execution is suspended waiting for input."""
        return self.status == ExecutionStatus.SUSPENDED


# Callback types
ProgressCallback = Callable[[ExecutionProgress], None]
StepCallback = Callable[[str, str, dict | None], None]  # step_id, status, outputs


class WorkflowExecutor:
    """Executes compiled Burr workflow applications.

    The executor provides a higher-level API for running workflows with:
    - Progress tracking via callbacks
    - Graceful interruption handling (Ctrl+C)
    - Timeout management at workflow and step level
    - Suspension for human input requests
    - Audit logging (FileAuditLogger)

    Note:
        For checkpoint/resume and Burr web UI tracking, configure these
        at compile time via WorkflowCompiler.compile():
        - db_path: SQLite database for state checkpointing
        - enable_tracking: Enable Burr web UI at http://localhost:7241

    Example:
        # Compile with persistence enabled
        compiler = WorkflowCompiler()
        app = compiler.compile(
            workflow_dict,
            db_path=Path("./workflow.db"),
            enable_tracking=True,
        )

        executor = WorkflowExecutor(
            on_progress=lambda p: print(f"Progress: {p.percent_complete}%"),
        )

        result = await executor.run(app, inputs={"target": "example.com"})

        if result.suspended:
            # Handle human input request
            user_input = input(result.suspension.prompt)
            result = await executor.resume(input_value=user_input)
    """
    
    def __init__(
        self,
        exec_context: Optional["ExecutionContext"] = None,
        output_handler: Optional["OutputHandler"] = None,
        audit_logger: Optional["AuditLogger"] = None,
        on_progress: ProgressCallback | None = None,
        on_step: StepCallback | None = None,
        default_timeout: int = 3600,  # 1 hour default
        step_timeout: int = 300,  # 5 minutes per step
        capture_timing: bool = True,
    ):
        """Initialize the executor.

        Args:
            exec_context: Execution context for shell/prompts
            output_handler: Handler for output display
            audit_logger: Audit logger for execution logging
            on_progress: Callback for progress updates
            on_step: Callback for step transitions
            default_timeout: Default workflow timeout in seconds
            step_timeout: Default timeout per step in seconds
            capture_timing: Enable accurate step timing via Burr lifecycle hooks

        Note:
            For checkpoint/resume and Burr web UI tracking, configure
            db_path and enable_tracking when compiling the workflow.

            For accurate step timing, pass executor.timing_hook to the compiler:
                executor = WorkflowExecutor(capture_timing=True)
                app = compiler.compile(workflow, timing_hook=executor.timing_hook)
        """
        self.exec_context = exec_context
        self.output_handler = output_handler
        self.audit_logger = audit_logger
        self.on_progress = on_progress
        self.on_step = on_step
        self.default_timeout = default_timeout
        self.step_timeout = step_timeout
        self.capture_timing = capture_timing

        # Create timing hook if enabled
        self._timing_hook: StepTimingHook | None = None
        if capture_timing:
            self._timing_hook = StepTimingHook()

        # Execution state (use threading.Event for thread-safe signal handling)
        self._interrupted = threading.Event()
        self._current_app: Application | None = None
        self._progress: ExecutionProgress | None = None
        self._suspension: SuspensionRequest | None = None
        self._execution_id: str | None = None
        self._start_time: float | None = None

    @property
    def timing_hook(self) -> StepTimingHook | None:
        """Get the timing hook for passing to the compiler.

        Example:
            executor = WorkflowExecutor(capture_timing=True)
            app = compiler.compile(workflow, timing_hook=executor.timing_hook)
            result = await executor.run(app, inputs={...})
        """
        return self._timing_hook

    def _validate_and_coerce_inputs(
        self,
        inputs: dict[str, Any] | None,
        input_definitions: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Validate and coerce input values against their definitions.

        This ensures runtime inputs match the types declared in the workflow YAML.

        Args:
            inputs: User-provided input values
            input_definitions: Input schema from workflow definition

        Returns:
            Coerced input values

        Raises:
            WorkflowExecutionError: If required input missing or type coercion fails
        """
        if not input_definitions:
            return inputs or {}

        inputs = inputs or {}
        coerced = {}

        for name, spec in input_definitions.items():
            # Handle both InputDefinition objects and plain dicts
            if hasattr(spec, "model_dump"):
                spec = spec.model_dump()
            elif not isinstance(spec, dict):
                # Simple value used as default
                coerced[name] = inputs.get(name, spec)
                continue

            # Get value with default fallback
            value = inputs.get(name)
            default = spec.get("default")
            required = spec.get("required", True)

            if value is None:
                if default is not None:
                    value = default
                elif required:
                    raise WorkflowExecutionError(
                        f"Missing required input: {name}"
                    )
                else:
                    coerced[name] = None
                    continue

            # Coerce to declared type
            input_type = spec.get("type", "string")
            try:
                coerced[name] = self._coerce_input_type(value, input_type, spec)
            except (ValueError, TypeError) as e:
                raise WorkflowExecutionError(
                    f"Input '{name}' type coercion failed: {e}"
                ) from e

            # Validate enum constraint
            enum_values = spec.get("enum")
            if enum_values and coerced[name] not in enum_values:
                raise WorkflowExecutionError(
                    f"Input '{name}' value '{coerced[name]}' not in allowed values: {enum_values}"
                )

            # Validate pattern constraint (strings only)
            pattern = spec.get("pattern")
            if pattern and input_type == "string":
                import re
                if not re.match(pattern, str(coerced[name])):
                    raise WorkflowExecutionError(
                        f"Input '{name}' value '{coerced[name]}' does not match pattern: {pattern}"
                    )

        return coerced

    def _coerce_input_type(
        self,
        value: Any,
        target_type: str,
        spec: dict[str, Any],
    ) -> Any:
        """Coerce a value to the target type.

        Args:
            value: Input value to coerce
            target_type: Target type (string, number, boolean, array, object)
            spec: Full input specification for additional constraints

        Returns:
            Coerced value

        Raises:
            ValueError: If coercion is not possible
        """
        if value is None:
            return None

        if target_type == "string":
            return str(value)

        elif target_type == "number":
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, str):
                # Try int first, then float
                try:
                    return int(value)
                except ValueError:
                    return float(value)
            raise ValueError(f"Cannot convert {type(value).__name__} to number")

        elif target_type == "integer":
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str):
                return int(value)
            if isinstance(value, float):
                if value.is_integer():
                    return int(value)
                raise ValueError(f"Float {value} is not an integer")
            raise ValueError(f"Cannot convert {type(value).__name__} to integer")

        elif target_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lower = value.lower()
                if lower in ("true", "yes", "1", "on"):
                    return True
                if lower in ("false", "no", "0", "off"):
                    return False
                raise ValueError(f"Cannot convert string '{value}' to boolean")
            if isinstance(value, (int, float)):
                return bool(value)
            raise ValueError(f"Cannot convert {type(value).__name__} to boolean")

        elif target_type == "array":
            if isinstance(value, list):
                return value
            if isinstance(value, tuple):
                return list(value)
            if isinstance(value, str):
                # Try JSON parsing first (preferred for explicit arrays)
                import json
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return parsed
                    elif isinstance(parsed, dict):
                        # Valid JSON but not an array - error, don't fall through
                        raise ValueError(
                            "Expected JSON array but got object. "
                            "Use 'object' type for JSON objects."
                        )
                    else:
                        # Scalar JSON value (string, number, bool, null)
                        # Wrap in array
                        return [parsed]
                except json.JSONDecodeError:
                    pass

                # Fallback: Split by comma for plain strings
                # Note: This means "foo,bar" becomes ["foo", "bar"]
                # For a single-element array with a comma, use JSON: '["foo,bar"]'
                if "," in value:
                    logger.debug(
                        "Array input contains comma but is not valid JSON. "
                        "Splitting by comma. For a literal comma in array elements, "
                        "use JSON format: '[\"element,with,commas\"]'"
                    )
                    return [v.strip() for v in value.split(",")]
                else:
                    # Single element without comma - wrap in array
                    return [value.strip()]
            raise ValueError(f"Cannot convert {type(value).__name__} to array")

        elif target_type == "object":
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                import json
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError as e:
                    raise ValueError("Cannot parse string as JSON object") from e
            raise ValueError(f"Cannot convert {type(value).__name__} to object")

        else:
            # Unknown type - return as-is
            logger.warning(f"Unknown input type '{target_type}', passing value as-is")
            return value

    async def run(
        self,
        app: "Application",
        inputs: dict[str, Any] | None = None,
        timeout: int | None = None,
        resume_from: str | None = None,
        workflow_version: str | None = None,
    ) -> ExecutionResult:
        """Run a workflow application.

        Args:
            app: Compiled Burr Application
            inputs: Input values for the workflow
            timeout: Workflow timeout in seconds (overrides default)
            resume_from: Optional checkpoint to resume from
            workflow_version: Optional workflow version for tracking

        Returns:
            ExecutionResult with final state and outputs

        Raises:
            WorkflowExecutionError: If execution fails
            WorkflowTimeoutError: If timeout exceeded
            WorkflowInterruptedError: If interrupted by signal
        """
        self._current_app = app
        self._interrupted.clear()  # Reset interrupt flag for new execution
        self._suspension = None
        self._start_time = time.monotonic()
        self._execution_id = str(uuid.uuid4())[:8]  # Short ID for readability

        timeout = timeout or self.default_timeout
        workflow_name = getattr(app, 'uid', 'workflow')

        # Initialize progress tracking
        self._progress = ExecutionProgress(
            workflow_name=workflow_name,
            status=ExecutionStatus.RUNNING,
            workflow_version=workflow_version,
            started_at=datetime.now(),
            steps_total=self._count_steps(app),
        )

        # Log workflow start
        if self.audit_logger:
            await self.audit_logger.workflow_start(
                workflow_name=workflow_name,
                workflow_version=workflow_version,
                inputs=inputs or {},
                execution_id=self._execution_id,
            )

        # Set up signal handlers for graceful interruption
        original_handlers = self._setup_signal_handlers()

        try:
            # Run with timeout
            result = await asyncio.wait_for(
                self._execute(app, inputs),
                timeout=timeout,
            )
            return result

        except asyncio.TimeoutError:
            self._progress.status = ExecutionStatus.TIMEOUT
            error_msg = f"Workflow timeout after {timeout} seconds"
            result = ExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                final_state=self._get_current_state(app),
                outputs={},
                progress=self._progress,
                error=error_msg,
            )
            await self._log_workflow_end("timeout", error_msg)
            return result

        except KeyboardInterrupt:
            self._progress.status = ExecutionStatus.INTERRUPTED
            error_msg = "Workflow interrupted by user"
            result = ExecutionResult(
                status=ExecutionStatus.INTERRUPTED,
                final_state=self._get_current_state(app),
                outputs={},
                progress=self._progress,
                error=error_msg,
            )
            await self._log_workflow_end("interrupted", error_msg)
            return result

        except WorkflowExecutionError as e:
            self._progress.status = ExecutionStatus.FAILED
            error_msg = str(e)
            result = ExecutionResult(
                status=ExecutionStatus.FAILED,
                final_state=self._get_current_state(app),
                outputs={},
                progress=self._progress,
                error=error_msg,
            )
            await self._log_workflow_end("failure", error_msg)
            return result

        finally:
            self._restore_signal_handlers(original_handlers)
            self._current_app = None
    
    async def resume(
        self,
        input_value: Any = None,
    ) -> ExecutionResult:
        """Resume a suspended workflow with user input.

        Rebuilds the Burr Application with updated state containing the user's
        input in __resume_data[step_id], then continues execution from the
        suspended step.

        Args:
            input_value: The user-provided input value

        Returns:
            ExecutionResult after resuming execution

        Raises:
            WorkflowExecutionError: If no suspended workflow to resume
        """
        from burr.core import ApplicationBuilder

        if self._suspension is None:
            raise WorkflowExecutionError("No suspended workflow to resume")

        step_id = self._suspension.step_id
        suspended_state = self._suspension.suspended_state
        original_app = self._suspension.app

        if suspended_state is None or original_app is None:
            raise WorkflowExecutionError(
                "Suspension state not captured. Cannot resume."
            )

        # Build resume state with user input in __resume_data
        resume_data = dict(suspended_state.get("__resume_data", {}) or {})
        resume_data[step_id] = input_value

        # Create new state with resume data and clear suspension flag
        initial_state = {
            **suspended_state,
            "__resume_data": resume_data,
            "__suspend_for_input": False,
        }

        # Rebuild application with graph from original app
        # ApplicationGraph is a subclass of Graph, compatible with .with_graph()
        resumed_app = (
            ApplicationBuilder()
            .with_graph(original_app.graph)
            .with_state(State(initial_state))
            .with_entrypoint(step_id)  # Resume at the suspended step
            .with_identifiers(
                app_id=original_app.uid,
                partition_key=original_app.partition_key,
            )
            .build()
        )

        # Clear suspension tracking
        self._suspension = None

        # Continue execution with rebuilt app
        return await self._execute(resumed_app, inputs=None)
    
    async def step(self, app: "Application") -> StepProgress | None:
        """Execute a single step of the workflow.
        
        Args:
            app: The Burr Application
            
        Returns:
            StepProgress for the executed step, or None if complete
        """
        if self._interrupted.is_set():
            return None

        # Execute single step
        try:
            result = app.step()
            if result is None:
                return None
                
            action, output_dict, new_state = result
            
            # Create step progress
            progress = StepProgress(
                step_id=action.name,
                step_name=getattr(action, 'step_name', action.name),
                status="completed",
                completed_at=datetime.now(),
                outcome=output_dict.get("outcome", "success"),
            )
            
            # Update overall progress
            if self._progress:
                self._progress.steps_completed += 1
                self._progress.current_step = action.name
                self._progress.step_history.append(progress)
                
                # Fire callback
                if self.on_step:
                    self.on_step(action.name, "completed", output_dict)
                if self.on_progress:
                    self.on_progress(self._progress)
            
            return progress
            
        except Exception as e:
            logger.exception(f"Step execution failed: {e}")
            raise WorkflowExecutionError(f"Step execution failed: {e}") from e
    
    async def _execute(
        self,
        app: "Application",
        inputs: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Internal execution loop."""

        # Validate and coerce inputs against schema
        input_definitions = app.state.get("__input_definitions")
        if input_definitions or inputs:
            coerced_inputs = self._validate_and_coerce_inputs(inputs, input_definitions)
            # Merge coerced inputs into the inputs dict for the workflow
            inputs = coerced_inputs

        # Notify start
        if self.on_progress:
            self.on_progress(self._progress)

        try:
            # Use Burr's async iteration

            # Burr's aiterate() yields AFTER action execution completes.
            # Use the timing hook (if available) for accurate per-step timing.
            async for action, result, state in app.aiterate(inputs=inputs):
                if self._interrupted.is_set():
                    self._progress.status = ExecutionStatus.INTERRUPTED
                    break

                step_id = action.name
                step_name = getattr(action, 'step_name', action.name)
                getattr(action, 'step_type', 'unknown')
                step_outcome = result.get("outcome", "success") if result else "success"
                step_error = result.get("error") if result else None

                # Get step duration from timing hook if available (accurate)
                # Fall back to result dict or 0.0 if hook not available
                step_duration_ms = 0.0
                if self._timing_hook:
                    step_duration_ms = self._timing_hook.get_duration_ms(step_id)
                elif result and isinstance(result, dict):
                    step_duration_ms = result.get("duration_ms", 0.0)


                # Log step completion (start event omitted - action already executed)
                # For full before/after step events, use Burr's LifecycleHook system
                if self.audit_logger:
                    await self.audit_logger.step_end(
                        step_id=step_id,
                        outcome=step_outcome,
                        duration_ms=step_duration_ms,
                        execution_id=self._execution_id,
                        output=result,
                        error=step_error,
                    )

                # Update progress
                self._progress.steps_completed += 1
                self._progress.current_step = step_id

                step_progress = StepProgress(
                    step_id=step_id,
                    step_name=step_name,
                    status="completed",
                    completed_at=datetime.now(),
                    outcome=step_outcome,
                    error=step_error,
                )
                self._progress.step_history.append(step_progress)

                # Fire callbacks
                if self.on_step:
                    self.on_step(step_id, "completed", result)
                if self.on_progress:
                    self.on_progress(self._progress)

                # Check for suspension request (human input needed)
                if state.get("__suspend_for_input"):
                    return self._handle_suspension(app, state)

                # Check for workflow exit
                if state.get("__workflow_exit"):
                    break

            # Workflow completed
            if not self._interrupted.is_set():
                self._progress.status = ExecutionStatus.COMPLETED

            final_state = self._get_current_state(app)

            # Log successful completion
            await self._log_workflow_end("success", None)

            return ExecutionResult(
                status=self._progress.status,
                final_state=final_state,
                outputs=self._extract_outputs(final_state),
                progress=self._progress,
            )

        except Exception as e:
            self._progress.status = ExecutionStatus.FAILED
            logger.exception(f"Workflow execution failed: {e}")
            await self._log_workflow_end("failure", str(e))
            
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                final_state=self._get_current_state(app),
                outputs={},
                progress=self._progress,
                error=str(e),
            )
    
    def _handle_suspension(
        self,
        app: "Application",
        state: State,
    ) -> ExecutionResult:
        """Handle workflow suspension for user input."""
        self._progress.status = ExecutionStatus.SUSPENDED

        # Capture the current state and app for resume
        suspended_state = dict(state.get_all())

        # Extract step_id from state (set by human action) or progress
        step_id = state.get("__suspend_step_id") or self._progress.current_step or "unknown"

        # Extract suspension details from state
        self._suspension = SuspensionRequest(
            step_id=step_id,
            suspension_type=state.get("__suspend_input_type", "input"),
            prompt=state.get("__suspend_prompt", "User input required"),
            options=state.get("__suspend_choices"),
            default=state.get("__suspend_default"),
            timeout=state.get("__suspend_timeout"),
            # Store for resume
            suspended_state=suspended_state,
            app=app,
        )

        return ExecutionResult(
            status=ExecutionStatus.SUSPENDED,
            final_state=suspended_state,
            outputs={},
            progress=self._progress,
            suspension=self._suspension,
        )
    
    def _get_current_state(self, app: "Application") -> dict[str, Any]:
        """Get current state as dictionary."""
        try:
            return dict(app.state.get_all())
        except Exception:
            return {}
    
    def _extract_outputs(self, state: dict[str, Any]) -> dict[str, Any]:
        """Extract workflow outputs from final state."""
        outputs = {}

        # Get step outputs
        steps = state.get("steps", {})
        for step_id, step_data in steps.items():
            if isinstance(step_data, dict) and "outputs" in step_data:
                outputs[step_id] = step_data["outputs"]

        # Get explicit outputs
        if "outputs" in state:
            outputs["workflow"] = state["outputs"]

        return outputs

    async def _log_workflow_end(
        self,
        outcome: str,
        error: str | None,
    ) -> None:
        """Log workflow completion to audit logger."""
        if not self.audit_logger or not self._execution_id:
            return

        # Calculate duration
        duration_ms = 0.0
        if self._start_time is not None:
            duration_ms = (time.monotonic() - self._start_time) * 1000

        # Count step outcomes from progress
        total_steps = 0
        successful_steps = 0
        failed_steps = 0
        skipped_steps = 0

        if self._progress and self._progress.step_history:
            for step in self._progress.step_history:
                total_steps += 1
                if step.outcome == "success":
                    successful_steps += 1
                elif step.outcome == "failure":
                    failed_steps += 1
                elif step.outcome == "skipped":
                    skipped_steps += 1

        try:
            await self.audit_logger.workflow_end(
                outcome=outcome,
                duration_ms=duration_ms,
                execution_id=self._execution_id,
                total_steps=total_steps,
                successful_steps=successful_steps,
                failed_steps=failed_steps,
                skipped_steps=skipped_steps,
                error=error,
            )
            await self.audit_logger.flush()
        except Exception as e:
            logger.warning(f"Failed to log workflow end: {e}")

    def _count_steps(self, app: "Application") -> int:
        """Count total steps in the workflow.

        Uses the public Application.graph property to access the ApplicationGraph,
        which contains the actions list as List[Action].
        """
        try:
            # Use public API: app.graph returns ApplicationGraph with actions: List[Action]
            graph = app.graph
            if graph and hasattr(graph, 'actions'):
                # Exclude internal nodes (prefixed with __)
                return len([
                    action.name for action in graph.actions
                    if not action.name.startswith("__")
                ])
        except (AttributeError, TypeError) as e:
            logger.debug(f"Failed to count workflow steps: {e}")
        return 0
    
    def _setup_signal_handlers(self) -> dict:
        """Set up signal handlers for graceful interruption."""
        original = {}
        
        def handle_interrupt(signum, frame):
            self._interrupted.set()
            logger.info("Workflow interruption requested")
        
        try:
            original[signal.SIGINT] = signal.signal(signal.SIGINT, handle_interrupt)
            original[signal.SIGTERM] = signal.signal(signal.SIGTERM, handle_interrupt)
        except (ValueError, OSError) as e:
            # Signal handling not available (e.g., not main thread)
            logger.warning(
                f"Could not set up signal handlers (not main thread?): {e}. "
                "Ctrl+C interruption may not work."
            )

        return original
    
    def _restore_signal_handlers(self, original: dict) -> None:
        """Restore original signal handlers."""
        for sig, handler in original.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
    
    def interrupt(self) -> None:
        """Request workflow interruption."""
        self._interrupted.set()
        logger.info("Workflow interruption requested programmatically")
    
    @property
    def is_running(self) -> bool:
        """Check if a workflow is currently running."""
        return (
            self._progress is not None and
            self._progress.status == ExecutionStatus.RUNNING
        )
    
    @property
    def current_progress(self) -> ExecutionProgress | None:
        """Get current execution progress."""
        return self._progress


# Convenience function
async def run_workflow(
    app: "Application",
    inputs: dict[str, Any] | None = None,
    timeout: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> ExecutionResult:
    """Convenience function to run a workflow.
    
    Args:
        app: Compiled Burr Application
        inputs: Input values
        timeout: Timeout in seconds
        on_progress: Progress callback
        
    Returns:
        ExecutionResult
    """
    executor = WorkflowExecutor(on_progress=on_progress)
    return await executor.run(app, inputs=inputs, timeout=timeout)
