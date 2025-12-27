"""
Base persistence backend implementation.

Provides an abstract base class with rich features (partitioning, execution
tracking, cleanup) that conforms to the minimal PersistenceBackend Protocol.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..protocols import PersistenceBackend


@dataclass
class WorkflowCheckpoint:
    """Saved workflow checkpoint."""
    workflow_id: str
    app_id: str
    partition_key: Optional[str]
    state: dict[str, Any]
    position: str
    sequence_id: int
    created_at: datetime
    updated_at: datetime


@dataclass
class ExecutionRecord:
    """Record of a workflow execution."""
    workflow_id: str
    app_id: str
    partition_key: Optional[str]
    status: str  # "running", "completed", "failed", "interrupted"
    started_at: datetime
    completed_at: Optional[datetime]
    final_position: Optional[str]
    error: Optional[str]
    step_count: int


class BasePersistenceBackend(PersistenceBackend, ABC):
    """Abstract base class for persistence backends.

    Extends the minimal PersistenceBackend Protocol with rich features:
    - Partition key support for multi-tenant deployments
    - Execution lifecycle tracking (start/end)
    - Typed dataclasses for checkpoints and execution records
    - Cleanup of old data

    Implementations must provide:
    - save_checkpoint: Save workflow state
    - load_checkpoint: Load workflow state
    - list_executions: List execution history
    - cleanup: Remove old data
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the backend (create tables, etc.)."""
        ...

    @abstractmethod
    async def save_checkpoint(
        self,
        workflow_id: str,
        app_id: str,
        state: dict[str, Any],
        position: str,
        sequence_id: int,
        *,
        partition_key: Optional[str] = None,
    ) -> None:
        """Save a workflow checkpoint.

        Args:
            workflow_id: Workflow definition identifier
            app_id: Unique execution instance ID
            state: Current workflow state
            position: Current action/node name
            sequence_id: Step sequence number
            partition_key: Optional partition for multi-tenant support
        """
        ...

    @abstractmethod
    async def load_checkpoint(
        self,
        workflow_id: str,
        app_id: str,
        *,
        partition_key: Optional[str] = None,
    ) -> Optional[WorkflowCheckpoint]:
        """Load a workflow checkpoint.

        Args:
            workflow_id: Workflow definition identifier
            app_id: Unique execution instance ID
            partition_key: Optional partition key

        Returns:
            WorkflowCheckpoint if found, None otherwise
        """
        ...

    @abstractmethod
    async def delete_checkpoint(
        self,
        workflow_id: str,
        app_id: str,
        *,
        partition_key: Optional[str] = None,
    ) -> bool:
        """Delete a workflow checkpoint.

        Args:
            workflow_id: Workflow definition identifier
            app_id: Unique execution instance ID
            partition_key: Optional partition key

        Returns:
            True if checkpoint was deleted, False if not found
        """
        ...

    @abstractmethod
    async def record_execution_start(
        self,
        workflow_id: str,
        app_id: str,
        *,
        partition_key: Optional[str] = None,
    ) -> None:
        """Record that a workflow execution has started.

        Args:
            workflow_id: Workflow definition identifier
            app_id: Unique execution instance ID
            partition_key: Optional partition key
        """
        ...

    @abstractmethod
    async def record_execution_end(
        self,
        workflow_id: str,
        app_id: str,
        status: str,
        *,
        partition_key: Optional[str] = None,
        final_position: Optional[str] = None,
        error: Optional[str] = None,
        step_count: int = 0,
    ) -> None:
        """Record that a workflow execution has ended.

        Args:
            workflow_id: Workflow definition identifier
            app_id: Unique execution instance ID
            status: Final status ("completed", "failed", "interrupted")
            partition_key: Optional partition key
            final_position: Last executed action name
            error: Error message if failed
            step_count: Total steps executed
        """
        ...

    @abstractmethod
    async def list_executions(
        self,
        workflow_id: str,
        *,
        partition_key: Optional[str] = None,
        limit: int = 10,
        status_filter: Optional[str] = None,
    ) -> list[ExecutionRecord]:
        """List recent executions of a workflow.

        Args:
            workflow_id: Workflow definition identifier
            partition_key: Optional partition key filter
            limit: Maximum number of results
            status_filter: Filter by status

        Returns:
            List of execution records, newest first
        """
        ...

    @abstractmethod
    async def cleanup_old_checkpoints(
        self,
        max_age_days: int = 30,
        *,
        keep_failed: bool = True,
    ) -> int:
        """Clean up old checkpoints.

        Args:
            max_age_days: Maximum age in days
            keep_failed: Whether to keep failed execution checkpoints

        Returns:
            Number of checkpoints deleted
        """
        ...

    async def close(self) -> None:
        """Close the backend connection."""
        pass

    # Protocol-conforming wrapper methods
    # These delegate to the richer ABC methods with default partition_key=None

    async def save_state(
        self,
        workflow_id: str,
        app_id: str,
        state: dict[str, Any],
        position: str,
        sequence_id: int,
    ) -> None:
        """Protocol-conforming save_state wrapper.

        Delegates to save_checkpoint with partition_key=None.
        """
        await self.save_checkpoint(
            workflow_id=workflow_id,
            app_id=app_id,
            state=state,
            position=position,
            sequence_id=sequence_id,
        )

    async def load_state(
        self,
        workflow_id: str,
        app_id: str,
    ) -> Optional[dict[str, Any]]:
        """Protocol-conforming load_state wrapper.

        Delegates to load_checkpoint with partition_key=None.
        Returns state dict or None.
        """
        checkpoint = await self.load_checkpoint(workflow_id, app_id)
        if checkpoint is None:
            return None
        return checkpoint.state
