"""Memory mixin for llm-assistant.

This module provides AGENTS.md-style memory functionality:
- Global AGENTS.md from config dir (~/.config/llm-assistant/AGENTS.md)
- Local AGENTS.md from cwd (./AGENTS.md)
- # command for writing memories
- /memory command for viewing and managing memories
"""

from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from .utils import get_config_dir, parse_command, ConsoleHelper

if TYPE_CHECKING:
    from rich.console import Console


class MemoryMixin:
    """Mixin providing AGENTS.md memory functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - _debug: Debug logging method
    """

    # Type hints for attributes provided by main class
    console: 'Console'

    def _debug(self, msg: str) -> None:
        """Debug logging - implemented by main class."""
        pass  # Overridden by TerminatorAssistantSession

    # Memory content (loaded at startup, refreshed on change)
    _global_memory: str
    _global_memory_path: Optional[Path]
    _local_memory: str
    _local_memory_path: Optional[Path]

    def _get_global_agents_path(self) -> Path:
        """Get global AGENTS.md path in config directory."""
        return get_config_dir() / "AGENTS.md"

    def _find_agents_file(self, directory: Path) -> Optional[Path]:
        """Find AGENTS.md file with case-insensitive search.

        Priority: AGENTS.md > Agents.md > agents.md
        """
        # Check in priority order
        for variant in ["AGENTS.md", "Agents.md", "agents.md"]:
            candidate = directory / variant
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _get_local_agents_path(self) -> Optional[Path]:
        """Get local AGENTS.md path from cwd if exists."""
        return self._find_agents_file(Path.cwd())

    def _load_memories(self) -> None:
        """Load global and local AGENTS.md files."""
        # Load global memory (case-insensitive)
        config_dir = get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        global_path = self._find_agents_file(config_dir)
        if global_path:
            self._global_memory_path = global_path
            try:
                self._global_memory = global_path.read_text()
                self._debug(f"Loaded global memory: {global_path} ({len(self._global_memory)} chars)")
            except Exception as e:
                ConsoleHelper.warning(self.console, f"Failed to load global AGENTS.md: {e}")
                self._global_memory = ""
        else:
            self._global_memory_path = None
            self._global_memory = ""

        # Load local memory (case-insensitive)
        local_path = self._get_local_agents_path()
        if local_path:
            self._local_memory_path = local_path
            try:
                self._local_memory = local_path.read_text()
                self._debug(f"Loaded local memory: {local_path} ({len(self._local_memory)} chars)")
            except Exception as e:
                ConsoleHelper.warning(self.console, f"Failed to load local AGENTS.md: {e}")
                self._local_memory = ""
        else:
            self._local_memory_path = None
            self._local_memory = ""

    def _get_memory_content(self) -> str:
        """Get combined memory content for system prompt injection.

        Returns global and local memory with clear section headers.
        """
        parts = []

        if self._global_memory.strip():
            parts.append(f"## Global Memory\n\n{self._global_memory.strip()}")

        if self._local_memory.strip():
            parts.append(f"## Project Memory\n\n{self._local_memory.strip()}")

        if not parts:
            return ""

        return "\n\n".join(parts)

    def _write_memory(self, note: str, target: str = "local") -> bool:
        """Append note to AGENTS.md file.

        Args:
            note: The note text to append
            target: "local" for cwd, "global" for config dir

        Returns:
            True on success, False on failure
        """
        if not note.strip():
            ConsoleHelper.warning(self.console, "Empty note, nothing saved")
            return False

        # Determine target path (case-insensitive to match existing file)
        if target == "global":
            config_dir = get_config_dir()
            config_dir.mkdir(parents=True, exist_ok=True)
            existing = self._find_agents_file(config_dir)
            path = existing if existing else config_dir / "AGENTS.md"
        else:
            # For local, use existing file if found (case-insensitive)
            # Otherwise create as AGENTS.md
            existing = self._get_local_agents_path()
            path = existing if existing else Path.cwd() / "AGENTS.md"

        # Format entry with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- {timestamp}: {note.strip()}"

        try:
            if path.exists():
                content = path.read_text()

                # Find or create ## Notes section
                if "## Notes" in content:
                    # Append to existing Notes section
                    # Find the Notes section and append at its end
                    lines = content.split("\n")
                    result_lines = []
                    in_notes_section = False
                    inserted = False

                    for i, line in enumerate(lines):
                        result_lines.append(line)

                        if line.strip() == "## Notes":
                            in_notes_section = True
                        elif in_notes_section and line.startswith("## ") and line.strip() != "## Notes":
                            # End of Notes section (next section started)
                            # Insert entry before this line
                            result_lines.insert(-1, entry)
                            result_lines.insert(-1, "")
                            in_notes_section = False
                            inserted = True

                    # If Notes section is last or we didn't insert yet
                    if not inserted:
                        # Ensure trailing newline before entry
                        if result_lines and result_lines[-1].strip():
                            result_lines.append("")
                        result_lines.append(entry)

                    new_content = "\n".join(result_lines)
                else:
                    # Add Notes section at end
                    if not content.endswith("\n"):
                        content += "\n"
                    new_content = f"{content}\n## Notes\n\n{entry}\n"

                path.write_text(new_content)
            else:
                # Create new file with Notes section
                new_content = f"## Notes\n\n{entry}\n"
                path.write_text(new_content)

            # Reload memories to update cached content
            self._load_memories()
            return True

        except PermissionError:
            ConsoleHelper.error(self.console, f"Permission denied: {path}")
            return False
        except Exception as e:
            ConsoleHelper.error(self.console, f"Failed to write memory: {e}")
            return False

    def _handle_hash_command(self, args: str) -> bool:
        """Handle # command for writing memories.

        # <note>        - Write to local AGENTS.md
        # global <note> - Write to global AGENTS.md

        Returns True to continue REPL.
        """
        if not args:
            ConsoleHelper.warning(self.console, "Usage: # <note> or # global <note>")
            return True

        # Check for "global" prefix
        if args.lower().startswith("global "):
            note = args[7:].strip()
            target = "global"
        else:
            note = args
            target = "local"

        if self._write_memory(note, target):
            target_desc = "global AGENTS.md" if target == "global" else "local AGENTS.md"
            ConsoleHelper.success(self.console, f"Memory saved to {target_desc}")

        return True

    def _handle_memory_command(self, args: str) -> bool:
        """Handle /memory commands. Returns True to continue REPL."""
        cmd, rest = parse_command(args)

        if cmd == "" or cmd == "list":
            # /memory or /memory list - show all memory
            self._show_memory()
        elif cmd == "reload":
            # /memory reload - reload from disk
            self._load_memories()
            ConsoleHelper.success(self.console, "Memory reloaded")
        elif cmd == "global":
            # /memory global - show only global
            self._show_memory(global_only=True)
        elif cmd == "local":
            # /memory local - show only local
            self._show_memory(local_only=True)
        else:
            ConsoleHelper.warning(self.console, "Usage: /memory [list|reload|global|local]")

        return True

    def _show_memory(self, global_only: bool = False, local_only: bool = False) -> None:
        """Display loaded memory content."""
        from rich.markdown import Markdown
        from rich.panel import Panel

        has_content = False

        if not local_only and self._global_memory.strip():
            global_path = self._global_memory_path or self._get_global_agents_path()
            self.console.print(Panel(
                Markdown(self._global_memory),
                title=f"[bold]Global Memory[/] [dim]({global_path})[/]",
                border_style="blue"
            ))
            has_content = True

        if not global_only and self._local_memory.strip():
            local_path = self._local_memory_path or Path.cwd() / "AGENTS.md"
            self.console.print(Panel(
                Markdown(self._local_memory),
                title=f"[bold]Project Memory[/] [dim]({local_path})[/]",
                border_style="green"
            ))
            has_content = True

        if not has_content:
            if global_only:
                ConsoleHelper.info(self.console, f"No global memory loaded. Create: {self._get_global_agents_path()}")
            elif local_only:
                ConsoleHelper.info(self.console, "No project memory loaded. Create AGENTS.md in current directory.")
            else:
                ConsoleHelper.info(self.console, "No memory loaded.")
                self.console.print(f"[dim]Global: {self._get_global_agents_path()}[/]")
                self.console.print(f"[dim]Local:  ./AGENTS.md[/]")
