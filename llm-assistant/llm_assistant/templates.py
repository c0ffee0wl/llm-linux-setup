"""Jinja2 template loader and rendering functions.

This module provides functions to render Jinja2 templates for:
- System prompt (with mode, date, platform, shell, environment)
- Help text (with voice status)
- Watch mode prompt
- Safety evaluation prompt
- Report analysis prompt
- Squash prompt (context compression)
- Web companion HTML
"""

from datetime import date
from jinja2 import Environment, PackageLoader, select_autoescape


# Create Jinja2 environment with package loader
_env = Environment(
    loader=PackageLoader('llm_assistant', 'templates'),
    autoescape=select_autoescape(['html']),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_system_prompt(mode: str, platform: str, shell: str, environment: str) -> str:
    """Render the system prompt template with context variables.

    Args:
        mode: Operating mode ("assistant" or "agent")
        platform: OS platform string (e.g., "Linux (Kali)")
        shell: Shell name (e.g., "bash", "zsh")
        environment: Environment type (e.g., "native", "wsl")

    Returns:
        Rendered system prompt string
    """
    template = _env.get_template('system_prompt.j2')
    return template.render(
        mode=mode,
        date=date.today().isoformat(),
        platform=platform,
        shell=shell,
        environment=environment,
    )


def render_help_text(voice_status: str) -> str:
    """Render the help text template.

    Args:
        voice_status: Voice input status string (e.g., "available", "not available")

    Returns:
        Rendered help text string
    """
    template = _env.get_template('help_text.j2')
    return template.render(voice_status=voice_status)


def render_watch_prompt(iteration_count: int, goal: str, exec_status: str, context: str) -> str:
    """Render the watch mode prompt template.

    Args:
        iteration_count: Current watch iteration number
        goal: User-defined watch goal
        exec_status: Exec terminal status string
        context: Current terminal context

    Returns:
        Rendered watch mode prompt string
    """
    template = _env.get_template('prompts/watch_prompt.j2')
    return template.render(
        iteration_count=iteration_count,
        goal=goal,
        exec_status=exec_status,
        context=context,
    )


def render_safety_evaluation(command: str, history_context: str = None) -> str:
    """Render the safety evaluation prompt for auto mode.

    Args:
        command: The command to evaluate
        history_context: Optional recent command history for context

    Returns:
        Rendered safety evaluation prompt string
    """
    template = _env.get_template('prompts/safety_evaluation.j2')
    return template.render(command=command, history_context=history_context)


def render_report_analysis(language: str) -> str:
    """Render the report analysis prompt for pentest findings.

    Args:
        language: ISO 639-1 language code for output (e.g., "en", "de")

    Returns:
        Rendered report analysis prompt string
    """
    template = _env.get_template('prompts/report_analysis.j2')
    return template.render(language=language)


def render_squash_prompt(keep_section: str, summary_parts: str) -> str:
    """Render the squash prompt for context compression.

    Args:
        keep_section: Optional instruction about what to preserve (includes leading newlines if set)
        summary_parts: The conversation history to summarize (newline-joined)

    Returns:
        Rendered squash prompt string
    """
    template = _env.get_template('prompts/squash_prompt.j2')
    return template.render(keep_section=keep_section, summary_parts=summary_parts)


def get_web_companion_html() -> str:
    """Load and render the web companion HTML template.

    Returns:
        HTML string for the web companion interface
    """
    template = _env.get_template('web_companion.html')
    return template.render()
