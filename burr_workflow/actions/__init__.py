"""Workflow actions for burr_workflow."""

from .base import ActionResult, BaseAction
from .control import BreakAction, ContinueAction, ExitAction, FailAction, WaitAction
from .file import FileReadAction, FileWriteAction
from .http import HTTPAction
from .human import HumanDecideAction, HumanInputAction

# Direct SingleStepAction loop nodes (used by compiler)
from .loop_nodes import (
    IteratorAdvanceNode,
    IteratorCheckNode,
    IteratorFinalizeNode,
    IteratorInitNode,
)
from .notify import NotifyDesktopAction, NotifyWebhookAction
from .parse import ParseJSONAction, ParseRegexAction
from .registry import (
    ActionRegistry,
    get_default_registry,
    register_llm_actions,
    register_report_actions,
)
from .report import ReportAddAction, ReportListAction
from .script import BashScriptAction, PythonScriptAction
from .shell import ShellAction
from .state import StateAppendAction, StateSetAction

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
