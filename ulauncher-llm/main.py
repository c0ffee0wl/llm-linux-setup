"""ulauncher-llm - AI assistant extension for Ulauncher.

Connects to the llm-assistant daemon via Unix socket, providing
both simple mode ('llm' keyword) and full assistant mode ('@' keyword)
with streaming responses and tool execution feedback.
"""

import uuid

from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent, ItemEnterEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.CopyToClipboardAction import CopyToClipboardAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction
from ulauncher.api.shared.action.DoNothingAction import DoNothingAction
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction

from query import query_daemon_sync, execute_slash_command_sync
from helpers import strip_markdown_for_copy, truncate_query, extract_code_blocks, format_for_display, get_clipboard_text


class LLMExtension(Extension):
    """Main extension class for ulauncher-llm.

    Uses synchronous blocking calls (like ulauncher-gemini-direct) for
    daemon communication. Maintains session for conversation continuity.
    """

    def __init__(self):
        super().__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryListener())
        self.subscribe(ItemEnterEvent, ItemEnterListener())

        # Persistent session ID for conversation continuity
        # Using a stable ID means queries within same Ulauncher session
        # maintain conversation context
        self.session_id = f"ulauncher:{uuid.uuid4().hex[:8]}"

        # Last response for /last command
        self.last_response = None


class KeywordQueryListener(EventListener):
    """Handles keyword query events from Ulauncher.

    Triggered when user types 'llm <query>' or '@ <query>'.
    Determines mode from keyword and initiates streaming response.
    """

    def on_event(self, event, extension):
        keyword = event.get_keyword()
        query = event.get_argument()

        # Get all keyword preferences
        simple_kw = extension.preferences.get('kw_simple', 'llm')
        simple_clip_kw = extension.preferences.get('kw_simple_clip', 'llmc')
        assistant_clip_kw = extension.preferences.get('kw_assistant_clip', '@c')

        # Determine mode and clipboard inclusion from keyword
        include_clipboard = keyword in (simple_clip_kw, assistant_clip_kw)
        mode = "simple" if keyword in (simple_kw, simple_clip_kw) else "assistant"

        # Empty query: show help
        if not query or not query.strip():
            return self._show_help(mode)

        query = query.strip()

        # Handle slash commands immediately
        if query.startswith('/'):
            result = self._handle_slash_command(query, extension, mode)
            if result is not None:
                return result
            # Unknown slash command - fall through to treat as query

        # Show query preview - actual query sent on Enter
        if include_clipboard:
            clipboard_text = get_clipboard_text()
            clipboard_preview = f" (+{len(clipboard_text)} chars)" if clipboard_text else " (empty)"
            description = f"Press Enter to send with clipboard{clipboard_preview}"
        else:
            clipboard_text = ""
            description = "Press Enter to send"

        return RenderResultListAction([
            ExtensionResultItem(
                icon='images/icon.png',
                name=f"Ask: {truncate_query(query, 70)}",
                description=description,
                highlightable=False,
                on_enter=ExtensionCustomAction({
                    'action': 'query',
                    'query': query,
                    'mode': mode,
                    'clipboard': clipboard_text
                }, keep_app_open=True)
            )
        ])

    def _show_help(self, mode: str):
        """Show help items when query is empty.

        Args:
            mode: Current mode ("simple" or "assistant")

        Returns:
            RenderResultListAction with help items
        """
        mode_desc = "Simple mode (no tools)" if mode == "simple" else "Assistant mode (tools enabled)"

        items = [
            ExtensionResultItem(
                icon='images/icon.png',
                name="Type your question...",
                description=mode_desc,
                highlightable=False,
                on_enter=DoNothingAction()
            ),
            ExtensionResultItem(
                icon='images/icon.png',
                name="/new",
                description="Start fresh conversation",
                highlightable=False,
                on_enter=DoNothingAction()
            ),
            ExtensionResultItem(
                icon='images/icon.png',
                name="/status",
                description="Show session info",
                highlightable=False,
                on_enter=DoNothingAction()
            ),
        ]

        return RenderResultListAction(items)

    def _handle_slash_command(self, command: str, extension, mode: str):
        """Handle slash commands like /new, /status, /help.

        Args:
            command: The slash command (e.g., "/new")
            extension: The extension instance
            mode: Current mode

        Returns:
            RenderResultListAction with command result or None to continue
        """
        command = command.lower().strip()

        if command in ('/new', '/reset', '/clear'):
            # Show preview, execute on Enter (synchronous)
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name="Start new conversation",
                    description="Press Enter to reset",
                    highlightable=False,
                    on_enter=ExtensionCustomAction({
                        'action': 'slash',
                        'cmd': 'new'
                    }, keep_app_open=True)
                )
            ])

        elif command in ('/status', '/info'):
            # Show preview, execute on Enter (synchronous)
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name="Get session status",
                    description="Press Enter to fetch",
                    highlightable=False,
                    on_enter=ExtensionCustomAction({
                        'action': 'slash',
                        'cmd': 'status'
                    }, keep_app_open=True)
                )
            ])

        elif command == '/help':
            mode_desc = "Simple mode (no tools)" if mode == "simple" else "Assistant mode (with tools)"
            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name="/new",
                    description="Start fresh conversation",
                    highlightable=False,
                    on_enter=DoNothingAction()
                ),
                ExtensionResultItem(
                    icon='images/icon.png',
                    name="/status",
                    description="Show session info",
                    highlightable=False,
                    on_enter=DoNothingAction()
                ),
                ExtensionResultItem(
                    icon='images/icon.png',
                    name="/model [name]",
                    description="List or switch models",
                    highlightable=False,
                    on_enter=DoNothingAction()
                ),
                ExtensionResultItem(
                    icon='images/icon.png',
                    name="/last",
                    description="Show last response",
                    highlightable=False,
                    on_enter=DoNothingAction()
                ),
                ExtensionResultItem(
                    icon='images/icon.png',
                    name="Type your question",
                    description=f"Current: {mode_desc}",
                    highlightable=False,
                    on_enter=DoNothingAction()
                ),
            ])

        elif command.startswith('/model'):
            parts = command.split(maxsplit=1)
            if len(parts) == 1:
                # List available models - show preview, execute on Enter
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name="List available models",
                        description="Press Enter to fetch",
                        highlightable=False,
                        on_enter=ExtensionCustomAction({
                            'action': 'slash',
                            'cmd': 'model'
                        }, keep_app_open=True)
                    )
                ])
            else:
                # Switch to specified model - show preview, execute on Enter
                model_name = parts[1].strip()
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name=f"Switch to: {model_name}",
                        description="Press Enter to switch",
                        highlightable=False,
                        on_enter=ExtensionCustomAction({
                            'action': 'slash',
                            'cmd': 'model',
                            'model_arg': model_name
                        }, keep_app_open=True)
                    )
                ])

        elif command == '/last':
            last = extension.last_response
            if last:
                plain_last = strip_markdown_for_copy(last)
                # Read display preferences
                try:
                    wrap_width = int(extension.preferences.get('wrap_width', '43'))
                except (ValueError, TypeError):
                    wrap_width = 43
                try:
                    wide_script_factor = float(extension.preferences.get('wide_script_factor', '0.5'))
                except (ValueError, TypeError):
                    wide_script_factor = 0.5
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name=format_for_display(last, wrap_width, wide_script_factor),
                        description="Enter: copy | Alt+Enter: copy markdown",
                        highlightable=False,
                        on_enter=CopyToClipboardAction(plain_last),
                        on_alt_enter=CopyToClipboardAction(last)
                    )
                ])
            else:
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name="No recent response",
                        description="Make a query first",
                        highlightable=False,
                        on_enter=HideWindowAction()
                    )
                ])

        # Unknown command - pass through as query
        return None


class ItemEnterListener(EventListener):
    """Handles Enter key presses on result items.

    Processes custom actions like copy operations.
    """

    def on_event(self, event, extension):
        data = event.get_data()

        if not isinstance(data, dict):
            return HideWindowAction()

        action = data.get('action')

        if action == 'continue':
            # Set query input to continue conversation
            mode = data.get('mode', 'assistant')
            if mode == 'simple':
                keyword = extension.preferences.get('kw_simple', 'llm')
            else:
                keyword = extension.preferences.get('kw_assistant', '@')
            return SetUserQueryAction(f"{keyword} ")

        elif action == 'query':
            # Execute the actual query synchronously (blocking)
            query = data.get('query', '')
            mode = data.get('mode', 'assistant')
            clipboard = data.get('clipboard', '')

            # Prepend clipboard content if provided
            if clipboard:
                query = f"[Clipboard content]\n{clipboard}\n\n[Question]\n{query}"

            # Use synchronous query - blocks until response is complete
            text, error, tools_used = query_daemon_sync(
                query=query,
                mode=mode,
                session_id=extension.session_id
            )

            # Store last response
            if text:
                extension.last_response = text

            if error:
                return RenderResultListAction([
                    ExtensionResultItem(
                        icon='images/icon.png',
                        name=f"Error: {error[:60]}",
                        description="Press Enter to dismiss",
                        highlightable=False,
                        on_enter=HideWindowAction()
                    )
                ])

            # Build result items
            items = []

            # Pre-compute stripped text for direct clipboard action
            plain_text = strip_markdown_for_copy(text)

            # Read display preferences
            try:
                wrap_width = int(extension.preferences.get('wrap_width', '43'))
            except (ValueError, TypeError):
                wrap_width = 43
            try:
                wide_script_factor = float(extension.preferences.get('wide_script_factor', '0.5'))
            except (ValueError, TypeError):
                wide_script_factor = 0.5

            # Main response item
            title = format_for_display(text, wrap_width, wide_script_factor)
            # Build description with tools info
            if tools_used:
                tools_str = ", ".join(tools_used)
                description = f"Used: {tools_str} | Enter: copy | Alt+Enter: markdown"
            else:
                description = "Enter: copy | Alt+Enter: copy markdown"
            items.append(ExtensionResultItem(
                icon='images/icon.png',
                name=title,
                description=description,
                highlightable=False,
                on_enter=CopyToClipboardAction(plain_text),
                on_alt_enter=CopyToClipboardAction(text)
            ))

            # Quick action: Copy code blocks only (if any)
            code_blocks = extract_code_blocks(text)
            if code_blocks:
                items.append(ExtensionResultItem(
                    icon='images/icon.png',
                    name="Copy code blocks",
                    description=f"{len(code_blocks)} code block(s) found",
                    highlightable=False,
                    on_enter=CopyToClipboardAction('\n\n'.join(code_blocks))
                ))

            # Quick action: Continue conversation
            items.append(ExtensionResultItem(
                icon='images/icon.png',
                name="Continue conversation...",
                description="Add follow-up question",
                highlightable=False,
                on_enter=ExtensionCustomAction({
                    'action': 'continue',
                    'mode': mode
                }, keep_app_open=True)
            ))

            return RenderResultListAction(items)

        elif action == 'slash':
            cmd = data.get('cmd')
            model_arg = data.get('model_arg')

            # Execute synchronously
            title, message = execute_slash_command_sync(
                command=cmd,
                session_id=extension.session_id,
                model_arg=model_arg
            )

            return RenderResultListAction([
                ExtensionResultItem(
                    icon='images/icon.png',
                    name=title,
                    description=message[:100] if message else "",
                    highlightable=False,
                    on_enter=HideWindowAction()
                )
            ])

        return HideWindowAction()


if __name__ == '__main__':
    LLMExtension().run()
