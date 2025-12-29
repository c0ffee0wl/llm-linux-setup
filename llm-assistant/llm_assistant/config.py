"""Configuration constants for llm-assistant.

This module contains all static configuration data including:
- Slash command definitions
- Model context limits
- TUI command detection
- Tool plugin configuration
- Display configuration for external tools
"""

import os


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
    "/voice": {"subcommands": ["auto", "off", "status"], "dynamic": None, "description": "Voice input control"},
    "/speech": {"subcommands": ["on", "off", "status"], "dynamic": None, "description": "TTS output control"},
    "/rag": {"subcommands": ["add", "search", "rebuild", "delete", "off", "status", "top-k", "mode"], "dynamic": "rag_collections", "description": "RAG search integration"},
    "/assistant": {"subcommands": [], "dynamic": None, "description": "Switch to assistant mode (conservative)"},
    "/agent": {"subcommands": [], "dynamic": None, "description": "Switch to agent mode (agentic)"},
    "/copy": {"subcommands": ["raw", "all"], "dynamic": None, "description": "Copy response(s) to clipboard"},
    "/web": {"subcommands": ["stop", "off"], "dynamic": None, "description": "Open web companion"},
    "/capture": {"subcommands": ["window", "region", "full", "rdp", "annotate"], "dynamic": None, "description": "Capture screenshot with optional prompt"},
    "/imagemage": {"subcommands": ["off", "status"], "dynamic": None, "description": "Load/unload image generation tool"},
    "/report": {"subcommands": ["list", "edit", "delete", "export", "severity", "init", "projects", "open"], "dynamic": "findings", "description": "Pentest finding management"},
    "/mcp": {"subcommands": ["load", "unload", "status"], "dynamic": "mcp_servers", "description": "Load/unload MCP servers"},
    "/skill": {"subcommands": ["load", "unload", "reload", "list"], "dynamic": "skills", "description": "Skill management"},
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
    '/quit',        # Exit
    '/exit',        # Alias for quit
    '/model',       # Switch or list models
    '/squash',      # Compress context
    '/kb',          # Knowledge base control
    '/memory',      # AGENTS.md memory
    '/rag',         # RAG search
    '/skill',       # Skill management
    '/mcp',         # MCP servers
    '/report',      # Pentest findings
    '/assistant',   # Switch to assistant mode
    '/imagemage',   # Image generation tool
    '/copy',        # Copy responses to clipboard
}


# TUI commands that require screenshot capture instead of text capture
TUI_COMMANDS = {
    # System monitors
    'htop', 'top', 'btop', 'gtop', 'glances', 'atop', 'nmon',
    'iotop', 'iftop', 'nethogs', 'bmon', 'vnstat', 'procs',
    # Editors
    'vim', 'vi', 'nvim', 'nano', 'emacs', 'helix', 'micro',
    'joe', 'pico', 'jed', 'ne', 'mg', 'kakoune', 'kak',
    # Pagers
    'less', 'more', 'most', 'bat',
    # File managers
    'mc', 'ranger', 'nnn', 'lf', 'vifm', 'fff', 'broot',
    'ncdu', 'duf', 'dust',
    # Terminal multiplexers
    'tmux', 'screen', 'byobu', 'zellij',
    # Git TUIs
    'tig', 'lazygit', 'gitui',
    # Container/K8s TUIs
    'k9s', 'lazydocker', 'dive', 'ctop',
    # Fuzzy finders (when run standalone)
    'fzf', 'sk', 'peco',
    # Periodic execution
    'watch',
    # Audio
    'alsamixer', 'pulsemixer',
    # Email/IRC
    'mutt', 'neomutt', 'aerc',
    'weechat', 'irssi',
    # Music players
    'cmus', 'ncmpcpp', 'moc', 'mocp',
    # Web browsers
    'lynx', 'w3m', 'links', 'elinks',
    # Task management
    'taskwarrior-tui', 'taskell',
    # Calendar
    'calcurse', 'khal',
}


# External tool plugins that are always available
EXTERNAL_TOOL_PLUGINS = ('search_google', 'fetch_url', 'fragment_bridge', 'fabric', 'llm-tools-mcp', 'sandboxed_python', 'capture_screen')

# Tools available only in agent mode (schema sent to model only in /agent)
AGENT_MODE_TOOLS = ()

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


# Display configuration for external tools: tool_name -> (param_name, action_verb, brief_description)
# Tools not in this dict will use generic "Calling {tool_name}..." message
EXTERNAL_TOOL_DISPLAY = {
    'search_google': ('query', 'Searching', 'Search the web'),
    'fetch_url': ('url', 'Fetching', 'Fetch web page content'),
    # Fragment bridge tools
    'load_yt': ('argument', 'Loading transcript', 'Load YouTube transcript'),
    'load_github': ('argument', 'Loading repo', 'Load GitHub repository'),
    'load_pdf': ('argument', 'Extracting PDF', 'Extract PDF content'),
    # Fabric pattern tool
    'prompt_fabric': ('task', 'Processing with Fabric', 'Execute Fabric AI pattern'),
    # Microsoft Learn MCP tools
    'microsoft_docs_search': ('query', 'Searching Microsoft Learn', 'Search Microsoft documentation'),
    'microsoft_docs_fetch': ('url', 'Fetching Microsoft documentation', 'Fetch Microsoft documentation page'),
    'microsoft_code_sample_search': ('query', 'Searching Microsoft code samples', 'Search Microsoft code examples'),
    # AWS Knowledge MCP tools (prefixed with aws___)
    'aws___search_documentation': ('query', 'Searching AWS docs', 'Search AWS documentation'),
    'aws___read_documentation': ('url', 'Reading AWS docs', 'Fetch AWS documentation page'),
    'aws___recommend': ('url', 'Getting AWS recommendations', 'Get related AWS content'),
    # Screen capture tool
    'capture_screen': ('mode', 'Capturing screenshot', 'Capture screen or window'),
    # Image generation tool (imagemage)
    'generate_image': ('prompt', 'Generating image', 'Generate or edit images with Gemini'),
    # Sandboxed Python execution
    'execute_python': ('code', 'Running Python', 'Execute Python code in sandbox'),
    # ArXiv MCP tools (optional)
    'search_papers': ('query', 'Searching arXiv', 'Search arXiv papers'),
    'download_paper': ('paper_id', 'Downloading arXiv paper', 'Download arXiv paper'),
    'list_papers': ('', 'Listing arXiv papers', 'List downloaded arXiv papers'),
    'read_paper': ('paper_id', 'Reading arXiv paper', 'Read arXiv paper content'),
    # Chrome DevTools CDP navigation tools (optional)
    'close_page': ('', 'Closing CDP page', 'Close browser page via CDP'),
    'list_pages': ('', 'Listing CDP pages', 'List open browser pages via CDP'),
    'navigate_page': ('url', 'Navigating CDP page', 'Navigate to URL via CDP'),
    'new_page': ('url', 'Opening CDP page', 'Open new browser page via CDP'),
    'select_page': ('pageId', 'Selecting CDP page', 'Select browser page via CDP'),
    'wait_for': ('selector', 'Waiting for CDP element', 'Wait for element via CDP'),
    # Chrome DevTools CDP MCP tools (optional)
    'get_network_request': ('requestId', 'Getting CDP request', 'Get CDP network request details'),
    'list_network_requests': ('', 'Listing CDP requests', 'List CDP network requests'),
    'evaluate_script': ('expression', 'Evaluating CDP script', 'Evaluate JavaScript via CDP'),
    'get_console_message': ('messageId', 'Getting CDP message', 'Get CDP console message'),
    'list_console_messages': ('', 'Listing CDP messages', 'List CDP console messages'),
    'take_screenshot': ('', 'Taking CDP screenshot', 'Capture page screenshot via CDP'),
    'take_snapshot': ('', 'Taking CDP snapshot', 'Capture DOM snapshot via CDP')
}


# Model-specific context limits (input tokens)
# Based on provider documentation as of 2025-12
MODEL_CONTEXT_LIMITS = {
    # Azure OpenAI / OpenAI - GPT-4.1 series (1M context)
    "gpt-4.1": 1000000,
    "gpt-4.1-mini": 1000000,
    "gpt-4.1-nano": 1000000,

    # Azure OpenAI / OpenAI - GPT-4o series (128k context)
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,

    # Azure OpenAI / OpenAI - GPT-5 series (272k context)
    "gpt-5": 270000,
    "gpt-5-mini": 270000,
    "gpt-5-nano": 270000,
    "gpt-5-chat": 110000,
    "gpt-5.1": 270000,
    "gpt-5.1-chat": 110000,
    "gpt-5.1-codex": 270000,
    "gpt-5.1-codex-mini": 270000,
    "gpt-5.1-codex-max": 270000,
    "gpt-5.2": 270000,
    "gpt-5.2-chat": 110000,

    # Azure OpenAI / OpenAI - Reasoning models (o-series)
    "o1": 200000,
    "o1-preview": 128000,
    "o1-mini": 128000,
    "o3": 200000,
    "o3-mini": 200000,
    "o3-pro": 200000,
    "o4-mini": 200000,
    "codex-mini": 200000,
}

# Default limits by provider prefix (fallback when model not in explicit list)
# Gemini/Vertex models have 1M, Claude models have 200k
PROVIDER_DEFAULT_LIMITS = {
    "azure/": 200000,       # Conservative default for unknown Azure models
    "vertex/": 1000000,     # Vertex models have 1M
    "gemini-": 1000000,     # Gemini models have 1M
    "claude-": 200000,      # Claude models have 200k (1M beta requires special header)
    "openai/": 128000,      # Conservative for unknown OpenAI models
}

# Absolute fallback
DEFAULT_CONTEXT_LIMIT = 200000


def is_tui_command(command: str) -> bool:
    """
    Detect if a command will launch a TUI application.

    Handles piped commands by checking the rightmost command,
    since that's what actually displays in the terminal.

    Args:
        command: Full command string (e.g., "htop -d 5" or "git log | less")

    Returns:
        True if command is a known TUI application
    """
    if not command.strip():
        return False

    # For piped commands, check the rightmost command (that's what displays)
    # e.g., "cat file | less" -> check "less"
    # e.g., "git log | head" -> check "head" (not TUI)
    if '|' in command:
        parts = command.split('|')
        command = parts[-1].strip()

    # Extract the base command (first word)
    base_cmd = command.split()[0] if command.split() else ""

    # Remove path if present (e.g., /usr/bin/htop -> htop)
    # Use lowercase for case-insensitive matching
    base_cmd = os.path.basename(base_cmd).lower()

    return base_cmd in TUI_COMMANDS
