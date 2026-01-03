"""Headless session for llm-assistant daemon mode.

This module provides HeadlessSession - a session class that works without
D-Bus/Terminator, suitable for use via Unix socket daemon.

Unlike TerminatorAssistantSession, HeadlessSession:
- Captures context from asciinema logs (not VTE terminals)
- Cannot execute commands directly (only suggest_command)
- Does not support watch mode (requires D-Bus monitoring)
- Does not support agent mode (requires execute_in_terminal)
"""

import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import llm
from llm import Tool
from rich.console import Console

from llm_tools_core import filter_new_blocks

# Mixin imports (9 mixins, excluding TerminalMixin and WatchMixin)
from .kb import KnowledgeBaseMixin
from .memory import MemoryMixin
from .rag import RAGMixin
from .skills import SkillsMixin
from .workflow import WorkflowMixin
from .report import ReportMixin
from .web import WebMixin
from .context import ContextMixin
from .mcp import MCPMixin
from .templates import render
from .utils import get_config_dir, logs_on, ConsoleHelper


# Try to import context capture from llm_tools_context
_get_command_blocks_func = None
try:
    from llm_tools_context import get_command_blocks as _pkg_get_command_blocks
    _get_command_blocks_func = _pkg_get_command_blocks
except ImportError:
    pass


def _get_command_blocks_subprocess(n_commands: int = 3) -> List[str]:
    """Fallback: get command blocks via subprocess."""
    try:
        result = subprocess.run(
            ['context', str(n_commands)],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            return []

        output = result.stdout
        if not output.strip():
            return []

        # Parse context output (lines prefixed with #c#)
        blocks = []
        current_block = []

        for line in output.split('\n'):
            if line.startswith('#c# '):
                content = line[4:]
            elif line.startswith('#c#'):
                content = line[3:]
            else:
                continue

            if content.strip():
                current_block.append(content)
            else:
                if current_block:
                    blocks.append('\n'.join(current_block))
                    current_block = []

        if current_block:
            blocks.append('\n'.join(current_block))

        return blocks

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return []


def get_command_blocks(n_commands: int = 3) -> List[str]:
    """Get command blocks from asciinema recording."""
    if _get_command_blocks_func is not None:
        try:
            return _get_command_blocks_func(n_commands=n_commands)
        except Exception:
            pass
    return _get_command_blocks_subprocess(n_commands)


def capture_shell_context(prev_hashes: Set[str]) -> Tuple[str, Set[str]]:
    """Capture context from asciinema with block-level deduplication."""
    blocks = get_command_blocks(n_commands=3)

    if not blocks:
        return "", prev_hashes

    new_blocks, current_hashes = filter_new_blocks(blocks, prev_hashes)

    if not new_blocks:
        return "[Content unchanged]", current_hashes

    context = '\n'.join(new_blocks)
    return context, current_hashes


def format_context_for_prompt(context: str) -> str:
    """Format captured context for injection into prompt."""
    if not context or context == "[Content unchanged]":
        return f"<terminal_context>{context}</terminal_context>"

    return f"""<terminal_context>
{context}
</terminal_context>"""


# External plugin tools to expose in headless mode
HEADLESS_TOOL_NAMES = (
    'execute_python',    # Python in sandbox
    'fetch_url',         # Web content
    'search_google',     # Google search
    'load_github',       # GitHub content
    'load_pdf',          # PDF extraction
    'load_yt',           # YouTube transcripts
    'prompt_fabric',     # AI patterns
    'capture_screen',    # Screenshot capture (X11 only)
)


def _suggest_command_impl(command: str) -> str:
    """Implementation for suggest_command tool."""
    from .utils import write_suggested_command
    write_suggested_command(command)
    return f"Command ready. User presses Ctrl+G to apply: {command}"


# Built-in suggest_command tool
SUGGEST_COMMAND_TOOL = Tool(
    name="suggest_command",
    description="""Suggest a shell command by placing it on the user's command line.

The command is saved for the user to apply. After your response, the user
presses Ctrl+G to place the command on their prompt, where they can review,
edit, and execute it by pressing Enter.

IMPORTANT: This does NOT execute the command. The user must press Ctrl+G
to apply, then Enter to execute. Use this when the user asks for a command
or when you want to propose a command for them to run.

Args:
    command: The shell command to suggest

Returns:
    Confirmation that command is ready (user presses Ctrl+G to apply)
""",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to place on the user's prompt"
            }
        },
        "required": ["command"]
    },
    implementation=_suggest_command_impl,
)


def get_headless_tools() -> List[Tool]:
    """Get tools available in headless mode."""
    tools = [SUGGEST_COMMAND_TOOL]

    all_tools = llm.get_tools()
    for name in HEADLESS_TOOL_NAMES:
        if name in all_tools:
            tool = all_tools[name]
            if isinstance(tool, Tool):
                tools.append(tool)
    return tools


def get_tool_implementations() -> Dict[str, callable]:
    """Get tool implementations for auto-dispatch."""
    implementations = {
        'suggest_command': _suggest_command_impl,
    }

    all_tools = llm.get_tools()
    for name in HEADLESS_TOOL_NAMES:
        if name in all_tools:
            tool = all_tools[name]
            if isinstance(tool, Tool) and hasattr(tool, 'implementation') and tool.implementation:
                implementations[name] = tool.implementation
    return implementations


class HeadlessSession(
    KnowledgeBaseMixin,
    MemoryMixin,
    RAGMixin,
    SkillsMixin,
    WorkflowMixin,
    ReportMixin,
    WebMixin,
    ContextMixin,
    MCPMixin,
):
    """Headless session for daemon mode - no D-Bus/Terminator required.

    This session class provides:
    - Context capture from asciinema logs
    - Tool execution (suggest_command + plugin tools)
    - RAG, Knowledge Base, Skills, Memory
    - MCP server integration
    - Report generation
    - Conversation persistence across daemon restarts

    NOT available (D-Bus dependent):
    - execute_in_terminal, send_keypress
    - capture_terminal, refresh_context, search_terminal
    - Watch mode
    - Agent mode (requires execute_in_terminal)
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        debug: bool = False,
        session_log: Optional[str] = None,
        terminal_id: Optional[str] = None,
    ):
        self.console = Console()
        self.debug = debug
        self.session_log = session_log
        self.terminal_id = terminal_id

        # Context tracking
        self.context_hashes: Set[str] = set()

        # Model setup
        self.model = llm.get_model(model_name) if model_name else llm.get_model()
        self.model_name = self.model.model_id

        # Conversation
        self.conversation: Optional[llm.Conversation] = None

        # Mode (only assistant supported in headless)
        self.mode = 'assistant'

        # Template variables
        self.headless = True
        self.exec_active = False
        self.watch_active = False

        # Logging
        self.logging_enabled = logs_on()

        # Initialize mixins that need it
        self._init_mixins()

    def _init_mixins(self):
        """Initialize mixin-specific state."""
        # RAGMixin
        if hasattr(RAGMixin, '_rag_init'):
            RAGMixin._rag_init(self)

        # KnowledgeBaseMixin
        if hasattr(KnowledgeBaseMixin, '_kb_init'):
            KnowledgeBaseMixin._kb_init(self)

        # MemoryMixin
        if hasattr(MemoryMixin, '_memory_init'):
            MemoryMixin._memory_init(self)

        # SkillsMixin
        if hasattr(SkillsMixin, '_skills_init'):
            SkillsMixin._skills_init(self)

        # MCPMixin (headless mode = no_exec_mode=True)
        if hasattr(MCPMixin, '_mcp_init'):
            MCPMixin._mcp_init(self, no_exec_mode=True)

        # WorkflowMixin
        if hasattr(WorkflowMixin, '_workflow_init'):
            WorkflowMixin._workflow_init(self)

        # ContextMixin (token management)
        self.terminal_content_hashes = {}
        self.toolresult_hash_updated = set()
        self.previous_capture_block_hashes = {}
        self.rewind_undo_buffer = None
        self.pending_summary = None
        self._tool_token_overhead = 0

    def _debug(self, msg: str):
        """Print debug message if debug mode enabled."""
        if self.debug:
            ConsoleHelper.debug(self.console, msg)

    def get_system_prompt(self) -> str:
        """Render the system prompt for headless mode."""
        skills_active = bool(getattr(self, 'loaded_skills', None))
        return render(
            'system_prompt.j2',
            headless=True,
            mode=self.mode,
            exec_active=False,
            watch_mode=False,  # Not available in headless mode
            watch_goal="",
            rag_active=getattr(self, 'rag_active', False),
            skills_active=skills_active,
            skills_xml=self._get_skills_xml() if skills_active else "",
            date=datetime.now().strftime("%Y-%m-%d"),
            platform=platform.system(),
            shell=os.environ.get("SHELL", "/bin/bash"),
            environment="",
        )

    def capture_context(self, session_log: Optional[str] = None) -> str:
        """Capture terminal context from asciinema log.

        Args:
            session_log: Path to asciinema session file (uses SESSION_LOG_FILE if not provided)

        Returns:
            Formatted context string
        """
        # Set SESSION_LOG_FILE for context capture
        if session_log:
            os.environ['SESSION_LOG_FILE'] = session_log
        elif self.session_log:
            os.environ['SESSION_LOG_FILE'] = self.session_log

        context, self.context_hashes = capture_shell_context(self.context_hashes)

        if not context:
            return ""

        return format_context_for_prompt(context)

    def get_tools(self) -> List[Tool]:
        """Get tools available in this session."""
        tools = get_headless_tools()
        existing_names = {t.name for t in tools}

        # Add MCP tools from active servers
        if hasattr(self, 'active_mcp_servers'):
            all_tools = llm.get_tools()
            for tool in all_tools.values():
                if not isinstance(tool, Tool):
                    continue
                # Check if this is an MCP tool (has server_name attribute)
                server = getattr(tool, 'server_name', None)
                if server and server in self.active_mcp_servers:
                    if tool.name not in existing_names:
                        tools.append(tool)
                        existing_names.add(tool.name)

        return tools

    def get_or_create_conversation(self) -> llm.Conversation:
        """Get existing conversation or create new one.

        Supports conversation persistence across daemon restarts:
        - Loads saved conversation ID if available
        - Saves conversation ID after creation
        """
        if self.conversation is None:
            # Try to load saved conversation if we have a terminal ID
            if self.terminal_id:
                saved_cid = self._get_active_conversation()
                if saved_cid:
                    try:
                        from llm.cli import load_conversation
                        loaded = load_conversation(saved_cid, database=str(self._get_logs_db_path()))
                        if loaded:
                            self.conversation = loaded
                            # Update model reference if conversation was loaded
                            self.model = loaded.model
                            self.model_name = self.model.model_id
                    except Exception:
                        # Conversation not found or load failed, create new
                        pass

            # Create new conversation if none loaded
            if self.conversation is None:
                self.conversation = self.model.conversation()

            # Save conversation ID for resume
            if self.terminal_id and self.conversation.id:
                self._save_active_conversation(self.conversation.id)

        return self.conversation

    def reset_conversation(self):
        """Start a fresh conversation."""
        self.conversation = self.model.conversation()
        self.context_hashes = set()

        # Save new conversation ID
        if self.terminal_id and self.conversation.id:
            self._save_active_conversation(self.conversation.id)

    def _get_sessions_dir(self) -> Path:
        """Get sessions tracking directory."""
        sessions_dir = get_config_dir() / 'daemon-sessions'
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return sessions_dir

    def _get_logs_db_path(self) -> Path:
        """Get path to conversation database."""
        return get_config_dir() / 'logs-daemon.db'

    def _get_active_conversation(self) -> Optional[str]:
        """Get saved conversation ID for this terminal."""
        if not self.terminal_id:
            return None

        # Sanitize terminal_id for filename
        safe_id = self.terminal_id.replace('/', '_').replace(':', '_')
        path = self._get_sessions_dir() / safe_id

        if path.exists():
            cid = path.read_text().strip()
            return cid if cid else None
        return None

    def _save_active_conversation(self, conversation_id: str) -> None:
        """Save conversation ID for this terminal."""
        if not self.terminal_id:
            return

        # Sanitize terminal_id for filename
        safe_id = self.terminal_id.replace('/', '_').replace(':', '_')
        path = self._get_sessions_dir() / safe_id
        path.write_text(conversation_id)
