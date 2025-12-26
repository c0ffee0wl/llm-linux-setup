"""
SQLite persistence backend for workflow state.

Provides durable storage for workflow checkpoints and execution history.
Uses aiosqlite for async database access.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .base import ExecutionRecord, PersistenceBackend, WorkflowCheckpoint


class SQLitePersistence(PersistenceBackend):
    """SQLite-based persistence backend.

    Stores workflow checkpoints and execution history in a SQLite database.
    Supports both synchronous and asynchronous operation.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        timeout: float = 30.0,
    ):
        """Initialize SQLite persistence.

        Args:
            db_path: Path to SQLite database file
            timeout: Connection timeout in seconds
        """
        self.db_path = db_path
        self.timeout = timeout
        self._conn: Optional[sqlite3.Connection] = None
        self._initialized = False

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        if self._initialized:
            return

        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect and create tables
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.timeout,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

        # Enable WAL mode for better concurrency
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        # Create checkpoints table
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                workflow_id TEXT NOT NULL,
                app_id TEXT NOT NULL,
                partition_key TEXT,
                state_json TEXT NOT NULL,
                position TEXT NOT NULL,
                sequence_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (workflow_id, app_id, partition_key)
            )
        """)

        # Create executions table
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                app_id TEXT NOT NULL,
                partition_key TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                final_position TEXT,
                error TEXT,
                step_count INTEGER DEFAULT 0,
                UNIQUE (workflow_id, app_id, partition_key)
            )
        """)

        # Create indexes
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_checkpoints_workflow
            ON checkpoints (workflow_id, partition_key)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_executions_workflow
            ON executions (workflow_id, partition_key, started_at DESC)
        """)

        self._conn.commit()
        self._initialized = True

    def _ensure_initialized(self) -> None:
        """Ensure the backend is initialized."""
        if not self._initialized or not self._conn:
            raise RuntimeError("SQLitePersistence not initialized. Call initialize() first.")

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
        """Save a workflow checkpoint."""
        self._ensure_initialized()

        now = datetime.utcnow().isoformat()
        state_json = json.dumps(state)

        # Use NULL for partition_key if not provided
        pk = partition_key or ""

        self._conn.execute("""
            INSERT INTO checkpoints
                (workflow_id, app_id, partition_key, state_json, position, sequence_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (workflow_id, app_id, partition_key)
            DO UPDATE SET
                state_json = excluded.state_json,
                position = excluded.position,
                sequence_id = excluded.sequence_id,
                updated_at = excluded.updated_at
        """, (workflow_id, app_id, pk, state_json, position, sequence_id, now, now))
        self._conn.commit()

    async def load_checkpoint(
        self,
        workflow_id: str,
        app_id: str,
        *,
        partition_key: Optional[str] = None,
    ) -> Optional[WorkflowCheckpoint]:
        """Load a workflow checkpoint."""
        self._ensure_initialized()

        pk = partition_key or ""

        cursor = self._conn.execute("""
            SELECT workflow_id, app_id, partition_key, state_json, position, sequence_id, created_at, updated_at
            FROM checkpoints
            WHERE workflow_id = ? AND app_id = ? AND partition_key = ?
        """, (workflow_id, app_id, pk))

        row = cursor.fetchone()
        if not row:
            return None

        return WorkflowCheckpoint(
            workflow_id=row["workflow_id"],
            app_id=row["app_id"],
            partition_key=row["partition_key"] or None,
            state=json.loads(row["state_json"]),
            position=row["position"],
            sequence_id=row["sequence_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def delete_checkpoint(
        self,
        workflow_id: str,
        app_id: str,
        *,
        partition_key: Optional[str] = None,
    ) -> bool:
        """Delete a workflow checkpoint."""
        self._ensure_initialized()

        pk = partition_key or ""

        cursor = self._conn.execute("""
            DELETE FROM checkpoints
            WHERE workflow_id = ? AND app_id = ? AND partition_key = ?
        """, (workflow_id, app_id, pk))
        self._conn.commit()

        return cursor.rowcount > 0

    async def record_execution_start(
        self,
        workflow_id: str,
        app_id: str,
        *,
        partition_key: Optional[str] = None,
    ) -> None:
        """Record that a workflow execution has started."""
        self._ensure_initialized()

        now = datetime.utcnow().isoformat()
        pk = partition_key or ""

        self._conn.execute("""
            INSERT INTO executions
                (workflow_id, app_id, partition_key, status, started_at)
            VALUES (?, ?, ?, 'running', ?)
            ON CONFLICT (workflow_id, app_id, partition_key)
            DO UPDATE SET
                status = 'running',
                started_at = excluded.started_at,
                completed_at = NULL,
                final_position = NULL,
                error = NULL,
                step_count = 0
        """, (workflow_id, app_id, pk, now))
        self._conn.commit()

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
        """Record that a workflow execution has ended."""
        self._ensure_initialized()

        now = datetime.utcnow().isoformat()
        pk = partition_key or ""

        self._conn.execute("""
            UPDATE executions
            SET status = ?,
                completed_at = ?,
                final_position = ?,
                error = ?,
                step_count = ?
            WHERE workflow_id = ? AND app_id = ? AND partition_key = ?
        """, (status, now, final_position, error, step_count, workflow_id, app_id, pk))
        self._conn.commit()

    async def list_executions(
        self,
        workflow_id: str,
        *,
        partition_key: Optional[str] = None,
        limit: int = 10,
        status_filter: Optional[str] = None,
    ) -> list[ExecutionRecord]:
        """List recent executions of a workflow."""
        self._ensure_initialized()

        query = """
            SELECT workflow_id, app_id, partition_key, status, started_at,
                   completed_at, final_position, error, step_count
            FROM executions
            WHERE workflow_id = ?
        """
        params: list[Any] = [workflow_id]

        if partition_key is not None:
            query += " AND partition_key = ?"
            params.append(partition_key or "")

        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(query, params)

        records = []
        for row in cursor.fetchall():
            records.append(ExecutionRecord(
                workflow_id=row["workflow_id"],
                app_id=row["app_id"],
                partition_key=row["partition_key"] or None,
                status=row["status"],
                started_at=datetime.fromisoformat(row["started_at"]),
                completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                final_position=row["final_position"],
                error=row["error"],
                step_count=row["step_count"],
            ))

        return records

    async def cleanup_old_checkpoints(
        self,
        max_age_days: int = 30,
        *,
        keep_failed: bool = True,
    ) -> int:
        """Clean up old checkpoints."""
        self._ensure_initialized()

        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()

        if keep_failed:
            # Only delete checkpoints for completed executions
            cursor = self._conn.execute("""
                DELETE FROM checkpoints
                WHERE updated_at < ?
                AND (workflow_id, app_id, partition_key) IN (
                    SELECT workflow_id, app_id, partition_key
                    FROM executions
                    WHERE status = 'completed'
                )
            """, (cutoff,))
        else:
            cursor = self._conn.execute("""
                DELETE FROM checkpoints
                WHERE updated_at < ?
            """, (cutoff,))

        self._conn.commit()
        return cursor.rowcount

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            self._initialized = False


# Burr integration adapter
class BurrStatePersisterAdapter:
    """Adapter to use SQLitePersistence with Burr's persistence API.

    This wraps our SQLitePersistence to implement Burr's
    BaseStateSaver interface.
    """

    def __init__(self, persistence: SQLitePersistence, workflow_id: str):
        """Initialize the adapter.

        Args:
            persistence: The underlying SQLitePersistence
            workflow_id: Workflow ID for this persister
        """
        self.persistence = persistence
        self.workflow_id = workflow_id

    def is_initialized(self) -> bool:
        """Check if the persister is initialized."""
        return self.persistence._initialized

    def is_async(self) -> bool:
        """Return True if this is an async persister."""
        return False  # We use sync SQLite for simplicity

    def save(
        self,
        partition_key: Optional[str],
        app_id: str,
        sequence_id: int,
        position: str,
        state: "State",  # type: ignore
        status: str,
        **kwargs: Any,
    ) -> None:
        """Save state (Burr interface)."""
        import asyncio

        # Get the current event loop or create one
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(
            self.persistence.save_checkpoint(
                workflow_id=self.workflow_id,
                app_id=app_id,
                state=state.get_all(),
                position=position,
                sequence_id=sequence_id,
                partition_key=partition_key,
            )
        )

    def load(
        self,
        partition_key: Optional[str],
        app_id: str,
        sequence_id: Optional[int] = None,
        **kwargs: Any,
    ) -> Optional[dict[str, Any]]:
        """Load state (Burr interface)."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        checkpoint = loop.run_until_complete(
            self.persistence.load_checkpoint(
                workflow_id=self.workflow_id,
                app_id=app_id,
                partition_key=partition_key,
            )
        )

        if not checkpoint:
            return None

        return {
            "state": checkpoint.state,
            "position": checkpoint.position,
            "sequence_id": checkpoint.sequence_id,
        }
