"""Skills mixin for llm-assistant.

This module provides skill functionality via llm-tools-skills:
- Skill discovery and loading
- Tool creation for skill_invoke and skill_load_file
- /skill command handling
"""

from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Any

from llm import Tool

from .utils import get_config_dir, check_import, parse_command, parse_comma_list, ConsoleHelper, render_grouped_list

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
        return check_import("llm_tools_skills")

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
                ConsoleHelper.error(self.console, f"Skill not found: {name}")
            return False

        path, props = available[name]
        self.loaded_skills[name] = (path, props)
        self._rebuild_skill_tools()
        # Re-render system prompt and notify web companion
        self._update_system_prompt(broadcast_type="skill")

        if not silent:
            ConsoleHelper.success(self.console, f"Loaded skill: {name}")
        return True

    def _unload_skill(self, name: str) -> bool:
        """Unload a skill."""
        if name not in self.loaded_skills:
            ConsoleHelper.warning(self.console, f"Skill not loaded: {name}")
            return False

        del self.loaded_skills[name]
        self._rebuild_skill_tools()
        # Re-render system prompt and notify web companion
        self._update_system_prompt(broadcast_type="skill")
        ConsoleHelper.success(self.console, f"Unloaded skill: {name}")
        return True

    def _rebuild_skill_tools(self):
        """Rebuild skill tools when skills change."""
        if not self.loaded_skills:
            self._skill_invoke_tool = None
            self._skill_load_file_tool = None
            self._skill_invoke_impl = None
            self._skill_load_file_impl = None
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

        # Set impl attributes to bound methods for _get_active_external_tools dispatch
        # (MCPMixin._mcp_init sets these to None, which shadows the methods)
        self._skill_invoke_impl = self._do_skill_invoke
        self._skill_load_file_impl = self._do_skill_load_file

    def _do_skill_invoke(self, name: str) -> str:
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

    def _do_skill_load_file(self, skill: str, file: str) -> str:
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
        skills_dir = self._get_skills_dir()

        def format_skill(name, data):
            path, props = data
            desc = props.description[:50] + "..." if len(props.description) > 50 else props.description
            return f"{name}: {desc}"

        render_grouped_list(
            self.console,
            "Skills",
            self.loaded_skills,
            available,
            format_skill,
            f"No skills found in {skills_dir}\nCreate skill directories with SKILL.md files."
        )

    def _handle_skill_command(self, args: str) -> bool:
        """Handle /skill commands. Returns True to continue REPL."""
        if not self._skills_available():
            ConsoleHelper.error(self.console, "Skills not available. Install llm-tools-skills.")
            ConsoleHelper.dim(self.console, "Run: pip install llm-tools-skills")
            return True

        cmd, rest = parse_command(args)

        if cmd == "" or cmd == "list":
            # /skill or /skill list - list skills
            self._list_skills()
        elif cmd == "load" and rest:
            # /skill load <name> or /skill load name1,name2
            for name in parse_comma_list(rest):
                self._load_skill(name)
        elif cmd == "unload" and rest:
            # /skill unload <name> or /skill unload name1,name2
            for name in parse_comma_list(rest):
                self._unload_skill(name)
        elif cmd == "reload":
            # /skill reload - reload all loaded skills
            names = list(self.loaded_skills.keys())
            if names:
                for name in names:
                    self._load_skill(name, silent=True)
                ConsoleHelper.success(self.console, f"Reloaded {len(names)} skill(s)")
            else:
                ConsoleHelper.warning(self.console, "No skills loaded to reload")
        else:
            ConsoleHelper.warning(self.console, "Usage: /skill [load|unload|reload|list] [name]")

        return True
