"""
HTTP action for making web requests.

Uses httpx for async HTTP with proper timeout and retry handling.
"""

from typing import Any, ClassVar, Optional, TYPE_CHECKING

from .base import AbstractAction, ActionResult

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class HTTPAction(AbstractAction):
    """Make HTTP requests.

    Supports all common HTTP methods with configurable headers,
    body, and timeout. Handles JSON automatically.

    Usage:
        - uses: http/request
          with:
            url: "https://api.example.com/data"
            method: GET
            headers:
              Authorization: "Bearer ${{ env.API_TOKEN }}"
            timeout: 30
    """

    action_type: ClassVar[str] = "http/request"

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Execute the HTTP request.

        Args:
            step_config: Step configuration with HTTP options
            context: Workflow context
            exec_context: Execution context for logging

        Returns:
            ActionResult with response data
        """
        try:
            import httpx
        except ImportError:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="httpx not installed. Run: pip install httpx",
            )

        from ..evaluator import ContextEvaluator

        with_config = self._get_with_config(step_config)
        evaluator = ContextEvaluator(context)

        # Required: URL
        url = with_config.get("url")
        if not url:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="'url' is required for http/request",
            )

        # Resolve URL expressions
        url = evaluator.resolve(url)

        # Get HTTP options
        method = with_config.get("method", "GET").upper()
        timeout = with_config.get("timeout", 30)
        follow_redirects = with_config.get("follow_redirects", True)
        verify_ssl = with_config.get("verify_ssl", True)

        # Build headers
        headers: dict[str, str] = {}
        if "headers" in with_config:
            headers = evaluator.resolve_all(with_config["headers"])

        # Handle secret headers (read from secrets dir)
        if "secret_headers" in with_config:
            secret_headers = await self._load_secret_headers(
                with_config["secret_headers"],
                context,
            )
            headers.update(secret_headers)

        # Build request body
        body = None
        json_body = None

        if "json" in with_config:
            json_body = evaluator.resolve_all(with_config["json"])
        elif "body" in with_config:
            body = evaluator.resolve(with_config["body"])

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=follow_redirects,
                verify=verify_ssl,
            ) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                    json=json_body,
                )

                # Build output
                outputs: dict[str, Any] = {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "url": str(response.url),
                }

                # Try to parse JSON response
                try:
                    outputs["json"] = response.json()
                except Exception:
                    outputs["text"] = response.text

                # Determine outcome based on status code
                success = 200 <= response.status_code < 400

                if not success:
                    # Determine retryable vs non-retryable HTTP errors
                    if response.status_code in (429, 502, 503, 504):
                        error_type = "HTTPRetryableError"
                    elif response.status_code >= 500:
                        error_type = "HTTPServerError"
                    else:
                        error_type = "HTTPClientError"

                    return ActionResult(
                        outputs=outputs,
                        outcome="failure",
                        error=f"HTTP {response.status_code}",
                        error_type=error_type,
                    )

                return ActionResult(
                    outputs=outputs,
                    outcome="success",
                )

        except httpx.TimeoutException as e:
            return ActionResult(
                outputs={"error_type": "timeout"},
                outcome="failure",
                error=f"Request timed out after {timeout}s: {e}",
                error_type="TimeoutError",
            )
        except httpx.RequestError as e:
            return ActionResult(
                outputs={"error_type": "request_error"},
                outcome="failure",
                error=f"Request failed: {e}",
                error_type="RequestError",
            )
        except Exception as e:
            return ActionResult(
                outputs={},
                outcome="failure",
                error=str(e),
                error_type=type(e).__name__,
            )

    async def _load_secret_headers(
        self,
        secret_map: dict[str, str],
        context: dict[str, Any],
    ) -> dict[str, str]:
        """Load header values from secrets directory.

        Args:
            secret_map: Map of header name to secret filename
            context: Workflow context (for secrets_dir)

        Returns:
            Dict of header name to secret value
        """
        from pathlib import Path

        secrets_dir = context.get("workflow", {}).get("secrets_dir")
        if not secrets_dir:
            return {}

        secrets_path = Path(secrets_dir)
        headers = {}

        for header_name, secret_file in secret_map.items():
            secret_path = secrets_path / secret_file
            if secret_path.exists():
                headers[header_name] = secret_path.read_text().strip()

        return headers
