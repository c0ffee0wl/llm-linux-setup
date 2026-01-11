"""MCP (Model Context Protocol) tools and server management for llm-assistant.

This module provides:
- Background MCP tool loading
- Tool list management (ASSISTANT_TOOLS, EXTERNAL_TOOLS, etc.)
- MCPMixin for server management commands
"""

import atexit
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Set

import llm
from llm import Tool

from .config import (
    EXTERNAL_TOOL_PLUGINS,
    AGENT_MODE_TOOLS,
    OPTIONAL_TOOL_PLUGINS,
    GEMINI_ONLY_TOOL_NAMES,
    EXEC_DEPENDENT_TOOLS,
)
from .utils import ConsoleHelper

if TYPE_CHECKING:
    from rich.console import Console


# =============================================================================
# Always-on external tools (always available and auto-dispatch)
_all_tools = llm.get_tools()

# =============================================================================
# Background MCP Loading
# =============================================================================
# Load MCP tools in background to avoid blocking startup
# User sees prompt immediately, MCP tools become available when loading completes

_mcp_future = None
_mcp_toolbox = None
_mcp_loaded = False
_mcp_lock = threading.Lock()
_mcp_executor = ThreadPoolExecutor(max_workers=1)


def _load_mcp_background():
    """Load MCP tools in background thread."""
    try:
        from llm_tools_mcp.register_tools import MCP
        return MCP()
    except ImportError:
        return None  # llm-tools-mcp not installed
    except Exception:
        return None  # MCP config missing or server unavailable


# Start loading immediately but don't block module import
_mcp_future = _mcp_executor.submit(_load_mcp_background)


def _cleanup_mcp():
    """Clean up MCP resources on exit.

    Properly shuts down the ThreadPoolExecutor and closes any MCP toolbox
    HTTP connections to prevent 'coroutine was never awaited' warnings.
    """
    global _mcp_toolbox, _mcp_executor

    # Close MCP toolbox HTTP connections if it has a close method
    if _mcp_toolbox is not None:
        try:
            # MCP toolbox may have an async close method
            if hasattr(_mcp_toolbox, 'close'):
                _mcp_toolbox.close()
            elif hasattr(_mcp_toolbox, 'aclose'):
                # For async close, we need to run it in an event loop
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Can't await in running loop from atexit
                        pass
                    else:
                        loop.run_until_complete(_mcp_toolbox.aclose())
                except RuntimeError:
                    # No event loop available
                    pass
        except Exception:
            pass  # Ignore cleanup errors

    # Shutdown the executor
    if _mcp_executor is not None:
        try:
            _mcp_executor.shutdown(wait=False)
        except Exception:
            pass


# Register cleanup handler
atexit.register(_cleanup_mcp)


def _rebuild_tool_lists():
    """Rebuild ASSISTANT_TOOLS and EXTERNAL_TOOLS after MCP tools load."""
    global ASSISTANT_TOOLS, EXTERNAL_TOOLS

    # Rebuild ASSISTANT_TOOLS with MCP tools included
    ASSISTANT_TOOLS = [
        tool for tool in _all_tools.values()
        if isinstance(tool, Tool)
        and getattr(tool, 'plugin', None) in ('assistant',) + EXTERNAL_TOOL_PLUGINS
        and tool.name not in GEMINI_ONLY_TOOL_NAMES
    ]

    # Rebuild EXTERNAL_TOOLS dispatch dict
    EXTERNAL_TOOLS = {
        name: tool.implementation
        for name, tool in _all_tools.items()
        if isinstance(tool, Tool)
        and hasattr(tool, 'implementation') and tool.implementation is not None
        and getattr(tool, 'plugin', None) in EXTERNAL_TOOL_PLUGINS
    }


def _ensure_mcp_loaded():
    """Wait for background MCP load to complete, then register tools.

    Call this before accessing ASSISTANT_TOOLS or EXTERNAL_TOOLS to ensure
    MCP tools are available. The first call waits for background load to
    complete, subsequent calls return immediately.
    """
    global _mcp_toolbox, _mcp_loaded, ASSISTANT_TOOLS, EXTERNAL_TOOLS
    if _mcp_loaded:
        return

    with _mcp_lock:
        if _mcp_loaded:
            return
        if _mcp_future:
            try:
                _mcp_toolbox = _mcp_future.result(timeout=30)
                if _mcp_toolbox:
                    for tool in _mcp_toolbox.tools():
                        _all_tools[tool.name] = tool
                    _rebuild_tool_lists()
            except Exception:
                pass  # Failed to load MCP tools
        _mcp_loaded = True


# Build ASSISTANT_TOOLS - base tools always offered to model
# (agent-mode, optional, and Gemini-only tools added dynamically via _get_active_tools())
ASSISTANT_TOOLS = [
    tool for tool in _all_tools.values()
    if isinstance(tool, Tool)
    and getattr(tool, 'plugin', None) in ('assistant',) + EXTERNAL_TOOL_PLUGINS
    and tool.name not in GEMINI_ONLY_TOOL_NAMES  # Exclude Gemini-only tools from base
]

# Build EXTERNAL_TOOLS - base dispatch dict (always-on tools)
EXTERNAL_TOOLS = {
    name: tool.implementation
    for name, tool in _all_tools.items()
    if isinstance(tool, Tool)
    and hasattr(tool, 'implementation') and tool.implementation is not None
    and getattr(tool, 'plugin', None) in EXTERNAL_TOOL_PLUGINS
}

# Separate dispatch dicts for conditional tools (agent-mode and optional)
AGENT_EXTERNAL_TOOLS = {
    name: tool.implementation
    for name, tool in _all_tools.items()
    if isinstance(tool, Tool)
    and hasattr(tool, 'implementation') and tool.implementation is not None
    and getattr(tool, 'plugin', None) in AGENT_MODE_TOOLS
}

OPTIONAL_EXTERNAL_TOOLS = {
    name: tool.implementation
    for name, tool in _all_tools.items()
    if isinstance(tool, Tool)
    and hasattr(tool, 'implementation') and tool.implementation is not None
    and getattr(tool, 'plugin', None) in OPTIONAL_TOOL_PLUGINS
}


class MCPMixin:
    """Mixin providing MCP server management functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - model_name: str (current model ID)
    - mode: str ("agent" or "assistant")

    Provides (via _mcp_init):
    - active_mcp_servers: set of active MCP server names
    - no_exec_mode: bool (True if running without Terminator/D-Bus)
    - loaded_optional_tools: set of loaded optional tool plugins
    - _skill_invoke_tool: Optional[Tool]
    - _skill_load_file_tool: Optional[Tool]
    - _skill_invoke_impl: callable or None
    - _skill_load_file_impl: callable or None
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    model_name: str
    mode: str

    # Type hints for attributes initialized by _mcp_init
    active_mcp_servers: Set[str]
    no_exec_mode: bool
    loaded_optional_tools: Set[str]

    def _mcp_init(self, no_exec_mode: bool = False):
        """Initialize MCP-related attributes.

        Args:
            no_exec_mode: True if running without Terminator/D-Bus (headless mode)
        """
        self.active_mcp_servers = self._get_default_mcp_servers()
        self.no_exec_mode = no_exec_mode
        self.loaded_optional_tools = set()
        # Skill tools - will be set by SkillsMixin if skills are loaded
        self._skill_invoke_tool = None
        self._skill_load_file_tool = None
        self._skill_invoke_impl = None
        self._skill_load_file_impl = None

    def _is_gemini_model(self) -> bool:
        """Check if current model is a Gemini model (vertex/* or gemini-*)."""
        return self.model_name.startswith("vertex/") or self.model_name.startswith("gemini-")

    def _get_default_mcp_servers(self) -> set:
        """Get non-optional MCP servers (loaded by default)."""
        _ensure_mcp_loaded()
        servers = set()
        for tool in _all_tools.values():
            server = getattr(tool, 'server_name', None)
            is_optional = getattr(tool, 'mcp_optional', False)
            if server and not is_optional:
                servers.add(server)
        return servers

    def _get_all_mcp_servers(self) -> dict:
        """Get all MCP servers with their optional status.

        Returns dict mapping server_name -> is_optional (bool)
        """
        _ensure_mcp_loaded()
        servers = {}
        for tool in _all_tools.values():
            server = getattr(tool, 'server_name', None)
            if server and server not in servers:
                servers[server] = getattr(tool, 'mcp_optional', False)
        return servers

    def _count_tools_for_server(self, server_name: str) -> int:
        """Count tools available from a specific MCP server."""
        count = 0
        for tool in _all_tools.values():
            if getattr(tool, 'server_name', None) == server_name:
                count += 1
        return count

    def _add_dynamic_tools(self, tools: list, existing_names: set) -> list:
        """Add dynamic tools (MCP, optional, Gemini-only, skills) to a base tool list.

        This is a shared helper used by both TUI (_get_active_tools) and
        Headless (get_tools) to avoid code duplication.

        Args:
            tools: Base tool list to extend (modified in place)
            existing_names: Set of tool names already in the list (modified in place)

        Returns:
            The extended tools list
        """
        # Ensure MCP tools are loaded
        _ensure_mcp_loaded()

        # Add MCP tools from active servers
        for tool in _all_tools.values():
            if not isinstance(tool, Tool):
                continue
            server = getattr(tool, 'server_name', None)
            if server and server in self.active_mcp_servers:
                if tool.name not in existing_names:
                    tools.append(tool)
                    existing_names.add(tool.name)

        # Add optional tools if manually loaded (e.g., /imagemage)
        for plugin_name in getattr(self, 'loaded_optional_tools', set()):
            for tool in _all_tools.values():
                if isinstance(tool, Tool) and getattr(tool, 'plugin', None) == plugin_name:
                    if tool.name not in existing_names:
                        tools.append(tool)
                        existing_names.add(tool.name)

        # Add Gemini-only tools if using Gemini/Vertex model
        if hasattr(self, '_is_gemini_model') and self._is_gemini_model():
            for tool in _all_tools.values():
                if isinstance(tool, Tool) and tool.name in GEMINI_ONLY_TOOL_NAMES:
                    if tool.name not in existing_names:
                        tools.append(tool)
                        existing_names.add(tool.name)

        # Add skill tools if any skills loaded
        if getattr(self, '_skill_invoke_tool', None):
            if self._skill_invoke_tool.name not in existing_names:
                tools.append(self._skill_invoke_tool)
                existing_names.add(self._skill_invoke_tool.name)
        if getattr(self, '_skill_load_file_tool', None):
            if self._skill_load_file_tool.name not in existing_names:
                tools.append(self._skill_load_file_tool)
                existing_names.add(self._skill_load_file_tool.name)

        return tools

    def _get_active_tools(self) -> list:
        """Get currently active tools based on mode, model, and loaded state.

        Returns tools that should be offered to the model. Includes:
        - Base ASSISTANT_TOOLS (always available, includes capture_screen)
        - Agent-mode tools when in /agent mode (currently none)
        - MCP tools from active servers
        - Optional tools (imagemage) when manually loaded via /imagemage
        - Gemini-only tools (view_youtube_native) when using Gemini/Vertex model
        - Skill tools when skills are loaded
        """
        # Ensure MCP tools are loaded (waits for background load on first call)
        _ensure_mcp_loaded()

        # Start with base ASSISTANT_TOOLS, filtering out inactive MCP servers
        tools = [t for t in ASSISTANT_TOOLS if (
            getattr(t, 'server_name', None) is None or  # Not an MCP tool
            getattr(t, 'server_name', None) in self.active_mcp_servers  # MCP server is active
        )]

        # Filter out exec-dependent tools in no-exec mode
        if self.no_exec_mode:
            tools = [t for t in tools if t.name not in EXEC_DEPENDENT_TOOLS]

        existing_names = {t.name for t in tools}

        # Add agent-mode tools if in agent mode (TUI-specific)
        if getattr(self, 'mode', None) == "agent":
            for plugin_name in AGENT_MODE_TOOLS:
                for tool in _all_tools.values():
                    if isinstance(tool, Tool) and getattr(tool, 'plugin', None) == plugin_name:
                        if tool.name not in existing_names:
                            tools.append(tool)
                            existing_names.add(tool.name)

        # Add dynamic tools (MCP, optional, Gemini-only, skills) via shared helper
        return self._add_dynamic_tools(tools, existing_names)

    def _get_active_external_tools(self) -> dict:
        """Get dispatch dict for currently active external tools.

        Returns tool implementations for auto-dispatch. Includes:
        - Base EXTERNAL_TOOLS (always available)
        - MCP tools from active servers
        - Agent-mode tools when in /agent mode
        - Optional tools when manually loaded

        MCP tools are filtered by active_mcp_servers set - only tools from
        active servers are included.
        """
        # Ensure MCP tools are loaded (waits for background load on first call)
        _ensure_mcp_loaded()

        tools = dict(EXTERNAL_TOOLS)  # Base dispatch (always available)

        # Add MCP tools from active servers
        for name, tool in _all_tools.items():
            if not isinstance(tool, Tool):
                continue
            server = getattr(tool, 'server_name', None)
            if server and server in self.active_mcp_servers:
                if hasattr(tool, 'implementation') and tool.implementation:
                    tools[name] = tool.implementation

        # Filter out exec-dependent tools in no-exec mode
        if self.no_exec_mode:
            tools = {name: impl for name, impl in tools.items() if name not in EXEC_DEPENDENT_TOOLS}

        # Add agent-mode tools if in agent mode
        if self.mode == "agent":
            tools.update(AGENT_EXTERNAL_TOOLS)

        # Add optional tools if loaded
        for plugin_name in self.loaded_optional_tools:
            for name, impl in OPTIONAL_EXTERNAL_TOOLS.items():
                tool = _all_tools.get(name)
                if tool and getattr(tool, 'plugin', None) == plugin_name:
                    tools[name] = impl

        # Add skill tool implementations if any skills loaded
        if self._skill_invoke_tool:
            tools["skill_invoke"] = self._skill_invoke_impl
        if self._skill_load_file_tool:
            tools["skill_load_file"] = self._skill_load_file_impl

        return tools

    def _handle_mcp_load(self, server_name: str):
        """Load an MCP server (optional or previously unloaded default)."""
        all_servers = self._get_all_mcp_servers()
        if server_name not in all_servers:
            ConsoleHelper.error(self.console, f"Unknown server: {server_name}")
            available = ', '.join(sorted(all_servers.keys()))
            ConsoleHelper.dim(self.console, f"Available: {available}")
            return

        if server_name in self.active_mcp_servers:
            ConsoleHelper.warning(self.console, f"{server_name} already loaded")
            return

        self.active_mcp_servers.add(server_name)
        tool_count = self._count_tools_for_server(server_name)
        ConsoleHelper.success(self.console, f"{server_name} loaded ({tool_count} tools)")

    def _handle_mcp_unload(self, server_name: str):
        """Unload any MCP server (default or optional)."""
        if server_name in self.active_mcp_servers:
            self.active_mcp_servers.discard(server_name)
            ConsoleHelper.success(self.console, f"{server_name} unloaded")
        else:
            all_servers = self._get_all_mcp_servers()
            if server_name in all_servers:
                ConsoleHelper.warning(self.console, f"{server_name} not loaded")
            else:
                ConsoleHelper.error(self.console, f"Unknown server: {server_name}")

    def _handle_mcp_status(self):
        """Show MCP server status (all servers, grouped by type)."""
        all_servers = self._get_all_mcp_servers()

        if not all_servers:
            ConsoleHelper.dim(self.console, "No MCP servers configured")
            return

        # Group by optional status
        default_servers = {s for s, opt in all_servers.items() if not opt}
        optional_servers = {s for s, opt in all_servers.items() if opt}

        ConsoleHelper.bold(self.console, "MCP Servers:")

        # Show default servers
        if default_servers:
            self.console.print("  [dim]Default:[/]")
            for server in sorted(default_servers):
                if server in self.active_mcp_servers:
                    tool_count = self._count_tools_for_server(server)
                    self.console.print(f"    [green]●[/] {server} ({tool_count} tools)")
                else:
                    self.console.print(f"    [dim]○[/] {server} [dim](unloaded)[/]")

        # Show optional servers
        if optional_servers:
            self.console.print("  [dim]Optional:[/]")
            for server in sorted(optional_servers):
                if server in self.active_mcp_servers:
                    tool_count = self._count_tools_for_server(server)
                    self.console.print(f"    [green]●[/] {server} ({tool_count} tools)")
                else:
                    self.console.print(f"    [dim]○[/] {server}")

        if not self.active_mcp_servers:
            ConsoleHelper.dim(self.console, "Use /mcp load <server> to enable")
