"""Watch mode mixin for llm-assistant.

This module provides background terminal monitoring:
- Watch mode thread management
- Async watch loop for periodic context capture
- Context hash computation for change detection
- Automatic AI prompting on terminal changes
"""

import asyncio
import difflib
import hashlib
import threading
from typing import TYPE_CHECKING, List, Optional

import llm
from rich.markdown import Markdown
from rich.panel import Panel

from llm_tools_core import PromptDetector
from .templates import render
from .utils import ConsoleHelper
from .utils import is_watch_response_dismissive

if TYPE_CHECKING:
    from rich.console import Console


class WatchMixin:
    """Mixin providing watch mode functionality.

    Expects these attributes on self:
    - console: Rich Console for output
    - watch_mode: bool indicating if watch mode is active
    - watch_goal: Optional[str] goal for watch mode
    - watch_interval: int seconds between checks
    - watch_lock: threading.Lock for thread safety
    - watch_thread: Optional[threading.Thread]
    - watch_task: Optional asyncio task
    - event_loop: Optional asyncio event loop
    - previous_watch_context_hash: Optional[str] for deduplication
    - previous_watch_iteration_count: int for tracking iterations
    - previous_watch_context: Optional[str] for diff computation
    - watch_start_time: Optional[float] timestamp when watch enabled
    - watch_total_iterations: int total loop iterations
    - watch_ai_calls: int number of AI calls made
    - watch_alerts_shown: int number of alerts displayed
    - plugin_dbus: D-Bus plugin service object
    - exec_terminal_uuid: str UUID of exec terminal
    - chat_terminal_uuid: str UUID of chat terminal (for exclusion)
    - content_change_receiver: Optional ContentChangeReceiver for signal-based monitoring
    - capture_context: method to capture terminal context
    - _prompt: method to prompt the model
    - _build_system_prompt: method to build system prompt
    - _log_response: method to log responses
    - _debug: method for debug output
    - get_foreground_process: method to get terminal's foreground process info
    - subscribe_content_changes: method to subscribe to content change signals
    - unsubscribe_content_changes: method to unsubscribe from content change signals
    """

    # Type hints for attributes provided by main class
    console: 'Console'
    watch_mode: bool
    watch_goal: Optional[str]
    watch_interval: int
    watch_lock: threading.Lock
    watch_thread: Optional[threading.Thread]
    watch_task: Optional[object]
    event_loop: Optional[object]
    previous_watch_context_hash: Optional[str]
    previous_watch_iteration_count: int
    previous_watch_context: Optional[str]
    watch_start_time: Optional[float]
    watch_total_iterations: int
    watch_ai_calls: int
    watch_alerts_shown: int
    plugin_dbus: object
    exec_terminal_uuid: str

    def _compute_context_hash(self, context: str, attachments: List[llm.Attachment]) -> str:
        """Compute SHA256 hash of context for change detection."""
        hasher = hashlib.sha256()
        normalized_context = ' '.join(context.split())  # Normalize whitespace
        hasher.update(normalized_context.encode('utf-8'))
        for attachment in attachments:
            if hasattr(attachment, 'path') and attachment.path:
                hasher.update(attachment.path.encode('utf-8'))
        return hasher.hexdigest()

    def _start_watch_mode_thread(self):
        """Start watch mode in a background thread with its own event loop"""
        def watch_thread_target():
            self.event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.event_loop)
            try:
                self.watch_task = self.event_loop.create_task(self.watch_loop())
                self.event_loop.run_until_complete(self.watch_task)
            except asyncio.CancelledError:
                pass  # Expected when watch mode is disabled
            except Exception as e:
                ConsoleHelper.error(self.console, f"Watch mode error: {e}")
            finally:
                self.watch_task = None
                self.event_loop.close()

        self.watch_thread = threading.Thread(target=watch_thread_target, daemon=True)
        self.watch_thread.start()

    def _get_watched_terminal_uuids(self) -> List[str]:
        """Get list of terminal UUIDs to watch (all except chat terminal)."""
        try:
            terminals = self.plugin_dbus.enumerate_terminals()
            chat_uuid = getattr(self, 'chat_terminal_uuid', None)
            return [t['uuid'] for t in terminals if t.get('uuid') != chat_uuid]
        except Exception:
            return []

    def _update_watch_subscriptions(self, current_uuids: set, subscribed_uuids: set) -> set:
        """Update content change subscriptions for watch mode.

        Returns the new set of subscribed UUIDs.
        """
        # Unsubscribe from terminals that no longer exist
        for uuid in subscribed_uuids - current_uuids:
            self.unsubscribe_content_changes(uuid)

        # Subscribe to new terminals
        for uuid in current_uuids - subscribed_uuids:
            self.subscribe_content_changes(uuid)

        return current_uuids.copy()

    async def watch_loop(self):
        """
        Background monitoring of all terminals (like tmuxai watch mode).

        Implements intelligent change detection:
        1. Hash-based skip: Don't send unchanged context to AI
        2. History-aware prompt: Tell AI to focus on NEW content when changes detected
        3. Signal-based wakeup: Wake immediately on terminal content changes
        """
        # Check if signal-based watching is available
        use_signals = (hasattr(self, 'content_change_receiver') and
                      self.content_change_receiver and
                      self.content_change_receiver.is_running())
        subscribed_uuids: set = set()

        if use_signals:
            # Initial subscription to all watched terminals
            current_uuids = set(self._get_watched_terminal_uuids())
            subscribed_uuids = self._update_watch_subscriptions(current_uuids, subscribed_uuids)
            # Drain any pending signals
            self.content_change_receiver.get_all_changes()
            self._debug(f"Watch mode: signal-based monitoring ({len(subscribed_uuids)} terminals)")

        try:
            while self.watch_mode:
                try:
                    # Increment total iterations counter
                    self.watch_total_iterations += 1

                    # Update subscriptions if terminals changed (new tabs, closed tabs)
                    if use_signals:
                        current_uuids = set(self._get_watched_terminal_uuids())
                        if current_uuids != subscribed_uuids:
                            subscribed_uuids = self._update_watch_subscriptions(current_uuids, subscribed_uuids)
                            self._debug(f"Watch mode: updated subscriptions ({len(subscribed_uuids)} terminals)")

                    # Thread-safe capture and prompt - hold lock during D-Bus calls and conversation
                    # This prevents race conditions with main thread's D-Bus operations
                    context = None
                    tui_attachments = []
                    response_text = None
                    exec_status = ""
                    should_skip = False

                    with self.watch_lock:
                        # Capture all terminal content (including exec output for watch)
                        # Returns (context_text, tui_attachments) tuple for TUI screenshot support
                        # NOTE: dedupe_unchanged=False because watch mode uses hash-based
                        # deduplication at the whole-context level (more efficient - skips
                        # AI call entirely when unchanged, rather than sending "[Content unchanged]")
                        context, tui_attachments = self.capture_context(
                            include_exec_output=True,
                            dedupe_unchanged=False
                        )

                        # Check exec terminal idle state using PromptDetector and foreground process
                        try:
                            exec_content = self.plugin_dbus.capture_terminal_content(
                                self.exec_terminal_uuid, -1
                            )
                            if exec_content:
                                is_idle = PromptDetector.detect_prompt_at_end(exec_content)
                                if is_idle:
                                    exec_status = "[Exec: idle]"
                                else:
                                    # Try to get actual process name
                                    try:
                                        process_info = self.get_foreground_process(self.exec_terminal_uuid)
                                        if process_info and process_info.get('name'):
                                            exec_status = f"[Exec: running {process_info['name']}]"
                                        else:
                                            exec_status = "[Exec: command running]"
                                    except Exception:
                                        exec_status = "[Exec: command running]"
                        except Exception:
                            exec_status = "[Exec: unknown]"

                        if not context.strip():
                            # No context to analyze
                            should_skip = True
                        else:
                            # CHANGE DETECTION: Compute hash and compare with previous
                            current_hash = self._compute_context_hash(context, tui_attachments)

                            if current_hash == self.previous_watch_context_hash:
                                # Context unchanged - skip AI call entirely
                                should_skip = True
                            else:
                                # Compute diff if we have previous context
                                context_to_send = context
                                is_diff_mode = False

                                if self.previous_watch_context is not None:
                                    diff_lines = list(difflib.unified_diff(
                                        self.previous_watch_context.splitlines(keepends=True),
                                        context.splitlines(keepends=True),
                                        fromfile='previous',
                                        tofile='current',
                                        lineterm=''
                                    ))
                                    if diff_lines:
                                        diff_text = '\n'.join(diff_lines)
                                        # Fallback if diff > 80% of full context
                                        if len(diff_text) < len(context) * 0.8:
                                            context_to_send = diff_text
                                            is_diff_mode = True

                                # Update stored context and hash
                                self.previous_watch_context = context
                                self.previous_watch_context_hash = current_hash
                                self.previous_watch_iteration_count += 1

                                # Build prompt with diff or full context
                                # Wrap in <watch_prompt> tag for filtering in web companion
                                # (stripped by _strip_context, so watch iterations don't
                                # appear in regular conversation view)
                                prompt = '<watch_prompt>' + render('prompts/watch_prompt.j2',
                                    iteration_count=self.previous_watch_iteration_count,
                                    goal=self.watch_goal,
                                    exec_status=exec_status,
                                    context=context_to_send,
                                    is_diff_mode=is_diff_mode,
                                ) + '</watch_prompt>'

                                try:
                                    # Include TUI screenshots if any were captured
                                    # Always pass system prompt on every call (required for Gemini/Vertex
                                    # which is stateless - systemInstruction must be sent on every request)
                                    #
                                    # IMPORTANT: stream=False minimizes lock hold time by getting the
                                    # complete response in one call. Lock is necessary because the
                                    # conversation object is not thread-safe and is shared with main thread.
                                    self.watch_ai_calls += 1
                                    response = self._prompt(
                                        prompt,
                                        system=self._build_system_prompt(),
                                        attachments=tui_attachments if tui_attachments else None,
                                        stream=False  # Reduce lock hold time
                                    )
                                    response_text = response.text()
                                    # Log watch mode response to database
                                    self._log_response(response)
                                except Exception as response_error:
                                    # Don't update hash on error - will retry next iteration
                                    self.previous_watch_context_hash = None
                                    ConsoleHelper.warning(self.console, f"Watch mode response error: {response_error}")

                    # Only show if AI has actionable feedback - outside lock
                    if not should_skip and response_text and response_text.strip():
                        if '<NoComment/>' in response_text:
                            pass  # AI explicitly indicated nothing to report
                        elif not is_watch_response_dismissive(response_text):
                            # Fallback for models that don't follow NoComment instruction
                            self.watch_alerts_shown += 1
                            self.console.print()
                            self.console.print(Panel(
                                Markdown(response_text),
                                title="[bold yellow]Watch Mode Alert[/]",
                                border_style="yellow"
                            ))
                            self.console.print()

                except Exception as e:
                    ConsoleHelper.error(self.console, f"Watch mode error: {e}")

                # Wait for content change signal or timeout
                if use_signals:
                    # Use asyncio.to_thread to call blocking get_change from async context
                    # This wakes immediately on content change or waits full interval
                    await asyncio.to_thread(
                        self.content_change_receiver.get_change,
                        timeout=self.watch_interval
                    )
                else:
                    await asyncio.sleep(self.watch_interval)
        finally:
            # Cleanup: unsubscribe from all terminals
            if use_signals and subscribed_uuids:
                for uuid in subscribed_uuids:
                    self.unsubscribe_content_changes(uuid)
                self._debug("Watch mode: unsubscribed from all terminals")
