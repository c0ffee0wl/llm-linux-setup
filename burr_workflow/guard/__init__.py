"""LLM Guard integration for workflow guardrails.

Provides input/output scanning with llm-guard library integration.
Gracefully degrades when llm-guard is not installed.
"""

from .scanner import LLM_GUARD_AVAILABLE, GuardError, GuardScanner, ScanResult, initialize_models
from .vault import VaultManager

__all__ = [
    "GuardScanner",
    "ScanResult",
    "GuardError",
    "VaultManager",
    "LLM_GUARD_AVAILABLE",
    "initialize_models",
]
