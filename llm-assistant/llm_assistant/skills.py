"""Skills mixin for llm-assistant.

This module provides skill functionality via llm-tools-skills:
- Skill discovery and loading
- Tool creation for skill_invoke and skill_load_file
- /skill command handling
"""

from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Any

from llm import Tool

from .utils import get_config_dir

if TYPE_CHECKING:
    from rich.console import Console


class SkillsMixin:
    """Mixin providing skills functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - loaded_skills: dict[str, tuple[Path, Any]] for loaded skills
    - _skill_invoke_tool: Optional[Tool]
    - _skill_load_file_tool: Optional[Tool]
    - system_prompt: str for system prompt
    - _update_system_prompt: method to re-render and broadcast
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    loaded_skills: Dict[str, Tuple[Path, Any]]
    _skill_invoke_tool: Optional[Tool]
    _skill_load_file_tool: Optional[Tool]
    system_prompt: str

    def _skills_available(self) -> bool:
        """Check if llm-tools-skills is installed."""
        try:
            import llm_tools_skills
            return True
        except ImportError:
            return False

    def _get_skills_dir(self) -> Path:
        """Get default skills directory."""
        return get_config_dir() / "skills"

    def _auto_load_all_skills(self):
        """Auto-load all available skills at startup."""
        if not self._skills_available():
            return

        available = self._discover_available_skills()
        for name in available:
            self._load_skill(name, silent=True)

    def _discover_available_skills(self) -> Dict[str, Tuple[Path, Any]]:
        """Discover all available skills from the skills directory."""
        from llm_tools_skills import discover_skills, read_properties

        result = {}
        skills_dir = self._get_skills_dir()
        if skills_dir.exists():
            discovered = discover_skills(skills_dir)
            for name, path in discovered.items():
                try:
                    props = read_properties(path)
                    result[name] = (path, props)
                except ValueError:
                    pass  # Skip invalid skills
        return result

    def _get_skills_xml(self) -> str:
        """Generate <available_skills> XML for system prompt."""
        from llm_tools_skills import to_prompt
        skill_dirs = [path for path, _ in self.loaded_skills.values()]
        return to_prompt(skill_dirs)

    def _list_skill_files(self, skill_dir: Path) -> List[str]:
        """List all loadable files in skill directory (recursively)."""
        from llm_tools_skills import TEXT_FILE_EXTENSIONS, TEXT_FILE_NAMES

        files = []

        def collect(directory: Path, prefix: str = ""):
            try:
                for item in sorted(directory.iterdir()):
                    rel_path = f"{prefix}{item.name}" if prefix else item.name
                    if item.is_dir():
                        collect(item, f"{rel_path}/")
                    elif item.is_file() and item.name.lower() != "skill.md":
                        if item.suffix.lower() in TEXT_FILE_EXTENSIONS or item.name in TEXT_FILE_NAMES:
                            files.append(rel_path)
            except PermissionError:
                pass

        collect(skill_dir)
        return files

    def _load_skill(self, name: str, silent: bool = False) -> bool:
        """Load a skill by name."""
        available = self._discover_available_skills()
        if name not in available:
            if not silent:
                self.console.print(f"[red]Skill not found: {name}[/]")
            return False

        path, props = available[name]
        self.loaded_skills[name] = (path, props)
        self._rebuild_skill_tools()
        # Re-render system prompt and notify web companion
        self._update_system_prompt(broadcast_type="skill")

        if not silent:
            self.console.print(f"[green]✓[/] Loaded skill: {name}")
        return True

    def _unload_skill(self, name: str) -> bool:
        """Unload a skill."""
        if name not in self.loaded_skills:
            self.console.print(f"[yellow]Skill not loaded: {name}[/]")
            return False

        del self.loaded_skills[name]
        self._rebuild_skill_tools()
        # Re-render system prompt and notify web companion
        self._update_system_prompt(broadcast_type="skill")
        self.console.print(f"[green]✓[/] Unloaded skill: {name}")
        return True

    def _rebuild_skill_tools(self):
        """Rebuild skill tools when skills change."""
        if not self.loaded_skills:
            self._skill_invoke_tool = None
            self._skill_load_file_tool = None
            return

        # Create skill_invoke tool (XML is in system prompt, not here)
        self._skill_invoke_tool = Tool(
            name="skill_invoke",
            description="Load a skill's full instructions and see available files. Call with skill name from <available_skills> in system prompt.",
            input_schema={"type": "object", "properties": {"name": {"type": "string", "description": "Skill name to invoke"}}, "required": ["name"]},
        )

        # Create skill_load_file tool
        self._skill_load_file_tool = Tool(
            name="skill_load_file",
            description="Load a specific file from a skill's directory (scripts, references, assets).",
            input_schema={"type": "object", "properties": {
                "skill": {"type": "string", "description": "Skill name"},
                "file": {"type": "string", "description": "File path relative to skill directory"}
            }, "required": ["skill", "file"]},
        )

    def _skill_invoke_impl(self, name: str) -> str:
        """Tool implementation: Load a skill's full SKILL.md content."""
        if name not in self.loaded_skills:
            return f"Error: Skill '{name}' not loaded. Use /skill load {name} first."

        path, props = self.loaded_skills[name]
        skill_file = path / "SKILL.md"
        content = skill_file.read_text()

        # List available files
        files = self._list_skill_files(path)
        if files:
            content += "\n\nAvailable files:\n" + "\n".join(f"  - {f}" for f in files)

        return content

    def _skill_load_file_impl(self, skill: str, file: str) -> str:
        """Tool implementation: Load a specific file from a skill directory."""
        if skill not in self.loaded_skills:
            return f"Error: Skill '{skill}' not loaded."

        path, _ = self.loaded_skills[skill]
        file_path = path / file

        # Security: ensure file is within skill directory
        try:
            file_path.resolve().relative_to(path.resolve())
        except ValueError:
            return f"Error: Path '{file}' is outside skill directory"

        if not file_path.exists():
            return f"Error: File '{file}' not found in skill '{skill}'"

        return file_path.read_text()

    def _list_skills(self):
        """List available and loaded skills."""
        available = self._discover_available_skills()

        self.console.print("\n[bold]Skills[/]")

        if self.loaded_skills:
            self.console.print("\n[green]Loaded:[/]")
            for name in sorted(self.loaded_skills.keys()):
                path, props = self.loaded_skills[name]
                desc = props.description[:50] + "..." if len(props.description) > 50 else props.description
                self.console.print(f"  • {name}: {desc}")

        unloaded = [n for n in available.keys() if n not in self.loaded_skills]
        if unloaded:
            self.console.print("\n[dim]Available:[/]")
            for name in sorted(unloaded):
                path, props = available[name]
                desc = props.description[:50] + "..." if len(props.description) > 50 else props.description
                self.console.print(f"  • {name}: {desc}")

        if not available and not self.loaded_skills:
            skills_dir = self._get_skills_dir()
            self.console.print(f"\n[dim]No skills found in {skills_dir}[/]")
            self.console.print("[dim]Create skill directories with SKILL.md files.[/]")

    def _handle_skill_command(self, args: str) -> bool:
        """Handle /skill commands. Returns True to continue REPL."""
        if not self._skills_available():
            self.console.print("[red]Skills not available. Install llm-tools-skills.[/]")
            self.console.print("[dim]Run: pip install llm-tools-skills[/]")
            return True

        parts = args.strip().split(maxsplit=1)

        if not parts or parts[0] == "" or parts[0] == "list":
            # /skill or /skill list - list skills
            self._list_skills()
        elif parts[0] == "load" and len(parts) > 1:
            # /skill load <name> or /skill load name1,name2
            names = [n.strip() for n in parts[1].split(",") if n.strip()]
            for name in names:
                self._load_skill(name)
        elif parts[0] == "unload" and len(parts) > 1:
            # /skill unload <name> or /skill unload name1,name2
            names = [n.strip() for n in parts[1].split(",") if n.strip()]
            for name in names:
                self._unload_skill(name)
        elif parts[0] == "reload":
            # /skill reload - reload all loaded skills
            names = list(self.loaded_skills.keys())
            if names:
                for name in names:
                    self._load_skill(name, silent=True)
                self.console.print(f"[green]✓[/] Reloaded {len(names)} skill(s)")
            else:
                self.console.print("[yellow]No skills loaded to reload[/]")
        else:
            self.console.print("[yellow]Usage: /skill [load|unload|reload|list] [name][/]")

        return True
