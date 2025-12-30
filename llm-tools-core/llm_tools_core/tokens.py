"""Token estimation utilities for LLM tools.

Provides simple token estimation for context window management.

The primary method uses character-based estimation (4 chars ≈ 1 token),
which is fast and reasonably accurate for English text. When available,
API-provided token counts should be preferred (see llm-assistant's
context management for API-based tracking).

Used by:
- llm-assistant (fallback when API tokens unavailable)
- llm-inlineassistant (context estimation)
- Any tool that needs rough token estimates

Note: Actual token counts vary by model tokenizer. For accurate counts,
use the model's tokenizer or API-provided usage metrics.
"""

# Standard approximation: 4 characters per token
# This is a rough heuristic that works reasonably well for English text.
# Source: OpenAI estimates "one token is around 4 characters of English text"
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count using character-based approximation.

    Uses the 4 chars = 1 token heuristic, which is fast and
    reasonably accurate for English text.

    Args:
        text: Text to estimate tokens for

    Returns:
        Estimated token count

    Examples:
        >>> estimate_tokens("Hello, world!")  # 13 chars
        3
        >>> estimate_tokens("This is a longer piece of text.")  # 32 chars
        8
    """
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


def estimate_tokens_json(data: object) -> int:
    """Estimate token count for JSON-serializable data.

    Serializes the data to JSON and estimates tokens from the
    resulting string. Useful for estimating tool schema overhead.

    Args:
        data: JSON-serializable object

    Returns:
        Estimated token count

    Examples:
        >>> estimate_tokens_json({"name": "test", "value": 42})
        6
    """
    import json
    try:
        json_str = json.dumps(data, indent=2)
        return estimate_tokens(json_str)
    except (TypeError, ValueError):
        return 0


def estimate_context_usage(
    system_prompt: str,
    messages_chars: int,
    tool_schema_chars: int = 0
) -> int:
    """Estimate total context usage for an LLM request.

    Combines estimates for system prompt, messages, and tool schemas
    into a single token estimate.

    Args:
        system_prompt: The system prompt text
        messages_chars: Total character count of all messages
        tool_schema_chars: Character count of tool schemas (optional)

    Returns:
        Estimated total tokens
    """
    system_tokens = estimate_tokens(system_prompt)
    message_tokens = messages_chars // CHARS_PER_TOKEN
    tool_tokens = tool_schema_chars // CHARS_PER_TOKEN

    return system_tokens + message_tokens + tool_tokens


def is_approaching_limit(
    current_tokens: int,
    max_tokens: int,
    threshold: float = 0.8
) -> bool:
    """Check if current token count is approaching the limit.

    Args:
        current_tokens: Current estimated token count
        max_tokens: Maximum allowed tokens (context window size)
        threshold: Fraction of max to consider "approaching" (default 0.8 = 80%)

    Returns:
        True if current_tokens >= max_tokens * threshold

    Examples:
        >>> is_approaching_limit(80000, 100000)  # 80% of 100k
        True
        >>> is_approaching_limit(70000, 100000)  # 70% of 100k
        False
    """
    return current_tokens >= max_tokens * threshold
