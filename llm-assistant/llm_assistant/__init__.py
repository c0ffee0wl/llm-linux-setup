"""llm-assistant - Terminal LLM Assistant for Terminator

A Terminator-integrated LLM assistant that provides:
- Automatic Exec terminal creation
- Terminal content capture and analysis
- Command execution with approval
- Watch Mode for proactive monitoring
- Context squashing to manage token limits
- Streaming responses with markdown rendering
- Robust error handling and recovery

Inspired by TmuxAI but designed for Terminator terminal emulator.
"""

__version__ = "1.0.0"
__all__ = ["TerminatorAssistantSession", "main"]


def __getattr__(name):
    """Lazy import heavy modules to avoid unnecessary audio initialization.

    This prevents importing session.py (which imports voice.py, which
    initializes sounddevice/PortAudio) when only the main classes are needed.
    Note: filter_new_blocks has moved to llm_tools_core.
    """
    if name == "TerminatorAssistantSession":
        from .session import TerminatorAssistantSession
        return TerminatorAssistantSession
    elif name == "main":
        from .cli import main
        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
