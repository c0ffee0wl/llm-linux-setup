"""Web companion mixin for llm-assistant.

This module provides web companion functionality:
- Web server for browser-based UI
- WebSocket broadcasting for real-time updates
- Debug info for web companion
"""

import threading
import time
from typing import TYPE_CHECKING, Optional, Set, Dict, List

from .templates import render

# Web companion (optional - graceful degradation if not installed)
try:
    from fastapi import FastAPI, WebSocket
    from fastapi.responses import HTMLResponse
    import uvicorn
    import webbrowser
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False

if TYPE_CHECKING:
    from rich.console import Console


class WebMixin:
    """Mixin providing web companion functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - web_server_thread: Optional[threading.Thread]
    - web_port: int for server port
    - web_clients: Set of WebSocket connections
    - web_server: uvicorn.Server instance
    - web_event_loop: asyncio event loop
    - model_name: str for current model
    - mode: str ("agent" or "assistant")
    - watch_mode: bool
    - watch_goal: Optional[str]
    - max_context_size: int
    - conversation: llm.Conversation
    - active_rag_collection: Optional[str]
    - loaded_kbs: dict for loaded knowledge bases
    - active_mcp_servers: set of active MCP servers
    - estimate_tokens: method to get current token count
    - _strip_context: method to strip context from prompts
    - _build_system_prompt: method to build system prompt
    - _get_active_tools: method to get active tools
    - _get_all_mcp_servers: method to get all MCP servers
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    web_server_thread: Optional[threading.Thread]
    web_port: int
    web_clients: Set
    web_server: Optional[object]
    web_event_loop: Optional[object]
    model_name: str
    mode: str
    watch_mode: bool
    watch_goal: Optional[str]
    max_context_size: int
    active_rag_collection: Optional[str]
    loaded_kbs: Dict[str, str]
    active_mcp_servers: Set[str]

    def _get_web_html(self) -> str:
        """Return the HTML for the web companion interface from Jinja2 template."""
        return render('web_companion.html')

    def _start_web_server(self):
        """Start the web companion server in a background thread."""
        if not WEB_AVAILABLE:
            self.console.print("[red]Web companion not available. Install: llm install fastapi uvicorn[/]")
            return False

        if self.web_server_thread and self.web_server_thread.is_alive():
            self.console.print(f"[yellow]Web server already running at http://localhost:{self.web_port}[/]")
            return True

        # Create FastAPI app
        app = FastAPI()
        session = self  # Closure reference

        @app.get("/", response_class=HTMLResponse)
        async def get_index():
            return session._get_web_html()

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            session.web_clients.add(websocket)
            try:
                # Send session info (model, mode)
                await websocket.send_json({
                    "type": "session_info",
                    "model": session.model_name,
                    "mode": session.mode  # "agent" or "assistant"
                })

                # Send watch mode status
                await websocket.send_json({
                    "type": "watch_status",
                    "active": session.watch_mode,
                    "goal": session.watch_goal if session.watch_mode else None
                })

                # Send current token usage
                current_tokens = session.estimate_tokens()
                max_tokens = session.max_context_size
                pct = (current_tokens / max_tokens * 100) if max_tokens > 0 else 0
                await websocket.send_json({
                    "type": "token_update",
                    "current_tokens": current_tokens,
                    "max_tokens": max_tokens,
                    "percentage": pct
                })

                # Send conversation history on connect
                history = []
                for r in session.conversation.responses:
                    # Skip responses that are still streaming - they'll be sent via live broadcast
                    # text() calls _force() which blocks until done, causing connection delays
                    if not getattr(r, '_done', False):
                        continue
                    if hasattr(r, 'prompt') and r.prompt:
                        # Strip terminal context from prompts (same as DB storage)
                        clean_prompt = session._strip_context(r.prompt.prompt or "")
                        if clean_prompt and clean_prompt.strip():
                            history.append({"role": "user", "content": clean_prompt})
                    assistant_text = r.text()
                    if assistant_text and assistant_text.strip():
                        assistant_msg = {"role": "assistant", "content": assistant_text}
                        # Include thinking traces if available
                        if hasattr(r, 'response_json') and r.response_json:
                            thinking = r.response_json.get("thinking_traces", [])
                            if thinking:
                                assistant_msg["thinking_traces"] = thinking
                        history.append(assistant_msg)
                await websocket.send_json({"type": "history", "messages": history})

                # Keep connection alive and handle requests
                while True:
                    try:
                        msg = await websocket.receive_text()
                        # Handle debug info request
                        if msg == "debug_request":
                            try:
                                debug_data = session._get_debug_info()
                                await websocket.send_json({"type": "debug_info", **debug_data})
                            except Exception as e:
                                await websocket.send_json({
                                    "type": "debug_info",
                                    "system_prompt": f"Error getting debug info: {e}",
                                    "tools": [],
                                    "messages": [],
                                    "model": "unknown",
                                    "mode": "unknown",
                                    "options": {},
                                    "max_context": 0,
                                    "current_tokens": 0,
                                    "loaded_kbs": [],
                                    "active_rag": None
                                })
                    except Exception:
                        break
            finally:
                session.web_clients.discard(websocket)

        # Configure uvicorn
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.web_port,
            log_level="warning",
            access_log=False
        )
        self.web_server = uvicorn.Server(config)

        # Run in background thread
        def run_server():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # Store loop reference for cross-thread broadcasting
            session.web_event_loop = loop
            loop.run_until_complete(self.web_server.serve())

        self.web_server_thread = threading.Thread(target=run_server, daemon=True)
        self.web_server_thread.start()

        # Give server time to start and event loop to be set
        time.sleep(0.5)
        return True

    def _stop_web_server(self):
        """Stop the web companion server."""
        if self.web_server:
            self.web_server.should_exit = True
            self.web_server = None
            self.web_server_thread = None
            self.web_event_loop = None
            self.web_clients.clear()
            self.console.print("[green]✓[/] Web server stopped")
        else:
            self.console.print("[yellow]Web server is not running[/]")

    def _broadcast_to_web(self, message: dict):
        """Broadcast a message to all connected web clients.

        Thread-safe: schedules the broadcast on the web server's event loop.
        """
        if not self.web_clients or not self.web_event_loop:
            return

        import asyncio

        async def send_to_all():
            disconnected = set()
            # Iterate over a copy to avoid modification during iteration
            for client in list(self.web_clients):
                try:
                    await client.send_json(message)
                except Exception:
                    disconnected.add(client)
            # Remove disconnected clients
            for client in disconnected:
                self.web_clients.discard(client)

        # Schedule coroutine on the web server's event loop (thread-safe)
        try:
            asyncio.run_coroutine_threadsafe(send_to_all(), self.web_event_loop)
        except RuntimeError:
            # Event loop closed or not running
            pass

    def _broadcast_watch_status(self):
        """Broadcast current watch mode status to web clients."""
        self._broadcast_to_web({
            "type": "watch_status",
            "active": self.watch_mode,
            "goal": self.watch_goal if self.watch_mode else None,
            "system_prompt": self._build_system_prompt()
        })

    def _broadcast_rag_status(self):
        """Broadcast current RAG status to web clients."""
        self._broadcast_to_web({
            "type": "rag_status",
            "active": bool(self.active_rag_collection),
            "collection": self.active_rag_collection,
            "system_prompt": self._build_system_prompt()
        })

    def _broadcast_token_update(self):
        """Broadcast current token usage to web clients."""
        current_tokens = self.estimate_tokens()
        max_tokens = self.max_context_size
        pct = (current_tokens / max_tokens * 100) if max_tokens > 0 else 0
        self._broadcast_to_web({
            "type": "token_update",
            "current_tokens": current_tokens,
            "max_tokens": max_tokens,
            "percentage": pct
        })

    def _broadcast_tool_call(self, tool_name: str, arguments: dict, result: str = None, status: str = "pending"):
        """Broadcast a tool call event to web clients."""
        self._broadcast_to_web({
            "type": "tool_call",
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "status": status
        })

    def _get_debug_info(self) -> dict:
        """Return debug information for the web companion."""
        # Build current system prompt (what would be sent on next request)
        current_system_prompt = self._build_system_prompt()

        # Build full conversation with ALL context (unstripped)
        messages = []
        for r in self.conversation.responses:
            # Skip responses that are still streaming to avoid blocking
            if not getattr(r, '_done', False):
                continue
            if hasattr(r, 'prompt') and r.prompt:
                # Include FULL prompt (not stripped) - use .prompt property
                full_prompt = r.prompt.prompt or ""
                # Also capture the system prompt used for this turn
                turn_system = r.prompt.system or ""

                # Build attachments info
                attachments_info = []
                if r.prompt.attachments:
                    for att in r.prompt.attachments:
                        att_info = {"type": att.type or "unknown"}
                        if att.path:
                            att_info["path"] = att.path
                        elif att.url:
                            att_info["url"] = att.url
                        else:
                            att_info["inline"] = True
                        # Include content size if available
                        if att.content:
                            att_info["size"] = len(att.content)
                        attachments_info.append(att_info)

                messages.append({
                    "role": "user",
                    "content": full_prompt,
                    "system": turn_system,  # System prompt for this turn
                    "has_context": "<terminal_context>" in full_prompt,
                    "attachments": attachments_info,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens
                })

                # Include tool results if present (tool outputs sent as input)
                if r.prompt.tool_results:
                    for tr in r.prompt.tool_results:
                        messages.append({
                            "role": "tool_result",
                            "tool_name": tr.name,
                            "content": tr.output or "",
                            "tool_call_id": tr.tool_call_id
                        })

            # Assistant response
            assistant_text = r.text()
            if assistant_text:
                assistant_msg = {"role": "assistant", "content": assistant_text}
                # Include thinking traces if available
                if hasattr(r, 'response_json') and r.response_json:
                    thinking = r.response_json.get("thinking_traces", [])
                    if thinking:
                        assistant_msg["thinking_traces"] = thinking
                messages.append(assistant_msg)

            # Tool calls made by assistant
            try:
                tool_calls = list(r.tool_calls())
                for tc in tool_calls:
                    messages.append({
                        "role": "tool_call",
                        "tool_name": tc.name,
                        "arguments": tc.arguments,
                        "tool_call_id": tc.tool_call_id
                    })
            except Exception:
                pass  # No tool calls or error accessing them

        # Build tool definitions (what's sent to the model)
        # Uses _get_active_tools() to reflect current mode and loaded optional tools
        tools = []
        for tool in self._get_active_tools():
            tool_info = {
                "name": tool.name,
                "description": tool.description or "",
            }
            # Get input schema - different attribute names depending on tool type
            if hasattr(tool, 'input_schema'):
                tool_info["parameters"] = tool.input_schema
            elif hasattr(tool, 'schema') and isinstance(tool.schema, dict):
                tool_info["parameters"] = tool.schema.get('parameters', {})
            else:
                tool_info["parameters"] = {}
            # Include plugin source if available
            if hasattr(tool, 'plugin') and tool.plugin:
                tool_info["plugin"] = tool.plugin
            tools.append(tool_info)

        # Get model options from most recent response (or empty if no responses yet)
        options = {}
        if self.conversation.responses:
            last_response = self.conversation.responses[-1]
            if hasattr(last_response, 'prompt') and last_response.prompt:
                try:
                    options = {
                        k: v for k, v in dict(last_response.prompt.options).items()
                        if v is not None
                    }
                except Exception:
                    pass

        # Get MCP server status
        mcp_servers = {}
        try:
            all_servers = self._get_all_mcp_servers()
            mcp_servers = {
                "active": list(self.active_mcp_servers),
                "all": {name: {"optional": is_opt, "loaded": name in self.active_mcp_servers}
                        for name, is_opt in all_servers.items()}
            }
        except Exception:
            pass

        return {
            "system_prompt": current_system_prompt,
            "tools": tools,
            "messages": messages,
            "model": self.model_name or "unknown",
            "mode": self.mode,
            "options": options,
            "max_context": self.max_context_size,
            "current_tokens": self.estimate_tokens(),
            "loaded_kbs": list(self.loaded_kbs.keys()) if self.loaded_kbs else [],
            "active_rag": self.active_rag_collection,
            "mcp_servers": mcp_servers
        }
