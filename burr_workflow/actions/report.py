"""
Report actions for finding management.

These actions use the ReportBackend protocol, allowing any
finding storage backend to be used (llm-assistant, standalone, etc.).
"""

from typing import Any, ClassVar, Optional, TYPE_CHECKING

from .base import AbstractAction, ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext, ReportBackend


class ReportAddAction(AbstractAction):
    """Add a finding with LLM-assisted analysis.

    Usage:
        - uses: report/add
          with:
            note: "SQL injection in login form"
            severity: 8  # optional override
            context: ${{ steps.scan.outputs.raw_output }}  # optional

    Outputs:
        - finding_id: Unique finding identifier (e.g., "F001")
        - title: Finding title (LLM-generated or from note)
        - severity: Severity 1-9 (9 = critical)
        - success: Whether finding was added successfully
    """

    action_type: ClassVar[str] = "report/add"

    def __init__(self, report_backend: "ReportBackend"):
        """Initialize with report backend.

        Args:
            report_backend: Backend for finding storage
        """
        self.report_backend = report_backend

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Add a finding.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with finding details
        """
        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        # Get required note
        note = with_config.get("note", "")
        note = evaluator.resolve(note)

        if not note:
            return ActionResult(
                outputs={"success": False, "error": "No note provided"},
                outcome="failure",
                error="No note provided for report/add",
                error_type="ValidationError",
            )

        # Check if project is initialized
        if not self.report_backend.findings_project:
            return ActionResult(
                outputs={"success": False, "error": "No findings project initialized"},
                outcome="failure",
                error="No findings project initialized. Initialize with /report init",
                error_type="ConfigurationError",
            )

        # Get optional parameters
        severity_override: Optional[int] = None
        if "severity" in with_config:
            sev = with_config["severity"]
            if isinstance(sev, str):
                sev = evaluator.resolve(sev)
            try:
                severity_override = int(sev)
                if not 1 <= severity_override <= 9:
                    return ActionResult(
                        outputs={"success": False, "error": "Severity must be 1-9"},
                        outcome="failure",
                        error="Severity must be 1-9 (OWASP Risk Rating)",
                        error_type="ValidationError",
                    )
            except (ValueError, TypeError):
                return ActionResult(
                    outputs={"success": False, "error": "Invalid severity value"},
                    outcome="failure",
                    error=f"Invalid severity value: {sev}",
                    error_type="ValidationError",
                )

        finding_context = with_config.get("context", "")
        if finding_context:
            finding_context = evaluator.resolve_all(finding_context)
            if isinstance(finding_context, dict):
                import json
                finding_context = json.dumps(finding_context, indent=2)

        try:
            result = await self.report_backend.add_finding(
                note,
                severity_override=severity_override,
                context=finding_context if finding_context else None,
            )

            if result.get("success", False):
                return ActionResult(
                    outputs=result,
                    outcome="success",
                )
            else:
                return ActionResult(
                    outputs=result,
                    outcome="failure",
                    error=result.get("error", "Unknown error adding finding"),
                    error_type="ReportError",
                )

        except Exception as e:
            return ActionResult(
                outputs={"success": False, "error": str(e)},
                outcome="failure",
                error=f"Failed to add finding: {e}",
                error_type="ReportError",
            )


class ReportListAction(AbstractAction):
    """List findings in current project.

    Usage:
        - uses: report/list

    Outputs:
        - findings: List of finding summaries
        - project: Current project name
        - count: Number of findings
    """

    action_type: ClassVar[str] = "report/list"

    def __init__(self, report_backend: "ReportBackend"):
        """Initialize with report backend.

        Args:
            report_backend: Backend for finding storage
        """
        self.report_backend = report_backend

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """List findings in current project.

        Args:
            step_config: Step configuration
            context: Workflow context
            exec_context: Execution context

        Returns:
            ActionResult with findings list
        """
        project = self.report_backend.findings_project

        if not project:
            return ActionResult(
                outputs={"findings": [], "project": None, "count": 0},
                outcome="success",
            )

        # For now, return basic info - implementations can enhance
        return ActionResult(
            outputs={
                "project": project,
                "findings": [],  # Backend would provide actual list
                "count": 0,
            },
            outcome="success",
        )
