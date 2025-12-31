"""Command-line interface for llm-assistant.

This module provides:
- resolve_model_query: Fuzzy model name matching
- main: Entry point with argument parsing

Note: session.py is imported lazily to avoid loading audio dependencies
(voice.py, sounddevice) when running in daemon mode.
"""

from typing import List, Optional, Tuple

import click
import llm


def resolve_model_query(queries: List[str]) -> Optional[str]:
    """
    Resolve model using fuzzy query matching (like llm -q).
    Returns first model matching ALL query strings.
    """
    if not queries:
        return None
    for model in llm.get_models():
        model_id = model.model_id.lower()
        if all(q.lower() in model_id for q in queries):
            return model.model_id
    return None


@click.command()
@click.option('-m', '--model',
              help='LLM model to use (e.g., azure/gpt-4.1-mini, gemini-2.5-flash)')
@click.option('-q', '--query', multiple=True,
              help='Select model by fuzzy matching (can be used multiple times)')
@click.option('--debug', is_flag=True,
              help='Enable debug output for troubleshooting')
@click.option('--max-context', type=int, default=None,
              help='Max context tokens before auto-squash (default: auto-detected from model)')
@click.option('-c', '--continue', 'continue_', is_flag=True,
              help='Continue the most recent conversation')
@click.option('--cid', '--conversation', 'conversation_id',
              help='Continue conversation with given ID')
@click.option('--no-log', is_flag=True,
              help='Disable conversation logging to database')
@click.option('--agent', is_flag=True,
              help='Start in agent mode (agentic, 100 tool iterations)')
@click.option('--no-exec', is_flag=True,
              help='Run without exec terminal (works in any terminal, uses asciinema context)')
@click.option('--daemon', is_flag=True,
              help='Run as daemon server for headless clients (Unix socket)')
@click.option('--foreground', is_flag=True,
              help='Run daemon in foreground with request logging (for debugging)')
def main(
    model: Optional[str],
    query: Tuple[str, ...],
    debug: bool,
    max_context: Optional[int],
    continue_: bool,
    conversation_id: Optional[str],
    no_log: bool,
    agent: bool,
    no_exec: bool,
    daemon: bool,
    foreground: bool,
):
    """Terminator LLM Assistant - Terminal assistant for pair programming."""
    # Resolve model: -m flag > query > default
    model_name = model
    if not model_name and query:
        model_name = resolve_model_query(list(query))
        if not model_name:
            click.echo(f"Error: No model found matching queries {' '.join(query)}", err=True)
            raise SystemExit(1)

    # Daemon mode: start the socket server instead of interactive session
    if daemon or foreground:
        from .daemon import main as daemon_main
        daemon_main(model_id=model_name, debug=debug, foreground=foreground)
        return

    # Import session here to avoid loading audio dependencies in daemon mode
    from .session import TerminatorAssistantSession

    session = TerminatorAssistantSession(
        model_name=model_name,
        debug=debug,
        max_context_size=max_context,
        continue_=continue_,
        conversation_id=conversation_id,
        no_log=no_log,
        agent_mode=agent,
        no_exec=no_exec
    )
    session.run()
