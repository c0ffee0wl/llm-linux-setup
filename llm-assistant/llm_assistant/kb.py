"""Knowledge Base mixin for llm-assistant.

This module provides knowledge base functionality:
- Loading/unloading markdown KB files
- Auto-loading from config
- /kb command handling
"""

from pathlib import Path
from typing import TYPE_CHECKING

from .utils import get_config_dir, parse_command, parse_comma_list, ConsoleHelper, render_grouped_list

if TYPE_CHECKING:
    from rich.console import Console


class KnowledgeBaseMixin:
    """Mixin providing knowledge base functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - loaded_kbs: dict[str, str] for loaded KB content
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    loaded_kbs: dict

    def _get_kb_dir(self) -> Path:
        """Get or create KB directory in config directory."""
        kb_dir = get_config_dir() / "kb"
        kb_dir.mkdir(parents=True, exist_ok=True)
        return kb_dir

    def _load_auto_kbs(self):
        """Load KBs listed in config.yaml auto_load."""
        config = self._load_config()
        auto_load = config.get("knowledge_base", {}).get("auto_load", [])
        for name in auto_load:
            self._load_kb(name, silent=True)

    def _load_kb(self, name: str, silent: bool = False) -> bool:
        """Load a KB file by name."""
        kb_dir = self._get_kb_dir()

        # Try with .md extension first
        kb_path = kb_dir / f"{name}.md"
        if not kb_path.exists():
            # Try without extension
            kb_path = kb_dir / name
            if not kb_path.exists():
                if not silent:
                    ConsoleHelper.error(self.console, f"KB not found: {name}")
                    self.console.print(f"[dim]Looking in: {kb_dir}[/]")
                return False

        try:
            content = kb_path.read_text()
            self.loaded_kbs[name] = content
            if not silent:
                ConsoleHelper.success(self.console, f"Loaded KB: {name} ({len(content)} chars)")
            return True
        except Exception as e:
            if not silent:
                ConsoleHelper.error(self.console, f"Failed to load {name}: {e}")
            return False

    def _unload_kb(self, name: str) -> bool:
        """Unload a KB from session."""
        if name in self.loaded_kbs:
            del self.loaded_kbs[name]
            ConsoleHelper.success(self.console, f"Unloaded KB: {name}")
            return True
        ConsoleHelper.warning(self.console, f"KB not loaded: {name}")
        return False

    def _get_loaded_kb_content(self) -> str:
        """Get combined content of all loaded KBs."""
        if not self.loaded_kbs:
            return ""
        parts = []
        for name, content in self.loaded_kbs.items():
            parts.append(f"## {name}\n\n{content}")
        return "\n\n---\n\n".join(parts)

    def _handle_kb_command(self, args: str) -> bool:
        """Handle /kb commands. Returns True to continue REPL."""
        cmd, rest = parse_command(args)

        if cmd == "" or cmd == "list":
            # /kb or /kb list - list KBs
            self._list_kbs()
        elif cmd == "load" and rest:
            # /kb load <name> or /kb load name1,name2,name3
            for name in parse_comma_list(rest):
                self._load_kb(name)
        elif cmd == "unload" and rest:
            # /kb unload <name> or /kb unload name1,name2,name3
            for name in parse_comma_list(rest):
                self._unload_kb(name)
        elif cmd == "reload":
            # /kb reload - reload all loaded KBs
            names = list(self.loaded_kbs.keys())
            if names:
                for name in names:
                    self._load_kb(name)
            else:
                ConsoleHelper.warning(self.console, "No KBs loaded to reload")
        else:
            ConsoleHelper.warning(self.console, "Usage: /kb [load|unload|reload|list] [name]")

        return True

    def _list_kbs(self):
        """List available and loaded KBs."""
        kb_dir = self._get_kb_dir()
        # Build available dict: {name: None} for consistency with render_grouped_list
        available = {f.stem: None for f in kb_dir.glob("*.md")}

        def format_kb(name, content):
            if content is not None:
                return f"{name} ({len(content)} chars)"
            return name

        render_grouped_list(
            self.console,
            "Knowledge Bases",
            self.loaded_kbs,
            available,
            format_kb,
            f"No KBs found in {kb_dir}\nCreate markdown files to use as knowledge bases."
        )
