"""llm-inlineassistant - Inline AI assistant with daemon architecture.

A lightweight AI assistant for shell and espanso integration:
- Triggered via `@ <query>` syntax in bash/zsh (shell mode)
- Triggered via `@llm`/`@llma` in espanso (text expansion mode)
- Fast startup via daemon architecture (<100ms after first call)
- Per-terminal conversation continuity
- Context from asciinema recordings
- Shares tools and templates with llm-assistant
"""

__version__ = "1.0.0"
