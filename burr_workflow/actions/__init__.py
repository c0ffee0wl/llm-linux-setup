"""Workflow actions for burr_workflow."""

from .base import BaseAction, ActionResult
from .registry import ActionRegistry, get_default_registry, register_report_actions
from .shell import ShellAction
from .http import HTTPAction
from .state import StateSetAction
from .control import ExitAction, FailAction
from .report import ReportAddAction, ReportListAction
from .iterator import (
    IteratorInitAction,
    IteratorCheckAction,
    IteratorAdvanceAction,
    IteratorFinalizeAction,
)
# Direct SingleStepAction loop nodes (used by compiler)
from .loop_nodes import (
    IteratorInitNode,
    IteratorCheckNode,
    IteratorAdvanceNode,
    IteratorFinalizeNode,
)

__all__ = [
    # Base
    "BaseAction",
    "ActionResult",
    # Registry
    "ActionRegistry",
    "get_default_registry",
    "register_report_actions",
    # Actions
    "ShellAction",
    "HTTPAction",
    "StateSetAction",
    "ExitAction",
    "FailAction",
    "ReportAddAction",
    "ReportListAction",
    # Iterator (legacy - for backwards compatibility)
    "IteratorInitAction",
    "IteratorCheckAction",
    "IteratorAdvanceAction",
    "IteratorFinalizeAction",
    # Loop Nodes (direct SingleStepAction - used by compiler)
    "IteratorInitNode",
    "IteratorCheckNode",
    "IteratorAdvanceNode",
    "IteratorFinalizeNode",
]
