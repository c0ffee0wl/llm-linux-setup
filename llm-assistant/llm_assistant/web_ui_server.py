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
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Optional, Set

from aiohttp import web
import llm
from llm import ToolResult

from llm_tools_core.markdown import strip_markdown
from llm_tools_core import (
    MAX_TOOL_ITERATIONS,
    ConversationHistory,
    AtHandler,
    RAGHandler,
    gather_context,
    format_gui_context,
)
from llm_tools_core.hashing import hash_gui_context

from .headless_session import get_tool_implementations

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
        self.app.router.add_post("/context", self.handle_context)

        # API routes for history, completions, capture, and RAG
        self.app.router.add_get("/api/history", self.handle_api_history)
        self.app.router.add_get("/api/history/search", self.handle_api_history_search)
        self.app.router.add_get("/api/history/{id}", self.handle_api_history_item)
        self.app.router.add_get("/api/completions", self.handle_api_completions)
        self.app.router.add_post("/api/capture", self.handle_api_capture)
        self.app.router.add_get("/api/rag/collections", self.handle_api_rag_collections)
        self.app.router.add_post("/api/rag/search", self.handle_api_rag_search)
        self.app.router.add_post("/api/rag/activate", self.handle_api_rag_activate)
        self.app.router.add_post("/api/rag/add", self.handle_api_rag_add)

        # Static file serving
        if self.static_dir.exists():
            self.app.router.add_static("/static/", self.static_dir)

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
        """
        try:
            reader = await request.multipart()
            field = await reader.next()

            if field is None or field.name != "file":
                return web.json_response({"error": "No file field"}, status=400)

            # Get original filename for extension
            filename = field.filename or "upload"
            ext = Path(filename).suffix or ".png"

            # Save to temp file
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=ext, prefix="llm-upload-"
            ) as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
                temp_path = f.name

            return web.json_response({"path": temp_path})

        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_context(self, request: web.Request) -> web.Response:
        """Handle context updates from GTK client.

        POST body: {"session": "...", "context": {...}}
        """
        try:
            data = await request.json()
            session_id = data.get("session")
            context = data.get("context", {})

            if session_id:
                # Store context for this session
                # Context will be used when the session sends its first query
                if not hasattr(self, "_session_contexts"):
                    self._session_contexts: Dict[str, dict] = {}
                self._session_contexts[session_id] = context

            return web.json_response({"ok": True})

        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connections for streaming and commands."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Get session ID from query params
        session_id = request.query.get("session", f"browser:{int(time.time() * 1000)}")

        # Track this client
        if session_id not in self.ws_clients:
            self.ws_clients[session_id] = set()
        self.ws_clients[session_id].add(ws)

        # Send connection confirmation
        model_id = self.daemon.model_id or "default"
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
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            # Remove client on disconnect
            if session_id in self.ws_clients:
                self.ws_clients[session_id].discard(ws)
                if not self.ws_clients[session_id]:
                    del self.ws_clients[session_id]

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
        elif msg_type == "branch":
            await self._handle_branch(session_id, ws, msg)
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
                system_prompt = (
                    session.get_system_prompt()
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
                error[0] = e
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        # Start producer in thread pool
        loop.run_in_executor(None, sync_producer)

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
        implementations: Dict[str, callable],
        ws: web.WebSocketResponse,
    ) -> ToolResult:
        """Execute a single tool call and return the result."""
        tool_name = (tool_call.name or "").lower().strip()
        tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        tool_call_id = tool_call.tool_call_id

        # Emit tool start event
        await ws.send_json({
            "type": "tool_start",
            "tool": tool_name,
            "args": tool_args,
        })

        if tool_name not in implementations:
            await ws.send_json({
                "type": "tool_done",
                "tool": tool_name,
                "result": f"Error: Tool '{tool_name}' not available",
            })
            return ToolResult(
                name=tool_call.name,
                output=f"Error: Tool '{tool_name}' not available",
                tool_call_id=tool_call_id,
            )

        try:
            impl = implementations[tool_name]
            result = impl(**tool_args)

            # Handle different result types
            if hasattr(result, "output"):
                output = result.output
            elif isinstance(result, str):
                output = result
            else:
                output = str(result)

            await ws.send_json({
                "type": "tool_done",
                "tool": tool_name,
                "result": output[:500] if len(output) > 500 else output,
            })

            return ToolResult(
                name=tool_call.name,
                output=output,
                tool_call_id=tool_call_id,
            )

        except Exception as e:
            error_msg = f"Error executing {tool_name}: {e}"
            await ws.send_json({
                "type": "tool_done",
                "tool": tool_name,
                "result": error_msg,
            })

            return ToolResult(
                name=tool_call.name,
                output=error_msg,
                tool_call_id=tool_call_id,
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

        if not query:
            await ws.send_json({"type": "error", "message": "Empty query"})
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
        state = self.daemon.get_session_state(session_id)
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
            # Lazy-init state dict (same pattern as _session_contexts)
            if not hasattr(self, "_gui_context_state"):
                self._gui_context_state: Dict[str, tuple] = {}

            # Capture fresh context from X11
            gui_context = gather_context()

            if gui_context and gui_context.get('session_type') == 'x11':
                focused_hash, window_hashes = hash_gui_context(gui_context)
                prev_focused, prev_windows = self._gui_context_state.get(session_id, ("", set()))

                # Determine what changed
                is_first = not prev_focused
                if focused_hash == prev_focused and window_hashes == prev_windows:
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
                    self._gui_context_state[session_id] = (focused_hash, window_hashes)

                full_query = f"{context_block}\n\n{query}"
        else:
            # For non-guiassistant sessions: Use popup-posted context (legacy behavior)
            context = None
            if hasattr(self, "_session_contexts") and session_id in self._session_contexts:
                context = self._session_contexts[session_id]
                # Clear context after first use
                del self._session_contexts[session_id]

            # Add context to query if present (legacy format)
            if context:
                context_str = json.dumps(context, indent=2)
                full_query = f"<gui_context>\n{context_str}\n</gui_context>\n\n{query}"

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
                    results = handler.search(rag_collection, query, top_k=5)
                    if results:
                        rag_context = handler.format_context(results)
                        full_query = f"{rag_context}\n\n{full_query}"
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
                await ws.send_json({
                    "type": "text",
                    "content": accumulated_text,
                    "messageId": message_id,
                })

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
            while tool_calls and iteration < MAX_TOOL_ITERATIONS:
                iteration += 1

                # Execute each tool call and collect results
                tool_results = []
                for tool_call in tool_calls:
                    result = await self._execute_tool_call(
                        tool_call, implementations, ws
                    )
                    tool_results.append(result)

                # Continue conversation with tool results (empty prompt)
                response_holder[0] = None  # Reset for next response
                async for chunk in self._stream_llm_response(
                    "", session, cancel_flag, response_holder, tool_results=tool_results
                ):
                    accumulated_text += chunk
                    await ws.send_json({
                        "type": "text",
                        "content": accumulated_text,
                        "messageId": message_id,
                    })

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
                            await ws.send_json({
                                "type": "thinking",
                                "content": thinking,
                            })
                except Exception:
                    pass  # Continue without thinking trace on error

            await ws.send_json({"type": "done"})

        except Exception as e:
            cancel_flag.set()
            await ws.send_json({"type": "error", "message": str(e)})

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
            await ws.send_json({"type": "error", "message": "No content"})
            return

        state = self.daemon.get_session_state(session_id)
        if not state.session.conversation:
            await ws.send_json({"type": "error", "message": "No active conversation"})
            return

        # Truncate conversation
        responses = state.session.conversation.responses
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
            await ws.send_json({"type": "error", "message": "No user content"})
            return

        state = self.daemon.get_session_state(session_id)
        if state.session.conversation:
            responses = state.session.conversation.responses
            if responses:
                responses.pop()

        # Re-query with the user content
        await self._handle_query(session_id, ws, {"query": user_content})

    async def _handle_branch(
        self,
        session_id: str,
        ws: web.WebSocketResponse,
        msg: dict,
    ):
        """Handle branch conversation."""
        messages = msg.get("messages", [])
        new_session_id = f"{session_id}-branch-{int(time.time() * 1000)}"

        # Fork uses the daemon's fork handler logic
        source_state = self.daemon.get_session_state(session_id)
        if not source_state.session.conversation:
            await ws.send_json({"type": "error", "message": "No conversation to branch"})
            return

        from .headless_session import HeadlessSession

        # Count turns to copy
        turns_to_keep = sum(1 for m in messages if m.get("role") == "assistant")

        # Create new session
        new_session = HeadlessSession(
            model_name=source_state.session.model_name,
            debug=source_state.session.debug,
            terminal_id=new_session_id,
        )

        if turns_to_keep > 0:
            new_session.get_or_create_conversation()
            source_responses = source_state.session.conversation.responses
            new_session.conversation.responses = list(source_responses[:turns_to_keep])

        # Register new session
        from .daemon import SessionState

        new_state = SessionState(new_session_id, new_session)
        self.daemon.sessions[new_session_id] = new_state

        await ws.send_json({
            "type": "branched",
            "newSessionId": new_session_id,
        })

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
        state = self.daemon.get_session_state(session_id)
        messages = []

        if state.session.conversation:
            for r in state.session.conversation.responses:
                # Skip still-streaming responses
                if not getattr(r, "_done", True):
                    continue

                if hasattr(r, "prompt") and r.prompt:
                    prompt_text = r.prompt.prompt or ""
                    # Strip context from prompt (all formats)
                    if "<gui_context>" in prompt_text:
                        prompt_text = re.sub(
                            r"<gui_context>.*?</gui_context>\s*",
                            "",
                            prompt_text,
                            flags=re.DOTALL,
                        )
                    # Legacy <context> tag (for backward compat)
                    if "<context>" in prompt_text:
                        prompt_text = re.sub(
                            r"<context>.*?</context>\s*",
                            "",
                            prompt_text,
                            flags=re.DOTALL,
                        )
                    if "<terminal_context>" in prompt_text:
                        prompt_text = re.sub(
                            r"<terminal_context>.*?</terminal_context>\s*",
                            "",
                            prompt_text,
                            flags=re.DOTALL,
                        )
                    if prompt_text.strip():
                        messages.append({"role": "user", "content": prompt_text.strip()})

                assistant_text = r.text()
                if assistant_text and assistant_text.strip():
                    messages.append({"role": "assistant", "content": assistant_text})

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
            state = self.daemon.get_session_state(session_id)
            state.session.reset_conversation()
            if hasattr(state.session, 'context_hashes'):
                state.session.context_hashes = set()
            await ws.send_json({
                "type": "commandResult",
                "command": "new",
                "success": True,
            })

        elif command == "status":
            state = self.daemon.get_session_state(session_id)
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

                    new_model = llm.get_model(args)
                    self.daemon.model_id = args
                    state = self.daemon.get_session_state(session_id)
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

    async def handle_api_history(self, request: web.Request) -> web.Response:
        """Get conversation history grouped by date.

        GET /api/history?limit=50&source=gui
        """
        try:
            limit = int(request.query.get("limit", "50"))
            source = request.query.get("source")

            history = ConversationHistory()
            # Pass source to get_grouped_by_date for proper filtering before LIMIT
            grouped = history.get_grouped_by_date(limit=limit, source=source)

            # Convert to JSON-serializable format
            result = {}
            for group_name, conversations in grouped.items():
                result[group_name] = [
                    {
                        "id": c.id,
                        "name": c.name,
                        "model": c.model,
                        "source": c.source,
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
            conversation = history.get_conversation(conversation_id)

            if conversation is None:
                return web.json_response({"error": "Conversation not found"}, status=404)

            return web.json_response({
                "id": conversation.id,
                "name": conversation.name,
                "model": conversation.model,
                "source": conversation.source,
                "messages": [
                    {
                        "id": m.id,
                        "role": m.role,
                        "content": m.content,
                        "datetime_utc": m.datetime_utc,
                    }
                    for m in conversation.messages
                ],
            })
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
            results = history.search(query, limit=limit)

            return web.json_response({
                "results": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "model": c.model,
                        "source": c.source,
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
            completions = handler.get_completions(prefix, cwd=cwd)

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
                result = capture_screen(mode=mode, delay=delay)

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

    async def handle_api_rag_collections(self, request: web.Request) -> web.Response:
        """List RAG collections.

        GET /api/rag/collections
        """
        try:
            handler = RAGHandler()
            if not handler.available():
                return web.json_response({"collections": [], "available": False})

            collections = handler.list_collections()
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

            handler = RAGHandler()
            if not handler.available():
                return web.json_response(
                    {"error": "llm-tools-rag not installed"},
                    status=500
                )

            results = handler.search(collection, query, top_k=top_k)
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
        """Activate a RAG collection for the session.

        POST /api/rag/activate
        Body: {"session": "session_id", "collection": "name"}
        """
        try:
            data = await request.json()
            session_id = data.get("session")
            collection = data.get("collection")

            if not session_id or not collection:
                return web.json_response(
                    {"error": "Missing session or collection"},
                    status=400
                )

            # Store active collection for this session
            state = self.daemon.get_session_state(session_id)
            if hasattr(state.session, "active_rag_collection"):
                state.session.active_rag_collection = collection
            else:
                # Store on daemon if session doesn't have the attribute
                if not hasattr(self, "_rag_sessions"):
                    self._rag_sessions: Dict[str, str] = {}
                self._rag_sessions[session_id] = collection

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
            collection = data.get("collection")
            path = data.get("path")
            refresh = data.get("refresh", False)

            if not collection or not path:
                return web.json_response(
                    {"error": "Missing collection or path"},
                    status=400
                )

            handler = RAGHandler()
            if not handler.available():
                return web.json_response(
                    {"error": "llm-tools-rag not installed"},
                    status=500
                )

            result = handler.add_documents(collection, path, refresh=refresh)
            return web.json_response({
                "status": result.status,
                "path": result.path,
                "chunks": result.chunks,
                "reason": result.reason,
                "error": result.error,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

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

    def get_url(self) -> str:
        """Get the server URL."""
        return f"http://localhost:{self.port}"
