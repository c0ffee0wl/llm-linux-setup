"""Persistence backends for workflow audit logging.

Note:
    For workflow state checkpointing, use Burr's built-in SQLitePersister
    by passing db_path to WorkflowCompiler.compile().

    For Burr web UI tracking, pass enable_tracking=True to compile().
"""

from .audit import FileAuditLogger

__all__ = [
    "FileAuditLogger",
]
