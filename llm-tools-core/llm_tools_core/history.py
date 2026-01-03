"""Conversation history access for llm-assistant and llm-guiassistant.

Provides shared access to conversation history stored in SQLite databases.
Supports querying, searching, and grouping by date with source detection.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .xdg import get_config_dir


@dataclass
class ConversationSummary:
    """Summary of a conversation for list display."""
    id: str
    name: Optional[str]
    model: str
    source: str  # 'gui', 'tui', 'inline', 'cli', or '?'
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
    source: str
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
        self.daemon_db_path = self.config_dir / "logs-daemon.db"

    def _get_connection(self, db_path: Path) -> Optional[sqlite3.Connection]:
        """Get a database connection if the database exists."""
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_sources_table(self, conn: sqlite3.Connection) -> None:
        """Ensure conversation_sources table exists in daemon database."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_sources (
                conversation_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                created_at TEXT
            )
        """)
        conn.commit()

    def _get_source_for_conversation(
        self, daemon_conn: Optional[sqlite3.Connection], conversation_id: str
    ) -> str:
        """Get the source for a conversation from the daemon database."""
        if daemon_conn is None:
            return "?"

        try:
            self._ensure_sources_table(daemon_conn)
            row = daemon_conn.execute(
                "SELECT source FROM conversation_sources WHERE conversation_id = ?",
                (conversation_id,)
            ).fetchone()
            return row["source"] if row else "?"
        except sqlite3.Error:
            return "?"

    def _get_sources_for_conversations(
        self, daemon_conn: Optional[sqlite3.Connection], conversation_ids: List[str]
    ) -> Dict[str, str]:
        """Get sources for multiple conversations at once."""
        if daemon_conn is None or not conversation_ids:
            return {cid: "?" for cid in conversation_ids}

        try:
            self._ensure_sources_table(daemon_conn)
            placeholders = ",".join("?" * len(conversation_ids))
            rows = daemon_conn.execute(
                f"SELECT conversation_id, source FROM conversation_sources WHERE conversation_id IN ({placeholders})",
                conversation_ids
            ).fetchall()
            sources = {row["conversation_id"]: row["source"] for row in rows}
            # Fill in missing sources
            for cid in conversation_ids:
                if cid not in sources:
                    sources[cid] = "?"
            return sources
        except sqlite3.Error:
            return {cid: "?" for cid in conversation_ids}

    def record_source(self, conversation_id: str, source: str) -> bool:
        """Record the source for a conversation.

        Args:
            conversation_id: The conversation ID
            source: Source type ('gui', 'tui', 'inline', 'cli')

        Returns:
            True if recorded successfully
        """
        daemon_conn = self._get_connection(self.daemon_db_path)
        if daemon_conn is None:
            # Create the database
            self.config_dir.mkdir(parents=True, exist_ok=True)
            daemon_conn = sqlite3.connect(str(self.daemon_db_path))

        try:
            self._ensure_sources_table(daemon_conn)
            daemon_conn.execute(
                """INSERT OR REPLACE INTO conversation_sources
                   (conversation_id, source, created_at)
                   VALUES (?, ?, ?)""",
                (conversation_id, source, datetime.now(timezone.utc).isoformat())
            )
            daemon_conn.commit()
            return True
        except sqlite3.Error:
            return False
        finally:
            daemon_conn.close()

    def get_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
        source: Optional[str] = None
    ) -> List[ConversationSummary]:
        """Get recent conversations.

        Args:
            limit: Maximum number of conversations to return
            offset: Number of conversations to skip
            source: Optional filter by source ('gui', 'tui', 'inline', 'cli')

        Returns:
            List of conversation summaries ordered by most recent first
        """
        logs_conn = self._get_connection(self.logs_db_path)
        if logs_conn is None:
            return []

        daemon_conn = self._get_connection(self.daemon_db_path)

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

            # Get sources for all conversations
            conversation_ids = [row["id"] for row in rows]
            sources = self._get_sources_for_conversations(daemon_conn, conversation_ids)

            # Filter by source if specified
            summaries = []
            for row in rows:
                conv_source = sources.get(row["id"], "?")
                if source is not None and conv_source != source:
                    continue

                # Create preview from first prompt
                preview = row["first_prompt"] or ""
                if len(preview) > 100:
                    preview = preview[:100] + "..."

                summaries.append(ConversationSummary(
                    id=row["id"],
                    name=row["name"],
                    model=row["model"] or "unknown",
                    source=conv_source,
                    datetime_utc=row["last_datetime"] or "",
                    message_count=row["message_count"] or 0,
                    preview=preview
                ))

            return summaries
        finally:
            logs_conn.close()
            if daemon_conn:
                daemon_conn.close()

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

        daemon_conn = self._get_connection(self.daemon_db_path)

        try:
            # Get conversation metadata
            conv_row = logs_conn.execute(
                "SELECT id, name, model FROM conversations WHERE id = ?",
                (conversation_id,)
            ).fetchone()

            if conv_row is None:
                return None

            # Get source
            source = self._get_source_for_conversation(daemon_conn, conversation_id)

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
                source=source,
                messages=messages
            )
        finally:
            logs_conn.close()
            if daemon_conn:
                daemon_conn.close()

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

        daemon_conn = self._get_connection(self.daemon_db_path)

        try:
            # Use FTS5 for search
            rows = logs_conn.execute("""
                SELECT DISTINCT
                    c.id,
                    c.name,
                    c.model,
                    MAX(r.datetime_utc) as last_datetime,
                    COUNT(r.id) as message_count,
                    snippet(responses_fts, 0, '<mark>', '</mark>', '...', 30) as preview
                FROM responses_fts
                JOIN responses r ON responses_fts.rowid = r.rowid
                JOIN conversations c ON r.conversation_id = c.id
                WHERE responses_fts MATCH ?
                GROUP BY c.id
                ORDER BY last_datetime DESC
                LIMIT ?
            """, (query, limit)).fetchall()

            # Get sources
            conversation_ids = [row["id"] for row in rows]
            sources = self._get_sources_for_conversations(daemon_conn, conversation_ids)

            summaries = []
            for row in rows:
                summaries.append(ConversationSummary(
                    id=row["id"],
                    name=row["name"],
                    model=row["model"] or "unknown",
                    source=sources.get(row["id"], "?"),
                    datetime_utc=row["last_datetime"] or "",
                    message_count=row["message_count"] or 0,
                    preview=row["preview"] or ""
                ))

            return summaries
        except sqlite3.OperationalError:
            # FTS table might not exist
            return []
        finally:
            logs_conn.close()
            if daemon_conn:
                daemon_conn.close()

    def get_grouped_by_date(
        self, limit: int = 50, source: Optional[str] = None
    ) -> Dict[str, List[ConversationSummary]]:
        """Get conversations grouped by date.

        Args:
            limit: Maximum total conversations to return
            source: Optional filter by source ('gui', 'tui', 'inline', 'cli')

        Returns:
            Dict with keys 'Today', 'Yesterday', 'This Week', 'Older'
            containing lists of conversation summaries
        """
        # If source filter is specified, fetch more to account for filtering
        # then trim to limit after filtering
        fetch_limit = limit * 3 if source else limit
        conversations = self.get_conversations(limit=fetch_limit, source=source)
        # Trim to actual limit after filtering
        conversations = conversations[:limit]

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
        """Delete a conversation and its source mapping.

        Args:
            conversation_id: The conversation ID to delete

        Returns:
            True if deleted successfully
        """
        logs_conn = self._get_connection(self.logs_db_path)
        if logs_conn is None:
            return False

        daemon_conn = self._get_connection(self.daemon_db_path)

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

            # Delete from sources table
            if daemon_conn:
                self._ensure_sources_table(daemon_conn)
                daemon_conn.execute(
                    "DELETE FROM conversation_sources WHERE conversation_id = ?",
                    (conversation_id,)
                )
                daemon_conn.commit()

            return True
        except sqlite3.Error:
            return False
        finally:
            logs_conn.close()
            if daemon_conn:
                daemon_conn.close()
