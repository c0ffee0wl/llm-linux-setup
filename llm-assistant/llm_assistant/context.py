"""Context management mixin for llm-assistant.

This module provides context window management:
- Token estimation (API-based and character-based)
- Context squashing (compression of old messages)
- Context stripping (removing ephemeral terminal content)
- Squash chain tracking for conversation continuity
"""

import json
import re
from typing import TYPE_CHECKING, Optional

import llm

from llm_tools_core.tokens import CHARS_PER_TOKEN, estimate_tokens, estimate_tokens_json

from .utils import get_config_dir, ConsoleHelper
from .templates import render

if TYPE_CHECKING:
    from rich.console import Console


# Compiled once at import time; _strip_context runs twice per turn.
_CONTEXT_TAG_PATTERNS = tuple(
    re.compile(rf'<{tag}>.*?</{tag}>\s*', re.DOTALL)
    for tag in (
        'terminal_context',
        'gui_context',
        'conversation_summary',
        'retrieved_documents',
        'watch_prompt',
    )
)
_EXCESS_BLANK_LINES = re.compile(r'\n{3,}')


class ContextMixin:
    """Mixin providing context management functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - conversation: llm.Conversation
    - model: llm model instance
    - system_prompt: str current system prompt
    - original_system_prompt: str for preservation
    - max_context_size: int maximum context tokens
    - context_squash_threshold: float (e.g., 0.8 for 80%)
    - _tool_token_overhead: int estimated tool tokens
    - logging_enabled: bool for database logging
    - terminal_content_hashes: dict for deduplication
    - toolresult_hash_updated: set for hash tracking
    - previous_capture_block_hashes: dict for block hashes
    - rewind_undo_buffer: Optional buffer for undo
    - pending_summary: Optional[str] for squash summary
    - _get_active_tools: method to get active tools
    - _render_system_prompt: method to render system prompt
    - _get_memory_content: method to get AGENTS.md content
    - _get_loaded_kb_content: method to get KB content
    - _get_workflow_context: method to get workflow context
    - _debug: method for debug output
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    conversation: llm.Conversation
    model: object
    system_prompt: str
    original_system_prompt: str
    max_context_size: int
    context_squash_threshold: float
    _tool_token_overhead: int
    logging_enabled: bool
    terminal_content_hashes: dict
    toolresult_hash_updated: set
    previous_capture_block_hashes: dict
    rewind_undo_buffer: Optional[object]
    pending_summary: Optional[str]

    def _estimate_tool_schema_tokens(self) -> int:
        """Estimate token count for all tool schemas as sent to the API."""
        try:
            active_tools = self._get_active_tools()
            tool_schemas = []
            for tool in active_tools:
                if hasattr(tool, 'input_schema'):
                    params = tool.input_schema
                elif hasattr(tool, 'schema') and isinstance(tool.schema, dict):
                    params = tool.schema.get('parameters', {})
                else:
                    params = {}
                tool_schemas.append({
                    'name': tool.name,
                    'description': tool.description or '',
                    'parameters': params,
                })

            tokens = estimate_tokens_json(tool_schemas)
            self._debug(f"Estimated tool schemas: {tokens} tokens ({len(active_tools)} tools)")
            return tokens

        except Exception as e:
            active_tools = self._get_active_tools()
            fallback = len(active_tools) * 200
            self._debug(f"Tool schema measurement exception: {e}, using estimate: {fallback}")
            return fallback

    def estimate_tokens(self, with_source: bool = False):
        """Estimate current context window size in tokens.

        Returns the actual tokens that would be sent on the next API call:
        - Uses the last response's input_tokens + output_tokens (accurate from API)
        - Falls back to char-based estimation if API tokens unavailable

        Note: input_tokens from API is cumulative (includes full conversation history),
        so the last response's tokens represent the current context window size.

        Args:
            with_source: If True, returns tuple (tokens, source) where source is
                        "API" or "estimated". If False, returns just the token count.
        """
        source = "estimated"
        tokens = 0

        try:
            system_prompt_len = len(self.system_prompt)

            if not self.conversation.responses:
                tokens = system_prompt_len // CHARS_PER_TOKEN + self._tool_token_overhead
                source = "estimated"
            else:
                last_response = self.conversation.responses[-1]

                if last_response.input_tokens is not None:
                    # input_tokens is cumulative across the conversation,
                    # so the latest value is the current context size.
                    source = "API"
                    tokens = last_response.input_tokens
                else:
                    source = "estimated"
                    total_chars = system_prompt_len

                    for resp in self.conversation.responses:
                        if hasattr(resp, 'prompt') and resp.prompt and resp.prompt.prompt:
                            total_chars += len(resp.prompt.prompt)

                        if resp is self.conversation.responses[-1] and not getattr(resp, '_done', False):
                            # In-progress: use accumulated chunks to avoid blocking on resp.text()
                            total_chars += len("".join(getattr(resp, '_chunks', [])))
                        else:
                            total_chars += len(resp.text())

                    tokens = (total_chars // CHARS_PER_TOKEN) + self._tool_token_overhead

        except Exception as e:
            ConsoleHelper.warning(self.console, f"Token estimation failed: {e}")
            tokens = estimate_tokens(self.system_prompt) + len(self.conversation.responses) * 500
            source = "estimated"

        return (tokens, source) if with_source else tokens

    def check_and_squash_context(self):
        """Auto-squash when context reaches threshold (like tmuxai)"""
        current_tokens = self.estimate_tokens()

        if current_tokens >= self.max_context_size * self.context_squash_threshold:
            ConsoleHelper.warning(self.console, "Context approaching limit, auto-squashing...")

            # Record pre-squash tokens
            pre_squash_tokens = current_tokens

            self.squash_context()

            # Validate squashing reduced tokens
            post_squash_tokens = self.estimate_tokens()
            tokens_saved = pre_squash_tokens - post_squash_tokens

            if tokens_saved > 0:
                self.console.print(
                    f"[green]✓[/] Context squashed: {pre_squash_tokens:,} → {post_squash_tokens:,} "
                    f"(-{tokens_saved:,} tokens, -{tokens_saved/pre_squash_tokens*100:.1f}%)"
                )
            else:
                # Squashing didn't help (or made it worse!)
                self.console.print(
                    f"[yellow]⚠ Warning: Squashing ineffective[/] "
                    f"(before: {pre_squash_tokens:,}, after: {post_squash_tokens:,})"
                )

                # Still over threshold? Warn user
                if post_squash_tokens >= self.max_context_size * 0.9:  # 90% threshold
                    self.console.print(
                        f"[red]⚠ Context still very large ({post_squash_tokens:,} tokens)[/]"
                    )
                    self.console.print(
                        "[yellow]Consider:[/]\n"
                        "  • Use /reset to clear conversation\n"
                        "  • Use a model with larger context window\n"
                        "  • Reduce terminal content in watch mode"
                    )

    def squash_context(self, keep: Optional[str] = None):
        """Compress earlier messages into summary (like Claude Code's /compact).

        Args:
            keep: Optional instruction for what to preserve (e.g., 'API patterns')
        """
        if len(self.conversation.responses) <= 5:  # Keep at least 5 recent exchanges
            ConsoleHelper.warning(self.console, "Too few messages to squash")
            return

        try:
            # Get responses to squash (all but last 3 - we'll re-execute those)
            responses_to_squash = self.conversation.responses[:-3]

            # Build summary from old responses using public APIs
            summary_parts = []
            for i, response in enumerate(responses_to_squash, 1):
                # Extract prompt text using public API
                prompt_text = ""
                if hasattr(response, 'prompt') and response.prompt:
                    prompt_text = response.prompt.prompt or ""

                # Extract response text using public API
                response_text = response.text()

                if prompt_text:
                    summary_parts.append(f"{i}. User: {prompt_text[:200]}...")
                if response_text:
                    summary_parts.append(f"{i}. AI: {response_text[:200]}...")

            # Build keep instruction if provided
            keep_section = ""
            if keep:
                keep_section = f"\n\nIMPORTANT: Preserve full details about: {keep}"

            # Generate summary using a standalone prompt (not in conversation)
            summary_prompt = render('prompts/squash_prompt.j2',
                keep_section=keep_section,
                summary_parts=chr(10).join(summary_parts),
            )

            summary_response = self.model.prompt(summary_prompt)
            summary = summary_response.text()

            # Create new conversation and update system prompt
            # Re-render system prompt to preserve current watch mode state
            # Store summary for next user message (keeps system prompt clean)
            self.pending_summary = summary
            self._update_system_prompt()

            # Save old conversation ID and source before creating new one
            old_conversation_id = self.conversation.id
            old_source = getattr(self.conversation, 'source', None)

            # Create completely fresh conversation
            self.conversation = llm.Conversation(model=self.model)
            # Preserve source for origin tracking (not a constructor parameter)
            if old_source:
                self.conversation.source = old_source
            new_conversation_id = self.conversation.id

            # Record link between old and new conversation for --continue tracking
            if self.logging_enabled:
                self._record_squash_link(old_conversation_id, new_conversation_id)

            # Clear per-terminal content hashes (summary replaces full history)
            self.terminal_content_hashes.clear()
            self.toolresult_hash_updated.clear()
            self.previous_capture_block_hashes.clear()

            # Clear rewind undo buffer (new conversation = no undo)
            self.rewind_undo_buffer = None

            ConsoleHelper.success(self.console, "Context squashed")
            ConsoleHelper.info(self.console, f"New session: {new_conversation_id}")
            ConsoleHelper.dim(self.console, f"(Previous: {old_conversation_id})")
            ConsoleHelper.info(self.console, "Summary will be included with your next message")

        except Exception as e:
            ConsoleHelper.error(self.console, f"Error squashing context: {e}")

    def _build_system_prompt(self) -> str:
        """Build system prompt with memory, KB, and workflow context appended.

        The base system prompt is rendered by _render_system_prompt() at init/mode change.
        This method appends memory, KB, and workflow context for the current request.
        """
        prompt = self.system_prompt

        # Append memory content (AGENTS.md) - before KB
        memory_content = self._get_memory_content()
        if memory_content:
            memory_instructions = """## Persistent Memory (AGENTS.md)

The `<memory>` section below contains user preferences and project-specific notes that persist across sessions.
- Apply these preferences to personalize responses and follow user conventions
- Project Memory takes precedence over Global Memory for project-specific topics
- Treat memory entries as authoritative user instructions"""
            prompt = f"{prompt}\n\n{memory_instructions}\n\n<memory>\n{memory_content}\n</memory>"

        # Append KB content if any loaded
        kb_content = self._get_loaded_kb_content()
        if kb_content:
            prompt = f"{prompt}\n\n<knowledge>\n# Knowledge Base\n\n{kb_content}\n</knowledge>"

        # Append workflow context if a workflow is active (from WorkflowMixin)
        workflow_context = self._get_workflow_context()
        if workflow_context:
            prompt = f"{prompt}\n\n{workflow_context}"

        return prompt

    def _strip_context(self, prompt_text):
        """Remove injected context sections from prompt for clean display.

        Strips:
        - <terminal_context>: Ephemeral terminal captures
        - <conversation_summary>: Squash summaries
        - <retrieved_documents>: RAG search results
        - <watch_prompt>: Watch mode iteration prompts
        - <gui_context>: GUI assistant context

        Stripping these for DB storage preserves privacy; stripping for
        web companion shows clean user messages (debug view shows full content).

        Uses XML-style tags for robust parsing (less likely to appear in user content).
        """
        if not prompt_text:
            return prompt_text

        result = prompt_text
        for pattern in _CONTEXT_TAG_PATTERNS:
            result = pattern.sub('', result)
        result = _EXCESS_BLANK_LINES.sub('\n\n', result)

        return result.strip()

    def _record_squash_link(self, old_id, new_id):
        """Record link between squashed conversations.

        Stores link in llm's config directory (squash-links.json)
        to allow tracking conversation history across squash boundaries.
        """
        from datetime import datetime, timezone
        links_path = get_config_dir() / 'squash-links.json'

        links = {}
        try:
            links = json.loads(links_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass  # Missing or corrupted file: start fresh

        links[new_id] = {'previous': old_id, 'squashed_at': datetime.now(timezone.utc).isoformat()}
        links_path.write_text(json.dumps(links, indent=2))

    def _load_squash_chain_info(self, conversation_id):
        """Load info about squash chain for a conversation.

        Displays info if this conversation was created from a squash operation.
        """
        links_path = get_config_dir() / 'squash-links.json'
        try:
            links = json.loads(links_path.read_text())
        except (OSError, json.JSONDecodeError):
            return

        # Check if this conversation has a previous squash
        if conversation_id in links:
            prev_id = links[conversation_id].get('previous')
            if prev_id:
                self.console.print(f"  (Squashed from: {prev_id})")
