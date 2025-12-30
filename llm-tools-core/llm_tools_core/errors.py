"""Error codes and exceptions for llm-assistant daemon protocol.

Provides shared error codes used in NDJSON responses:
- llm-assistant daemon (error responses)
- llm-inlineassistant (error handling)
- espanso-llm (error handling)
"""


class ErrorCode:
    """Standard error codes for daemon protocol.

    These codes are used in NDJSON error responses:
    {"type": "error", "code": "MODEL_ERROR", "message": "..."}
    """

    # Client errors
    EMPTY_QUERY = "EMPTY_QUERY"      # Query text is empty
    PARSE_ERROR = "PARSE_ERROR"      # Invalid JSON request
    INVALID_MODE = "INVALID_MODE"    # Unknown mode specified

    # Server errors
    MODEL_ERROR = "MODEL_ERROR"      # LLM API error
    TOOL_ERROR = "TOOL_ERROR"        # Tool execution failed
    INTERNAL = "INTERNAL"            # Unexpected server error

    # Communication errors
    TIMEOUT = "TIMEOUT"              # Request timed out
    SOCKET_ERROR = "SOCKET_ERROR"    # Socket communication error
    DAEMON_UNAVAILABLE = "DAEMON_UNAVAILABLE"  # Daemon not running


class DaemonError(Exception):
    """Base exception for daemon communication errors."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class EmptyQueryError(DaemonError):
    """Raised when query text is empty."""

    def __init__(self, message: str = "Query text is empty"):
        super().__init__(ErrorCode.EMPTY_QUERY, message)


class ModelError(DaemonError):
    """Raised when LLM API call fails."""

    def __init__(self, message: str):
        super().__init__(ErrorCode.MODEL_ERROR, message)


class ToolError(DaemonError):
    """Raised when tool execution fails."""

    def __init__(self, message: str):
        super().__init__(ErrorCode.TOOL_ERROR, message)


class TimeoutError(DaemonError):
    """Raised when request times out."""

    def __init__(self, message: str = "Request timed out"):
        super().__init__(ErrorCode.TIMEOUT, message)


class DaemonUnavailableError(DaemonError):
    """Raised when daemon is not running."""

    def __init__(self, message: str = "Daemon is not running"):
        super().__init__(ErrorCode.DAEMON_UNAVAILABLE, message)


def format_error_response(code: str, message: str) -> dict:
    """Format an error as an NDJSON response dict.

    Args:
        code: Error code from ErrorCode class
        message: Human-readable error message

    Returns:
        Dict suitable for NDJSON serialization

    Examples:
        >>> format_error_response(ErrorCode.TIMEOUT, "Request timed out")
        {'type': 'error', 'code': 'TIMEOUT', 'message': 'Request timed out'}
    """
    return {
        "type": "error",
        "code": code,
        "message": message,
    }
