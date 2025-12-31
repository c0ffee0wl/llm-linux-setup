"""
File-based audit logger implementation.

Provides durable audit logging with dual output:
- JSONL: Append-only, crash-safe raw event log
- Markdown: Human-readable execution summaries
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..protocols import AuditLogger


@dataclass
class FileAuditLogger(AuditLogger):
    """File-based implementation of AuditLogger protocol.

    Writes audit events to two files per execution:
    - {execution_id}.jsonl: Machine-parseable event stream
    - {execution_id}.md: Human-readable summary

    The JSONL file is written incrementally (append-only) for crash safety.
    The Markdown file is written/updated at key milestones.

    Args:
        log_dir: Directory for audit log files
        max_output_size: Maximum size for step outputs in logs (bytes)
        flush_on_write: Whether to flush after each write (slower but safer)
    """

    log_dir: Path
    max_output_size: int = 10000
    flush_on_write: bool = True

    # Internal state
    _jsonl_file: Any | None = field(default=None, repr=False)
    _md_content: dict = field(default_factory=dict, repr=False)
    _current_execution_id: str | None = field(default=None, repr=False)

    def __post_init__(self):
        """Ensure log directory exists."""
        self.log_dir = Path(self.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._md_content = {}

    def _get_timestamp(self) -> str:
        """Get current ISO 8601 timestamp."""
        return datetime.now(timezone.utc).isoformat()

    def _truncate_output(self, data: Any) -> Any:
        """Truncate large outputs to max_output_size."""
        if data is None:
            return None

        text = json.dumps(data) if not isinstance(data, str) else data
        if len(text) > self.max_output_size:
            truncated = text[: self.max_output_size]
            return f"{truncated}... [TRUNCATED, {len(text)} bytes total]"
        return data

    def _write_jsonl(self, event: dict) -> None:
        """Write event to JSONL file."""
        if self._jsonl_file is None:
            return

        line = json.dumps(event, default=str, ensure_ascii=False)
        self._jsonl_file.write(line + "\n")

        if self.flush_on_write:
            self._jsonl_file.flush()
            os.fsync(self._jsonl_file.fileno())

    def _open_files(self, execution_id: str) -> None:
        """Open log files for a new execution."""
        if self._jsonl_file is not None:
            self._jsonl_file.close()

        self._current_execution_id = execution_id
        jsonl_path = self.log_dir / f"{execution_id}.jsonl"
        self._jsonl_file = open(jsonl_path, "a", encoding="utf-8")
        self._md_content = {
            "execution_id": execution_id,
            "workflow_name": "",
            "workflow_version": "",
            "start_time": "",
            "end_time": "",
            "outcome": "",
            "inputs": {},
            "steps": [],
            "error": None,
            "stats": {},
        }

    async def workflow_start(
        self,
        workflow_name: str,
        workflow_version: str | None,
        inputs: dict[str, Any],
        *,
        execution_id: str,
        timestamp: str | None = None,
    ) -> None:
        """Log workflow execution start."""
        ts = timestamp or self._get_timestamp()

        # Open new log files
        self._open_files(execution_id)

        # Update markdown content
        self._md_content["workflow_name"] = workflow_name
        self._md_content["workflow_version"] = workflow_version or "unversioned"
        self._md_content["start_time"] = ts
        self._md_content["inputs"] = inputs

        # Write JSONL event
        event = {
            "event": "workflow_start",
            "timestamp": ts,
            "execution_id": execution_id,
            "workflow_name": workflow_name,
            "workflow_version": workflow_version,
            "inputs": inputs,
        }
        self._write_jsonl(event)

    async def step_start(
        self,
        step_id: str,
        step_name: str | None,
        step_type: str,
        *,
        execution_id: str,
        timestamp: str | None = None,
    ) -> None:
        """Log step start."""
        ts = timestamp or self._get_timestamp()

        event = {
            "event": "step_start",
            "timestamp": ts,
            "execution_id": execution_id,
            "step_id": step_id,
            "step_name": step_name,
            "step_type": step_type,
        }
        self._write_jsonl(event)

    async def step_end(
        self,
        step_id: str,
        outcome: str,
        duration_ms: float,
        *,
        execution_id: str,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        """Log step completion."""
        ts = timestamp or self._get_timestamp()

        # Truncate large outputs
        truncated_output = self._truncate_output(output)

        event = {
            "event": "step_end",
            "timestamp": ts,
            "execution_id": execution_id,
            "step_id": step_id,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "output": truncated_output,
            "error": error,
        }
        self._write_jsonl(event)

        # Track for markdown summary
        self._md_content["steps"].append(
            {
                "step_id": step_id,
                "outcome": outcome,
                "duration_ms": duration_ms,
                "error": error,
            }
        )

    async def workflow_end(
        self,
        outcome: str,
        duration_ms: float,
        *,
        execution_id: str,
        total_steps: int,
        successful_steps: int,
        failed_steps: int,
        skipped_steps: int,
        error: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        """Log workflow completion."""
        ts = timestamp or self._get_timestamp()

        event = {
            "event": "workflow_end",
            "timestamp": ts,
            "execution_id": execution_id,
            "outcome": outcome,
            "duration_ms": duration_ms,
            "total_steps": total_steps,
            "successful_steps": successful_steps,
            "failed_steps": failed_steps,
            "skipped_steps": skipped_steps,
            "error": error,
        }
        self._write_jsonl(event)

        # Update markdown content
        self._md_content["end_time"] = ts
        self._md_content["outcome"] = outcome
        self._md_content["error"] = error
        self._md_content["stats"] = {
            "total_steps": total_steps,
            "successful_steps": successful_steps,
            "failed_steps": failed_steps,
            "skipped_steps": skipped_steps,
            "duration_ms": duration_ms,
        }

        # Write markdown summary
        await self._write_markdown()

    async def log_event(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        execution_id: str,
        timestamp: str | None = None,
    ) -> None:
        """Log arbitrary audit event."""
        ts = timestamp or self._get_timestamp()

        event = {
            "event": event_type,
            "timestamp": ts,
            "execution_id": execution_id,
            "data": data,
        }
        self._write_jsonl(event)

    async def flush(self) -> None:
        """Ensure all pending writes are persisted."""
        if self._jsonl_file is not None:
            self._jsonl_file.flush()
            os.fsync(self._jsonl_file.fileno())

    async def close(self) -> None:
        """Close all open files."""
        if self._jsonl_file is not None:
            self._jsonl_file.close()
            self._jsonl_file = None

    async def _write_markdown(self) -> None:
        """Write markdown summary file."""
        if not self._current_execution_id:
            return

        md_path = self.log_dir / f"{self._current_execution_id}.md"
        content = self._format_markdown()

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

    def _format_markdown(self) -> str:
        """Format markdown summary."""
        c = self._md_content
        stats = c.get("stats", {})

        # Header
        lines = [
            f"# Workflow Execution: {c['workflow_name']}",
            "",
            f"**Execution ID:** `{c['execution_id']}`  ",
            f"**Version:** {c['workflow_version']}  ",
            f"**Started:** {c['start_time']}  ",
            f"**Ended:** {c['end_time']}  ",
            f"**Outcome:** {c['outcome'].upper()}  ",
            "",
        ]

        # Stats
        if stats:
            duration_s = stats.get("duration_ms", 0) / 1000
            lines.extend(
                [
                    "## Summary",
                    "",
                    f"- **Duration:** {duration_s:.2f}s",
                    f"- **Total Steps:** {stats.get('total_steps', 0)}",
                    f"- **Successful:** {stats.get('successful_steps', 0)}",
                    f"- **Failed:** {stats.get('failed_steps', 0)}",
                    f"- **Skipped:** {stats.get('skipped_steps', 0)}",
                    "",
                ]
            )

        # Error if present
        if c.get("error"):
            lines.extend(
                [
                    "## Error",
                    "",
                    "```",
                    c["error"],
                    "```",
                    "",
                ]
            )

        # Inputs
        if c.get("inputs"):
            lines.extend(
                [
                    "## Inputs",
                    "",
                    "```json",
                    json.dumps(c["inputs"], indent=2, default=str),
                    "```",
                    "",
                ]
            )

        # Step results
        if c.get("steps"):
            lines.extend(
                [
                    "## Steps",
                    "",
                    "| Step | Outcome | Duration |",
                    "|------|---------|----------|",
                ]
            )

            for step in c["steps"]:
                duration_s = step.get("duration_ms", 0) / 1000
                outcome_icon = {
                    "success": "OK",
                    "failure": "FAIL",
                    "skipped": "SKIP",
                }.get(step.get("outcome", ""), step.get("outcome", ""))

                lines.append(
                    f"| `{step['step_id']}` | {outcome_icon} | {duration_s:.2f}s |"
                )

            lines.append("")

            # Failed step details
            failed_steps = [s for s in c["steps"] if s.get("error")]
            if failed_steps:
                lines.extend(
                    [
                        "### Failed Steps",
                        "",
                    ]
                )
                for step in failed_steps:
                    lines.extend(
                        [
                            f"#### {step['step_id']}",
                            "",
                            "```",
                            step.get("error", "Unknown error"),
                            "```",
                            "",
                        ]
                    )

        return "\n".join(lines)

    def __del__(self):
        """Cleanup on garbage collection."""
        if self._jsonl_file is not None:
            try:
                self._jsonl_file.close()
            except Exception:
                pass
