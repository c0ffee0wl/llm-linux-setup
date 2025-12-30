"""RAG (Retrieval-Augmented Generation) mixin for llm-assistant.

This module provides RAG functionality via llm-tools-rag:
- Collection management (list, add, delete, rebuild)
- Search (one-shot and persistent)
- Context injection
- /rag command handling
"""

from typing import TYPE_CHECKING, Optional

from .ui import Spinner
from .utils import check_import, parse_command, parse_comma_list, ConsoleHelper

if TYPE_CHECKING:
    from rich.console import Console


class RAGMixin:
    """Mixin providing RAG functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - active_rag_collection: Optional[str] for active collection
    - pending_rag_context: Optional[str] for one-shot search results
    - rag_top_k: int for number of results
    - rag_search_mode: str for search mode (hybrid, vector, keyword)
    - system_prompt: str for system prompt
    - _update_system_prompt: method to re-render and broadcast
    - _debug: method for debug output
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    active_rag_collection: Optional[str]
    pending_rag_context: Optional[str]
    rag_top_k: int
    rag_search_mode: str
    system_prompt: str

    def _rag_available(self) -> bool:
        """Check if llm-tools-rag is installed."""
        return check_import("llm_tools_rag")

    def _handle_rag_command(self, args: str) -> bool:
        """Handle /rag commands. Returns True to continue REPL."""
        if not self._rag_available():
            ConsoleHelper.error(self.console, "RAG not available. Install llm-tools-rag.")
            ConsoleHelper.dim(self.console, "Run install-llm-tools.sh or: llm install git+https://github.com/c0ffee0wl/llm-tools-rag")
            return True

        cmd, rest = parse_command(args)

        if cmd == "":
            # /rag - list collections and show active
            self._rag_list_collections()
        elif cmd == "off":
            self.active_rag_collection = None
            self.pending_rag_context = None
            # Re-render system prompt and notify web companion
            self._update_system_prompt(broadcast_type="rag")
            ConsoleHelper.success(self.console, "RAG deactivated")
        elif cmd == "status":
            self._rag_show_status()
        elif cmd == "search":
            # /rag search <collection> <query>
            search_cmd, search_query = parse_command(rest)
            if search_cmd and search_query:
                self._rag_oneshot_search(search_cmd, search_query)
            else:
                ConsoleHelper.error(self.console, "Usage: /rag search <collection> <query>")
        elif cmd == "top-k":
            # /rag top-k <n>
            try:
                self.rag_top_k = int(rest) if rest else 5
                ConsoleHelper.success(self.console, f"RAG top-k set to {self.rag_top_k}")
            except ValueError:
                ConsoleHelper.error(self.console, "Invalid top-k value")
        elif cmd == "mode":
            # /rag mode <hybrid|vector|keyword>
            mode = rest.strip() if rest else ""
            if mode in ("hybrid", "vector", "keyword"):
                self.rag_search_mode = mode
                ConsoleHelper.success(self.console, f"RAG mode set to {mode}")
            else:
                ConsoleHelper.error(self.console, "Invalid mode. Use: hybrid, vector, keyword")
        elif cmd == "add":
            # /rag add <collection> <path>
            add_cmd, add_path = parse_command(rest)
            if add_cmd and add_path:
                self._rag_add_documents(add_cmd, add_path)
            else:
                ConsoleHelper.error(self.console, "Usage: /rag add <collection> <path|git:url|glob>")
        elif cmd == "rebuild":
            # /rag rebuild <collection>
            collection = rest.strip() if rest else ""
            if collection:
                self._rag_rebuild_collection(collection)
            else:
                ConsoleHelper.error(self.console, "Usage: /rag rebuild <collection>")
        elif cmd == "delete":
            # /rag delete <collection>
            collection = rest.strip() if rest else ""
            if collection:
                self._rag_delete_collection(collection)
            else:
                ConsoleHelper.error(self.console, "Usage: /rag delete <collection>")
        else:
            # Assume it's a collection name for activation: /rag <collection>
            self._rag_activate_collection(cmd)

        return True

    def _rag_list_collections(self):
        """List available RAG collections."""
        from llm_tools_rag import get_collection_list

        collections = get_collection_list()

        self.console.print()
        ConsoleHelper.bold(self.console, "RAG Collections")

        if not collections:
            self.console.print()
            ConsoleHelper.dim(self.console, "No RAG collections found")
            ConsoleHelper.dim(self.console, "Create with: /rag add <name> <path>")
            return

        for coll in collections:
            name = coll['name']
            chunks = coll.get('chunks', '?')
            docs = coll.get('documents', '?')
            is_active = name == self.active_rag_collection
            active_marker = " [bold green](ACTIVE)[/]" if is_active else ""
            self.console.print(f"  • {name}: {chunks} chunks, {docs} docs{active_marker}")

        if self.active_rag_collection:
            self.console.print()
            self.console.print(f"[green]Active:[/] {self.active_rag_collection}")
            ConsoleHelper.dim(self.console, f"Top-k: {self.rag_top_k}, Mode: {self.rag_search_mode}")
        else:
            self.console.print()
            ConsoleHelper.dim(self.console, "RAG not active. Activate with: /rag <collection>")

    def _rag_show_status(self):
        """Show current RAG status."""
        if self.active_rag_collection:
            try:
                from llm_tools_rag import get_collection_stats
                stats = get_collection_stats(self.active_rag_collection)
                self.console.print(f"[green]Active collection:[/] {self.active_rag_collection}")
                self.console.print(f"[dim]Chunks:[/] {stats['total_chunks']}")
                self.console.print(f"[dim]Documents:[/] {stats['unique_documents']}")
            except Exception:
                self.console.print(f"[green]Active collection:[/] {self.active_rag_collection}")
            self.console.print(f"[dim]Top-k:[/] {self.rag_top_k}")
            self.console.print(f"[dim]Mode:[/] {self.rag_search_mode}")
        else:
            ConsoleHelper.warning(self.console, "RAG not active")
            ConsoleHelper.dim(self.console, "Activate with: /rag <collection>")

    def _rag_activate_collection(self, name: str):
        """Activate a RAG collection for persistent search."""
        from llm_tools_rag import collection_exists

        if not collection_exists(name):
            ConsoleHelper.error(self.console, f"Collection '{name}' not found")
            ConsoleHelper.dim(self.console, f"Create with: /rag add {name} <documents>")
            return

        self.active_rag_collection = name
        # Re-render system prompt and notify web companion
        self._update_system_prompt(broadcast_type="rag")
        ConsoleHelper.success(self.console, f"RAG activated: {name}")
        ConsoleHelper.dim(self.console, "Retrieved context will be injected into every prompt")

    def _rag_oneshot_search(self, collection: str, query: str):
        """One-shot RAG search without activating persistent mode."""
        from llm_tools_rag import collection_exists, search_collection

        if not collection_exists(collection):
            ConsoleHelper.error(self.console, f"Collection '{collection}' not found")
            return

        with Spinner(f"Searching {collection}...", self.console):
            results = search_collection(collection, query, self.rag_top_k, self.rag_search_mode)

        if not results:
            ConsoleHelper.warning(self.console, "No results found")
            return

        # Store for next prompt injection (one-shot mode)
        self.pending_rag_context = self._format_rag_results(results)
        ConsoleHelper.success(self.console, f"Found {len(results)} results. Context will be injected into next prompt.")

        # Show preview
        for i, chunk in enumerate(results[:3], 1):
            source = chunk.get('metadata', {}).get('source', 'unknown')
            preview = chunk.get('content', '')[:100].replace('\n', ' ') + "..."
            self.console.print(f"[dim]{i}. {source}:[/] {preview}")

    def _rag_add_documents(self, collection: str, path: str):
        """Add documents to a RAG collection (creates if needed)."""
        from llm_tools_rag import add_to_collection, collection_exists

        is_new = not collection_exists(collection)
        action = "Creating" if is_new else "Adding to"

        ConsoleHelper.info(self.console, f"{action} collection '{collection}'...")

        try:
            # No spinner - llm-tools-rag shows its own "Embedding X/Y chunks..." progress
            result = add_to_collection(collection, path)

            if result["status"] == "success":
                ConsoleHelper.success(self.console, f"Added {result.get('chunks', '?')} chunks")
                # Auto-activate the collection
                self.active_rag_collection = collection
                # Re-render system prompt and notify web companion
                self._update_system_prompt(broadcast_type="rag")
                ConsoleHelper.dim(self.console, f"Collection '{collection}' now active")
            elif result["status"] == "skipped":
                ConsoleHelper.warning(self.console, f"Skipped: {result.get('reason', 'already indexed')}")
            else:
                ConsoleHelper.error(self.console, f"Error: {result.get('error', 'unknown')}")

        except Exception as e:
            ConsoleHelper.error(self.console, str(e))

    def _rag_rebuild_collection(self, collection: str):
        """Rebuild a RAG collection's index."""
        from llm_tools_rag import collection_exists, rebuild_collection_index

        if not collection_exists(collection):
            ConsoleHelper.error(self.console, f"Collection '{collection}' not found")
            return

        try:
            with Spinner(f"Rebuilding {collection}...", self.console):
                rebuild_collection_index(collection)
            ConsoleHelper.success(self.console, f"Rebuilt index for '{collection}'")
        except Exception as e:
            ConsoleHelper.error(self.console, f"Error rebuilding: {e}")

    def _rag_delete_collection(self, collection: str):
        """Delete a RAG collection."""
        from llm_tools_rag import collection_exists, remove_collection

        if not collection_exists(collection):
            ConsoleHelper.error(self.console, f"Collection '{collection}' not found")
            return

        # Confirm deletion
        ConsoleHelper.warning(self.console, f"Delete collection '{collection}'? (y/N)")
        confirm = input().strip().lower()
        if confirm != 'y':
            ConsoleHelper.dim(self.console, "Cancelled")
            return

        try:
            remove_collection(collection)
            ConsoleHelper.success(self.console, f"Deleted collection '{collection}'")

            # Deactivate if was active
            if self.active_rag_collection == collection:
                self.active_rag_collection = None
                # Re-render system prompt and notify web companion
                self._update_system_prompt(broadcast_type="rag")
                ConsoleHelper.dim(self.console, "RAG deactivated")

        except Exception as e:
            ConsoleHelper.error(self.console, f"Error deleting: {e}")

    def _retrieve_rag_context(self, query: str) -> str:
        """Retrieve and format RAG context for query."""
        if not self.active_rag_collection:
            return ""

        try:
            from llm_tools_rag import search_collection
            results = search_collection(
                self.active_rag_collection,
                query,
                top_k=self.rag_top_k,
                mode=self.rag_search_mode
            )

            if not results:
                return ""

            return self._format_rag_results(results)
        except Exception as e:
            self._debug(f"RAG retrieval error: {e}")
            return ""

    def _format_rag_results(self, results: list) -> str:
        """Format retrieved chunks for context injection."""
        if not results:
            return ""

        parts = ["<retrieved_documents>"]
        for i, r in enumerate(results, 1):
            source = r.get('metadata', {}).get('source', 'unknown')
            content = r.get('content', '')
            parts.append(f"\n[{i}. {source}]\n{content}")
        parts.append("\n</retrieved_documents>")

        return "\n".join(parts)
