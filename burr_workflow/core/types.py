"""
Core type definitions for the workflow engine.

These types define the data structures used throughout
the workflow engine for state management and action results.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, TypedDict


class StepOutcome(str, Enum):
    """Possible outcomes of a step execution."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"  # Condition not met (if: false)
    SUSPENDED = "suspended"  # Waiting for human input
    PARTIAL = "partial"  # Loop with some failures
    BREAK = "break"  # Loop exited via break_if


# ActionResult is now canonical in burr_workflow.actions.base
# Import from there for new code


@dataclass
class StepResult:
    """Complete result of a step including metadata.

    Used for tracking and persistence, includes timing
    and retry information.
    """

    step_id: str
    step_name: str | None
    outcome: StepOutcome
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_type: str | None = None
    duration_ms: float | None = None
    retry_count: int = 0


@dataclass
class LoopContext:
    """Context available within a loop iteration.

    This mirrors Jinja2's loop variable structure for
    familiarity, with additional fields for workflow needs.
    """

    # Current iteration data
    items: list[Any]
    item: Any
    index: int  # 1-based (Jinja2 standard)
    index0: int  # 0-based (Jinja2 standard)
    total: int

    # Position flags
    first: bool
    last: bool

    # Reverse indices
    revindex: int  # 1-based from end
    revindex0: int  # 0-based from end

    # Loop metadata
    output: dict[str, Any] | None = None  # Current iteration output
    parent: Optional["LoopContext"] = None  # For nested loops

    # Internal tracking
    __loop_id: str | None = None
    __ancestor_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for state storage."""
        return {
            "items": self.items,
            "item": self.item,
            "index": self.index,
            "index0": self.index0,
            "total": self.total,
            "first": self.first,
            "last": self.last,
            "revindex": self.revindex,
            "revindex0": self.revindex0,
            "output": self.output,
            "parent": self.parent.to_dict() if self.parent else None,
            "__loop_id": self.__loop_id,
            "__ancestor_ids": self.__ancestor_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], _depth: int = 0) -> "LoopContext":
        """Create LoopContext from dictionary.

        Args:
            data: Dictionary representation of LoopContext
            _depth: Internal counter to prevent infinite recursion

        Returns:
            LoopContext instance

        Raises:
            ValueError: If nesting depth exceeds 100 (possible cycle)
        """
        if _depth > 100:
            raise ValueError("Loop context nesting too deep (possible cycle)")
        parent_data = data.get("parent")
        # Note: __loop_id and __ancestor_ids are name-mangled by Python
        # We need to use the mangled names when passing to __init__
        return cls(
            items=data["items"],
            item=data["item"],
            index=data["index"],
            index0=data["index0"],
            total=data["total"],
            first=data["first"],
            last=data["last"],
            revindex=data["revindex"],
            revindex0=data["revindex0"],
            output=data.get("output"),
            parent=cls.from_dict(parent_data, _depth + 1) if parent_data else None,
            _LoopContext__loop_id=data.get("__loop_id"),
            _LoopContext__ancestor_ids=data.get("__ancestor_ids", []),
        )


class WorkflowState(TypedDict, total=False):
    """Type hints for workflow state dictionary.

    This defines the expected shape of the Burr State
    for workflow execution. All keys are optional.
    """

    # User-provided inputs
    inputs: dict[str, Any]

    # Environment variables (evaluated once at start)
    env: dict[str, Any]

    # Step outputs: steps.{step_id}.{outputs|outcome|error}
    steps: dict[str, dict[str, Any]]

    # Current loop context (if in a loop)
    loop: dict[str, Any]

    # Internal: Loop management
    __loop_stack: list[dict[str, Any]]
    __loop_depth: int
    __loop_results: list[dict[str, Any]]
    __loop_errors: list[dict[str, Any]]
    __loop_iteration_count: int
    __loop_success_count: int
    __loop_break_requested: bool
    __loop_break_reason: str
    __loop_break_item: Any
    __loop_break_index: int
    __loop_results_file: str
    __loop_results_limited: bool  # True when results hit max_results limit

    # Internal: Routing
    __next: str
    __condition_met: bool

    # Internal: Cleanup tracking
    __cleanup_complete: bool
    __cleanup_warnings: list[str]
    __cleanup_errors: list[str]

    # Internal: Workflow control
    __interrupted: bool
    __workflow_exit: bool
    __workflow_failed: bool

    # Internal: Guard state (LLM Guard integration)
    __guard_vault: dict[str, Any]           # Vault state for anonymize/deanonymize
    __guard_input_content: str              # Input content for output relevance
    __guard_scan_results: dict[str, Any]    # Last scan results for debugging
    __guard_warning: str                    # Warning message when on_fail=continue


# Reserved state keys that user actions cannot override
# This prevents control flow hijacking from user-generated output
RESERVED_STATE_KEYS = frozenset([
    "__next",
    "__condition_met",
    "__loop_break_requested",
    "__loop_results",
    "__loop_errors",
    "__loop_iteration_count",
    "__loop_success_count",
    "__loop_depth",
    "__loop_stack",
    "__loop_break_reason",
    "__loop_break_item",
    "__loop_break_index",
    "__loop_results_file",
    "__loop_results_limited",
    "__loop_config",              # Loop configuration (used by loop_nodes.py)
    "__loop_items",               # Loop items list (used by loop_nodes.py)
    "__step_outcome",             # Step outcome for routing (used by adapters.py)
    "__step_error",               # Step error flag for routing (used by adapters.py)
    "__cleanup_complete",
    "__cleanup_warnings",
    "__cleanup_errors",
    "__interrupted",
    "__workflow_exit",
    "__workflow_failed",
    # Suspension control (for human input)
    "__suspend_for_input",
    "__suspend_step_id",
    "__suspend_prompt",
    "__suspend_input_type",
    "__suspend_choices",
    "__suspend_timeout",
    "__suspend_default",
    "__suspend_feedback_type",
    "__resume_data",
    # Guard state keys (LLM Guard integration)
    "__guard_vault",           # Vault state for anonymize/deanonymize flow
    "__guard_input_content",   # Input content for output relevance checks
    "__guard_scan_results",    # Last scan results for debugging
    "__guard_warning",         # Warning message when on_fail=continue
    # Lifecycle handler state keys
    "__on_complete_done",      # Tracks if on_complete has run
    "__on_failure_done",       # Tracks if on_failure has run
])


# Supported schema versions
SUPPORTED_SCHEMA_VERSIONS = ["1.0"]
DEPRECATED_VERSIONS: list[str] = []
