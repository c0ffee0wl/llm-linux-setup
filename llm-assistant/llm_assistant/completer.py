"""Tab completion for slash commands in llm-assistant.

This module provides the SlashCommandCompleter class for:
- Command name completion (when typing /)
- Subcommand completion (off, status, load, etc.)
- Dynamic completions (model names, KB names, RAG collections)
"""

from typing import TYPE_CHECKING

import llm
from prompt_toolkit.completion import Completer, Completion

from .config import SLASH_COMMANDS

if TYPE_CHECKING:
    from .session import TerminatorAssistantSession


class SlashCommandCompleter(Completer):
    """Tab completion for slash commands in llm-assistant.

    Supports:
    - Command names (when typing /)
    - Subcommands (off, status, load, etc.)
    - Dynamic completions (model names, KB names)
    """

    def __init__(self, session: 'TerminatorAssistantSession' = None):
        """Initialize completer with optional session reference.

        Args:
            session: TerminatorAssistantSession instance for dynamic completions.
                     Can be set later via set_session().
        """
        self.session = session

    def set_session(self, session: 'TerminatorAssistantSession'):
        """Set session reference (for deferred initialization)."""
        self.session = session

    def get_completions(self, document, complete_event):
        """Yield completions based on current input."""
        text = document.text_before_cursor

        # Only complete if starts with /
        if not text.startswith('/'):
            return

        parts = text.split()

        # parts will always have at least 1 element since text starts with '/'
        # (split() on "/" returns ['/'], on "/mo" returns ['/mo'], etc.)

        if len(parts) == 1 and not text.endswith(' '):
            # Completing command name: "/" -> all commands, "/mo" -> "/model"
            yield from self._complete_commands(parts[0])

        elif len(parts) >= 1:
            # Completing after command: "/model " or "/kb load "
            cmd = parts[0].lower()

            if cmd not in SLASH_COMMANDS:
                return

            cmd_info = SLASH_COMMANDS[cmd]

            if len(parts) == 1 and text.endswith(' '):
                # Just command + space: "/model " - show subcommands or dynamic
                yield from self._complete_first_arg(cmd, cmd_info, "")

            elif len(parts) == 2 and not text.endswith(' '):
                # Partial first arg: "/model az" or "/kb lo"
                partial = parts[1]
                yield from self._complete_first_arg(cmd, cmd_info, partial)

            elif len(parts) == 2 and text.endswith(' '):
                # First arg complete, need second: "/kb load "
                subcommand = parts[1].lower()
                yield from self._complete_second_arg(cmd, subcommand, "")

            elif len(parts) == 3 and not text.endswith(' '):
                # Partial second arg: "/kb load pro"
                subcommand = parts[1].lower()
                partial = parts[2]
                yield from self._complete_second_arg(cmd, subcommand, partial)

    def _complete_commands(self, partial: str):
        """Complete command names."""
        partial_lower = partial.lower()
        for cmd, info in SLASH_COMMANDS.items():
            if cmd.lower().startswith(partial_lower):
                yield Completion(
                    cmd,
                    start_position=-len(partial),
                    display_meta=info.get("description", "")
                )

    def _complete_first_arg(self, cmd: str, cmd_info: dict, partial: str):
        """Complete first argument (subcommand or dynamic value)."""
        partial_lower = partial.lower()

        # Subcommands first
        for sub in cmd_info.get("subcommands", []):
            if sub.lower().startswith(partial_lower):
                yield Completion(
                    sub,
                    start_position=-len(partial)
                )

        # Dynamic completions
        dynamic_type = cmd_info.get("dynamic")
        if dynamic_type == "models":
            yield from self._complete_models(partial)
        elif dynamic_type == "kb" and cmd == "/kb":
            # For /kb without subcommand, don't show KB names yet
            pass
        elif dynamic_type == "rag_collections" and cmd == "/rag":
            # For /rag without subcommand, show collection names for activation
            yield from self._complete_rag_collections(partial)
        elif cmd == "/rewind":
            # Show recent turn numbers for quick rewind
            if self.session and self.session.conversation.responses:
                total = len(self.session.conversation.responses)
                for i in range(max(1, total - 5), total):
                    num_str = str(i)
                    if num_str.startswith(partial):
                        yield Completion(num_str, start_position=-len(partial))

    def _complete_second_arg(self, cmd: str, subcommand: str, partial: str):
        """Complete second argument (KB names after load/unload, RAG collections, finding IDs, MCP servers)."""
        if cmd == "/kb":
            if subcommand == "load":
                yield from self._complete_available_kbs(partial)
            elif subcommand == "unload":
                yield from self._complete_loaded_kbs(partial)
        elif cmd == "/rag":
            if subcommand in ("add", "search", "rebuild", "delete"):
                yield from self._complete_rag_collections(partial)
        elif cmd == "/report":
            if subcommand in ("edit", "delete", "severity"):
                yield from self._complete_findings(partial)
            elif subcommand in ("open", "init"):
                yield from self._complete_report_projects(partial)
        elif cmd == "/mcp":
            if subcommand in ("load", "unload"):
                yield from self._complete_mcp_servers(partial)
        elif cmd == "/skill":
            if subcommand == "load":
                yield from self._complete_available_skills(partial)
            elif subcommand == "unload":
                yield from self._complete_loaded_skills(partial)

    def _complete_models(self, partial: str):
        """Complete model names dynamically."""
        partial_lower = partial.lower()
        try:
            for model in llm.get_models():
                model_id = model.model_id
                if model_id.lower().startswith(partial_lower):
                    yield Completion(
                        model_id,
                        start_position=-len(partial)
                    )
        except Exception:
            pass  # Graceful degradation if llm unavailable

    def _complete_available_kbs(self, partial: str):
        """Complete available KB names for /kb load."""
        if not self.session:
            return
        partial_lower = partial.lower()
        try:
            kb_dir = self.session._get_kb_dir()
            for kb_file in kb_dir.glob("*.md"):
                name = kb_file.stem
                # Only show KBs not already loaded
                if name not in self.session.loaded_kbs:
                    if name.lower().startswith(partial_lower):
                        yield Completion(
                            name,
                            start_position=-len(partial)
                        )
        except Exception:
            pass

    def _complete_loaded_kbs(self, partial: str):
        """Complete loaded KB names for /kb unload."""
        if not self.session:
            return
        partial_lower = partial.lower()
        try:
            for name in self.session.loaded_kbs.keys():
                if name.lower().startswith(partial_lower):
                    yield Completion(
                        name,
                        start_position=-len(partial)
                    )
        except Exception:
            pass

    def _complete_mcp_servers(self, partial: str):
        """Complete MCP server names for /mcp load/unload."""
        if not self.session:
            return
        partial_lower = partial.lower()
        try:
            all_servers = self.session._get_all_mcp_servers()
            for server_name in sorted(all_servers.keys()):
                if server_name.lower().startswith(partial_lower):
                    yield Completion(
                        server_name,
                        start_position=-len(partial)
                    )
        except Exception:
            pass

    def _complete_available_skills(self, partial: str):
        """Complete available skill names for /skill load."""
        if not self.session:
            return
        partial_lower = partial.lower()
        try:
            available = self.session._discover_available_skills()
            for name in sorted(available.keys()):
                # Only show skills not already loaded
                if name not in self.session.loaded_skills:
                    if name.lower().startswith(partial_lower):
                        _, props = available[name]
                        desc = props.description[:40] if props.description else ""
                        yield Completion(
                            name,
                            start_position=-len(partial),
                            display_meta=desc
                        )
        except Exception:
            pass

    def _complete_loaded_skills(self, partial: str):
        """Complete loaded skill names for /skill unload."""
        if not self.session:
            return
        partial_lower = partial.lower()
        try:
            for name in self.session.loaded_skills.keys():
                if name.lower().startswith(partial_lower):
                    yield Completion(
                        name,
                        start_position=-len(partial)
                    )
        except Exception:
            pass

    def _complete_rag_collections(self, partial: str):
        """Complete RAG collection names."""
        partial_lower = partial.lower()
        try:
            from llm_tools_rag import get_collection_list
            for coll in get_collection_list():
                name = coll['name']
                if name.lower().startswith(partial_lower):
                    yield Completion(
                        name,
                        start_position=-len(partial)
                    )
        except ImportError:
            pass  # llm-tools-rag not installed
        except Exception:
            pass  # Graceful degradation

    def _complete_findings(self, partial: str):
        """Complete finding IDs (F001, F002, etc.) for current project."""
        if not self.session or not self.session.findings_project:
            return
        partial_upper = partial.upper()
        try:
            project_dir = self.session.findings_base_dir / self.session.findings_project
            findings_file = project_dir / "findings.md"
            if findings_file.exists():
                _, findings = self.session._parse_findings_file(findings_file)
                for meta, _ in findings:
                    fid = meta.get('id', '')
                    if fid.upper().startswith(partial_upper):
                        title = meta.get('title', '')[:30]
                        yield Completion(
                            fid,
                            start_position=-len(partial),
                            display_meta=title
                        )
        except Exception:
            pass

    def _complete_report_projects(self, partial: str):
        """Complete report project names for /report open."""
        if not self.session:
            return
        partial_lower = partial.lower()
        try:
            base_dir = self.session.findings_base_dir
            if base_dir.exists():
                for project_dir in base_dir.iterdir():
                    if project_dir.is_dir():
                        name = project_dir.name
                        if name.lower().startswith(partial_lower):
                            yield Completion(
                                name,
                                start_position=-len(partial)
                            )
        except Exception:
            pass
