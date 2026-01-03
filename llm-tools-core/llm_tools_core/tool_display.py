"""Tool display configuration for consistent tool action messages.

This module provides shared configuration for displaying tool execution status
across llm-assistant, llm-inlineassistant, and llm-guiassistant.
"""

from typing import Dict, Tuple, Optional


# Full tool display configuration: tool_name -> (param_name, action_verb, brief_description)
# param_name: The parameter to show when displaying tool call
# action_verb: Present participle for status messages (e.g., "Searching...")
# brief_description: Short description for tooltips/help
TOOL_DISPLAY: Dict[str, Tuple[str, str, str]] = {
    # Core tools
    'execute_python': ('code', 'Executing Python', 'Execute Python code in sandbox'),
    'suggest_command': ('command', 'Preparing command', 'Suggest shell command'),

    # Web tools
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
    'take_snapshot': ('', 'Taking CDP snapshot', 'Capture DOM snapshot via CDP'),
}


def get_action_verb(tool_name: str) -> str:
    """Get the action verb for a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Action verb (e.g., "Searching") or generic message
    """
    if tool_name in TOOL_DISPLAY:
        return TOOL_DISPLAY[tool_name][1]
    return f'Executing {tool_name}'


def get_tool_info(tool_name: str) -> Optional[Tuple[str, str, str]]:
    """Get full info for a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Tuple of (param_name, action_verb, description) or None
    """
    return TOOL_DISPLAY.get(tool_name)


def get_action_verb_map() -> Dict[str, str]:
    """Get a simple tool_name -> action_verb mapping.

    Useful for clients that only need the action verb.

    Returns:
        Dict mapping tool names to action verbs
    """
    return {name: info[1] for name, info in TOOL_DISPLAY.items()}
