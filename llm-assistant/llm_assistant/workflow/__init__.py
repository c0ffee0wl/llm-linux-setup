"""
Workflow integration for llm-assistant.

This module provides the integration layer between the burr_workflow
engine and the llm-assistant terminal assistant. It includes:

- WorkflowMixin: Mixin class for TerminatorAssistantSession
- AssistantExecutionContext: Bridge to session.execute_command()
- AssistantLLMClient: Bridge to session.model for LLM actions

Usage:
    The WorkflowMixin should be added to the session class inheritance:
    
    class TerminatorAssistantSession(
        KnowledgeBaseMixin, MemoryMixin, RAGMixin, SkillsMixin,
        WorkflowMixin,  # Add here
        ReportMixin, WebMixin, TerminalMixin, ContextMixin,
        WatchMixin, MCPMixin
    ):
        pass
"""

from .context import AssistantExecutionContext
from .llm_client import AssistantLLMClient, LLMSchemaValidationError, LLMChoiceError
from .mixin import WorkflowMixin

__all__ = [
    "WorkflowMixin",
    "AssistantExecutionContext",
    "AssistantLLMClient",
    "LLMSchemaValidationError",
    "LLMChoiceError",
]
