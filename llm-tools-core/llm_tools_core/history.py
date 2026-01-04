"""Conversation history access for llm-assistant and llm-guiassistant.

Provides shared access to conversation history stored in SQLite databases.
Supports querying, searching, and grouping by date.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .xdg import get_config_dir


def _strip_context_tags(text: str) -> str:
    """Strip injected context tags from text for display."""
    if not text:
        return text
    # Strip all context tag types
    text = re.sub(r'<gui_context>.*?</gui_context>\s*', '', text, flags=re.DOTALL)
    text = re.sub(r'<terminal_context>.*?</terminal_context>\s*', '', text, flags=re.DOTALL)
    text = re.sub(r'<retrieved_documents>.*?</retrieved_documents>\s*', '', text, flags=re.DOTALL)
    text = re.sub(r'<context>.*?</context>\s*', '', text, flags=re.DOTALL)
    return text.strip()


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
                preview = _strip_context_tags(row["first_prompt"] or "")
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

        Args:
            conversation_id: The conversation ID

        Returns:
            Full conversation with all messages, or None if not found
        """
        logs_conn = self._get_connection(self.logs_db_path)
        if logs_conn is None:
            return None

        try:
            # Get conversation metadata
            conv_row = logs_conn.execute(
                "SELECT id, name, model FROM conversations WHERE id = ?",
                (conversation_id,)
            ).fetchone()

            if conv_row is None:
                return None

            # Get all responses (messages)
            response_rows = logs_conn.execute("""
                SELECT id, prompt, response, datetime_utc, input_tokens, output_tokens
                FROM responses
                WHERE conversation_id = ?
                ORDER BY datetime_utc ASC
            """, (conversation_id,)).fetchall()

            messages = []
            for row in response_rows:
                # Add user message (prompt)
                if row["prompt"]:
                    messages.append(Message(
                        id=f"{row['id']}_user",
                        role="user",
                        content=row["prompt"],
                        datetime_utc=row["datetime_utc"] or "",
                        input_tokens=row["input_tokens"]
                    ))
                # Add assistant message (response)
                if row["response"]:
                    messages.append(Message(
                        id=f"{row['id']}_assistant",
                        role="assistant",
                        content=row["response"],
                        datetime_utc=row["datetime_utc"] or "",
                        output_tokens=row["output_tokens"]
                    ))

            return FullConversation(
                id=conv_row["id"],
                name=conv_row["name"],
                model=conv_row["model"] or "unknown",
                messages=messages
            )
        finally:
            logs_conn.close()

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
                preview = _strip_context_tags(row["first_prompt"] or "")
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
        """Delete a conversation.

        Args:
            conversation_id: The conversation ID to delete

        Returns:
            True if deleted successfully
        """
        logs_conn = self._get_connection(self.logs_db_path)
        if logs_conn is None:
            return False

        try:
            # Delete from logs database
            logs_conn.execute(
                "DELETE FROM responses WHERE conversation_id = ?",
                (conversation_id,)
            )
            logs_conn.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,)
            )
            logs_conn.commit()

            return True
        except sqlite3.Error:
            return False
        finally:
            logs_conn.close()
