"""Configuration constants for llm-assistant.

This module contains all static configuration data including:
- Slash command definitions
- Model context limits (imported from llm_tools_core)
- TUI command detection (imported from llm_tools_core)
- Tool plugin configuration
- Display configuration for external tools
"""


# Slash command definitions for tab completion
# Structure: command -> {subcommands: [...], dynamic: str|None, description: str}
SLASH_COMMANDS = {
    "/help": {"subcommands": [], "dynamic": None, "description": "Show help"},
    "/clear": {"subcommands": [], "dynamic": None, "description": "Clear conversation"},
    "/new": {"subcommands": [], "dynamic": None, "description": "Start new conversation"},
    "/reset": {"subcommands": [], "dynamic": None, "description": "Full reset"},
    "/refresh": {"subcommands": [], "dynamic": None, "description": "Refresh context"},
    "/info": {"subcommands": [], "dynamic": None, "description": "Session info"},
    "/status": {"subcommands": [], "dynamic": None, "description": "Session info"},
    "/quit": {"subcommands": [], "dynamic": None, "description": "Exit"},
    "/exit": {"subcommands": [], "dynamic": None, "description": "Exit"},
    "/model": {"subcommands": ["-q"], "dynamic": "models", "description": "Switch or list models"},
    "/watch": {"subcommands": ["off", "status"], "dynamic": None, "description": "Watch mode control"},
    "/squash": {"subcommands": [], "dynamic": None, "description": "Compress context"},
    "/rewind": {"subcommands": ["undo"], "dynamic": None, "description": "Rewind to previous turn"},
    "/kb": {"subcommands": ["load", "unload", "reload"], "dynamic": "kb", "description": "Knowledge base control"},
    "/memory": {"subcommands": ["list", "reload", "global", "local"], "dynamic": None, "description": "View/manage AGENTS.md memory"},
    "/auto": {"subcommands": ["full", "off", "status"], "dynamic": None, "description": "Auto mode control"},
    "/voice": {"subcommands": ["auto", "off", "status", "clean", "clean on", "clean off", "loop", "loop off"], "dynamic": None, "description": "Voice input control"},
    "/speech": {"subcommands": ["on", "off", "status"], "dynamic": None, "description": "TTS output control"},
    "/rag": {"subcommands": ["add", "search", "rebuild", "delete", "off", "status", "top-k", "mode"], "dynamic": "rag_collections", "description": "RAG search integration"},
    "/copy": {"subcommands": ["raw", "all"], "dynamic": None, "description": "Copy response(s) to clipboard"},
    "/web": {"subcommands": ["stop", "off"], "dynamic": None, "description": "Open web companion"},
    "/screenshot": {"subcommands": ["window", "region", "full", "rdp", "annotate"], "dynamic": None, "description": "Capture screenshot with optional prompt"},
    "/imagemage": {"subcommands": ["off", "status"], "dynamic": None, "description": "Load/unload image generation tool"},
    "/report": {"subcommands": ["list", "edit", "delete", "export", "severity", "init", "projects", "open"], "dynamic": "findings", "description": "Pentest finding management"},
    "/mcp": {"subcommands": ["load", "unload", "status"], "dynamic": "mcp_servers", "description": "Load/unload MCP servers"},
    "/skill": {"subcommands": ["load", "unload", "reload", "list"], "dynamic": "skills", "description": "Skill management"},
    "/sources": {"subcommands": ["on", "off", "status"], "dynamic": None, "description": "Toggle source citations"},
}


# Slash commands available in headless/daemon mode (allowlist)
# Only these commands work via thin client (@ command)
HEADLESS_AVAILABLE_COMMANDS = {
    '/help',        # Show available commands
    '/clear',       # Clear conversation
    '/reset',       # Full reset
    '/new',         # Alias for clear
    '/info',        # Session info
    '/status',      # Alias for info
    '/model',       # Switch or list models
    '/squash',      # Compress context
    '/kb',          # Knowledge base control
    '/memory',      # AGENTS.md memory
    '/rag',         # RAG search
    '/skill',       # Skill management
    '/mcp',         # MCP servers
    '/report',      # Pentest findings
    '/copy',        # Copy responses to clipboard
    '/sources',     # Source citation control
}


# TUI commands - imported from shared library
from llm_tools_core import TUI_COMMANDS, is_tui_command  # noqa: F401 (re-export)


# External tool plugins that are always available
# Note: 'llm-tools-mcp' is NOT included here - MCP tools are added dynamically
# via _add_dynamic_tools() based on active_mcp_servers filtering
EXTERNAL_TOOL_PLUGINS = ('search_google', 'fetch_url', 'fragment_bridge', 'fabric', 'sandboxed_python', 'capture_screen')

# Tools that require Terminator/D-Bus exec terminal (disabled in --no-exec mode)
EXEC_DEPENDENT_TOOLS = {
    'execute_in_terminal',
    'send_keypress',
    'capture_terminal',
    'refresh_context',
    'search_terminal',
}

# Optional tools that can be loaded/unloaded via slash command (/imagemage)
OPTIONAL_TOOL_PLUGINS = ('imagemage',)

# Tools that require Gemini/Vertex models (native video/audio processing)
# These are excluded from base tools and added dynamically when using Gemini
GEMINI_ONLY_TOOL_NAMES = ('view_youtube_native',)


# Display configuration for external tools - imported from shared library
# Structure: tool_name -> (param_name, action_verb, brief_description)
# Tools not in this dict will use generic "Calling {tool_name}..." message
from llm_tools_core import TOOL_DISPLAY as EXTERNAL_TOOL_DISPLAY, get_action_verb  # noqa: F401 (re-export)


# Model context limits - imported from shared library
