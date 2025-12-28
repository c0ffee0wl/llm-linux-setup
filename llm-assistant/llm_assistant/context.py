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

from .utils import get_config_dir, ConsoleHelper
from .templates import render

if TYPE_CHECKING:
    from rich.console import Console

# Note: hash_blocks and filter_new_blocks have been moved to llm_tools_core.hashing
# Consumers should import directly: from llm_tools_core import hash_blocks, filter_new_blocks


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
        """
        Estimate token count for all tool schemas.

        Uses char-based estimation (4 chars = 1 token) which is fast and
        consistent. The actual token count varies by model tokenizer, but
        this estimate is sufficient for context window tracking.

        Returns:
            Estimated token count for all tool schemas
        """
        try:
            # Build JSON representation of tool schemas (as sent to API)
            active_tools = self._get_active_tools()
            tool_schemas = []
            for tool in active_tools:
                # Get parameters - handle both input_schema (llm.Tool) and schema (other tools)
                if hasattr(tool, 'input_schema'):
                    params = tool.input_schema
                elif hasattr(tool, 'schema') and isinstance(tool.schema, dict):
                    params = tool.schema.get('parameters', {})
                else:
                    params = {}
                schema = {
                    'name': tool.name,
                    'description': tool.description or '',
                    'parameters': params
                }
                tool_schemas.append(schema)

            tools_json = json.dumps(tool_schemas, indent=2)

            # Estimate tokens using char-based method (4 chars = 1 token)
            tokens = len(tools_json) // 4
            self._debug(f"Estimated tool schemas: {tokens} tokens ({len(active_tools)} tools, {len(tools_json)} chars)")
            return tokens

        except Exception as e:
            # Fallback
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
            # System prompt is already rendered for current mode by Jinja2
            system_prompt_len = len(self.system_prompt)

            if not self.conversation.responses:
                # No responses yet - estimate system prompt + tools
                # System prompt chars / 4, plus measured tool overhead
                base_tokens = system_prompt_len // 4
                tool_tokens = self._tool_token_overhead  # Already measured in __init__
                tokens = base_tokens + tool_tokens
                source = "estimated"
            else:
                # Use the LAST response's tokens (represents current context window)
                last_response = self.conversation.responses[-1]

                if last_response.input_tokens is not None:
                    # API provided accurate token counts
                    source = "API"
                    # Use input_tokens only - output tokens are already included in next request's input
                    # When the next request is made, its input_tokens will include the previous output
                    # So we only need input_tokens to know how much context we're using
                    tokens = last_response.input_tokens
                else:
                    # Fallback: char-based estimation for current context
                    # Sum ALL responses - mirrors how build_messages() reconstructs history
                    source = "estimated"
                    total_chars = system_prompt_len  # System prompt

                    # Sum ALL responses (each API call sends full history)
                    for resp in self.conversation.responses:
                        # User prompt
                        if hasattr(resp, 'prompt') and resp.prompt and resp.prompt.prompt:
                            total_chars += len(resp.prompt.prompt)

                        # Assistant response - handle in-progress for last response
                        if resp is self.conversation.responses[-1] and not getattr(resp, '_done', False):
                            # In-progress: use accumulated chunks to avoid blocking
                            total_chars += len("".join(getattr(resp, '_chunks', [])))
                        else:
                            total_chars += len(resp.text())

                    # Add measured tool overhead
                    tokens = (total_chars // 4) + self._tool_token_overhead

        except Exception as e:
            ConsoleHelper.warning(self.console, f"Token estimation failed: {e}")
            # Ultimate fallback
            tokens = len(self.system_prompt) // 4 + len(self.conversation.responses) * 500
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

            # Save old conversation ID before creating new one
            old_conversation_id = self.conversation.id

            # Create completely fresh conversation
            self.conversation = llm.Conversation(model=self.model)
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

    def _strip_context(self, prompt_text):
        """Remove injected context sections from prompt for clean display.

        Strips:
        - <terminal_context>: Ephemeral terminal captures
        - <conversation_summary>: Squash summaries
        - <retrieved_documents>: RAG search results

        Stripping these for DB storage preserves privacy; stripping for
        web companion shows clean user messages (debug view shows full content).

        Uses XML-style tags for robust parsing (less likely to appear in user content).
        """
        if not prompt_text:
            return prompt_text

        result = prompt_text

        # Remove terminal context section
        result = re.sub(
            r'<terminal_context>.*?</terminal_context>\s*',
            '',
            result,
            flags=re.DOTALL
        )

        # Remove conversation summary section
        result = re.sub(
            r'<conversation_summary>.*?</conversation_summary>\s*',
            '',
            result,
            flags=re.DOTALL
        )

        # Remove RAG retrieved documents section
        result = re.sub(
            r'<retrieved_documents>.*?</retrieved_documents>\s*',
            '',
            result,
            flags=re.DOTALL
        )

        # Clean up multiple consecutive newlines
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()

    def _record_squash_link(self, old_id, new_id):
        """Record link between squashed conversations.

        Stores link in llm's config directory (squash-links.json)
        to allow tracking conversation history across squash boundaries.
        """
        from datetime import datetime
        links_path = get_config_dir() / 'squash-links.json'

        links = {}
        if links_path.exists():
            try:
                links = json.loads(links_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass  # Start fresh if file is corrupted

        links[new_id] = {'previous': old_id, 'squashed_at': datetime.utcnow().isoformat()}
        links_path.write_text(json.dumps(links, indent=2))

    def _load_squash_chain_info(self, conversation_id):
        """Load info about squash chain for a conversation.

        Displays info if this conversation was created from a squash operation.
        """
        links_path = get_config_dir() / 'squash-links.json'
        if not links_path.exists():
            return

        try:
            links = json.loads(links_path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        # Check if this conversation has a previous squash
        if conversation_id in links:
            prev_id = links[conversation_id].get('previous')
            if prev_id:
                self.console.print(f"  (Squashed from: {prev_id})")
