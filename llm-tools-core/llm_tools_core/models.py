"""Model context limits and detection utilities.

This module provides model-specific context limits used by:
- llm-assistant (context management, auto-squashing)
- llm-inlineassistant (context estimation)
- Any tool that needs to know model context windows
"""

from typing import Dict

# Model-specific context limits (input tokens)
# Based on provider documentation as of 2025-12
MODEL_CONTEXT_LIMITS: Dict[str, int] = {
    # Azure OpenAI / OpenAI - GPT-4.1 series (1M context)
    "gpt-4.1": 1000000,
    "gpt-4.1-mini": 1000000,
    "gpt-4.1-nano": 1000000,

    # Azure OpenAI / OpenAI - GPT-4o series (128k context)
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,

    # Azure OpenAI / OpenAI - GPT-5 series (272k context)
    "gpt-5": 270000,
    "gpt-5-mini": 270000,
    "gpt-5-nano": 270000,
    "gpt-5-chat": 110000,
    "gpt-5.1": 270000,
    "gpt-5.1-chat": 110000,
    "gpt-5.1-codex": 270000,
    "gpt-5.1-codex-mini": 270000,
    "gpt-5.1-codex-max": 270000,
    "gpt-5.2": 270000,
    "gpt-5.2-chat": 110000,

    # Azure OpenAI / OpenAI - Reasoning models (o-series)
    "o1": 200000,
    "o1-preview": 128000,
    "o1-mini": 128000,
    "o3": 200000,
    "o3-mini": 200000,
    "o3-pro": 200000,
    "o4-mini": 200000,
    "codex-mini": 200000,
}

# Default limits by provider prefix (fallback when model not in explicit list)
# Gemini/Vertex models have 1M, Claude models have 200k
PROVIDER_DEFAULT_LIMITS: Dict[str, int] = {
    "azure/": 200000,       # Conservative default for unknown Azure models
    "vertex/": 1000000,     # Vertex models have 1M
    "gemini-": 1000000,     # Gemini models have 1M
    "claude-": 200000,      # Claude models have 200k (1M beta requires special header)
    "openai/": 128000,      # Conservative for unknown OpenAI models
}

# Absolute fallback
DEFAULT_CONTEXT_LIMIT = 200000


def get_model_context_limit(model_name: str) -> int:
    """Get context limit for a model.

    Resolution order:
    1. Exact model name match (after stripping provider prefix)
    2. Provider prefix match (azure/, vertex/, gemini-, claude-)
    3. Default fallback (200k)

    Args:
        model_name: Model identifier (e.g., "azure/gpt-4.1", "gemini-2.5-flash")

    Returns:
        Context limit in tokens

    Examples:
        >>> get_model_context_limit("azure/gpt-4.1")
        1000000
        >>> get_model_context_limit("claude-3-opus")
        200000
        >>> get_model_context_limit("gemini-2.5-flash")
        1000000
    """
    # Strip common provider prefixes for lookup
    base_name = model_name
    for prefix in ("azure/", "vertex/", "openai/"):
        if model_name.startswith(prefix):
            base_name = model_name[len(prefix):]
            break

    # Try exact match first
    if base_name in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[base_name]

    # Try provider prefix fallback
    for prefix, limit in PROVIDER_DEFAULT_LIMITS.items():
        if model_name.startswith(prefix):
            return limit

    # Absolute fallback
    return DEFAULT_CONTEXT_LIMIT
