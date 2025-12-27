"""Burr lifecycle hooks for workflow execution.

This module provides hooks that integrate with Burr's lifecycle system
to capture accurate timing and other execution metadata.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from burr.lifecycle import PreRunStepHookAsync, PostRunStepHookAsync

if TYPE_CHECKING:
    from burr.core import Action, State


@dataclass
class StepTiming:
    """Timing data for a single step execution.

    Attributes:
        step_id: The step identifier
        start_ns: Start time in nanoseconds (from perf_counter_ns)
        end_ns: End time in nanoseconds (None if not completed)
    """

    step_id: str
    start_ns: int
    end_ns: Optional[int] = None

    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds."""
        if self.end_ns is None:
            return 0.0
        return (self.end_ns - self.start_ns) / 1_000_000


@dataclass
class StepTimingHook(PreRunStepHookAsync, PostRunStepHookAsync):
    """Captures accurate step timing via Burr lifecycle hooks.

    This hook records precise timestamps before and after each step
    executes, providing accurate duration measurements that account
    for the actual execution time rather than iteration overhead.

    Usage:
        timing_hook = StepTimingHook()
        builder.with_hooks(timing_hook)

        # After execution:
        for step_id, timing in timing_hook.timings.items():
            print(f"{step_id}: {timing.duration_ms:.2f}ms")

    Attributes:
        timings: Dictionary mapping step IDs to their timing data
    """

    timings: dict[str, StepTiming] = field(default_factory=dict)
    _current: Optional[StepTiming] = field(default=None, repr=False)

    async def pre_run_step(
        self,
        *,
        action: "Action",
        state: "State",
        inputs: dict[str, Any],
        sequence_id: int,
        app_id: str,
        partition_key: Optional[str] = None,
        **future_kwargs: Any,
    ) -> None:
        """Called before each step executes.

        Records the start timestamp for timing measurement.
        """
        step_id = action.name
        self._current = StepTiming(step_id=step_id, start_ns=time.perf_counter_ns())

    async def post_run_step(
        self,
        *,
        action: "Action",
        state: "State",
        result: Optional[dict[str, Any]],
        exception: Optional[Exception],
        sequence_id: int,
        app_id: str,
        partition_key: Optional[str] = None,
        **future_kwargs: Any,
    ) -> None:
        """Called after each step completes (success or failure).

        Records the end timestamp and stores the timing data.
        """
        if self._current is not None:
            self._current.end_ns = time.perf_counter_ns()
            self.timings[self._current.step_id] = self._current
            self._current = None

    def get_duration_ms(self, step_id: str) -> float:
        """Get duration for a specific step in milliseconds.

        Args:
            step_id: The step identifier to look up

        Returns:
            Duration in milliseconds, or 0.0 if step not found
        """
        timing = self.timings.get(step_id)
        return timing.duration_ms if timing else 0.0

    def clear(self) -> None:
        """Clear all timing data.

        Useful for reusing the hook across multiple executions.
        """
        self.timings.clear()
        self._current = None
