"""llm-assistant - Terminal AI Assistant for Terminator

A Terminator-integrated AI assistant that provides:
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
    initializes sounddevice/PortAudio) when only utility functions like
    filter_new_blocks from context.py are needed.
    """
    if name == "TerminatorAssistantSession":
        from .session import TerminatorAssistantSession
        return TerminatorAssistantSession
    elif name == "main":
        from .cli import main
        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
