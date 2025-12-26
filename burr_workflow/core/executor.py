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
import signal
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional, TYPE_CHECKING
import logging

from burr.core.state import State

from .errors import (
    WorkflowExecutionError,
    WorkflowTimeoutError,
    WorkflowInterruptedError,
)

if TYPE_CHECKING:
    from burr.core import Application
    from ..protocols import ExecutionContext, OutputHandler, PersistenceBackend

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
    step_name: Optional[str]
    status: str  # "pending", "running", "completed", "failed", "skipped"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    outcome: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ExecutionProgress:
    """Overall execution progress."""
    workflow_name: str
    status: ExecutionStatus
    current_step: Optional[str] = None
    steps_completed: int = 0
    steps_total: int = 0
    started_at: Optional[datetime] = None
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
    options: Optional[list[str]] = None
    default: Optional[str] = None
    timeout: Optional[int] = None


@dataclass
class ExecutionResult:
    """Result of workflow execution."""
    status: ExecutionStatus
    final_state: dict[str, Any]
    outputs: dict[str, Any]
    progress: ExecutionProgress
    error: Optional[str] = None
    suspension: Optional[SuspensionRequest] = None
    
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
StepCallback = Callable[[str, str, Optional[dict]], None]  # step_id, status, outputs


class WorkflowExecutor:
    """Executes compiled Burr workflow applications.
    
    The executor provides a higher-level API for running workflows with:
    - Automatic state persistence and resume
    - Progress tracking via callbacks
    - Graceful interruption handling (Ctrl+C)
    - Timeout management at workflow and step level
    - Suspension for human input requests
    
    Example:
        executor = WorkflowExecutor(
            persistence=SQLitePersistence("./workflows.db"),
            on_progress=lambda p: print(f"Progress: {p.percent_complete}%"),
        )
        
        result = await executor.run(app, inputs={"target": "example.com"})
        
        if result.suspended:
            # Handle human input request
            user_input = input(result.suspension.prompt)
            result = await executor.resume(app, input_value=user_input)
    """
    
    def __init__(
        self,
        exec_context: Optional["ExecutionContext"] = None,
        output_handler: Optional["OutputHandler"] = None,
        persistence: Optional["PersistenceBackend"] = None,
        on_progress: Optional[ProgressCallback] = None,
        on_step: Optional[StepCallback] = None,
        default_timeout: int = 3600,  # 1 hour default
        step_timeout: int = 300,  # 5 minutes per step
    ):
        """Initialize the executor.
        
        Args:
            exec_context: Execution context for shell/prompts
            output_handler: Handler for output display
            persistence: Backend for state persistence
            on_progress: Callback for progress updates
            on_step: Callback for step transitions
            default_timeout: Default workflow timeout in seconds
            step_timeout: Default timeout per step in seconds
        """
        self.exec_context = exec_context
        self.output_handler = output_handler
        self.persistence = persistence
        self.on_progress = on_progress
        self.on_step = on_step
        self.default_timeout = default_timeout
        self.step_timeout = step_timeout
        
        # Execution state
        self._interrupted = False
        self._current_app: Optional["Application"] = None
        self._progress: Optional[ExecutionProgress] = None
        self._suspension: Optional[SuspensionRequest] = None

    def _validate_and_coerce_inputs(
        self,
        inputs: Optional[dict[str, Any]],
        input_definitions: Optional[dict[str, Any]],
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
                )

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
                # Try JSON parsing
                import json
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
                # Split by comma as fallback
                return [v.strip() for v in value.split(",")]
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
                except json.JSONDecodeError:
                    raise ValueError(f"Cannot parse string as JSON object")
            raise ValueError(f"Cannot convert {type(value).__name__} to object")

        else:
            # Unknown type - return as-is
            logger.warning(f"Unknown input type '{target_type}', passing value as-is")
            return value

    async def run(
        self,
        app: "Application",
        inputs: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
        resume_from: Optional[str] = None,
    ) -> ExecutionResult:
        """Run a workflow application.
        
        Args:
            app: Compiled Burr Application
            inputs: Input values for the workflow
            timeout: Workflow timeout in seconds (overrides default)
            resume_from: Optional checkpoint to resume from
            
        Returns:
            ExecutionResult with final state and outputs
            
        Raises:
            WorkflowExecutionError: If execution fails
            WorkflowTimeoutError: If timeout exceeded
            WorkflowInterruptedError: If interrupted by signal
        """
        self._current_app = app
        self._interrupted = False
        self._suspension = None
        
        timeout = timeout or self.default_timeout
        
        # Initialize progress tracking
        self._progress = ExecutionProgress(
            workflow_name=getattr(app, 'uid', 'workflow'),
            status=ExecutionStatus.RUNNING,
            started_at=datetime.now(),
            steps_total=self._count_steps(app),
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
            return ExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                final_state=self._get_current_state(app),
                outputs={},
                progress=self._progress,
                error=f"Workflow timeout after {timeout} seconds",
            )
            
        except KeyboardInterrupt:
            self._progress.status = ExecutionStatus.INTERRUPTED
            return ExecutionResult(
                status=ExecutionStatus.INTERRUPTED,
                final_state=self._get_current_state(app),
                outputs={},
                progress=self._progress,
                error="Workflow interrupted by user",
            )
            
        except WorkflowExecutionError as e:
            self._progress.status = ExecutionStatus.FAILED
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                final_state=self._get_current_state(app),
                outputs={},
                progress=self._progress,
                error=str(e),
            )
            
        finally:
            self._restore_signal_handlers(original_handlers)
            self._current_app = None
    
    async def resume(
        self,
        app: "Application",
        input_value: Any = None,
        input_key: str = "__user_input",
    ) -> ExecutionResult:
        """Resume a suspended workflow with user input.
        
        Args:
            app: The suspended Burr Application
            input_value: The user-provided input value
            input_key: State key to store the input
            
        Returns:
            ExecutionResult after resuming execution
        """
        if self._suspension is None:
            raise WorkflowExecutionError("No suspended workflow to resume")
        
        # Update state with user input
        current_state = app.state
        new_state = current_state.update(**{input_key: input_value})
        
        # Clear suspension
        self._suspension = None
        
        # Resume execution with updated state
        # Note: Burr's fork() or similar may be needed here
        # For now, we use the app with updated inputs
        return await self.run(app, inputs={input_key: input_value})
    
    async def step(self, app: "Application") -> Optional[StepProgress]:
        """Execute a single step of the workflow.
        
        Args:
            app: The Burr Application
            
        Returns:
            StepProgress for the executed step, or None if complete
        """
        if self._interrupted:
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
            raise WorkflowExecutionError(f"Step execution failed: {e}")
    
    async def _execute(
        self,
        app: "Application",
        inputs: Optional[dict[str, Any]] = None,
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
            last_action = None
            last_result = None

            async for action, result, state in app.aiterate(inputs=inputs):
                if self._interrupted:
                    self._progress.status = ExecutionStatus.INTERRUPTED
                    break
                
                last_action = action
                last_result = result
                
                # Update progress
                self._progress.steps_completed += 1
                self._progress.current_step = action.name
                
                step_progress = StepProgress(
                    step_id=action.name,
                    step_name=getattr(action, 'step_name', action.name),
                    status="completed",
                    completed_at=datetime.now(),
                    outcome=result.get("outcome", "success") if result else "success",
                    error=result.get("error") if result else None,
                )
                self._progress.step_history.append(step_progress)
                
                # Fire callbacks
                if self.on_step:
                    self.on_step(action.name, "completed", result)
                if self.on_progress:
                    self.on_progress(self._progress)
                
                # Check for suspension request
                if state.get("__suspend_requested"):
                    return self._handle_suspension(app, state)
                
                # Check for workflow exit
                if state.get("__workflow_exit"):
                    break
            
            # Workflow completed
            if not self._interrupted:
                self._progress.status = ExecutionStatus.COMPLETED
            
            final_state = self._get_current_state(app)
            
            return ExecutionResult(
                status=self._progress.status,
                final_state=final_state,
                outputs=self._extract_outputs(final_state),
                progress=self._progress,
            )
            
        except Exception as e:
            self._progress.status = ExecutionStatus.FAILED
            logger.exception(f"Workflow execution failed: {e}")
            
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
        
        # Extract suspension details from state
        self._suspension = SuspensionRequest(
            step_id=self._progress.current_step or "unknown",
            suspension_type=state.get("__suspend_type", "input"),
            prompt=state.get("__suspend_prompt", "User input required"),
            options=state.get("__suspend_options"),
            default=state.get("__suspend_default"),
            timeout=state.get("__suspend_timeout"),
        )
        
        return ExecutionResult(
            status=ExecutionStatus.SUSPENDED,
            final_state=self._get_current_state(app),
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
    
    def _count_steps(self, app: "Application") -> int:
        """Count total steps in the workflow."""
        try:
            # Burr exposes actions through the graph
            if hasattr(app, '_graph') and hasattr(app._graph, '_actions'):
                # Exclude internal nodes
                return len([
                    a for a in app._graph._actions
                    if not a.startswith("__")
                ])
        except Exception:
            pass
        return 0
    
    def _setup_signal_handlers(self) -> dict:
        """Set up signal handlers for graceful interruption."""
        original = {}
        
        def handle_interrupt(signum, frame):
            self._interrupted = True
            logger.info("Workflow interruption requested")
        
        try:
            original[signal.SIGINT] = signal.signal(signal.SIGINT, handle_interrupt)
            original[signal.SIGTERM] = signal.signal(signal.SIGTERM, handle_interrupt)
        except (ValueError, OSError):
            # Signal handling not available (e.g., not main thread)
            pass
        
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
        self._interrupted = True
        logger.info("Workflow interruption requested programmatically")
    
    @property
    def is_running(self) -> bool:
        """Check if a workflow is currently running."""
        return (
            self._progress is not None and
            self._progress.status == ExecutionStatus.RUNNING
        )
    
    @property
    def current_progress(self) -> Optional[ExecutionProgress]:
        """Get current execution progress."""
        return self._progress


# Convenience function
async def run_workflow(
    app: "Application",
    inputs: Optional[dict[str, Any]] = None,
    timeout: Optional[int] = None,
    on_progress: Optional[ProgressCallback] = None,
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
