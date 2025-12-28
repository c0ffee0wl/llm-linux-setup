"""Workflow actions for burr_workflow."""

from .base import BaseAction, ActionResult
from .registry import (
    ActionRegistry,
    get_default_registry,
    register_report_actions,
    register_llm_actions,
)
from .shell import ShellAction
from .http import HTTPAction
from .state import StateSetAction, StateAppendAction
from .control import ExitAction, FailAction, BreakAction, ContinueAction, WaitAction
from .human import HumanInputAction, HumanDecideAction
from .file import FileReadAction, FileWriteAction
from .parse import ParseJSONAction, ParseRegexAction
from .notify import NotifyDesktopAction, NotifyWebhookAction
from .script import PythonScriptAction, BashScriptAction
from .report import ReportAddAction, ReportListAction
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
    "register_llm_actions",
    # Actions
    "ShellAction",
    "HTTPAction",
    "StateSetAction",
    "StateAppendAction",
    "ExitAction",
    "FailAction",
    "BreakAction",
    "ContinueAction",
    "WaitAction",
    "HumanInputAction",
    "HumanDecideAction",
    "FileReadAction",
    "FileWriteAction",
    "ParseJSONAction",
    "ParseRegexAction",
    "NotifyDesktopAction",
    "NotifyWebhookAction",
    "PythonScriptAction",
    "BashScriptAction",
    "ReportAddAction",
    "ReportListAction",
    # Loop Nodes (direct SingleStepAction - used by compiler)
    "IteratorInitNode",
    "IteratorCheckNode",
    "IteratorAdvanceNode",
    "IteratorFinalizeNode",
]
