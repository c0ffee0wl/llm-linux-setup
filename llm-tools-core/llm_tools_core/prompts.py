"""Shared system prompts for LLM tools.

Provides common system prompt templates used by:
- llm-assistant (daemon simple mode)
- espanso-llm (text expansion)
- Any tool that needs non-interactive LLM responses
"""

from datetime import datetime


def build_simple_system_prompt(include_datetime: bool = True) -> str:
    """Build the system prompt for simple/non-interactive mode.

    This prompt instructs the model to provide direct responses without
    asking for clarification, making it suitable for:
    - Text expansion (espanso)
    - Single-shot queries
    - Non-interactive contexts

    Args:
        include_datetime: Whether to include current date/time context (default True)

    Returns:
        System prompt string

    Examples:
        >>> prompt = build_simple_system_prompt()
        >>> "non-interactive" in prompt
        True
    """
    base_prompt = (
        "You are operating in a non-interactive mode.\n"
        "Do NOT use introductory phrases, greetings, or opening messages.\n"
        "You CANNOT ask the user for clarification, additional details, or preferences.\n"
        "When given a request, make reasonable assumptions based on the context and provide a complete, helpful response immediately.\n"
        "If a request is ambiguous, choose the most common or logical interpretation and proceed accordingly.\n"
        "Always deliver a substantive response rather than asking questions.\n"
        "NEVER ask the user for follow-up questions or clarifications.\n\n"
        "Follow a clear writing style:\n"
        "- Write in plain, clear, and understandable language.\n"
        "- Use active voice, avoid passive constructions.\n"
        "- Choose vivid, concrete words over abstract ones.\n"
        "- Use adjectives only where necessary.\n"
        "- Prefer strong, specific verbs over weak ones.\n"
        "- AVOID: nominalizations, filler words (actually, basically, really, just), overly complex sentences."
    )

    if include_datetime:
        now = datetime.now()
        context = (
            f"Current date: {now.strftime('%Y-%m-%d')}\n"
            f"Current time: {now.strftime('%H:%M')}"
        )
        return f"{base_prompt}\n\nContext:\n{context}"

    return base_prompt


def build_context_section(date_str: str = None, time_str: str = None) -> str:
    """Build a context section with date/time information.

    Args:
        date_str: Date string (default: current date as YYYY-MM-DD)
        time_str: Time string (default: current time as HH:MM)

    Returns:
        Formatted context section string
    """
    now = datetime.now()
    date_str = date_str or now.strftime('%Y-%m-%d')
    time_str = time_str or now.strftime('%H:%M')

    return f"Current date: {date_str}\nCurrent time: {time_str}"


def wrap_terminal_context(content: str) -> str:
    """Wrap terminal content in XML-style tags for structured injection.

    Args:
        content: Terminal content to wrap

    Returns:
        Content wrapped in <terminal_context> tags
    """
    return f"<terminal_context>\n{content}\n</terminal_context>"


def wrap_conversation_summary(summary: str) -> str:
    """Wrap conversation summary in XML-style tags for structured injection.

    Args:
        summary: Summary content to wrap

    Returns:
        Content wrapped in <conversation_summary> tags
    """
    return f"<conversation_summary>\n{summary}\n</conversation_summary>"


def wrap_retrieved_documents(documents: str) -> str:
    """Wrap RAG retrieved documents in XML-style tags for structured injection.

    Args:
        documents: Retrieved document content to wrap

    Returns:
        Content wrapped in <retrieved_documents> tags
    """
    return f"<retrieved_documents>\n{documents}\n</retrieved_documents>"
