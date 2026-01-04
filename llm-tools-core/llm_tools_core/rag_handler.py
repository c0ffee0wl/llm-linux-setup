"""RAG handler wrapper for llm-assistant and llm-guiassistant.

Provides a thin wrapper around llm-tools-rag for use in GUI and CLI.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# Citation rules appended to RAG context when sources is enabled
RAG_CITATION_RULES = """
---
When using information from the retrieved documents above, you MUST:
1. Add inline citations [1], [2], etc. in your response when referencing information
2. Include a "#### Sources" section at the end of your response
3. Format sources as: [n] /path/to/source

Example output format:
"This is how X works [1]. Another important aspect is Y [2].

#### Sources
[1] /path/to/file.md
[2] /path/to/other.md"
"""


@dataclass
class SearchResult:
    """A RAG search result."""
    content: str
    source: str
    score: float
    metadata: Dict[str, Any]


@dataclass
class AddResult:
    """Result of adding a document to RAG collection."""
    status: str  # 'success', 'skipped', 'error'
    path: str
    chunks: int
    reason: Optional[str]
    error: Optional[str]


class RAGHandler:
    """Wrapper for llm-tools-rag functionality."""

    _available: Optional[bool] = None

    @classmethod
    def available(cls) -> bool:
        """Check if llm-tools-rag is installed.

        Returns:
            True if llm-tools-rag is available
        """
        if cls._available is None:
            try:
                from llm_tools_rag.config import list_collections
                cls._available = True
            except ImportError:
                cls._available = False
        return cls._available

    def list_collections(self) -> List[Dict[str, Any]]:
        """List available RAG collections with metadata.

        Returns:
            List of dicts with 'name', 'chunks', and 'documents' keys
        """
        if not self.available():
            return []

        try:
            from llm_tools_rag import get_collection_list
            return get_collection_list()  # Returns [{name, chunks, documents}]
        except Exception:
            return []

    def search(
        self,
        collection: str,
        query: str,
        top_k: int = 5,
        mode: str = "hybrid",
        filters: Optional[Dict[str, str]] = None
    ) -> List[SearchResult]:
        """Search a RAG collection.

        Args:
            collection: Collection name
            query: Search query
            top_k: Number of results to return
            mode: Search mode ('vector', 'keyword', 'hybrid')
            filters: Optional metadata filters

        Returns:
            List of search results
        """
        if not self.available():
            return []

        try:
            from llm_tools_rag.engine import get_or_create_engine

            engine = get_or_create_engine(collection)
            results = engine.search(
                query,
                top_k=top_k,
                mode=mode,
                filters=filters
            )

            return [
                SearchResult(
                    content=r.get("content", ""),
                    source=r.get("metadata", {}).get("source", "unknown"),
                    score=r.get("score", 0.0),
                    metadata=r.get("metadata", {})
                )
                for r in results
            ]
        except Exception:
            return []

    def add_documents(
        self,
        collection: str,
        path: str,
        refresh: bool = False,
        model: Optional[str] = None
    ) -> AddResult:
        """Add documents to a RAG collection.

        Supports multiple source formats:
        - File paths: /path/to/file.pdf, /path/to/dir/
        - Git repos: git:/local/repo, git:https://github.com/user/repo
        - Web URLs: https://example.com/doc.pdf
        - Glob patterns: *.py

        Args:
            collection: Collection name
            path: Document path, URL, or pattern
            refresh: Force reindex if document exists
            model: Optional embedding model override

        Returns:
            AddResult with status and details
        """
        if not self.available():
            return AddResult(
                status="error",
                path=path,
                chunks=0,
                reason=None,
                error="llm-tools-rag not available"
            )

        try:
            from llm_tools_rag.engine import get_or_create_engine

            engine = get_or_create_engine(collection, model)
            result = engine.add_document(path, refresh=refresh)

            return AddResult(
                status=result.get("status", "error"),
                path=path,
                chunks=result.get("chunks", 0),
                reason=result.get("reason"),
                error=result.get("error")
            )
        except Exception as e:
            return AddResult(
                status="error",
                path=path,
                chunks=0,
                reason=None,
                error=str(e)
            )

    def format_context(
        self,
        results: List[SearchResult],
        max_tokens: int = 4000,
        sources: bool = True
    ) -> str:
        """Format search results for injection into prompt.

        Args:
            results: List of search results
            max_tokens: Approximate max tokens (4 chars per token)
            sources: If True, include source headers and citation rules.
                     If False, include only content without source references.

        Returns:
            Formatted context string
        """
        if not results:
            return ""

        max_chars = max_tokens * 4
        lines = ["## Retrieved Context\n\n"]
        current_chars = len(lines[0])

        if sources:
            # Build deduplicated source map: source_path -> citation_number
            source_to_num: Dict[str, int] = {}
            for result in results:
                if result.source not in source_to_num:
                    source_to_num[result.source] = len(source_to_num) + 1

            for result in results:
                cite_num = source_to_num[result.source]
                source_line = f"### [{cite_num}] {result.source}\n"
                content_preview = result.content

                # Truncate if needed
                available = max_chars - current_chars - len(source_line) - 10
                if available <= 0:
                    break

                if len(content_preview) > available:
                    content_preview = content_preview[:available] + "..."

                lines.append(source_line)
                lines.append(content_preview)
                lines.append("\n\n")

                current_chars += len(source_line) + len(content_preview) + 2

            # Append citation rules
            lines.append(RAG_CITATION_RULES)
        else:
            # No sources - just content
            for result in results:
                content_preview = result.content

                available = max_chars - current_chars - 10
                if available <= 0:
                    break

                if len(content_preview) > available:
                    content_preview = content_preview[:available] + "..."

                lines.append(content_preview)
                lines.append("\n\n")

                current_chars += len(content_preview) + 2

        return "".join(lines)

    def get_collection_info(self, collection: str) -> Optional[Dict[str, Any]]:
        """Get information about a RAG collection.

        Args:
            collection: Collection name

        Returns:
            Dict with collection stats, or None if not found
        """
        if not self.available():
            return None

        try:
            from llm_tools_rag.engine import get_or_create_engine

            engine = get_or_create_engine(collection)
            return engine.get_stats()
        except Exception:
            return None

    def delete_collection(self, collection: str) -> bool:
        """Delete a RAG collection.

        Args:
            collection: Collection name

        Returns:
            True if deleted successfully
        """
        if not self.available():
            return False

        try:
            from llm_tools_rag.engine import get_or_create_engine

            engine = get_or_create_engine(collection)
            engine.delete_collection()
            return True
        except Exception:
            return False
