"""RAG (Retrieval-Augmented Generation) mixin for llm-assistant.

This module provides RAG functionality via llm-tools-rag:
- Collection management (list, add, delete, rebuild)
- Search (one-shot and persistent)
- Context injection
- /rag command handling
"""

from typing import TYPE_CHECKING, Optional

from .ui import Spinner

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
    - _render_system_prompt: method to re-render system prompt
    - _broadcast_rag_status: method to broadcast status
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
        try:
            import llm_tools_rag
            return True
        except ImportError:
            return False

    def _handle_rag_command(self, args: str) -> bool:
        """Handle /rag commands. Returns True to continue REPL."""
        if not self._rag_available():
            self.console.print("[red]RAG not available. Install llm-tools-rag.[/]")
            self.console.print("[dim]Run install-llm-tools.sh or: llm install git+https://github.com/c0ffee0wl/llm-tools-rag[/]")
            return True

        parts = args.strip().split(maxsplit=1)

        if not parts or parts[0] == "":
            # /rag - list collections and show active
            self._rag_list_collections()
        elif parts[0] == "off":
            self.active_rag_collection = None
            self.pending_rag_context = None
            # Re-render system prompt to remove RAG guidance
            self.system_prompt = self._render_system_prompt()
            self._broadcast_rag_status()
            self.console.print("[green]✓[/] RAG deactivated")
        elif parts[0] == "status":
            self._rag_show_status()
        elif parts[0] == "search":
            # /rag search <collection> <query>
            search_args = parts[1] if len(parts) > 1 else ""
            search_parts = search_args.split(maxsplit=1)
            if len(search_parts) == 2:
                self._rag_oneshot_search(search_parts[0], search_parts[1])
            else:
                self.console.print("[red]Usage: /rag search <collection> <query>[/]")
        elif parts[0] == "top-k":
            # /rag top-k <n>
            try:
                self.rag_top_k = int(parts[1]) if len(parts) > 1 else 5
                self.console.print(f"[green]✓[/] RAG top-k set to {self.rag_top_k}")
            except ValueError:
                self.console.print("[red]Invalid top-k value[/]")
        elif parts[0] == "mode":
            # /rag mode <hybrid|vector|keyword>
            mode = parts[1].strip() if len(parts) > 1 else ""
            if mode in ("hybrid", "vector", "keyword"):
                self.rag_search_mode = mode
                self.console.print(f"[green]✓[/] RAG mode set to {mode}")
            else:
                self.console.print("[red]Invalid mode. Use: hybrid, vector, keyword[/]")
        elif parts[0] == "add":
            # /rag add <collection> <path>
            add_args = parts[1] if len(parts) > 1 else ""
            add_parts = add_args.split(maxsplit=1)
            if len(add_parts) == 2:
                self._rag_add_documents(add_parts[0], add_parts[1])
            else:
                self.console.print("[red]Usage: /rag add <collection> <path|git:url|glob>[/]")
        elif parts[0] == "rebuild":
            # /rag rebuild <collection>
            collection = parts[1].strip() if len(parts) > 1 else ""
            if collection:
                self._rag_rebuild_collection(collection)
            else:
                self.console.print("[red]Usage: /rag rebuild <collection>[/]")
        elif parts[0] == "delete":
            # /rag delete <collection>
            collection = parts[1].strip() if len(parts) > 1 else ""
            if collection:
                self._rag_delete_collection(collection)
            else:
                self.console.print("[red]Usage: /rag delete <collection>[/]")
        else:
            # Assume it's a collection name for activation: /rag <collection>
            collection = parts[0]
            self._rag_activate_collection(collection)

        return True

    def _rag_list_collections(self):
        """List available RAG collections."""
        from llm_tools_rag import get_collection_list

        collections = get_collection_list()

        self.console.print("\n[bold]RAG Collections[/]")

        if not collections:
            self.console.print("\n[dim]No RAG collections found[/]")
            self.console.print("[dim]Create with: /rag add <name> <path>[/]")
            return

        for coll in collections:
            name = coll['name']
            chunks = coll.get('chunks', '?')
            docs = coll.get('documents', '?')
            is_active = name == self.active_rag_collection
            active_marker = " [bold green](ACTIVE)[/]" if is_active else ""
            self.console.print(f"  • {name}: {chunks} chunks, {docs} docs{active_marker}")

        if self.active_rag_collection:
            self.console.print(f"\n[green]Active:[/] {self.active_rag_collection}")
            self.console.print(f"[dim]Top-k: {self.rag_top_k}, Mode: {self.rag_search_mode}[/]")
        else:
            self.console.print("\n[dim]RAG not active. Activate with: /rag <collection>[/]")

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
            self.console.print("[yellow]RAG not active[/]")
            self.console.print("[dim]Activate with: /rag <collection>[/]")

    def _rag_activate_collection(self, name: str):
        """Activate a RAG collection for persistent search."""
        from llm_tools_rag import collection_exists

        if not collection_exists(name):
            self.console.print(f"[red]Collection '{name}' not found[/]")
            self.console.print(f"[dim]Create with: /rag add {name} <documents>[/]")
            return

        self.active_rag_collection = name
        # Re-render system prompt to include RAG guidance
        self.system_prompt = self._render_system_prompt()
        self._broadcast_rag_status()
        self.console.print(f"[green]✓[/] RAG activated: {name}")
        self.console.print("[dim]Retrieved context will be injected into every prompt[/]")

    def _rag_oneshot_search(self, collection: str, query: str):
        """One-shot RAG search without activating persistent mode."""
        from llm_tools_rag import collection_exists, search_collection

        if not collection_exists(collection):
            self.console.print(f"[red]Collection '{collection}' not found[/]")
            return

        with Spinner(f"Searching {collection}...", self.console):
            results = search_collection(collection, query, self.rag_top_k, self.rag_search_mode)

        if not results:
            self.console.print("[yellow]No results found[/]")
            return

        # Store for next prompt injection (one-shot mode)
        self.pending_rag_context = self._format_rag_results(results)
        self.console.print(f"[green]✓[/] Found {len(results)} results. Context will be injected into next prompt.")

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

        self.console.print(f"[cyan]{action} collection '{collection}'...[/]")

        try:
            with Spinner(f"Processing {path}...", self.console):
                result = add_to_collection(collection, path)

            if result["status"] == "success":
                self.console.print(f"[green]✓[/] Added {result.get('chunks', '?')} chunks")
                # Auto-activate the collection
                self.active_rag_collection = collection
                # Re-render system prompt to include RAG guidance
                self.system_prompt = self._render_system_prompt()
                self._broadcast_rag_status()
                self.console.print(f"[dim]Collection '{collection}' now active[/]")
            elif result["status"] == "skipped":
                self.console.print(f"[yellow]⊘[/] Skipped: {result.get('reason', 'already indexed')}")
            else:
                self.console.print(f"[red]✗[/] Error: {result.get('error', 'unknown')}")

        except Exception as e:
            self.console.print(f"[red]Error: {e}[/]")

    def _rag_rebuild_collection(self, collection: str):
        """Rebuild a RAG collection's index."""
        from llm_tools_rag import collection_exists, rebuild_collection_index

        if not collection_exists(collection):
            self.console.print(f"[red]Collection '{collection}' not found[/]")
            return

        try:
            with Spinner(f"Rebuilding {collection}...", self.console):
                rebuild_collection_index(collection)
            self.console.print(f"[green]✓[/] Rebuilt index for '{collection}'")
        except Exception as e:
            self.console.print(f"[red]Error rebuilding: {e}[/]")

    def _rag_delete_collection(self, collection: str):
        """Delete a RAG collection."""
        from llm_tools_rag import collection_exists, remove_collection

        if not collection_exists(collection):
            self.console.print(f"[red]Collection '{collection}' not found[/]")
            return

        # Confirm deletion
        self.console.print(f"[yellow]Delete collection '{collection}'? (y/N)[/]")
        confirm = input().strip().lower()
        if confirm != 'y':
            self.console.print("[dim]Cancelled[/]")
            return

        try:
            remove_collection(collection)
            self.console.print(f"[green]✓[/] Deleted collection '{collection}'")

            # Deactivate if was active
            if self.active_rag_collection == collection:
                self.active_rag_collection = None
                # Re-render system prompt to remove RAG guidance
                self.system_prompt = self._render_system_prompt()
                self._broadcast_rag_status()
                self.console.print("[dim]RAG deactivated[/]")

        except Exception as e:
            self.console.print(f"[red]Error deleting: {e}[/]")

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
