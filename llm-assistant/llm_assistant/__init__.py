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

from .session import TerminatorAssistantSession
from .cli import main

__version__ = "1.0.0"
__all__ = ["TerminatorAssistantSession", "main"]
