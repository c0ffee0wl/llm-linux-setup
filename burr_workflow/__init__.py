"""
burr_workflow - A general-purpose workflow engine built on Burr.

This package provides a YAML-based workflow definition language with:
- Secure expression evaluation (Jinja2 sandbox)
- Shell command execution with safety features
- HTTP requests and LLM integration
- Loop constructs with state management
- Persistence and resume capabilities (via Burr's SQLitePersister)
- Web UI tracking (via Burr's LocalTrackingClient)

Usage:
    from pathlib import Path
    from burr_workflow import WorkflowCompiler, WorkflowExecutor

    # Load workflow
    with open("workflow.yaml") as f:
        workflow = yaml.safe_load(f)

    # Compile with optional persistence and tracking
    compiler = WorkflowCompiler()
    app = compiler.compile(
        workflow,
        db_path=Path("./workflow.db"),  # Enable checkpointing
        enable_tracking=True,            # Enable Burr web UI
    )

    # Execute
    executor = WorkflowExecutor()
    await executor.run(app, inputs={"target": "example.com"})
"""

__version__ = "0.1.0"

# Core types, errors, compiler, validator, and executor
# Actions
from .actions import (
    ActionRegistry,
    BaseAction,
    ExitAction,
    FailAction,
    HTTPAction,
    ReportAddAction,
    ShellAction,
    StateSetAction,
    get_default_registry,
    register_llm_actions,
    register_report_actions,
)

# Configuration
from .config import (
    WorkflowSettings,
    get_settings,
)
from .core import (
    ActionNotFoundError,
    ActionResult,
    BurrActionAdapter,
    CompiledStep,
    ExecutionProgress,
    ExecutionResult,
    ExecutionStatus,
    FlowAnalysisResult,
    FlowAnalyzer,
    LoopContext,
    StepAnalysis,
    StepError,
    StepOutcome,
    StepProgress,
    StepResult,
    StepTiming,
    StepTimingHook,
    SuspensionRequest,
    ValidationLevel,
    ValidationMessage,
    ValidationResult,
    WorkflowCompilationError,
    WorkflowCompiler,
    WorkflowError,
    WorkflowExecutionError,
    WorkflowExecutor,
    WorkflowInterruptedError,
    WorkflowState,
    WorkflowTimeoutError,
    WorkflowValidationError,
    WorkflowValidator,
    run_workflow,
    to_mermaid,
    validate_workflow,
    validate_workflow_yaml,
    visualize,
)

# Evaluator
from .evaluator import (
    ContextEvaluator,
    PathValidator,
)

# Persistence (audit logging only - state persistence uses Burr's SQLitePersister)
from .persistence import (
    FileAuditLogger,
)

# Protocols for integration
from .protocols import (
    ActionProvider,
    AuditLogger,
    ExecutionContext,
    LLMClient,
    OutputHandler,
    ReportBackend,
)

# Schemas
from .schemas import (
    JobDefinition,
    StepDefinition,
    WorkflowDefinition,
)

__all__ = [
    # Version
    "__version__",
    # Errors
    "WorkflowError",
    "WorkflowValidationError",
    "WorkflowCompilationError",
    "WorkflowExecutionError",
    "WorkflowTimeoutError",
    "WorkflowInterruptedError",
    "StepError",
    "ActionNotFoundError",
    # Types
    "StepOutcome",
    "ActionResult",
    "StepResult",
    "LoopContext",
    "WorkflowState",
    # Compiler
    "WorkflowCompiler",
    "CompiledStep",
    "BurrActionAdapter",
    # Validator
    "WorkflowValidator",
    "ValidationResult",
    "ValidationMessage",
    "ValidationLevel",
    "validate_workflow",
    "validate_workflow_yaml",
    # Executor
    "WorkflowExecutor",
    "ExecutionStatus",
    "ExecutionProgress",
    "ExecutionResult",
    "StepProgress",
    "SuspensionRequest",
    "run_workflow",
    # Flow Analyzer
    "FlowAnalyzer",
    "FlowAnalysisResult",
    "StepAnalysis",
    # Hooks
    "StepTimingHook",
    "StepTiming",
    # Visualization
    "visualize",
    "to_mermaid",
    # Protocols
    "ExecutionContext",
    "OutputHandler",
    "ActionProvider",
    "LLMClient",
    "ReportBackend",
    "AuditLogger",
    # Evaluator
    "ContextEvaluator",
    "PathValidator",
    # Schemas
    "WorkflowDefinition",
    "StepDefinition",
    "JobDefinition",
    # Persistence
    "FileAuditLogger",
    # Actions
    "BaseAction",
    "ActionRegistry",
    "get_default_registry",
    "register_report_actions",
    "register_llm_actions",
    "ShellAction",
    "HTTPAction",
    "StateSetAction",
    "ExitAction",
    "FailAction",
    "ReportAddAction",
    # Config
    "WorkflowSettings",
    "get_settings",
]
