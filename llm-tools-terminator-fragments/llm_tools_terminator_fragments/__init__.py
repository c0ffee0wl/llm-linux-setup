"""
llm-tools-terminator-fragments

LLM plugin to capture Terminator terminal content as context fragments.
Provides a tool interface for accessing terminal scrollback content.
"""

import html
import llm
from typing import Optional


@llm.hookimpl
def register_tools(register):
    """Register the Terminator fragment toolbox with LLM"""
    register(TerminatorFragments)


class TerminatorFragments(llm.Toolbox):
    """
    Toolbox to capture content from Terminator terminals.

    Provides access to visible scrollback content from Terminator VTE terminals
    via the TerminatorSidechatPlugin.
    """

    name = "TerminatorFragments"

    def capture(
        self,
        terminal_id: str = "focused",
        lines: Optional[int] = None
    ) -> str:
        """Capture visible content from Terminator terminal emulator.

        terminal_id options:
        - 'focused': Capture currently focused terminal
        - 'all': Capture all terminals
        - 'urn:uuid:...': Capture specific terminal by UUID

        lines: Number of scrollback lines to capture (default None = auto-detect visible viewport)

        Returns terminal content wrapped in XML tags."""
        try:
            # Get Terminator sidechat plugin
            plugin = self._get_plugin()
            if not plugin:
                return "ERROR: Terminator sidechat plugin not loaded. Ensure Terminator is running with the plugin enabled."

            if terminal_id == "focused":
                # Capture focused terminal
                uuid = plugin.get_focused_terminal_uuid()
                if not uuid:
                    return "ERROR: No terminal is currently focused"

                content = plugin.capture_terminal_content(uuid, lines)
                terminals = plugin.get_all_terminals_metadata()
                term_info = next((t for t in terminals if t['uuid'] == uuid), None)

                if term_info:
                    return self._format_terminal(
                        uuid,
                        content,
                        term_info['title'],
                        term_info['cwd']
                    )
                else:
                    return self._format_terminal(uuid, content, "Terminal", "~")

            elif terminal_id == "all":
                # Capture all terminals
                terminals = plugin.get_all_terminals_metadata()
                if not terminals:
                    return "ERROR: No terminals found"

                results = []
                for term in terminals:
                    content = plugin.capture_terminal_content(term['uuid'], lines)
                    results.append(self._format_terminal(
                        term['uuid'],
                        content,
                        term['title'],
                        term['cwd']
                    ))

                return "\n\n".join(results)

            else:
                # Capture specific terminal by UUID
                content = plugin.capture_terminal_content(terminal_id, lines)

                # Try to get metadata for better formatting
                terminals = plugin.get_all_terminals_metadata()
                term_info = next((t for t in terminals if t['uuid'] == terminal_id), None)

                if term_info:
                    return self._format_terminal(
                        terminal_id,
                        content,
                        term_info['title'],
                        term_info['cwd']
                    )
                else:
                    return self._format_terminal(terminal_id, content, "Terminal", "~")

        except Exception as e:
            return f"ERROR: Failed to capture terminal content: {str(e)}"

    def _get_plugin(self):
        """
        Get TerminatorSidechatPlugin instance via PluginRegistry.

        Returns:
            Plugin instance, or None if not available
        """
        try:
            from terminatorlib.plugin import PluginRegistry
            registry = PluginRegistry()
            registry.load_plugins()
            return registry.instances.get('TerminatorSidechatPlugin')
        except ImportError:
            return None
        except Exception:
            return None

    def _format_terminal(
        self,
        uuid: str,
        content: str,
        title: str,
        cwd: str
    ) -> str:
        """
        Format terminal content with XML tags for LLM comprehension.

        Args:
            uuid: Terminal UUID
            content: Terminal scrollback content
            title: Terminal title
            cwd: Current working directory

        Returns:
            Formatted XML string
        """
        # Escape XML special characters to prevent injection
        return f'''<terminal uuid="{html.escape(uuid)}" title="{html.escape(title)}" cwd="{html.escape(cwd)}">
{html.escape(content)}
</terminal>'''
