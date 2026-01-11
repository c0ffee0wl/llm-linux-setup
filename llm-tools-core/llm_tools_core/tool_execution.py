"""Shared tool execution logic for daemon and web UI server.

Provides a common implementation for executing tool calls with consistent
event emission and error handling.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol

from llm import ToolResult

from .mcp_citations import is_microsoft_doc_tool, format_microsoft_citations


@dataclass
class ToolEvent:
    """Event emitted during tool execution."""
    type: str  # "tool_start" or "tool_done"
    tool: str
    args: Optional[Dict[str, Any]] = None
    result: Optional[str] = None


class EventEmitter(Protocol):
    """Protocol for event emission callbacks."""
    async def __call__(self, event: dict) -> None:
        """Emit an event dict."""
        ...


async def execute_tool_call(
    tool_call,
    implementations: Dict[str, Callable],
    emit: EventEmitter,
    arg_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ToolResult:
    """Execute a single tool call and return the result.

    Args:
        tool_call: The tool call object with name, arguments, tool_call_id
        implementations: Dict mapping tool names to implementation functions
        emit: Async callback to emit events (tool_start, tool_done)
        arg_overrides: Optional dict mapping tool names to argument overrides.
                       Example: {"search_google": {"sources": False}}

    Returns:
        ToolResult with the execution output
    """
    tool_name = (tool_call.name or "").lower().strip()
    tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
    tool_call_id = tool_call.tool_call_id

    # Apply argument overrides if specified for this tool
    if arg_overrides and tool_name in arg_overrides:
        tool_args = {**tool_args, **arg_overrides[tool_name]}

    # Emit tool start event
    await emit({
        "type": "tool_start",
        "tool": tool_name,
        "args": tool_args,
        "tool_call_id": tool_call_id,
    })

    # Check if we have an implementation
    if tool_name not in implementations:
        error_output = f"Error: Tool '{tool_name}' not available"
        await emit({
            "type": "tool_done",
            "tool": tool_name,
            "result": error_output,
            "tool_call_id": tool_call_id,
        })
        return ToolResult(
            name=tool_call.name,
            output=error_output,
            tool_call_id=tool_call_id,
        )

    try:
        impl = implementations[tool_name]
        # Run tool in executor to avoid blocking event loop
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: impl(**tool_args)
        )

        # Handle different result types
        if hasattr(result, "output"):
            output = result.output
        elif isinstance(result, str):
            output = result
        else:
            output = str(result)

        # Post-process Microsoft MCP tools for citations
        if is_microsoft_doc_tool(tool_name):
            sources_enabled = True
            if arg_overrides and 'microsoft_sources' in arg_overrides:
                sources_enabled = arg_overrides['microsoft_sources'].get('sources', True)
            output = format_microsoft_citations(tool_name, output, sources_enabled)

        await emit({
            "type": "tool_done",
            "tool": tool_name,
            "result": output[:500] if len(output) > 500 else output,
            "tool_call_id": tool_call_id,
        })

        return ToolResult(
            name=tool_call.name,
            output=output,
            tool_call_id=tool_call_id,
        )

    except Exception as e:
        error_msg = f"Error executing {tool_name}: {e}"
        await emit({
            "type": "tool_done",
            "tool": tool_name,
            "result": error_msg,
            "tool_call_id": tool_call_id,
        })

        return ToolResult(
            name=tool_call.name,
            output=error_msg,
            tool_call_id=tool_call_id,
        )
