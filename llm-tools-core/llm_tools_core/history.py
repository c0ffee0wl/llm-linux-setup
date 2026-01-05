"""Conversation history access for llm-assistant and llm-guiassistant.

Provides shared access to conversation history stored in SQLite databases.
Supports querying, searching, and grouping by date.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import llm
import sqlite_utils

from .xdg import get_config_dir


def strip_context_tags(text: str) -> str:
    """Strip injected context tags from text for display.

    Removes: <gui_context>, <terminal_context>, <retrieved_documents>, <context>
    """
    if not text:
        return text
    # Strip all context tag types
    text = re.sub(r'<gui_context>.*?</gui_context>\s*', '', text, flags=re.DOTALL)
    text = re.sub(r'<terminal_context>.*?</terminal_context>\s*', '', text, flags=re.DOTALL)
    text = re.sub(r'<retrieved_documents>.*?</retrieved_documents>\s*', '', text, flags=re.DOTALL)
    text = re.sub(r'<context>.*?</context>\s*', '', text, flags=re.DOTALL)
    return text.strip()


# Truncation limit for tool results in history display
TOOL_RESULT_TRUNCATE_LIMIT = 2000


def format_tool_call_markdown(
    name: str,
    arguments: Optional[dict] = None,
    result: Optional[str] = None,
) -> str:
    """Format a tool call as markdown for history display.

    Used by both history.py and web_ui_server.py to ensure consistent
    formatting of tool calls in conversation history.

    Args:
        name: Tool name
        arguments: Tool arguments (dict or JSON string)
        result: Tool result output (optional)

    Returns:
        Markdown-formatted tool call string
    """
    parts = [f"\n\n**Tool Call:** `{name}`"]

    if arguments:
        # Handle both dict and string arguments
        args_str = arguments
        if isinstance(arguments, dict):
            args_str = json.dumps(arguments, indent=2)
        parts.append(f"```json\n{args_str}\n```")

    if result:
        # Truncate long results
        if len(result) > TOOL_RESULT_TRUNCATE_LIMIT:
            result = result[:TOOL_RESULT_TRUNCATE_LIMIT] + "\n... (truncated)"
        parts.append(f"\n\n**Result:**\n```\n{result}\n```")

    return "".join(parts)


@dataclass
class ConversationSummary:
    """Summary of a conversation for list display."""
    id: str
    name: Optional[str]
    model: str
    datetime_utc: str
    message_count: int
    preview: str


@dataclass
class Message:
    """A single message in a conversation."""
    id: str
    role: str  # 'user' or 'assistant'
    content: str
    datetime_utc: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass
class FullConversation:
    """Complete conversation with all messages."""
    id: str
    name: Optional[str]
    model: str
    messages: List[Message]
    source: Optional[str] = None  # Origin: "gui", "tui", "cli", "api", or None


class ConversationHistory:
    """Access conversation history from llm-assistant databases."""

    # Date group labels
    GROUP_TODAY = "Today"
    GROUP_YESTERDAY = "Yesterday"
    GROUP_THIS_WEEK = "This Week"
    GROUP_OLDER = "Older"

    def __init__(self, app_name: str = "llm-assistant"):
        """Initialize history access.

        Args:
            app_name: Application name for config directory
        """
        self.app_name = app_name
        self.config_dir = get_config_dir(app_name)
        self.logs_db_path = self.config_dir / "logs.db"

    def _get_connection(self, db_path: Path) -> Optional[sqlite3.Connection]:
        """Get a database connection if the database exists."""
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _get_sqlite_utils_db(self) -> Optional[sqlite_utils.Database]:
        """Get a sqlite_utils Database if the database exists.

        Used by get_conversation() to enable llm.Response.from_row() which
        requires sqlite_utils.Database for proper row access.
        """
        if not self.logs_db_path.exists():
            return None
        return sqlite_utils.Database(self.logs_db_path)

    def get_conversations(
        self,
        limit: int = 50,
        offset: int = 0
    ) -> List[ConversationSummary]:
        """Get recent conversations.

        Args:
            limit: Maximum number of conversations to return
            offset: Number of conversations to skip

        Returns:
            List of conversation summaries ordered by most recent first
        """
        logs_conn = self._get_connection(self.logs_db_path)
        if logs_conn is None:
            return []

        try:
            # Query conversations with their most recent response
            rows = logs_conn.execute("""
                SELECT
                    c.id,
                    c.name,
                    c.model,
                    MAX(r.datetime_utc) as last_datetime,
                    COUNT(r.id) as message_count,
                    (SELECT prompt FROM responses WHERE conversation_id = c.id ORDER BY datetime_utc LIMIT 1) as first_prompt
                FROM conversations c
                LEFT JOIN responses r ON c.id = r.conversation_id
                GROUP BY c.id
                ORDER BY last_datetime DESC
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()

            summaries = []
            for row in rows:
                # Create preview from first prompt, stripping context tags
                preview = strip_context_tags(row["first_prompt"] or "")
                if len(preview) > 100:
                    preview = preview[:100] + "..."

                summaries.append(ConversationSummary(
                    id=row["id"],
                    name=row["name"],
                    model=row["model"] or "unknown",
                    datetime_utc=row["last_datetime"] or "",
                    message_count=row["message_count"] or 0,
                    preview=preview
                ))

            return summaries
        finally:
            logs_conn.close()

    def get_conversation(self, conversation_id: str) -> Optional[FullConversation]:
        """Load a full conversation by ID.

        Uses llm.Response.from_row() to properly load all response data including
        tool calls, attachments, and response_json. Falls back to manual extraction
        if the model plugin isn't installed.

        Args:
            conversation_id: The conversation ID

        Returns:
            Full conversation with all messages, or None if not found
        """
        db = self._get_sqlite_utils_db()
        if db is None:
            return None

        # Get conversation metadata
        conv_rows = list(db["conversations"].rows_where(
            "id = ?", [conversation_id]
        ))
        if not conv_rows:
            return None
        conv_row = conv_rows[0]

        # Get response rows ordered by insertion (using sqlite_utils)
        response_rows = list(db["responses"].rows_where(
            "conversation_id = ?",
            [conversation_id],
            order_by="rowid",
        ))

        # Lazy-loaded tool calls for fallback mode (only fetched if needed)
        tool_calls_by_response: Optional[Dict[str, list]] = None

        def _get_tool_calls_fallback() -> Dict[str, list]:
            """Lazy-load tool calls from database for fallback mode."""
            nonlocal tool_calls_by_response
            if tool_calls_by_response is not None:
                return tool_calls_by_response

            tool_calls_by_response = {}
            try:
                for tc_row in db["tool_calls"].rows_where(
                    "response_id IN (SELECT id FROM responses WHERE conversation_id = ?)",
                    [conversation_id],
                    order_by="response_id, tool_call_id",
                ):
                    resp_id = tc_row["response_id"]
                    if resp_id not in tool_calls_by_response:
                        tool_calls_by_response[resp_id] = []
                    tool_calls_by_response[resp_id].append({
                        "name": tc_row["name"],
                        "arguments": tc_row["arguments"],
                        "tool_call_id": tc_row.get("tool_call_id"),
                    })
            except sqlite3.OperationalError:
                # tool_calls table might not exist in older databases
                pass
            return tool_calls_by_response

        # Lazy-loaded tool results (only fetched if needed)
        tool_results_by_tool_call_id: Optional[Dict[str, str]] = None

        def _get_tool_results_fallback() -> Dict[str, str]:
            """Lazy-load tool results from database, keyed by tool_call_id."""
            nonlocal tool_results_by_tool_call_id
            if tool_results_by_tool_call_id is not None:
                return tool_results_by_tool_call_id

            tool_results_by_tool_call_id = {}
            try:
                for tr_row in db["tool_results"].rows_where(
                    "response_id IN (SELECT id FROM responses WHERE conversation_id = ?)",
                    [conversation_id],
                ):
                    tc_id = tr_row.get("tool_call_id")
                    if tc_id:
                        tool_results_by_tool_call_id[tc_id] = tr_row.get("output", "")
            except sqlite3.OperationalError:
                # tool_results table might not exist in older databases
                pass
            return tool_results_by_tool_call_id

        messages = []
        for row in response_rows:
            response_id = row["id"]

            # Try using llm.Response.from_row() for proper reconstruction
            try:
                response = llm.Response.from_row(db, row)
                prompt_text = response.prompt.prompt
                response_text = response.text()
                tool_calls = [
                    {"name": tc.name, "arguments": tc.arguments, "tool_call_id": tc.tool_call_id}
                    for tc in response.tool_calls()
                ]
                input_tokens = response.input_tokens
                output_tokens = response.output_tokens
            except llm.UnknownModelError:
                # Fall back to manual extraction if model isn't installed
                prompt_text = row.get("prompt")
                response_text = row.get("response")
                tool_calls = _get_tool_calls_fallback().get(response_id, [])
                input_tokens = row.get("input_tokens")
                output_tokens = row.get("output_tokens")

            # Convert to Message format for display
            # User message (prompt)
            if prompt_text:
                messages.append(Message(
                    id=f"{response_id}_user",
                    role="user",
                    content=prompt_text,
                    datetime_utc=row.get("datetime_utc") or "",
                    input_tokens=input_tokens,
                ))

            # Assistant message (response + tool calls)
            content_parts = []
            if response_text:
                content_parts.append(response_text)

            # Format tool calls with results using shared function
            for tc in tool_calls:
                tc_id = tc.get("tool_call_id")
                result_output = None
                if tc_id:
                    results = _get_tool_results_fallback()
                    result_output = results.get(tc_id)

                content_parts.append(format_tool_call_markdown(
                    name=tc["name"],
                    arguments=tc.get("arguments"),
                    result=result_output,
                ))

            if content_parts:
                messages.append(Message(
                    id=f"{response_id}_assistant",
                    role="assistant",
                    content="".join(content_parts),
                    datetime_utc=row.get("datetime_utc") or "",
                    output_tokens=output_tokens,
                ))

        return FullConversation(
            id=conv_row["id"],
            name=conv_row.get("name"),
            model=conv_row.get("model") or "unknown",
            messages=messages,
            source=conv_row.get("source"),
        )

    def search(self, query: str, limit: int = 20) -> List[ConversationSummary]:
        """Search conversations using full-text search.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of matching conversation summaries
        """
        logs_conn = self._get_connection(self.logs_db_path)
        if logs_conn is None:
            return []

        try:
            # Use FTS5 for search - only search assistant responses
            # (avoids matching injected context in user prompts)
            # Note: snippet() doesn't work with external content FTS tables,
            # so we get the first prompt as preview instead
            rows = logs_conn.execute("""
                SELECT DISTINCT
                    c.id,
                    c.name,
                    c.model,
                    MAX(r.datetime_utc) as last_datetime,
                    COUNT(r.id) as message_count,
                    (SELECT prompt FROM responses
                     WHERE conversation_id = c.id
                     ORDER BY datetime_utc LIMIT 1) as first_prompt
                FROM responses_fts
                JOIN responses r ON responses_fts.rowid = r.rowid
                JOIN conversations c ON r.conversation_id = c.id
                WHERE responses_fts MATCH 'response:' || ?
                GROUP BY c.id
                ORDER BY last_datetime DESC
                LIMIT ?
            """, (query, limit)).fetchall()

            summaries = []
            for row in rows:
                # Create preview from first prompt, stripping context tags
                preview = strip_context_tags(row["first_prompt"] or "")
                if len(preview) > 100:
                    preview = preview[:100] + "..."

                summaries.append(ConversationSummary(
                    id=row["id"],
                    name=row["name"],
                    model=row["model"] or "unknown",
                    datetime_utc=row["last_datetime"] or "",
                    message_count=row["message_count"] or 0,
                    preview=preview
                ))

            return summaries
        except sqlite3.OperationalError:
            # FTS table might not exist
            return []
        finally:
            logs_conn.close()

    def get_grouped_by_date(
        self, limit: int = 50
    ) -> Dict[str, List[ConversationSummary]]:
        """Get conversations grouped by date.

        Args:
            limit: Maximum total conversations to return

        Returns:
            Dict with keys 'Today', 'Yesterday', 'This Week', 'Older'
            containing lists of conversation summaries
        """
        conversations = self.get_conversations(limit=limit)

        now = datetime.now(timezone.utc)
        today = now.date()
        yesterday = today - timedelta(days=1)
        week_ago = today - timedelta(days=7)

        grouped: Dict[str, List[ConversationSummary]] = {
            self.GROUP_TODAY: [],
            self.GROUP_YESTERDAY: [],
            self.GROUP_THIS_WEEK: [],
            self.GROUP_OLDER: [],
        }

        for conv in conversations:
            if not conv.datetime_utc:
                grouped[self.GROUP_OLDER].append(conv)
                continue

            try:
                # Parse ISO format datetime
                dt = datetime.fromisoformat(conv.datetime_utc.replace('Z', '+00:00'))
                conv_date = dt.date()

                if conv_date == today:
                    grouped[self.GROUP_TODAY].append(conv)
                elif conv_date == yesterday:
                    grouped[self.GROUP_YESTERDAY].append(conv)
                elif conv_date > week_ago:
                    grouped[self.GROUP_THIS_WEEK].append(conv)
                else:
                    grouped[self.GROUP_OLDER].append(conv)
            except (ValueError, TypeError):
                grouped[self.GROUP_OLDER].append(conv)

        return grouped

    def delete_conversation(self, conversation_id: str) -> bool:
        """Delete a conversation and all related records.

        Handles all foreign key relationships including:
        - responses (triggers auto-update FTS)
        - tool_results_attachments, tool_results, tool_calls, tool_responses
        - prompt_attachments, prompt_fragments, system_fragments

        Args:
            conversation_id: The conversation ID to delete

        Returns:
            True if deleted successfully
        """
        logs_conn = self._get_connection(self.logs_db_path)
        if logs_conn is None:
            return False

        try:
            # Get all response IDs for this conversation
            response_ids = [
                row[0] for row in logs_conn.execute(
                    "SELECT id FROM responses WHERE conversation_id = ?",
                    (conversation_id,)
                ).fetchall()
            ]

            if response_ids:
                placeholders = ",".join("?" * len(response_ids))

                # Delete tool_results_attachments (via tool_results)
                logs_conn.execute(f"""
                    DELETE FROM tool_results_attachments
                    WHERE tool_result_id IN (
                        SELECT id FROM tool_results WHERE response_id IN ({placeholders})
                    )
                """, response_ids)

                # Delete from tables with response_id foreign key
                for table in [
                    "tool_results",
                    "tool_calls",
                    "tool_responses",
                    "prompt_attachments",
                    "prompt_fragments",
                    "system_fragments",
                ]:
                    logs_conn.execute(
                        f"DELETE FROM {table} WHERE response_id IN ({placeholders})",
                        response_ids
                    )

            # Delete responses (FTS triggers handle responses_fts)
            logs_conn.execute(
                "DELETE FROM responses WHERE conversation_id = ?",
                (conversation_id,)
            )

            # Delete conversation
            logs_conn.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,)
            )

            logs_conn.commit()
            return True
        except sqlite3.Error as e:
            logs_conn.rollback()
            return False
        finally:
            logs_conn.close()
