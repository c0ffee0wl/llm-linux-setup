"""
Notification actions for workflow completion alerts.

This module provides actions for sending desktop notifications and webhook calls.
"""

import asyncio
import shutil
import sys
from typing import Any, Optional, TYPE_CHECKING

from .base import BaseAction
from ..core.types import ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class NotifyDesktopAction(BaseAction):
    """
    Send desktop notification.

    YAML usage:
        - uses: notify/desktop
          with:
            title: "Scan Complete"
            message: "Found ${{ count }} vulnerabilities"
            urgency: normal  # low | normal | critical
            icon: security   # optional, xdg icon name

    Backend detection order:
        1. notify-send (Linux/freedesktop - XFCE, GNOME, KDE)
        2. terminal-notifier (macOS)
        3. Silent no-op with warning if unavailable

    Parameters:
        - title: Notification title (required)
        - message: Notification body (required)
        - urgency: low, normal, or critical (default: normal)
        - icon: XDG icon name (optional, Linux only)

    Outputs:
        - sent: True if notification was delivered
        - backend: Which notification system was used
    """

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Send desktop notification."""
        with_config = step_config.get("with", {})

        title = with_config.get("title", "")
        message = with_config.get("message", "")
        urgency = with_config.get("urgency", "normal")
        icon = with_config.get("icon")

        if not title:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'title' provided for notify/desktop",
                error_type="ValidationError",
            )

        if not message:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'message' provided for notify/desktop",
                error_type="ValidationError",
            )

        # Validate urgency
        valid_urgencies = {"low", "normal", "critical"}
        if urgency not in valid_urgencies:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid urgency '{urgency}'. Must be one of: {valid_urgencies}",
                error_type="ValidationError",
            )

        # Detect notification backend
        notify_send = shutil.which("notify-send")
        terminal_notifier = shutil.which("terminal-notifier")

        if notify_send:
            # Linux/freedesktop (notify-send)
            args = [notify_send, "-u", urgency, title, message]
            if icon:
                args.insert(1, "-i")
                args.insert(2, icon)
            backend = "notify-send"

        elif terminal_notifier:
            # macOS (terminal-notifier)
            args = [terminal_notifier, "-title", title, "-message", message]
            backend = "terminal-notifier"

        else:
            # No notification system available
            if exec_context:
                exec_context.log("warning", "No desktop notification backend available")
            return ActionResult(
                outcome="success",
                outputs={"sent": False, "backend": None}
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

            if proc.returncode == 0:
                if exec_context:
                    exec_context.log("debug", f"Sent notification via {backend}")
                return ActionResult(
                    outcome="success",
                    outputs={"sent": True, "backend": backend}
                )
            else:
                return ActionResult(
                    outcome="failure",
                    outputs={"sent": False, "backend": backend},
                    error=f"Notification failed: {stderr.decode('utf-8', errors='replace')}",
                    error_type="NotificationError",
                )

        except asyncio.TimeoutError:
            return ActionResult(
                outcome="failure",
                outputs={"sent": False, "backend": backend},
                error="Notification command timed out",
                error_type="TimeoutError",
            )
        except Exception as e:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=str(e),
                error_type=type(e).__name__,
            )


class NotifyWebhookAction(BaseAction):
    """
    Send HTTP webhook notification.

    YAML usage:
        - uses: notify/webhook
          with:
            url: ${{ env.SLACK_WEBHOOK_URL }}
            method: POST
            body:
              text: "Workflow complete"
            headers:
              Authorization: "Bearer ${{ env.TOKEN }}"
            timeout: 30

    Parameters:
        - url: Webhook URL (required)
        - method: HTTP method (default: POST)
        - body: Request body (dict for JSON, string for raw)
        - headers: HTTP headers (optional)
        - timeout: Request timeout in seconds (default: 30)

    Outputs:
        - status_code: HTTP response status code
        - success: True if 2xx response
        - response: Response body (truncated)
    """

    reads = ["inputs", "env", "steps"]
    writes = ["steps"]

    async def execute(
        self,
        step_config: dict,
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Send webhook notification."""
        with_config = step_config.get("with", {})

        url = with_config.get("url", "")
        method = with_config.get("method", "POST").upper()
        body = with_config.get("body")
        headers = with_config.get("headers", {})
        timeout = with_config.get("timeout", 30)

        if not url:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="No 'url' provided for notify/webhook",
                error_type="ValidationError",
            )

        # Validate method
        valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        if method not in valid_methods:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=f"Invalid method '{method}'. Must be one of: {valid_methods}",
                error_type="ValidationError",
            )

        try:
            import httpx
        except ImportError:
            return ActionResult(
                outcome="failure",
                outputs={},
                error="httpx is required for notify/webhook (pip install httpx)",
                error_type="ImportError",
            )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Determine content type
                if isinstance(body, dict):
                    response = await client.request(
                        method,
                        url,
                        json=body,
                        headers=headers,
                    )
                elif body is not None:
                    response = await client.request(
                        method,
                        url,
                        content=str(body),
                        headers=headers,
                    )
                else:
                    response = await client.request(
                        method,
                        url,
                        headers=headers,
                    )

                success = 200 <= response.status_code < 300
                response_text = response.text[:1000] if response.text else ""

                if exec_context:
                    exec_context.log(
                        "debug" if success else "warning",
                        f"Webhook {method} {url} returned {response.status_code}"
                    )

                return ActionResult(
                    outcome="success" if success else "failure",
                    outputs={
                        "status_code": response.status_code,
                        "success": success,
                        "response": response_text,
                    },
                    error=None if success else f"Webhook returned {response.status_code}",
                    error_type=None if success else "WebhookError",
                )

        except httpx.TimeoutException:
            return ActionResult(
                outcome="failure",
                outputs={"status_code": None, "success": False},
                error=f"Webhook request timed out after {timeout}s",
                error_type="TimeoutError",
            )
        except httpx.RequestError as e:
            return ActionResult(
                outcome="failure",
                outputs={"status_code": None, "success": False},
                error=f"Webhook request failed: {e}",
                error_type="RequestError",
            )
        except Exception as e:
            return ActionResult(
                outcome="failure",
                outputs={},
                error=str(e),
                error_type=type(e).__name__,
            )
