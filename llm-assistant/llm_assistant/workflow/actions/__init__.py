"""
Custom workflow actions for llm-assistant integration.

These actions bridge the burr_workflow engine to llm-assistant session features:
- human/input: Interactive user prompts
- human/decide: Constrained user decisions (confirm/choice)
- report/add: Pentest finding management
"""

from .human_input import HumanInputAction, HumanDecideAction
from .report_add import ReportAddAction

__all__ = [
    "HumanInputAction",
    "HumanDecideAction",
    "ReportAddAction",
]
