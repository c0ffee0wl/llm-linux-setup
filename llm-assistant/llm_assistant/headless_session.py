"""Headless session for llm-assistant daemon mode.

This module provides HeadlessSession - a session class that works without
D-Bus/Terminator, suitable for use via Unix socket daemon.

Unlike TerminatorAssistantSession, HeadlessSession:
- Captures context from asciinema logs (not VTE terminals)
- Cannot execute commands directly (suggest_command for CLI tools, excluded for GUI)
- Does not support watch mode (requires D-Bus monitoring)
- Uses agent mode behavior (agentic prompts, 100 tool iterations)
"""

import asyncio
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import llm
from llm import Tool
from rich.console import Console

from llm_tools_core import filter_new_blocks, get_assistant_default_model, get_sessions_dir

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
from .utils import get_config_dir, get_logs_db_path, logs_on, get_judge_model, ConsoleHelper


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

    except Exception as e:
        import sys
        print(f"[context] Subprocess fallback failed: {e}", file=sys.stderr)
        return []


def get_command_blocks(n_commands: int = 3, session_log: Optional[str] = None) -> List[str]:
    """Get command blocks from asciinema recording.

    Args:
        n_commands: Number of recent prompt blocks to extract.
        session_log: Path to session log file. Passed directly to the library
            function to avoid mutating global os.environ (thread-safety).
    """
    if _get_command_blocks_func is not None:
        try:
            return _get_command_blocks_func(n_commands=n_commands, session_log=session_log)
        except Exception as e:
            import sys
            print(f"[context] Library get_command_blocks failed: {e}", file=sys.stderr)
    return _get_command_blocks_subprocess(n_commands)


def capture_shell_context(prev_hashes: Set[str], session_log: Optional[str] = None) -> Tuple[str, Set[str]]:
    """Capture context from asciinema with block-level deduplication.

    Args:
        prev_hashes: Set of hashes from previous capture for deduplication.
        session_log: Path to session log file to read context from.
    """
    blocks = get_command_blocks(n_commands=3, session_log=session_log)

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
    'sandboxed_shell',   # Shell command in sandbox
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
    from llm_tools_core.daemon import write_suggested_command
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
        source: Optional[str] = None,
    ):
        self.console = Console()
        self.debug = debug
        self.session_log = session_log
        self.terminal_id = terminal_id
        self.source = source  # Origin: "gui", "tui", "cli", "api", or None

        # Context tracking
        self.context_hashes: Set[str] = set()

        # Model setup (use centralized upgrade logic for assistant default)
        self.model = llm.get_model(model_name or get_assistant_default_model())
        self.model_name = self.model.model_id

        # Conversation
        self.conversation: Optional[llm.Conversation] = None

        # Mode (always agent)
        self.mode = 'agent'

        # Source citations (default: enabled)
        self._sources_enabled = True

        # Template variables
        self.headless = True
        self.exec = False
        self.watch = False

        # Watch mode (not supported in headless, but WebMixin references these)
        self.watch_mode = False
        self.watch_goal: Optional[str] = None

        # Context management (WebMixin, ContextMixin use these)
        self.max_context_size = 100000  # Default, can be overridden
        self.context_squash_threshold = 0.8

        # System prompt (initialized after mixins)
        self.system_prompt = ""

        # Logging
        self.logging_enabled = logs_on()

        # RAG state (RAGMixin)
        self.active_rag_collection: Optional[str] = None
        self.pending_rag_context: Optional[str] = None
        self.rag_top_k = 5
        self.rag_search_mode = "hybrid"  # hybrid|vector|keyword

        # Knowledge base state (KnowledgeBaseMixin)
        self.loaded_kbs: Dict[str, str] = {}  # name -> content

        # Skills state (SkillsMixin)
        self.loaded_skills: Dict[str, Tuple[Path, any]] = {}  # name -> (path, props)
        self._skill_invoke_tool: Optional[Tool] = None
        self._skill_load_file_tool: Optional[Tool] = None
        self._skill_invoke_impl = None
        self._skill_load_file_impl = None

        # Memory state (MemoryMixin)
        self._global_memory: str = ""
        self._global_memory_path: Optional[Path] = None
        self._local_memory: str = ""
        self._local_memory_path: Optional[Path] = None

        # Web companion state (WebMixin) - not used in headless but referenced
        self.web_clients: Set = set()
        self.web_server = None
        self.web_server_thread = None
        self.web_port = 8765
        self.web_event_loop = None

        # Initialize mixins that need it
        self._init_mixins()

        # Now render system prompt (after mixins initialized)
        self.system_prompt = self._render_system_prompt()

    def _init_mixins(self):
        """Initialize mixin-specific state."""
        # MemoryMixin - load AGENTS.md files
        if hasattr(self, '_load_memories'):
            self._load_memories()

        # MCPMixin (headless mode = no_exec_mode=True)
        if hasattr(MCPMixin, '_mcp_init'):
            MCPMixin._mcp_init(self, no_exec_mode=True)

        # WorkflowMixin
        if hasattr(WorkflowMixin, '_workflow_init'):
            WorkflowMixin._workflow_init(self)

        # SkillsMixin - auto-load all available skills
        if hasattr(self, '_auto_load_all_skills'):
            self._auto_load_all_skills()

        # ContextMixin (token management)
        self.terminal_content_hashes = {}
        self.toolresult_hash_updated = set()
        self.previous_capture_block_hashes = {}
        self.rewind_undo_buffer = None
        self.pending_summary = None
        self._tool_token_overhead = 0

        # ReportMixin stubs (terminal capture not available in headless mode)
        self.chat_terminal_uuid = ""  # No chat terminal in headless
        self.findings_base_dir = get_config_dir() / 'findings'
        self.findings_project: Optional[str] = None

    def _debug(self, msg: str):
        """Print debug message if debug mode enabled."""
        if self.debug:
            ConsoleHelper.debug(self.console, msg)

    def _get_judge_model(self):
        """Get or create the cached judge model for safety evaluation.

        Uses a lighter model from the same provider when available:
        - vertex/* → vertex/gemini-2.5-flash-lite
        - gemini-* → gemini-2.5-flash-lite
        - azure/*, gpt-*, o1-*, etc. → azure/gpt-4.1-mini

        Falls back to conversation model if no lighter alternative or schema unsupported.
        """
        if not hasattr(self, '_judge_model'):
            self._judge_model = get_judge_model(self.model)
            # Log which model we're using (only on first call)
            if self._judge_model.model_id != self.model.model_id:
                self._debug(f"Using {self._judge_model.model_id} as safety judge")
        return self._judge_model

    def _get_all_terminals_with_content(self) -> List[Dict]:
        """Get terminal content - not available in headless mode.

        Returns empty list since there are no VTE terminals in headless mode.
        ReportMixin uses this for capturing terminal context as evidence.
        """
        return []

    def get_system_prompt(self, gui: bool = False) -> str:
        """Render the system prompt for headless mode.

        Args:
            gui: If True, include GUI-specific sections (Mermaid diagrams)
        """
        skills = bool(getattr(self, 'loaded_skills', None))
        return render(
            'system_prompt.j2',
            headless=True,
            mode=self.mode,
            exec=False,
            watch_mode=False,  # Not available in headless mode
            watch_goal="",
            rag=bool(self.active_rag_collection),
            gui=gui,
            skills=skills,
            skills_xml=self._get_skills_xml() if skills else "",
            date=datetime.now().strftime("%Y-%m-%d"),
            platform=platform.system(),
            shell=os.environ.get("SHELL", "/bin/bash"),
            environment="",
        )

    def _render_system_prompt(self) -> str:
        """Render system prompt for headless mode.

        Required by WebMixin._update_system_prompt() and ContextMixin.
        Delegates to get_system_prompt() which handles GUI mode flag.
        """
        return self.get_system_prompt(gui=(self.source == "gui"))

    def _build_system_prompt(self) -> str:
        """Build system prompt with memory, KB, and workflow context appended.

        Required by WebMixin for web companion and debug info.
        The base system prompt is rendered by _render_system_prompt().
        This method appends memory, KB, and workflow context for the current request.
        """
        prompt = self.system_prompt

        # Append memory content (AGENTS.md) - before KB
        memory_content = self._get_memory_content()
        if memory_content:
            memory_instructions = """## Persistent Memory (AGENTS.md)

The `<memory>` section below contains user preferences and project-specific notes that persist across sessions.
- Apply these preferences to personalize responses and follow user conventions
- Project Memory takes precedence over Global Memory for project-specific topics
- Treat memory entries as authoritative user instructions"""
            prompt = f"{prompt}\n\n{memory_instructions}\n\n<memory>\n{memory_content}\n</memory>"

        # Append KB content if any loaded
        kb_content = self._get_loaded_kb_content()
        if kb_content:
            prompt = f"{prompt}\n\n<knowledge>\n# Knowledge Base\n\n{kb_content}\n</knowledge>"

        # Append workflow context if a workflow is active (from WorkflowMixin)
        workflow_context = self._get_workflow_context()
        if workflow_context:
            prompt = f"{prompt}\n\n{workflow_context}"

        return prompt

    async def capture_context(self, session_log: Optional[str] = None) -> str:
        """Capture terminal context from asciinema log.

        Runs the blocking context extraction (asciinema convert subprocess)
        in a thread to avoid blocking the asyncio event loop.

        Args:
            session_log: Path to asciinema session file (uses SESSION_LOG_FILE if not provided)

        Returns:
            Formatted context string
        """
        # Determine effective log path
        effective_log = session_log if session_log else self.session_log

        if not effective_log:
            # Clear env var to prevent stale values from affecting other code
            os.environ.pop('SESSION_LOG_FILE', None)
            self._debug("Context capture skipped: no session log path available")
            return ""

        # Validate file exists (prevents using stale paths from previous sessions)
        if not os.path.exists(effective_log):
            # Clear env var to prevent stale values from affecting other code
            os.environ.pop('SESSION_LOG_FILE', None)
            self._debug(f"Context capture skipped: session log not found: {effective_log}")
            return ""

        # Run blocking context capture in thread to avoid blocking the event loop
        # (capture_shell_context calls asciinema convert via subprocess)
        prev_hashes = self.context_hashes
        context, new_hashes = await asyncio.to_thread(
            capture_shell_context, prev_hashes, session_log=effective_log
        )
        self.context_hashes = new_hashes

        if not context:
            return ""

        return format_context_for_prompt(context)

    def get_tools(self) -> List[Tool]:
        """Get tools available in this session.

        Uses shared _add_dynamic_tools() helper from MCPMixin for:
        - MCP tools from active servers
        - Optional tools (imagemage) when loaded
        - Gemini-only tools when using Gemini/Vertex model
        - Skill tools when skills are loaded
        """
        tools = get_headless_tools()

        # Exclude suggest_command for GUI (it's for terminal-based sessions)
        if self.source == "gui":
            tools = [t for t in tools if t.name != "suggest_command"]

        existing_names = {t.name for t in tools}

        # Add dynamic tools via shared helper (MCP, optional, Gemini-only, skills)
        return self._add_dynamic_tools(tools, existing_names)

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
                        loaded = load_conversation(saved_cid, database=str(get_logs_db_path()))
                        if loaded:
                            self.conversation = loaded
                            # Update model reference if conversation was loaded
                            self.model = loaded.model
                            self.model_name = self.model.model_id
                            # Set source for future responses (may differ from historical source)
                            self.conversation.source = self.source
                    except Exception:
                        # Conversation not found or load failed, create new
                        pass

            # Create new conversation if none loaded
            if self.conversation is None:
                self.conversation = self.model.conversation()
                # Set source after creation (not a constructor parameter)
                self.conversation.source = self.source

            # Save conversation ID for resume
            if self.terminal_id and self.conversation.id:
                self._save_active_conversation(self.conversation.id)

        return self.conversation

    def reset_conversation(self):
        """Start a fresh conversation."""
        self.conversation = self.model.conversation()
        # Set source after creation (not a constructor parameter)
        self.conversation.source = self.source
        self.context_hashes = set()

        # Save new conversation ID
        if self.terminal_id and self.conversation.id:
            self._save_active_conversation(self.conversation.id)

    def _get_sessions_dir(self) -> Path:
        """Get sessions tracking directory.

        Returns /tmp/llm-assistant-{UID}/sessions/daemon/ for ephemeral
        session tracking that doesn't persist across reboots.
        """
        return get_sessions_dir('daemon')

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
