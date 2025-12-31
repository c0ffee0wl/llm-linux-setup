"""LLM configuration resolution utilities.

Provides functions to resolve LLM configuration with a clear precedence:
1. Step-level with: config (highest priority)
2. Action-type defaults from workflow llm: section
3. Global defaults from workflow llm: section
4. Protocol defaults (handled by LLMClient, lowest priority)
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..schemas.models import LLMDefaultsConfig

# Action type to config key mapping
ACTION_TYPE_MAP = {
    "llm/extract": "extract",
    "llm/decide": "decide",
    "llm/generate": "generate",
    "llm/instruct": "instruct",
}


def resolve_llm_config(
    with_config: dict[str, Any],
    action_type: str,
    workflow_llm_config: Optional["LLMDefaultsConfig"],
) -> dict[str, Any]:
    """Resolve LLM configuration with precedence.

    Precedence (highest to lowest):
    1. Step-level with: config
    2. Action-type defaults from workflow llm: section
    3. Global defaults from workflow llm: section
    4. Protocol defaults (handled by LLMClient)

    Args:
        with_config: The step's with: configuration
        action_type: Action type string (e.g., "llm/extract")
        workflow_llm_config: Workflow-level llm configuration

    Returns:
        Merged configuration dict with model, temperature, max_tokens
    """
    merged: dict[str, Any] = {}

    # Layer 3: Global workflow llm defaults
    if workflow_llm_config:
        for key in ("model", "temperature", "max_tokens"):
            val = getattr(workflow_llm_config, key, None)
            if val is not None:
                merged[key] = val

    # Layer 2: Action-type specific defaults
    if workflow_llm_config:
        action_key = ACTION_TYPE_MAP.get(action_type)
        if action_key:
            action_defaults = getattr(workflow_llm_config, action_key, None)
            if action_defaults:
                for key in ("model", "temperature", "max_tokens"):
                    val = getattr(action_defaults, key, None)
                    if val is not None:
                        merged[key] = val

    # Layer 1: Step-level config (highest priority)
    for key in ("model", "temperature", "max_tokens"):
        if key in with_config:
            merged[key] = with_config[key]

    return merged
