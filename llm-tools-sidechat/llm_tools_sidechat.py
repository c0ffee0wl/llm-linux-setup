"""
LLM tools for terminal control in sidechat.

These tools provide structured interfaces for terminal operations. They are
"stub" tools - they return structured JSON to indicate intent, but the actual
execution is handled by the sidechat application which processes tool_calls.

This approach provides schema validation at the model level while keeping
the actual terminal control logic in sidechat.
"""
import json
from typing import Optional

import llm


def execute_in_terminal(command: str) -> str:
    """
    Execute a shell command in the Exec terminal.

    Use this tool to run commands in the designated execution terminal.
    The command will be sent to the terminal and its output captured.

    State what you're doing in your response, but do NOT ask the user for
    permission - simply call this tool. The sidechat framework handles approval
    automatically.

    Args:
        command: The shell command to execute (e.g., "ls -la", "git status")

    Returns:
        JSON indicating the command has been queued for execution.
        Actual execution is handled by sidechat with user approval (you don't need to ask).
    """
    return json.dumps({
        "action": "execute",
        "command": command,
        "status": "queued"
    })


def send_keypress(keypress: str) -> str:
    """
    Send a keypress or key sequence to the Exec terminal.

    Use this for interactive applications (TUIs) that need keyboard input,
    such as vim, less, htop, or any application expecting keypresses.

    Supported special keys:
    - Enter, Escape, Tab, Space, Backspace, Delete
    - Ctrl+<key> (e.g., "Ctrl+C", "Ctrl+D", "Ctrl+Z")
    - Alt+<key> (e.g., "Alt+F", "Alt+B")
    - Arrow keys: Up, Down, Left, Right
    - Function keys: F1-F12
    - Page keys: PageUp, PageDown, Home, End

    For regular text input, just use the characters directly (e.g., ":wq" for vim).

    Always explain your reasoning before using this tool.

    Args:
        keypress: The key or key sequence to send (e.g., "Enter", "Ctrl+C", ":wq", "q")

    Returns:
        JSON indicating the keypress has been queued.
        Actual execution is handled by sidechat with user approval.
    """
    return json.dumps({
        "action": "keypress",
        "key": keypress,
        "status": "queued"
    })


def capture_terminal(scope: str = "exec") -> str:
    """
    Capture terminal content or screenshot.

    Use this to see the current state of the terminal(s). For TUI applications
    (vim, htop, etc.), this captures a screenshot. For regular command output,
    it captures the text content.

    Args:
        scope: Which terminals to capture:
               - "exec": Only the Exec terminal (default)
               - "all": All visible terminals

    Returns:
        JSON indicating capture has been queued.
        The captured content will be provided in the next context.
    """
    valid_scopes = ["exec", "all"]
    if scope not in valid_scopes:
        scope = "exec"

    return json.dumps({
        "action": "capture",
        "scope": scope,
        "status": "queued"
    })


def refresh_context() -> str:
    """
    Refresh the terminal context before continuing.

    Use this when you need updated terminal content before deciding
    what to do next. This is useful when:
    - You're waiting for a long-running command to complete
    - You want to see the current state without executing anything
    - The terminal content may have changed since your last observation

    Returns:
        JSON indicating refresh has been queued.
        Updated context will be provided in the next message.
    """
    return json.dumps({
        "action": "refresh",
        "status": "queued"
    })


def view_attachment(path_or_url: str) -> str:
    """
    Queue an image, PDF, audio, or video file for viewing in the next turn.

    Use this tool to view media files. The attachment will be visible to you
    in your NEXT conversation turn (not immediately).

    Supported types depend on the current model:
    - Images: All vision models (PNG, JPEG, WebP, GIF, HEIC)
    - PDF: Gemini, Claude 3.5+, GPT-4o (native viewing)
    - Audio: Gemini only (WAV, MP3, AAC, OGG, FLAC)
    - Video: Gemini only (MP4, WebM, MOV, AVI)

    If the model doesn't support a type, you'll receive an error suggesting
    alternatives (e.g., use load_pdf for text extraction instead).

    Args:
        path_or_url: Local file path or URL to the attachment

    Returns:
        JSON indicating the attachment has been queued.
        You will see the content in your next turn.
    """
    return json.dumps({
        "action": "view_attachment",
        "path_or_url": path_or_url,
        "status": "queued"
    })


def view_pdf(path_or_url: str) -> str:
    """
    View a PDF document using the model's native multimodal capability.

    Requires a PDF-capable model (Gemini, Claude 3.5+, GPT-4o). The model
    processes the PDF visually, seeing layout, images, and formatting.

    The PDF will be visible to you in your NEXT conversation turn (not immediately).

    For text extraction instead (works with any model, more token-efficient for
    text-heavy documents), use load_pdf().

    Args:
        path_or_url: Local file path or URL to the PDF document.
                     URLs are passed directly to the model API.

    Returns:
        JSON indicating the PDF has been queued for native viewing.
        Returns error if model doesn't support native PDF viewing.
    """
    return json.dumps({
        "action": "view_pdf",
        "path_or_url": path_or_url,
        "status": "queued"
    })


def view_youtube_native(url: str) -> str:
    """
    View a YouTube video with native Gemini visual+audio analysis.

    Requires Gemini model. For transcript-only (faster, cheaper, works with
    any model), use load_yt() instead.

    Use this when:
    - Visual content matters (demos, charts, screen recordings)
    - Audio nuances are important (tone, music, sound effects)
    - You need to see what's displayed, not just hear what's said

    Note: Native video processing is slower (~60s for 30min video) and more
    expensive (~$0.15) than transcript extraction (~5s, ~$0.005).

    Args:
        url: YouTube video URL (e.g., https://youtube.com/watch?v=xxx or https://youtu.be/xxx)

    Returns:
        JSON indicating video queued for native processing, or error if
        model doesn't support native YouTube.
    """
    return json.dumps({
        "action": "view_youtube_native",
        "url": url,
        "status": "queued"
    })


@llm.hookimpl
def register_tools(register):
    """Register sidechat terminal control tools."""
    register(execute_in_terminal)
    register(send_keypress)
    register(capture_terminal)
    register(refresh_context)
    # Multi-modal viewing tools
    register(view_attachment)
    register(view_pdf)
    register(view_youtube_native)
