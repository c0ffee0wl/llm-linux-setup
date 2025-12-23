"""Watch mode mixin for llm-assistant.

This module provides background terminal monitoring:
- Watch mode thread management
- Async watch loop for periodic context capture
- Context hash computation for change detection
- Automatic AI prompting on terminal changes
"""

import asyncio
import hashlib
import threading
from typing import TYPE_CHECKING, List, Optional

import llm
from rich.markdown import Markdown
from rich.panel import Panel

from .prompt_detection import PromptDetector
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
    - plugin_dbus: D-Bus plugin service object
    - exec_terminal_uuid: str UUID of exec terminal
    - capture_context: method to capture terminal context
    - _prompt: method to prompt the model
    - _build_system_prompt: method to build system prompt
    - _log_response: method to log responses
    - _debug: method for debug output
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

    async def watch_loop(self):
        """
        Background monitoring of all terminals (like tmuxai watch mode).

        Implements intelligent change detection:
        1. Hash-based skip: Don't send unchanged context to AI
        2. History-aware prompt: Tell AI to focus on NEW content when changes detected
        """
        while self.watch_mode:
            try:
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
                    # Enable per-terminal deduplication after first watch iteration
                    context, tui_attachments = self.capture_context(
                        include_exec_output=True,
                        dedupe_unchanged=self.previous_watch_iteration_count > 0
                    )

                    # Check exec terminal idle state using PromptDetector
                    try:
                        exec_content = self.plugin_dbus.capture_terminal_content(
                            self.exec_terminal_uuid, -1
                        )
                        if exec_content:
                            is_idle = PromptDetector.detect_prompt_at_end(exec_content)
                            exec_status = "[Exec: idle]" if is_idle else "[Exec: command running]"
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
                            # Context changed - update hash and proceed
                            self.previous_watch_context_hash = current_hash
                            self.previous_watch_iteration_count += 1

                            # HISTORY-AWARE PROMPT: Tell AI to focus on new content
                            prompt = render('prompts/watch_prompt.j2',
                                iteration_count=self.previous_watch_iteration_count,
                                goal=self.watch_goal,
                                exec_status=exec_status,
                                context=context,
                            )

                            try:
                                # Include TUI screenshots if any were captured
                                # Always pass system prompt on every call (required for Gemini/Vertex
                                # which is stateless - systemInstruction must be sent on every request)
                                #
                                # IMPORTANT: stream=False minimizes lock hold time by getting the
                                # complete response in one call. Lock is necessary because the
                                # conversation object is not thread-safe and is shared with main thread.
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
                    if not is_watch_response_dismissive(response_text):
                        self.console.print()
                        self.console.print(Panel(
                            Markdown(response_text),
                            title="[bold yellow]Watch Mode Alert[/]",
                            border_style="yellow"
                        ))
                        self.console.print()

            except Exception as e:
                ConsoleHelper.error(self.console, f"Watch mode error: {e}")

            await asyncio.sleep(self.watch_interval)
