"""Command-line interface for llm-assistant.

This module provides:
- resolve_model_query: Fuzzy model name matching
- main: Entry point with argument parsing
"""

import argparse
import sys
from typing import List, Optional

import llm

from .session import TerminatorAssistantSession


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


def main():
    """Entry point"""
    parser = argparse.ArgumentParser(
        description="Terminator AI Assistant - Terminal assistant for pair programming"
    )
    # llm-compatible model selection flags
    parser.add_argument(
        '-m', '--model',
        help='LLM model to use (e.g., azure/gpt-4.1-mini, gemini-2.5-flash)'
    )
    parser.add_argument(
        '-q', '--query',
        action='append',
        help='Select model by fuzzy matching (can be used multiple times)'
    )
    # Existing flags
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug output for troubleshooting'
    )
    parser.add_argument(
        '--max-context',
        type=int,
        default=None,
        help='Max context tokens before auto-squash (default: auto-detected from model)'
    )
    # Conversation persistence flags (llm-compatible)
    parser.add_argument(
        '-c', '--continue',
        dest='continue_',  # Underscore because 'continue' is a Python keyword
        action='store_true',
        help='Continue the most recent conversation'
    )
    parser.add_argument(
        '--cid', '--conversation',
        dest='conversation_id',
        help='Continue conversation with given ID'
    )
    parser.add_argument(
        '--no-log',
        action='store_true',
        help='Disable conversation logging to database'
    )
    parser.add_argument(
        '--agent',
        action='store_true',
        help='Start in agent mode (agentic, 100 tool iterations)'
    )
    parser.add_argument(
        '--no-exec',
        action='store_true',
        help='Run without exec terminal (works in any terminal, uses asciinema context)'
    )
    args = parser.parse_args()

    # Resolve model: -m flag > query > default
    model_name = args.model
    if not model_name and args.query:
        model_name = resolve_model_query(args.query)
        if not model_name:
            print(f"Error: No model found matching queries {' '.join(args.query)}", file=sys.stderr)
            sys.exit(1)

    session = TerminatorAssistantSession(
        model_name=model_name,
        debug=args.debug,
        max_context_size=args.max_context,
        continue_=args.continue_,
        conversation_id=args.conversation_id,
        no_log=args.no_log,
        agent_mode=args.agent,
        no_exec=args.no_exec
    )
    session.run()
