"""Knowledge Base mixin for llm-assistant.

This module provides knowledge base functionality:
- Loading/unloading markdown KB files
- Auto-loading from config
- /kb command handling
"""

from pathlib import Path
from typing import TYPE_CHECKING

from .utils import get_config_dir

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
                    self.console.print(f"[red]KB not found: {name}[/]")
                    self.console.print(f"[dim]Looking in: {kb_dir}[/]")
                return False

        try:
            content = kb_path.read_text()
            self.loaded_kbs[name] = content
            if not silent:
                self.console.print(f"[green]✓[/] Loaded KB: {name} ({len(content)} chars)")
            return True
        except Exception as e:
            if not silent:
                self.console.print(f"[red]Failed to load {name}: {e}[/]")
            return False

    def _unload_kb(self, name: str) -> bool:
        """Unload a KB from session."""
        if name in self.loaded_kbs:
            del self.loaded_kbs[name]
            self.console.print(f"[green]✓[/] Unloaded KB: {name}")
            return True
        self.console.print(f"[yellow]KB not loaded: {name}[/]")
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
        parts = args.strip().split(maxsplit=1)

        if not parts or parts[0] == "":
            # /kb - list KBs
            self._list_kbs()
        elif parts[0] == "load" and len(parts) > 1:
            # /kb load <name> or /kb load name1,name2,name3
            names = [n.strip() for n in parts[1].split(",") if n.strip()]
            for name in names:
                self._load_kb(name)
        elif parts[0] == "unload" and len(parts) > 1:
            # /kb unload <name> or /kb unload name1,name2,name3
            names = [n.strip() for n in parts[1].split(",") if n.strip()]
            for name in names:
                self._unload_kb(name)
        elif parts[0] == "reload":
            # /kb reload - reload all loaded KBs
            names = list(self.loaded_kbs.keys())
            if names:
                for name in names:
                    self._load_kb(name)
            else:
                self.console.print("[yellow]No KBs loaded to reload[/]")
        else:
            self.console.print("[yellow]Usage: /kb [load|unload|reload] [name][/]")

        return True

    def _list_kbs(self):
        """List available and loaded KBs."""
        kb_dir = self._get_kb_dir()
        available = sorted([f.stem for f in kb_dir.glob("*.md")])

        self.console.print("\n[bold]Knowledge Bases[/]")

        if self.loaded_kbs:
            self.console.print("\n[green]Loaded:[/]")
            for name in sorted(self.loaded_kbs.keys()):
                chars = len(self.loaded_kbs[name])
                self.console.print(f"  • {name} ({chars} chars)")

        unloaded = [n for n in available if n not in self.loaded_kbs]
        if unloaded:
            self.console.print("\n[dim]Available:[/]")
            for name in unloaded:
                self.console.print(f"  • {name}")

        if not available and not self.loaded_kbs:
            self.console.print(f"\n[dim]No KBs found in {kb_dir}[/]")
            self.console.print("[dim]Create markdown files to use as knowledge bases.[/]")
