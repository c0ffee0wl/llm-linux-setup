"""Web UI server for llm-guiassistant.

HTTP + WebSocket server using aiohttp that serves the conversation UI
and handles real-time streaming communication with GUI clients.

Architecture:
- GET / → Serve conversation.html
- GET /static/* → Serve JS/CSS assets
- POST /upload → Accept image uploads
- WS /ws?session=xxx → WebSocket for streaming + commands
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Optional, Set

from aiohttp import web
import llm
import sqlite_utils
from llm import ToolResult

from llm_tools_core.markdown import strip_markdown
from llm_tools_core.xdg import get_config_dir
from llm_tools_core.tool_execution import execute_tool_call
from llm_tools_core import (
    MAX_TOOL_ITERATIONS,
    ConversationHistory,
    AtHandler,
    RAGHandler,
    gather_context,
    format_gui_context,
    strip_context_tags,
    format_tool_call_markdown,
    get_assistant_default_model,
)
from llm_tools_core.hashing import hash_gui_context

from .headless_session import get_tool_implementations
from .utils import get_logs_db_path

if TYPE_CHECKING:
    from .daemon import AssistantDaemon


class WebUIServer:
    """HTTP + WebSocket server for GUI clients."""

    def __init__(self, daemon: "AssistantDaemon", port: int = 8741):
        self.daemon = daemon
        self.port = port
        self.app = web.Application(middlewares=[self._no_cache_middleware])
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None

        # Track WebSocket clients per session
        self.ws_clients: Dict[str, Set[web.WebSocketResponse]] = {}

        # Dedicated executor for LLM operations to avoid starving other async tasks
        self._llm_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")

        # Session state tracking (initialized upfront to avoid race conditions)
        self._gui_context_state: Dict[str, set] = {}  # session_id -> window_hashes (set)
        self._rag_sessions: Dict[str, str] = {}  # session_id -> collection_name

        # Lock for tool toggle operations (protects active_mcp_servers and loaded_optional_tools)
        self._tool_toggle_lock = asyncio.Lock()
        self._rag_sources: Dict[str, bool] = {}  # session_id -> sources flag
        self._session_no_log: Dict[str, bool] = {}  # session_id -> no_log flag
        self._session_temp_files: Dict[str, set] = {}  # session_id -> set of temp file paths

        # Cached database connection (created lazily, closed on stop)
        self._logs_db: Optional[sqlite_utils.Database] = None

        # Server start time for uptime tracking
        self._start_time = time.time()

        # Static assets directory - prefer development source if available
        # Check for LLM_DEV_STATIC env var first (explicit override)
        dev_static = os.environ.get("LLM_DEV_STATIC")
        if dev_static and Path(dev_static).exists():
            self.static_dir = Path(dev_static)
        else:
            # Use static dir relative to this file (works for both installed and source)
            self.static_dir = Path(__file__).parent / "static"

        self._setup_routes()

    @web.middleware
    async def _no_cache_middleware(self, request: web.Request, handler):
        """Add no-cache headers to all responses to ensure fresh JS/CSS."""
        response = await handler(request)
        # Don't modify WebSocket responses
        if not isinstance(response, web.WebSocketResponse):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    def _setup_routes(self):
        """Configure HTTP routes."""
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/ws", self.handle_websocket)
        self.app.router.add_post("/upload", self.handle_upload)
        # Note: /context endpoint removed - daemon captures context directly for guiassistant sessions

        # API routes for health, history, completions, capture, models, and RAG
        self.app.router.add_get("/api/health", self.handle_api_health)
        self.app.router.add_get("/api/models", self.handle_api_models)
        self.app.router.add_get("/api/history", self.handle_api_history)
        self.app.router.add_get("/api/history/search", self.handle_api_history_search)
        self.app.router.add_get("/api/history/{id}", self.handle_api_history_item)
        self.app.router.add_delete("/api/history/{id}", self.handle_api_history_delete)
        self.app.router.add_get("/api/completions", self.handle_api_completions)
        self.app.router.add_post("/api/capture", self.handle_api_capture)
        self.app.router.add_get("/api/thumbnail", self.handle_api_thumbnail)
        self.app.router.add_get("/api/rag/collections", self.handle_api_rag_collections)
        self.app.router.add_post("/api/rag/search", self.handle_api_rag_search)
        self.app.router.add_post("/api/rag/activate", self.handle_api_rag_activate)
        self.app.router.add_post("/api/rag/add", self.handle_api_rag_add)
        self.app.router.add_post("/api/rag/create", self.handle_api_rag_create)
        self.app.router.add_delete("/api/rag/delete/{name}", self.handle_api_rag_delete)
        self.app.router.add_get("/api/tools", self.handle_api_tools)
        self.app.router.add_post("/api/tools/toggle", self.handle_api_tools_toggle)

        # Static file serving
        if self.static_dir.exists():
            self.app.router.add_static("/static/", self.static_dir)

    def _get_logs_db(self, for_executor: bool = False) -> sqlite_utils.Database:
        """Get the conversation logs database.

        Args:
            for_executor: If True, create a new connection for use in executor threads.
                         If False, use the cached main-thread connection.

        IMPORTANT: When for_executor=True, caller MUST close the database connection
        after use to prevent resource leaks:
            db = self._get_logs_db(for_executor=True)
            try:
                # ... use db ...
            finally:
                db.conn.close()
        """
        if for_executor:
            # Create a fresh connection for executor threads (SQLite thread safety)
            from llm import migrations
            db_path = get_logs_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite_utils.Database(db_path)
            # Configure for concurrent access
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")  # WAL-safe durability
            db.execute("PRAGMA busy_timeout=5000")   # Wait 5s for locks
            migrations.migrate(db)
            return db

        # Main thread: use cached connection
        if self._logs_db is None:
            from llm import migrations
            db_path = get_logs_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._logs_db = sqlite_utils.Database(db_path)
            # Enable WAL mode for better concurrent access
            self._logs_db.execute("PRAGMA journal_mode=WAL")
            self._logs_db.execute("PRAGMA synchronous=NORMAL")  # WAL-safe durability
            self._logs_db.execute("PRAGMA busy_timeout=5000")   # Wait 5s for locks
            migrations.migrate(self._logs_db)
        return self._logs_db

    def _should_log(self, session_id: str) -> bool:
        """Check if logging is enabled for this session."""
        return not self._session_no_log.get(session_id, False)

    def _set_no_log(self, session_id: str, no_log: bool):
        """Set no-log flag for a session."""
        self._session_no_log[session_id] = no_log

    def _log_response_to_db(self, response) -> None:
        """Log a response to the database with proper connection cleanup.

        Must be called in an executor to avoid blocking the event loop.
        """
        db = self._get_logs_db(for_executor=True)
        try:
            response.log_to_db(db)
        finally:
            if hasattr(db, 'conn') and db.conn:
                db.conn.close()

    def _get_tool_results_for_conversation(
        self, conversation_id: str
    ) -> dict:
        """Query tool results from DB for a conversation.

        Must be called in an executor to avoid blocking the event loop.

        NOTE: Tool results are stored with the response_id of the response that
        USES them as input (the NEXT response), not the response that made the
        tool calls. So we query all tool_results for the entire conversation
        and key by tool_call_id for correct lookup.

        Args:
            conversation_id: Conversation ID string

        Returns:
            Dict mapping tool_call_id -> output (flat dict, like history.py)
        """
        results: dict = {}
        if not conversation_id:
            return results

        db = None
        try:
            db = self._get_logs_db(for_executor=True)
            # Query all tool_results for this conversation (same pattern as history.py)
            for tr_row in db["tool_results"].rows_where(
                "response_id IN (SELECT id FROM responses WHERE conversation_id = ?)",
                [conversation_id],
            ):
                tc_id = tr_row.get("tool_call_id")
                output = tr_row.get("output")
                if tc_id:
                    results[tc_id] = output or ""
        except Exception:
            pass  # Table might not exist in older databases
        finally:
            if db is not None and hasattr(db, 'conn') and db.conn:
                db.conn.close()

        return results

    def _delete_responses_from_db(
        self,
        response_ids: list,
    ) -> None:
        """Delete responses and all related records from the database.

        Must be called in an executor to avoid blocking the event loop.
        Follows the same pattern as ConversationHistory.delete_conversation.

        Uses the underlying sqlite3 connection for proper transaction handling
        (atomic: all deletions succeed or all fail).

        Args:
            response_ids: List of response ID strings to delete
        """
        if not response_ids:
            return

        db = self._get_logs_db(for_executor=True)
        conn = db.conn  # Get underlying sqlite3 connection for transaction support
        placeholders = ",".join("?" * len(response_ids))

        try:
            # Delete tool_results_attachments (via tool_results subquery)
            conn.execute(f"""
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
                conn.execute(
                    f"DELETE FROM {table} WHERE response_id IN ({placeholders})",
                    response_ids
                )

            # Delete responses (FTS triggers handle responses_fts automatically)
            conn.execute(
                f"DELETE FROM responses WHERE id IN ({placeholders})",
                response_ids
            )

            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.warning(f"Failed to delete responses from DB: {e}")
            # Don't raise - we still want to continue with the edit/regenerate
        finally:
            # Always close the executor connection
            if hasattr(db, 'conn') and db.conn:
                db.conn.close()

    async def handle_index(self, request: web.Request) -> web.Response:
        """Serve the main conversation HTML."""
        html_path = self.static_dir / "conversation.html"
        if html_path.exists():
            return web.FileResponse(html_path)
        return web.Response(
            text="<h1>llm-guiassistant</h1><p>Static assets not found. Run install script.</p>",
            content_type="text/html",
        )

    async def handle_upload(self, request: web.Request) -> web.Response:
        """Handle image uploads via multipart/form-data.

        Saves uploaded file to temp directory and returns the path.
        Session ID can be passed as query param for cleanup tracking.
        """
        try:
            # Get optional session ID for temp file tracking
            session_id = request.query.get("session", "")

            reader = await request.multipart()
            field = await reader.next()

            if field is None or field.name != "file":
                return web.json_response({"error": "No file field"}, status=400)

            # Get original filename for extension
            filename = field.filename or "upload"
            ext = Path(filename).suffix or ".png"

            # Save to temp file with size limit (50MB max)
            MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
            total_size = 0
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=ext, prefix="llm-upload-"
            ) as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_UPLOAD_SIZE:
                        # Clean up partial file
                        f.close()
                        os.unlink(f.name)
                        return web.json_response(
                            {"error": f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)"},
                            status=413
                        )
                    f.write(chunk)
                temp_path = f.name

            # Track temp file for cleanup when session ends
            if session_id:
                if session_id not in self._session_temp_files:
                    self._session_temp_files[session_id] = set()
                self._session_temp_files[session_id].add(temp_path)

            return web.json_response({"path": temp_path})

        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _safe_send_json(self, ws: web.WebSocketResponse, data: dict) -> bool:
        """Safely send JSON to a WebSocket, handling connection errors.

        Returns True if send succeeded, False if connection was closed.
        """
        try:
            if ws.closed:
                return False
            await ws.send_json(data)
            return True
        except (ConnectionResetError, ConnectionAbortedError, RuntimeError):
            # Connection was closed by client
            return False
        except Exception:
            # Other unexpected errors
            return False

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connections for streaming and commands."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Get session ID from query params with validation
        raw_session_id = request.query.get("session", "")
        # Validate session ID: alphanumeric, colon, hyphen, underscore; max 256 chars
        if raw_session_id and len(raw_session_id) <= 256 and re.match(r'^[\w:\-]+$', raw_session_id):
            session_id = raw_session_id
        else:
            session_id = f"browser:{int(time.time() * 1000)}"

        # Track this client
        if session_id not in self.ws_clients:
            self.ws_clients[session_id] = set()
        self.ws_clients[session_id].add(ws)

        # Send connection confirmation with actual current model
        model_id = self.daemon.model_id or get_assistant_default_model()
        await ws.send_json({
            "type": "connected",
            "sessionId": session_id,
            "model": model_id,
        })

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_message(session_id, ws, data)
                    except json.JSONDecodeError:
                        await ws.send_json({
                            "type": "error",
                            "message": "Invalid JSON",
                        })
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE,
                                  web.WSMsgType.CLOSED, web.WSMsgType.CLOSING):
                    break
        finally:
            # Remove client on disconnect
            if session_id in self.ws_clients:
                self.ws_clients[session_id].discard(ws)
                if not self.ws_clients[session_id]:
                    # Last client disconnected - clean up all session state
                    del self.ws_clients[session_id]
                    # Clean up session state to prevent memory leaks
                    if session_id in self._gui_context_state:
                        del self._gui_context_state[session_id]
                    if session_id in self._rag_sessions:
                        del self._rag_sessions[session_id]
                    if session_id in self._rag_sources:
                        del self._rag_sources[session_id]
                    if session_id in self._session_no_log:
                        del self._session_no_log[session_id]
                    # Clean up temp files for this session
                    if session_id in self._session_temp_files:
                        for temp_path in self._session_temp_files[session_id]:
                            try:
                                if os.path.exists(temp_path):
                                    os.unlink(temp_path)
                            except OSError:
                                pass  # Ignore cleanup errors
                        del self._session_temp_files[session_id]

        return ws

    async def _handle_message(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Handle a WebSocket message."""
        msg_type = msg.get("type", "")

        if msg_type == "query":
            await self._handle_query(session_id, ws, msg)
        elif msg_type == "edit":
            await self._handle_edit(session_id, ws, msg)
        elif msg_type == "regenerate":
            await self._handle_regenerate(session_id, ws, msg)
        elif msg_type == "resumeConversation":
            await self._handle_resume_conversation(session_id, ws, msg)
        elif msg_type == "forkConversation":
            await self._handle_fork_conversation(session_id, ws, msg)
        elif msg_type == "stripMarkdown":
            await self._handle_strip_markdown(ws, msg)
        elif msg_type == "getHistory":
            await self._handle_get_history(session_id, ws)
        elif msg_type == "command":
            await self._handle_command(session_id, ws, msg)
        else:
            await ws.send_json({
                "type": "error",
                "message": f"Unknown message type: {msg_type}",
            })

    async def _stream_llm_response(
        self,
        query: str,
        session,
        cancel_flag: threading.Event,
        response_holder: list,
        attachments: Optional[list] = None,
        tool_results: Optional[list] = None,
    ) -> AsyncIterator[str]:
        """Stream LLM response chunks and store response object in holder.

        Features:
        - Runs synchronous LLM in thread pool
        - Bounded queue for backpressure (max 50 pending chunks)
        - Error propagation from thread to async context
        - Cancellation support for clean disconnect handling

        Args:
            response_holder: Mutable list to store response object [response]
                            for tool call checking after iteration completes
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        error: list = [None]

        def sync_producer():
            """Runs in thread - iterates LLM response synchronously."""
            try:
                # Get tools if in assistant mode
                tools = session.get_tools() if hasattr(session, "get_tools") else []
                # Get system prompt with GUI-specific sections (Mermaid diagrams)
                system_prompt = (
                    session.get_system_prompt(gui=True)
                    if hasattr(session, "get_system_prompt")
                    else None
                )

                conversation = session.get_or_create_conversation()

                # Prepare prompt kwargs
                prompt_kwargs = {}
                if tools:
                    prompt_kwargs["tools"] = tools
                if len(conversation.responses) == 0 and system_prompt:
                    prompt_kwargs["system"] = system_prompt
                if attachments:
                    prompt_kwargs["attachments"] = attachments
                if tool_results:
                    prompt_kwargs["tool_results"] = tool_results

                response = conversation.prompt(query, **prompt_kwargs)
                response_holder[0] = response

                for chunk in response:
                    if cancel_flag.is_set():
                        break
                    # Block thread if queue full (backpressure)
                    future = asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
                    future.result(timeout=30)

            except Exception as e:
                import traceback
                logging.error(f"LLM streaming error: {e}\n{traceback.format_exc()}")
                error[0] = e
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        # Start producer in dedicated LLM thread pool
        loop.run_in_executor(self._llm_executor, sync_producer)

        # Yield chunks as they arrive
        try:
            while True:
                chunk = await asyncio.wait_for(queue.get(), timeout=120)
                if chunk is None:
                    if error[0]:
                        raise error[0]
                    break
                text_chunk = chunk if isinstance(chunk, str) else str(chunk)
                yield text_chunk
        except asyncio.CancelledError:
            cancel_flag.set()
            raise

    async def _execute_tool_call(
        self,
        tool_call,
        implementations: Dict[str, Callable],
        ws: web.WebSocketResponse,
        arg_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
        message_id: Optional[str] = None,
    ) -> ToolResult:
        """Execute a single tool call and return the result.

        Uses shared execute_tool_call from llm_tools_core.
        """
        async def emit(event: dict) -> None:
            await self._safe_send_json(ws, event)

        return await execute_tool_call(
            tool_call, implementations, emit, arg_overrides, message_id=message_id
        )

    async def _handle_query(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Handle a query message with streaming response and tool execution."""
        query = msg.get("query", "").strip()
        mode = msg.get("mode", "assistant")
        image_paths = msg.get("images", [])
        no_log = msg.get("noLog", False)

        # Set no-log flag for this session if requested
        if no_log:
            self._set_no_log(session_id, True)

        if not query:
            await self._safe_send_json(ws, {"type": "error", "message": "Empty query"})
            return

        # Create attachments from image paths
        attachments = []
        for path in image_paths:
            if path and os.path.isfile(path):
                try:
                    attachments.append(llm.Attachment(path=path))
                except Exception:
                    pass  # Skip invalid attachments

        # Get or create session in daemon
        state = self.daemon.get_session_state(session_id, source="gui")
        state.touch()
        session = state.session

        # Get tool implementations
        implementations = get_tool_implementations()

        # Add MCP and other active tool implementations if available
        if hasattr(session, "_get_active_external_tools"):
            active_impls = session._get_active_external_tools()
            implementations.update(active_impls)

        # Build prompt with context if available
        full_query = query

        # For guiassistant sessions: Capture fresh GUI context with deduplication
        if session_id.startswith("guiassistant:"):
            # Capture fresh context from X11 (run in executor with timeout)
            loop = asyncio.get_running_loop()
            try:
                gui_context = await asyncio.wait_for(
                    loop.run_in_executor(None, gather_context),
                    timeout=3.0  # 3 second timeout for X11 calls
                )
            except asyncio.TimeoutError:
                gui_context = None  # Skip context on timeout

            if gui_context and gui_context.get('session_type') == 'x11':
                window_hashes = hash_gui_context(gui_context)
                prev_windows = self._gui_context_state.get(session_id, set())

                # Determine what changed
                is_first = not prev_windows  # First message if no previous state
                if window_hashes == prev_windows:
                    # No changes - use compact message
                    context_block = "<gui_context>[Desktop context unchanged]</gui_context>"
                else:
                    # Something changed - format with deduplication
                    new_windows = window_hashes - prev_windows
                    context_block = format_gui_context(
                        gui_context,
                        new_windows,
                        is_first=is_first,
                        include_selection=is_first  # Selection only on first message
                    )
                    # Update state
                    self._gui_context_state[session_id] = window_hashes

                full_query = f"{context_block}\n\n{query}"

        # Inject RAG context if a collection is active
        rag_collection = None
        if hasattr(state.session, "active_rag_collection"):
            rag_collection = state.session.active_rag_collection
        elif hasattr(self, "_rag_sessions") and session_id in self._rag_sessions:
            rag_collection = self._rag_sessions[session_id]

        if rag_collection:
            handler = RAGHandler()
            if handler.available():
                try:
                    # Run RAG operations in executor to avoid blocking event loop
                    loop = asyncio.get_running_loop()
                    results = await loop.run_in_executor(
                        None, lambda: handler.search(rag_collection, query, top_k=5)
                    )
                    if results:
                        # Get sources setting for this session (default True)
                        sources = self._rag_sources.get(session_id, True)
                        rag_context = await loop.run_in_executor(
                            None, lambda: handler.format_context(results, sources=sources)
                        )
                        # Wrap in tags for filtering in display (same format as TUI)
                        full_query = f"<retrieved_documents>\n{rag_context}\n</retrieved_documents>\n\n{full_query}"
                except Exception:
                    pass  # Continue without RAG context on error

        accumulated_text = ""
        message_id = f"msg-{int(time.time() * 1000)}"
        cancel_flag = threading.Event()
        response_holder: list = [None]  # Thread-safe container for response

        try:
            # Stream initial response
            async for chunk in self._stream_llm_response(
                full_query, session, cancel_flag, response_holder, attachments
            ):
                accumulated_text += chunk
                if not await self._safe_send_json(ws, {
                    "type": "text",
                    "content": accumulated_text,
                    "messageId": message_id,
                }):
                    # Client disconnected, stop streaming
                    cancel_flag.set()
                    return

            # Get tool calls from response
            response = response_holder[0]
            tool_calls = []
            if response:
                try:
                    tool_calls = list(response.tool_calls())
                except Exception:
                    pass

            # Tool execution loop
            iteration = 0
            # Build arg overrides based on session settings (always explicit for search_google)
            # Also applies to Microsoft MCP tools for citation post-processing
            sources = self._rag_sources.get(session_id, True)
            arg_overrides = {
                "search_google": {"sources": sources},
                "microsoft_sources": {"sources": sources},
            }

            # Ensure message container exists before tool execution
            # (handles case where model goes straight to tool calls without initial text)
            if tool_calls and not accumulated_text:
                await self._safe_send_json(ws, {
                    "type": "text",
                    "content": "",
                    "messageId": message_id,
                })

            while tool_calls and iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                # Log the CURRENT response BEFORE executing tools and resetting
                # This ensures we capture the initial response with user's prompt
                if self._should_log(session_id) and response_holder[0]:
                    try:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(
                            None, lambda r=response_holder[0]: self._log_response_to_db(r)
                        )
                    except Exception:
                        pass  # Continue without logging on error

                # Execute each tool call and collect results
                tool_results = []
                for tool_call in tool_calls:
                    result = await self._execute_tool_call(
                        tool_call, implementations, ws, arg_overrides,
                        message_id=message_id
                    )
                    tool_results.append(result)

                # Continue conversation with tool results (empty prompt)
                response_holder[0] = None  # Reset for next response
                async for chunk in self._stream_llm_response(
                    "", session, cancel_flag, response_holder, tool_results=tool_results
                ):
                    accumulated_text += chunk
                    if not await self._safe_send_json(ws, {
                        "type": "text",
                        "content": accumulated_text,
                        "messageId": message_id,
                    }):
                        # Client disconnected, stop streaming
                        cancel_flag.set()
                        return

                # Check for more tool calls
                response = response_holder[0]
                tool_calls = []
                if response:
                    try:
                        tool_calls = list(response.tool_calls())
                    except Exception:
                        pass

            # Extract and broadcast thinking traces if present
            response = response_holder[0]
            if response:
                try:
                    # Check for thinking in response_json (Claude extended thinking)
                    response_json = getattr(response, "response_json", None)
                    if response_json:
                        thinking = None
                        # Handle different response formats
                        if isinstance(response_json, dict):
                            thinking = response_json.get("thinking")
                            # Also check content blocks for thinking
                            if not thinking and "content" in response_json:
                                content = response_json["content"]
                                if isinstance(content, list):
                                    thinking_blocks = [
                                        b.get("thinking", "") for b in content
                                        if isinstance(b, dict) and b.get("type") == "thinking"
                                    ]
                                    if thinking_blocks:
                                        thinking = "\n\n".join(thinking_blocks)
                        if thinking:
                            await self._safe_send_json(ws, {
                                "type": "thinking",
                                "content": thinking,
                            })
                except Exception:
                    pass  # Continue without thinking trace on error

            # Log conversation to database (unless no_log is set)
            if self._should_log(session_id) and response_holder[0]:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None, lambda r=response_holder[0]: self._log_response_to_db(r)
                    )
                except Exception:
                    pass  # Continue without logging on error

            # Include conversation ID so frontend can track it for forking
            conv_id = None
            if session.conversation:
                conv_id = session.conversation.id
            await self._safe_send_json(ws, {"type": "done", "conversationId": conv_id})

        except Exception as e:
            cancel_flag.set()
            await self._safe_send_json(ws, {"type": "error", "message": str(e)})

    async def _handle_edit(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Handle edit message - truncate and regenerate."""
        keep_turns = msg.get("keepTurns", 0)
        new_content = msg.get("newContent", "")

        if not new_content:
            await self._safe_send_json(ws, {"type": "error", "message": "No content"})
            return

        state = self.daemon.get_session_state(session_id, source="gui")
        if not state.session.conversation:
            await self._safe_send_json(ws, {"type": "error", "message": "No active conversation"})
            return

        responses = state.session.conversation.responses

        # Delete old responses from database (only if logging is enabled)
        if self._should_log(session_id) and keep_turns < len(responses):
            response_ids = [r.id for r in responses[keep_turns:] if hasattr(r, 'id') and r.id]
            if response_ids:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda ids=response_ids: self._delete_responses_from_db(ids)
                )

        # Truncate in-memory conversation
        if keep_turns < len(responses):
            state.session.conversation.responses = responses[:keep_turns]

        # Now send the edited content as a new query
        await self._handle_query(session_id, ws, {"query": new_content})

    async def _handle_regenerate(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Handle regenerate - pop last response and re-query."""
        user_content = msg.get("userContent", "")

        if not user_content:
            await self._safe_send_json(ws, {"type": "error", "message": "No user content"})
            return

        state = self.daemon.get_session_state(session_id, source="gui")
        if state.session.conversation:
            responses = state.session.conversation.responses

            # Delete last response from database (only if logging is enabled)
            if self._should_log(session_id) and responses:
                last_response = responses[-1]
                if hasattr(last_response, 'id') and last_response.id:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        lambda rid=last_response.id: self._delete_responses_from_db([rid])
                    )

            # Pop from in-memory conversation
            if responses:
                responses.pop()

        # Re-query with the user content
        await self._handle_query(session_id, ws, {"query": user_content})

    def _rebuild_conversation_responses(
        self,
        conversation: llm.Conversation,
        responses: list,
    ) -> None:
        """Attach pre-built Response objects to a conversation.

        This is a helper method used by both resume and fork operations
        to avoid code duplication.

        Args:
            conversation: The Conversation object to attach responses to
            responses: List of llm.Response objects (already built via from_row)
        """
        for response in responses:
            conversation.responses.append(response)

    def _load_conversation_from_db(
        self,
        conversation_id: str,
        require_gui_source: bool = True,
    ) -> dict:
        """Load a conversation and its responses from the database.

        This is a synchronous helper method that should be run in an executor.

        Uses llm.Response.from_row() for proper reconstruction which handles:
        - response_json (required by Vertex/Gemini for conversation continuation)
        - tool_calls (for conversations with tool usage)
        - attachments, fragments, and all other Response properties

        Args:
            conversation_id: The ID of the conversation to load
            require_gui_source: If True, only allow GUI-originated conversations

        Returns:
            dict with keys:
            - conversation_data: The conversation row data
            - responses: List of llm.Response objects (properly reconstructed)
            - error: Error message if failed (other keys will be missing)
        """
        db = self._get_logs_db(for_executor=True)
        try:
            conv_rows = list(db["conversations"].rows_where(
                "id = ?", [conversation_id]
            ))
            if not conv_rows:
                return {"error": "Conversation not found"}

            conv_data = conv_rows[0]

            if require_gui_source and conv_data.get("source") != "gui":
                return {"error": "Can only operate on GUI conversations"}

            response_rows = list(db["responses"].rows_where(
                "conversation_id = ?",
                [conversation_id],
                order_by="rowid",
            ))

            # Build full Response objects using the library's from_row() method
            # This properly handles response_json, tool_calls, attachments, etc.
            try:
                responses = [
                    llm.Response.from_row(db, row)
                    for row in response_rows
                ]
            except Exception as e:
                return {"error": f"Failed to reconstruct responses: {e}"}

            return {
                "conversation_data": conv_data,
                "responses": responses,
            }
        finally:
            if hasattr(db, 'conn') and db.conn:
                db.conn.close()

    async def _handle_resume_conversation(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Resume a historical conversation for editing.

        This loads a conversation from the database into the session state,
        allowing edit/regenerate operations on historical GUI conversations.
        """
        conversation_id = msg.get("conversationId")
        if not conversation_id:
            await ws.send_json({
                "type": "error",
                "message": "No conversationId provided",
            })
            return

        try:
            # Load conversation from database (in executor to avoid blocking)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._load_conversation_from_db(conversation_id)
            )

            if "error" in result:
                await ws.send_json({
                    "type": "error",
                    "message": result["error"],
                })
                return

            conv_data = result["conversation_data"]
            responses = result["responses"]

            # Get the model for this conversation
            model_id = conv_data.get("model", "") or get_assistant_default_model()
            model = llm.get_model(model_id)

            # Create a new conversation with the historical ID
            conversation = llm.Conversation(model=model, id=conversation_id)
            # Set source so future responses are tagged correctly
            conversation.source = "gui"

            # Attach the pre-built Response objects
            self._rebuild_conversation_responses(conversation, responses)

            # Update session state with resumed conversation
            state = self.daemon.get_session_state(session_id, source="gui")
            state.session.conversation = conversation

            await ws.send_json({
                "type": "conversationResumed",
                "conversationId": conversation_id,
                "model": model_id,
            })

        except Exception as e:
            logging.exception("Failed to resume conversation")
            await ws.send_json({
                "type": "error",
                "message": f"Failed to resume: {str(e)}",
            })

    async def _handle_fork_conversation(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Fork a conversation at a specific point.

        Creates a new conversation by cloning everything up to and including
        the specified message index. The new conversation has a new ID and
        becomes the active session.

        Only GUI-originated conversations can be forked.
        """
        conversation_id = msg.get("conversationId")
        fork_at_index = msg.get("forkAtIndex")  # 0-based index into responses

        if not conversation_id:
            await ws.send_json({
                "type": "error",
                "message": "No conversationId provided",
            })
            return

        if fork_at_index is None or fork_at_index < 0:
            await ws.send_json({
                "type": "error",
                "message": "Invalid fork index",
            })
            return

        try:
            # Run all database operations in executor to avoid blocking
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._clone_conversation(conversation_id, fork_at_index)
            )

            if "error" in result:
                await ws.send_json({
                    "type": "error",
                    "message": result["error"],
                })
                return

            new_conv_id = result["new_conversation_id"]
            model_id = result["model"]
            responses = result["responses"]

            # Load the forked conversation into session
            model = llm.get_model(model_id or get_assistant_default_model())
            conversation = llm.Conversation(model=model, id=new_conv_id)
            # Set source so future responses are tagged correctly
            conversation.source = "gui"

            # Attach the pre-built Response objects
            self._rebuild_conversation_responses(conversation, responses)

            # Update session state with forked conversation
            state = self.daemon.get_session_state(session_id, source="gui")
            state.session.conversation = conversation

            await ws.send_json({
                "type": "conversationForked",
                "originalId": conversation_id,
                "newId": new_conv_id,
                "model": model_id,
                "responseCount": len(responses),
            })

        except Exception as e:
            logging.exception("Failed to fork conversation")
            await ws.send_json({
                "type": "error",
                "message": f"Failed to fork: {str(e)}",
            })

    def _clone_conversation(
        self,
        conversation_id: str,
        fork_at_index: int,
    ) -> dict:
        """Clone a conversation up to and including fork_at_index.

        This performs all database operations synchronously (called in executor).
        Returns dict with new_conversation_id, model, and responses (for rebuilding),
        or error.

        Tables cloned:
        - conversations (new ID, same model, source='gui')
        - responses (up to fork_at_index, new IDs, new conversation_id)
        - prompt_attachments (links to same attachment_id)
        - prompt_fragments (links to same fragment_id)
        - system_fragments (links to same fragment_id)
        - tool_responses (links to same tool_id)
        - tool_calls (new IDs)
        - tool_results (new IDs)
        - tool_results_attachments (links to same attachment_id)
        """
        from llm.utils import monotonic_ulid

        db = self._get_logs_db(for_executor=True)
        conn = db.conn  # Get underlying sqlite3 connection for transaction support

        try:
            # Get original conversation
            conv_rows = list(db["conversations"].rows_where(
                "id = ?", [conversation_id]
            ))
            if not conv_rows:
                return {"error": "Conversation not found"}

            conv_data = conv_rows[0]

            # Only allow forking GUI-originated conversations
            if conv_data.get("source") != "gui":
                return {"error": "Can only fork GUI conversations"}

            # Get responses ordered by rowid (insertion order)
            all_responses = list(db["responses"].rows_where(
                "conversation_id = ?",
                [conversation_id],
                order_by="rowid",
            ))

            if fork_at_index >= len(all_responses):
                return {"error": f"Fork index {fork_at_index} out of range (max {len(all_responses) - 1})"}

            # Slice responses up to fork point
            responses_to_clone = all_responses[:fork_at_index + 1]

            # Cache table names for efficiency (avoid repeated calls)
            existing_tables = set(db.table_names())

            # Generate new conversation ID
            new_conv_id = str(monotonic_ulid()).lower()

            # Start transaction for all insertions
            # Insert new conversation
            db["conversations"].insert({
                "id": new_conv_id,
                "name": conv_data.get("name"),
                "model": conv_data.get("model"),
                "source": "gui",
            })

            # Clone responses and track ID mappings for junction tables
            response_id_map = {}
            cloned_responses = []

            for resp in responses_to_clone:
                old_resp_id = resp["id"]
                new_resp_id = str(monotonic_ulid()).lower()
                response_id_map[old_resp_id] = new_resp_id

                # Build cloned response data
                cloned_resp = {
                    "id": new_resp_id,
                    "conversation_id": new_conv_id,
                    "model": resp.get("model"),
                    "prompt": resp.get("prompt"),
                    "system": resp.get("system"),
                    "prompt_json": resp.get("prompt_json"),
                    "options_json": resp.get("options_json"),
                    "response": resp.get("response"),
                    "response_json": resp.get("response_json"),
                    "reply_to_id": resp.get("reply_to_id"),
                    "chat_id": resp.get("chat_id"),
                    "duration_ms": resp.get("duration_ms"),
                    "datetime_utc": resp.get("datetime_utc"),
                    "input_tokens": resp.get("input_tokens"),
                    "output_tokens": resp.get("output_tokens"),
                }
                db["responses"].insert(cloned_resp)
                cloned_responses.append(cloned_resp)

            # Clone junction tables for each response
            for old_resp_id, new_resp_id in response_id_map.items():
                # prompt_attachments (response_id, attachment_id)
                if "prompt_attachments" in existing_tables:
                    for row in db["prompt_attachments"].rows_where(
                        "response_id = ?", [old_resp_id]
                    ):
                        db["prompt_attachments"].insert({
                            "response_id": new_resp_id,
                            "attachment_id": row["attachment_id"],
                        })

                # prompt_fragments (response_id, fragment_id)
                if "prompt_fragments" in existing_tables:
                    for row in db["prompt_fragments"].rows_where(
                        "response_id = ?", [old_resp_id]
                    ):
                        db["prompt_fragments"].insert({
                            "response_id": new_resp_id,
                            "fragment_id": row["fragment_id"],
                        })

                # system_fragments (response_id, fragment_id)
                if "system_fragments" in existing_tables:
                    for row in db["system_fragments"].rows_where(
                        "response_id = ?", [old_resp_id]
                    ):
                        db["system_fragments"].insert({
                            "response_id": new_resp_id,
                            "fragment_id": row["fragment_id"],
                        })

                # tool_responses (response_id, tool_id)
                if "tool_responses" in existing_tables:
                    for row in db["tool_responses"].rows_where(
                        "response_id = ?", [old_resp_id]
                    ):
                        db["tool_responses"].insert({
                            "response_id": new_resp_id,
                            "tool_id": row["tool_id"],
                        })

                # tool_calls (response_id, tool_call_id, name, arguments)
                tool_call_id_map = {}
                if "tool_calls" in existing_tables:
                    for row in db["tool_calls"].rows_where(
                        "response_id = ?", [old_resp_id]
                    ):
                        old_tc_id = row.get("id")
                        new_tc_id = str(monotonic_ulid()).lower() if old_tc_id else None
                        if old_tc_id:
                            tool_call_id_map[old_tc_id] = new_tc_id

                        db["tool_calls"].insert({
                            "id": new_tc_id,
                            "response_id": new_resp_id,
                            "tool_call_id": row.get("tool_call_id"),
                            "name": row.get("name"),
                            "arguments": row.get("arguments"),
                        })

                # tool_results (response_id, tool_result_id, name, output, tool_call_id)
                tool_result_id_map = {}
                if "tool_results" in existing_tables:
                    for row in db["tool_results"].rows_where(
                        "response_id = ?", [old_resp_id]
                    ):
                        old_tr_id = row.get("id")
                        new_tr_id = str(monotonic_ulid()).lower() if old_tr_id else None
                        if old_tr_id:
                            tool_result_id_map[old_tr_id] = new_tr_id

                        # Map tool_call_id if it exists and was cloned
                        old_tool_call_id = row.get("tool_call_id")
                        new_tool_call_id = tool_call_id_map.get(old_tool_call_id, old_tool_call_id)

                        db["tool_results"].insert({
                            "id": new_tr_id,
                            "response_id": new_resp_id,
                            "tool_result_id": row.get("tool_result_id"),
                            "name": row.get("name"),
                            "output": row.get("output"),
                            "tool_call_id": new_tool_call_id,
                        })

                # tool_results_attachments (tool_result_id, attachment_id)
                if "tool_results_attachments" in existing_tables:
                    for old_tr_id, new_tr_id in tool_result_id_map.items():
                        for row in db["tool_results_attachments"].rows_where(
                            "tool_result_id = ?", [old_tr_id]
                        ):
                            db["tool_results_attachments"].insert({
                                "tool_result_id": new_tr_id,
                                "attachment_id": row["attachment_id"],
                            })

            # Commit the transaction
            conn.commit()

            # Build full Response objects for the cloned responses
            # This properly handles response_json, tool_calls, attachments, etc.
            try:
                responses = [
                    llm.Response.from_row(db, resp)
                    for resp in cloned_responses
                ]
            except Exception as e:
                return {"error": f"Failed to reconstruct cloned responses: {e}"}

            return {
                "new_conversation_id": new_conv_id,
                "model": conv_data.get("model"),
                "responses": responses,
            }
        except Exception as e:
            # Rollback on any error
            try:
                conn.rollback()
            except Exception:
                pass
            return {"error": f"Fork failed: {e}"}
        finally:
            # Always close the executor connection
            if hasattr(db, 'conn') and db.conn:
                db.conn.close()

    async def _handle_strip_markdown(
        self,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Handle stripMarkdown request."""
        text = msg.get("text", "")
        request_id = msg.get("requestId", "")

        stripped = strip_markdown(text, preserve_code_blocks=True)

        await ws.send_json({
            "type": "stripped",
            "text": stripped,
            "requestId": request_id,
        })

    async def _handle_get_history(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
    ):
        """Handle getHistory request - return conversation history."""
        state = self.daemon.get_session_state(session_id, source="gui")
        messages = []

        if state.session.conversation:
            # Filter to completed responses only
            responses = [
                r for r in state.session.conversation.responses
                if getattr(r, "_done", True)
            ]

            # Query tool results from DB in batch (run in executor to avoid blocking)
            # Note: Tool results are stored with the NEXT response's ID, so we query
            # by conversation_id and key by tool_call_id (flat dict, like history.py)
            conv_id = state.session.conversation.id
            loop = asyncio.get_running_loop()
            tool_results_by_call_id = await loop.run_in_executor(
                None, lambda: self._get_tool_results_for_conversation(conv_id)
            )

            for r in responses:
                if hasattr(r, "prompt") and r.prompt:
                    prompt_text = r.prompt.prompt or ""
                    # Strip all context tags from prompt using shared function
                    prompt_text = strip_context_tags(prompt_text)
                    if prompt_text.strip():
                        messages.append({"role": "user", "content": prompt_text.strip()})

                # Build assistant message with structured tool calls
                assistant_text = r.text() or ""
                structured_tool_calls = []

                # Extract tool calls with results as structured data
                try:
                    tool_calls = list(r.tool_calls())
                    if tool_calls:
                        logger.debug(f"_handle_get_history: found {len(tool_calls)} tool calls in response")

                    for idx, tc in enumerate(tool_calls):
                        # Generate fallback ID if tool_call_id is None
                        tc_id = tc.tool_call_id or f"tc-hist-{idx}"
                        # Look up result by tool_call_id (flat dict keyed by tool_call_id)
                        result = tool_results_by_call_id.get(tc.tool_call_id) if tc.tool_call_id else None
                        # Historical tool calls are always completed - use empty string if no result
                        structured_tool_calls.append({
                            "id": tc_id,
                            "name": tc.name,
                            "args": tc.arguments if isinstance(tc.arguments, dict) else {},
                            "result": result if result is not None else "",
                        })
                except Exception as e:
                    logger.debug(f"_handle_get_history: error extracting tool calls: {e}")
                    pass  # No tool calls or error accessing them

                if assistant_text.strip() or structured_tool_calls:
                    msg_data = {"role": "assistant", "content": assistant_text.strip()}
                    if structured_tool_calls:
                        msg_data["toolCalls"] = structured_tool_calls
                    messages.append(msg_data)

        await ws.send_json({"type": "history", "messages": messages})

    async def _handle_command(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Handle command messages (new, status, model)."""
        command = msg.get("command", "")
        args = msg.get("args", "")

        if command == "new":
            state = self.daemon.get_session_state(session_id, source="gui")
            state.session.reset_conversation()
            if hasattr(state.session, 'context_hashes'):
                state.session.context_hashes = set()
            # Reset GUI context state so next message shows full context
            if session_id in self._gui_context_state:
                del self._gui_context_state[session_id]
            # Reset no-log flag for new conversation
            if session_id in self._session_no_log:
                del self._session_no_log[session_id]
            await ws.send_json({
                "type": "commandResult",
                "command": "new",
                "success": True,
            })

        elif command == "status":
            state = self.daemon.get_session_state(session_id, source="gui")
            status = {
                "sessionId": session_id,
                "model": self.daemon.model_id or "default",
                "messages": 0,
            }
            if state.session.conversation:
                status["messages"] = len(state.session.conversation.responses)

            await ws.send_json({
                "type": "commandResult",
                "command": "status",
                "data": status,
            })

        elif command == "model":
            if args:
                try:
                    import llm

                    # Run in executor to avoid blocking event loop
                    loop = asyncio.get_running_loop()
                    new_model = await loop.run_in_executor(None, llm.get_model, args)
                    self.daemon.model_id = args
                    state = self.daemon.get_session_state(session_id, source="gui")
                    state.session.model = new_model
                    state.session.model_name = args
                    if state.session.conversation:
                        state.session.conversation.model = new_model
                    await ws.send_json({
                        "type": "commandResult",
                        "command": "model",
                        "success": True,
                        "model": args,
                    })
                except Exception as e:
                    await ws.send_json({
                        "type": "error",
                        "message": f"Failed to switch model: {e}",
                    })
            else:
                await ws.send_json({
                    "type": "commandResult",
                    "command": "model",
                    "model": self.daemon.model_id or "default",
                })

        else:
            await ws.send_json({
                "type": "error",
                "message": f"Unknown command: {command}",
            })

    # --- API Handlers ---

    async def handle_api_health(self, request: web.Request) -> web.Response:
        """Health check endpoint.

        GET /api/health
        Returns: {"status": "ok", "uptime": seconds}
        """
        uptime = int(time.time() - self._start_time) if hasattr(self, '_start_time') else 0
        return web.json_response({
            "status": "ok",
            "uptime": uptime,
            "sessions": len(self.ws_clients),
        })

    async def handle_api_models(self, request: web.Request) -> web.Response:
        """Get available models.

        GET /api/models?provider=azure
        Optional provider filter to match only models from that provider.
        """
        try:
            provider_filter = request.query.get("provider", None)
            current_model = self.daemon.model_id or get_assistant_default_model()

            # Run in executor to avoid blocking event loop
            loop = asyncio.get_running_loop()

            def get_models():
                models = []
                for model in llm.get_models():
                    model_id = model.model_id
                    # Determine provider from model_id
                    if "/" in model_id:
                        provider = model_id.split("/")[0]
                    elif model_id.startswith("gpt-"):
                        provider = "openai"
                    elif model_id.startswith("gemini"):
                        provider = "gemini"
                    elif model_id.startswith("claude"):
                        provider = "anthropic"
                    else:
                        provider = None

                    # Apply provider filter if specified
                    if provider_filter and provider != provider_filter:
                        continue

                    models.append({
                        "id": model_id,
                        "provider": provider,
                        "current": model_id == current_model,
                    })
                return models

            models = await loop.run_in_executor(None, get_models)

            return web.json_response({
                "models": models,
                "current": current_model,
            })
        except Exception as e:
            logger.error(f"Error getting models: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_history(self, request: web.Request) -> web.Response:
        """Get conversation history grouped by date.

        GET /api/history?limit=50
        """
        try:
            limit = int(request.query.get("limit", "50"))

            history = ConversationHistory()
            # Run in executor to avoid blocking event loop (DB query)
            loop = asyncio.get_running_loop()
            grouped = await loop.run_in_executor(
                None, lambda: history.get_grouped_by_date(limit=limit)
            )

            # Convert to JSON-serializable format
            result = {}
            for group_name, conversations in grouped.items():
                result[group_name] = [
                    {
                        "id": c.id,
                        "name": c.name,
                        "datetime_utc": c.datetime_utc,
                        "message_count": c.message_count,
                        "preview": c.preview,
                    }
                    for c in conversations
                ]

            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_history_item(self, request: web.Request) -> web.Response:
        """Get a single conversation by ID.

        GET /api/history/{id}
        """
        try:
            conversation_id = request.match_info["id"]
            history = ConversationHistory()
            # Run in executor to avoid blocking event loop (DB query)
            loop = asyncio.get_running_loop()
            conversation = await loop.run_in_executor(
                None, lambda: history.get_conversation(conversation_id)
            )

            if conversation is None:
                return web.json_response({"error": "Conversation not found"}, status=404)

            # Build messages with structured tool calls
            messages_data = []
            for m in conversation.messages:
                msg_data = {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "datetime_utc": m.datetime_utc,
                }
                # Include structured tool calls if present
                if m.tool_calls:
                    logger.debug(f"handle_api_history_item: message {m.id} has {len(m.tool_calls)} tool calls")
                    msg_data["toolCalls"] = [
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "args": tc.args,
                            # Historical tool calls are always completed - use empty string if no result
                            "result": tc.result if tc.result is not None else "",
                        }
                        for tc in m.tool_calls
                    ]
                messages_data.append(msg_data)

            return web.json_response({
                "id": conversation.id,
                "name": conversation.name,
                "source": conversation.source,
                "messages": messages_data,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_history_delete(self, request: web.Request) -> web.Response:
        """Delete a conversation by ID.

        DELETE /api/history/{id}
        """
        try:
            conversation_id = request.match_info["id"]
            history = ConversationHistory()
            # Run in executor to avoid blocking event loop (DB operation)
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(
                None, lambda: history.delete_conversation(conversation_id)
            )

            if success:
                return web.json_response({"success": True})
            else:
                return web.json_response({"error": "Failed to delete conversation"}, status=500)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_history_search(self, request: web.Request) -> web.Response:
        """Search conversations.

        GET /api/history/search?q=query&limit=20
        """
        try:
            query = request.query.get("q", "")
            limit = int(request.query.get("limit", "20"))

            if not query:
                return web.json_response({"error": "Missing query parameter 'q'"}, status=400)

            history = ConversationHistory()
            # Run in executor to avoid blocking event loop (DB query)
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None, lambda: history.search(query, limit=limit)
            )

            return web.json_response({
                "results": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "datetime_utc": c.datetime_utc,
                        "message_count": c.message_count,
                        "preview": c.preview,
                    }
                    for c in results
                ]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_completions(self, request: web.Request) -> web.Response:
        """Get @ autocomplete suggestions.

        GET /api/completions?prefix=@pdf:&cwd=/path
        """
        try:
            prefix = request.query.get("prefix", "")
            cwd = request.query.get("cwd")

            # Use home directory if cwd not provided or invalid
            # (don't use server's cwd which would be wrong for daemon)
            if not cwd:
                cwd = str(Path.home())
            else:
                cwd_path = Path(cwd)
                if not cwd_path.exists() or not cwd_path.is_dir():
                    cwd = str(Path.home())

            # Strip leading @ if present
            if prefix.startswith("@"):
                prefix = prefix[1:]

            handler = AtHandler(cwd=cwd)
            # Run in executor to avoid blocking event loop (filesystem I/O)
            loop = asyncio.get_running_loop()
            completions = await loop.run_in_executor(
                None, lambda: handler.get_completions(prefix, cwd=cwd)
            )

            return web.json_response({
                "completions": [
                    {
                        "text": c.text,
                        "description": c.description,
                        "type": c.type,
                    }
                    for c in completions
                ]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_capture(self, request: web.Request) -> web.Response:
        """Capture screenshot using llm-tools-capture-screen.

        POST /api/capture
        Body: {"mode": "window", "delay": 3}
        """
        try:
            data = await request.json()
            mode = data.get("mode", "window")
            delay = int(data.get("delay", 3))

            # Clamp delay to 0-60 seconds
            delay = max(0, min(60, delay))

            # Try to import and use capture_screen
            try:
                from llm_tools_capture_screen import capture_screen
                # Run in executor to avoid blocking event loop (subprocess)
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: capture_screen(mode=mode, delay=delay)
                )

                # capture_screen returns llm.ToolOutput with output dict containing path
                if hasattr(result, "output") and isinstance(result.output, dict):
                    path = result.output.get("path")
                    if path:
                        return web.json_response({"path": path})
                elif hasattr(result, "path"):
                    return web.json_response({"path": result.path})
                elif isinstance(result, str):
                    return web.json_response({"path": result})

                return web.json_response({"error": "No path returned"}, status=500)
            except ImportError:
                return web.json_response(
                    {"error": "llm-tools-capture-screen not installed"},
                    status=500
                )
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_thumbnail(self, request: web.Request) -> web.Response:
        """Serve a thumbnail for an image file.

        GET /api/thumbnail?path=/path/to/image.png
        Returns: JPEG thumbnail image
        """
        try:
            import os
            from io import BytesIO

            path = request.query.get("path")
            if not path:
                return web.Response(status=400, text="Missing path parameter")

            # Security: only allow files from temp directories
            # Use realpath to resolve symlinks and prevent traversal attacks
            path = os.path.realpath(path)
            cache_dir = os.path.realpath(os.path.expanduser("~/.cache/"))
            if not (path.startswith("/tmp/") or path.startswith(cache_dir + "/")):
                return web.Response(status=403, text="Access denied")

            if not os.path.isfile(path):
                return web.Response(status=404, text="File not found")

            # Try to use PIL for thumbnail generation
            try:
                from PIL import Image

                loop = asyncio.get_running_loop()

                def generate_thumbnail():
                    with Image.open(path) as img:
                        # Convert to RGB if necessary (for PNG with alpha)
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")
                        # Create thumbnail
                        img.thumbnail((96, 96), Image.Resampling.LANCZOS)
                        buffer = BytesIO()
                        img.save(buffer, format="JPEG", quality=70)
                        return buffer.getvalue()

                thumbnail_data = await loop.run_in_executor(None, generate_thumbnail)
                return web.Response(
                    body=thumbnail_data,
                    content_type="image/jpeg"
                )
            except ImportError:
                # Fallback: serve original file (async to avoid blocking)
                loop = asyncio.get_running_loop()

                def read_file():
                    with open(path, "rb") as f:
                        return f.read()

                file_data = await loop.run_in_executor(None, read_file)
                return web.Response(
                    body=file_data,
                    content_type="image/png"
                )
        except Exception as e:
            return web.Response(status=500, text=str(e))

    async def handle_api_rag_collections(self, request: web.Request) -> web.Response:
        """List RAG collections.

        GET /api/rag/collections
        """
        try:
            handler = RAGHandler()
            if not handler.available():
                return web.json_response({"collections": [], "available": False})

            # Run in executor to avoid blocking event loop (DB query)
            loop = asyncio.get_running_loop()
            collections = await loop.run_in_executor(
                None, handler.list_collections
            )
            return web.json_response({
                "collections": collections,
                "available": True,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_rag_search(self, request: web.Request) -> web.Response:
        """Search a RAG collection.

        POST /api/rag/search
        Body: {"collection": "name", "query": "search text", "top_k": 5}
        """
        try:
            data = await request.json()
            collection = data.get("collection")
            query = data.get("query")
            top_k = data.get("top_k", 5)

            if not collection or not query:
                return web.json_response(
                    {"error": "Missing collection or query"},
                    status=400
                )

            # Validate collection name (alphanumeric, underscore, hyphen; max 64 chars)
            if not re.match(r'^[\w\-]+$', collection) or len(collection) > 64:
                return web.json_response(
                    {"error": "Invalid collection name (alphanumeric, underscore, hyphen only; max 64 chars)"},
                    status=400
                )

            # Validate top_k bounds (reasonable range: 1-100)
            if not isinstance(top_k, int) or top_k < 1 or top_k > 100:
                return web.json_response(
                    {"error": "top_k must be an integer between 1 and 100"},
                    status=400
                )

            handler = RAGHandler()
            if not handler.available():
                return web.json_response(
                    {"error": "llm-tools-rag not installed"},
                    status=500
                )

            # Run in executor to avoid blocking event loop (DB query)
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None, lambda: handler.search(collection, query, top_k=top_k)
            )
            return web.json_response({
                "results": [
                    {
                        "content": r.content,
                        "source": r.source,
                        "score": r.score,
                    }
                    for r in results
                ]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_rag_activate(self, request: web.Request) -> web.Response:
        """Activate or deactivate a RAG collection for the session.

        POST /api/rag/activate
        Body: {"session": "session_id", "collection": "name", "sources": true}
        Body: {"session": "session_id", "collection": null}  # to deactivate
        """
        try:
            data = await request.json()
            session_id = data.get("session")
            collection = data.get("collection")  # Can be None to deactivate
            sources = data.get("sources", True)

            if not session_id:
                return web.json_response(
                    {"error": "Missing session"},
                    status=400
                )

            # Validate collection name if provided (None is allowed to deactivate)
            if collection is not None:
                if not re.match(r'^[\w\-]+$', collection) or len(collection) > 64:
                    return web.json_response(
                        {"error": "Invalid collection name (alphanumeric, underscore, hyphen only; max 64 chars)"},
                        status=400
                    )

            # Store active collection for this session (None = deactivate)
            state = self.daemon.get_session_state(session_id, source="gui")
            if hasattr(state.session, "active_rag_collection"):
                state.session.active_rag_collection = collection
            else:
                # Store on server if session doesn't have the attribute
                if collection:
                    self._rag_sessions[session_id] = collection
                elif session_id in self._rag_sessions:
                    del self._rag_sessions[session_id]

            # Store sources preference (always, affects both RAG and web search)
            self._rag_sources[session_id] = sources

            return web.json_response({"ok": True, "collection": collection})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_rag_add(self, request: web.Request) -> web.Response:
        """Add documents to a RAG collection.

        POST /api/rag/add
        Body: {"collection": "name", "path": "path/or/url"}

        Supported path formats:
        - /path/to/file.pdf, /path/to/dir/
        - git:/path/to/local/repo
        - git:https://github.com/user/repo
        - https://example.com/doc.pdf
        - *.py (glob patterns)
        """
        try:
            data = await request.json()
            collection = data.get("collection", "").strip()
            path = data.get("path", "").strip()
            refresh = data.get("refresh", False)

            if not collection or not path:
                return web.json_response(
                    {"error": "Missing collection or path"},
                    status=400
                )

            # Validate collection name (alphanumeric, underscore, hyphen; max 64 chars)
            if not re.match(r'^[\w\-]+$', collection) or len(collection) > 64:
                return web.json_response(
                    {"error": "Invalid collection name (alphanumeric, underscore, hyphen only; max 64 chars)"},
                    status=400
                )

            # Validate path based on type
            is_url = path.startswith('http://') or path.startswith('https://')
            is_git = path.startswith('git:')
            is_local = not is_url and not is_git

            if is_local:
                # Security: validate local file paths
                # Resolve the path and ensure it's in allowed directories
                expanded = os.path.expanduser(path)
                # Handle glob patterns: use the directory part for validation
                if '*' in expanded or '?' in expanded:
                    # For globs, validate the base directory
                    import glob as glob_module
                    base_dir = os.path.dirname(expanded.split('*')[0].split('?')[0])
                    if base_dir:
                        real_base = os.path.realpath(base_dir)
                    else:
                        real_base = os.path.realpath('.')
                else:
                    real_base = os.path.realpath(expanded)

                home_dir = os.path.realpath(os.path.expanduser('~'))
                # Allow: home directory and subdirs, /tmp, current working directory
                cwd = os.path.realpath(os.getcwd())
                allowed = (
                    real_base.startswith(home_dir + '/') or
                    real_base == home_dir or
                    real_base.startswith('/tmp/') or
                    real_base.startswith(cwd + '/') or
                    real_base == cwd
                )
                if not allowed:
                    return web.json_response(
                        {"error": "Path must be within home directory, /tmp, or current working directory"},
                        status=403
                    )

            handler = RAGHandler()
            if not handler.available():
                return web.json_response(
                    {"error": "llm-tools-rag not installed"},
                    status=500
                )

            # Run in executor to avoid blocking event loop (DB/network I/O)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, lambda: handler.add_documents(collection, path, refresh=refresh)
            )
            # Handle result defensively in case of unexpected return
            if result is None:
                return web.json_response({"error": "No result from add_documents"}, status=500)
            return web.json_response({
                "status": getattr(result, 'status', 'unknown'),
                "path": getattr(result, 'path', path),
                "chunks": getattr(result, 'chunks', 0),
                "reason": getattr(result, 'reason', None),
                "error": getattr(result, 'error', None),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_rag_create(self, request: web.Request) -> web.Response:
        """Create a new empty RAG collection.

        POST /api/rag/create
        Body: {"name": "collection-name"}
        """
        try:
            data = await request.json()
            name = data.get("name", "").strip()

            if not name:
                return web.json_response(
                    {"error": "Collection name is required"},
                    status=400
                )

            # Validate collection name (alphanumeric, underscore, hyphen; max 64 chars)
            if not re.match(r'^[\w\-]+$', name) or len(name) > 64:
                return web.json_response(
                    {"error": "Invalid collection name (alphanumeric, underscore, hyphen only; max 64 chars)"},
                    status=400
                )

            handler = RAGHandler()
            if not handler.available():
                return web.json_response(
                    {"error": "llm-tools-rag not installed"},
                    status=500
                )

            # Create empty collection by getting/creating the engine
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: __import__('llm_tools_rag.engine', fromlist=['get_or_create_engine']).get_or_create_engine(name)
            )

            return web.json_response({"status": "created", "name": name})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_api_rag_delete(self, request: web.Request) -> web.Response:
        """Delete a RAG collection.

        DELETE /api/rag/delete/{name}
        """
        try:
            name = request.match_info.get("name", "").strip()

            if not name:
                return web.json_response(
                    {"error": "Collection name is required"},
                    status=400
                )

            # Validate collection name (alphanumeric, underscore, hyphen; max 64 chars)
            if not re.match(r'^[\w\-]+$', name) or len(name) > 64:
                return web.json_response(
                    {"error": "Invalid collection name (alphanumeric, underscore, hyphen only; max 64 chars)"},
                    status=400
                )

            handler = RAGHandler()
            if not handler.available():
                return web.json_response(
                    {"error": "llm-tools-rag not installed"},
                    status=500
                )

            # Delete collection
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(
                None, lambda: handler.delete_collection(name)
            )

            if success:
                return web.json_response({"status": "deleted", "name": name})
            else:
                return web.json_response(
                    {"error": f"Failed to delete collection '{name}'"},
                    status=500
                )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # =========================================================================
    # Tools API endpoints
    # =========================================================================

    async def handle_api_tools(self, request: web.Request) -> web.Response:
        """Return tool configuration for the UI.

        GET /api/tools?session=xxx
        Returns MCP servers and optional tools with their enabled status.
        """
        session_id = request.query.get("session", "")
        if not session_id:
            return web.json_response({"error": "session parameter required"}, status=400)

        # Get session state from daemon (creates if not exists)
        state = self.daemon.get_session_state(session_id, source="gui")
        session = state.session

        # Import MCP functions and config
        from .mcp import _all_tools, _ensure_mcp_loaded
        from .config import OPTIONAL_TOOL_PLUGINS
        _ensure_mcp_loaded()

        # Get MCP servers
        mcp_servers = []
        all_servers = session._get_all_mcp_servers()
        active_servers = session.active_mcp_servers
        default_servers = session._get_default_mcp_servers()

        for server in sorted(all_servers):
            tool_count = sum(1 for t in _all_tools.values()
                            if getattr(t, 'server_name', None) == server)
            mcp_servers.append({
                'name': server,
                'enabled': server in active_servers,
                'optional': server not in default_servers,
                'tool_count': tool_count
            })

        # Get optional tools (imagemage, etc.)
        optional_tools = []
        loaded = getattr(session, 'loaded_optional_tools', set())
        for plugin in OPTIONAL_TOOL_PLUGINS:
            optional_tools.append({
                'name': plugin,
                'enabled': plugin in loaded
            })

        return web.json_response({
            'mcp_servers': mcp_servers,
            'optional_tools': optional_tools
        })

    async def handle_api_tools_toggle(self, request: web.Request) -> web.Response:
        """Toggle a tool or MCP server on/off.

        POST /api/tools/toggle
        Body: {"session": "xxx", "type": "mcp"|"optional", "name": "xxx", "enabled": true|false}
        """
        try:
            data = await request.json()
            session_id = data.get('session')
            tool_type = data.get('type')  # 'mcp' or 'optional'
            name = data.get('name')
            enabled = data.get('enabled')

            if not session_id:
                return web.json_response({'error': 'session parameter required'}, status=400)
            if not name:
                return web.json_response({'error': 'name parameter required'}, status=400)
            if tool_type not in ('mcp', 'optional'):
                return web.json_response({'error': 'type must be "mcp" or "optional"'}, status=400)

            state = self.daemon.get_session_state(session_id, source="gui")
            session = state.session

            # Validate tool/server name before modifying state
            if tool_type == 'mcp':
                all_servers = session._get_all_mcp_servers()
                if name not in all_servers:
                    return web.json_response({'error': f'Unknown MCP server: {name}'}, status=400)
            elif tool_type == 'optional':
                from .config import OPTIONAL_TOOL_PLUGINS
                if name not in OPTIONAL_TOOL_PLUGINS:
                    return web.json_response({'error': f'Unknown optional tool: {name}'}, status=400)

            # Use lock to protect concurrent modifications to session state sets
            async with self._tool_toggle_lock:
                if tool_type == 'mcp':
                    if enabled:
                        session.active_mcp_servers.add(name)
                    else:
                        session.active_mcp_servers.discard(name)
                elif tool_type == 'optional':
                    if not hasattr(session, 'loaded_optional_tools'):
                        session.loaded_optional_tools = set()
                    if enabled:
                        session.loaded_optional_tools.add(name)
                    else:
                        session.loaded_optional_tools.discard(name)

            return web.json_response({'success': True, 'name': name, 'enabled': enabled})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def start(self):
        """Start the web server."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        try:
            self.site = web.TCPSite(self.runner, "127.0.0.1", self.port)
            await self.site.start()
        except OSError as e:
            if "Address already in use" in str(e):
                # Try a random port as fallback
                import random

                self.port = random.randint(8750, 8799)
                self.site = web.TCPSite(self.runner, "127.0.0.1", self.port)
                await self.site.start()
            else:
                raise

    async def stop(self):
        """Stop the web server."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        # Shutdown the dedicated LLM executor (wait for pending tasks to complete)
        self._llm_executor.shutdown(wait=True)
        # Clean up all temp files from all sessions
        for session_id, temp_files in self._session_temp_files.items():
            for temp_path in temp_files:
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except OSError:
                    pass  # Ignore cleanup errors
        self._session_temp_files.clear()
        # Close the database connection
        if self._logs_db is not None:
            self._logs_db.close()
            self._logs_db = None

    def get_url(self) -> str:
        """Get the server URL."""
        return f"http://localhost:{self.port}"
