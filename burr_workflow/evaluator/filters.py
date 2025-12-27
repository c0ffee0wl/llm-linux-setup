"""
Custom Jinja2 filters for workflow expressions.

All filters here are considered safe for use in workflow expressions.
"""

import re
import shlex
from typing import Any, Callable


def shell_quote(value: Any) -> str:
    """Quote a value for safe shell interpolation.

    CRITICAL: Use this for ANY user input in shell commands.

    Example:
        run: echo ${{ inputs.message | shell_quote }}
    """
    return shlex.quote(str(value))


def safe_filename(value: str) -> str:
    """Sanitize a string for use as a filename.

    Removes or replaces characters that are unsafe in filenames
    across common filesystems (POSIX, Windows, etc.).

    Args:
        value: Input string

    Returns:
        Safe filename string
    """
    # Remove null bytes and control characters
    value = re.sub(r'[\x00-\x1f\x7f]', '', str(value))

    # Replace path separators and other unsafe chars
    unsafe_chars = r'[<>:"/\\|?*\x00-\x1f]'
    value = re.sub(unsafe_chars, '_', value)

    # Remove leading/trailing dots and spaces (Windows issue)
    value = value.strip('. ')

    # Limit length
    if len(value) > 255:
        value = value[:255]

    # Fallback for empty result
    if not value:
        value = 'unnamed'

    return value


def regex_replace(value: str, pattern: str, replacement: str) -> str:
    """Replace regex matches in a string.

    Args:
        value: Input string
        pattern: Regex pattern
        replacement: Replacement string

    Returns:
        String with replacements made
    """
    return re.sub(pattern, replacement, str(value))


def regex_match(value: str, pattern: str) -> bool:
    """Check if value matches a regex pattern.

    Args:
        value: Input string
        pattern: Regex pattern

    Returns:
        True if pattern matches
    """
    return bool(re.search(pattern, str(value)))


def json_encode(value: Any) -> str:
    """Encode a value as JSON string.

    Args:
        value: Value to encode

    Returns:
        JSON string
    """
    import json
    return json.dumps(value)


def json_decode(value: str) -> Any:
    """Decode a JSON string.

    Args:
        value: JSON string

    Returns:
        Decoded value
    """
    import json
    return json.loads(str(value))


def base64_encode(value: str) -> str:
    """Base64 encode a string.

    Args:
        value: Input string

    Returns:
        Base64 encoded string
    """
    import base64
    return base64.b64encode(str(value).encode()).decode()


def base64_decode(value: str) -> str:
    """Base64 decode a string.

    Args:
        value: Base64 encoded string

    Returns:
        Decoded string
    """
    import base64
    return base64.b64decode(str(value)).decode()


def url_encode(value: str) -> str:
    """URL encode a string.

    Args:
        value: Input string

    Returns:
        URL encoded string
    """
    from urllib.parse import quote
    return quote(str(value))


def url_decode(value: str) -> str:
    """URL decode a string.

    Args:
        value: URL encoded string

    Returns:
        Decoded string
    """
    from urllib.parse import unquote
    return unquote(str(value))


def truncate(value: str, length: int = 80, suffix: str = "...") -> str:
    """Truncate a string to a maximum length.

    Args:
        value: Input string
        length: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated string
    """
    value = str(value)
    if len(value) <= length:
        return value
    return value[:length - len(suffix)] + suffix


def lines(value: str) -> list[str]:
    """Split a string into lines.

    Args:
        value: Input string

    Returns:
        List of lines
    """
    return str(value).splitlines()


def indent(value: str, width: int = 4, first: bool = False) -> str:
    """Indent all lines of a string.

    Args:
        value: Input string
        width: Number of spaces to indent
        first: Whether to indent the first line

    Returns:
        Indented string
    """
    lines_list = str(value).splitlines()
    prefix = ' ' * width

    if not first and lines_list:
        return lines_list[0] + '\n' + '\n'.join(
            prefix + line for line in lines_list[1:]
        )
    return '\n'.join(prefix + line for line in lines_list)


def format_bytes(value: int) -> str:
    """Format a byte count as human-readable size.

    Args:
        value: Number of bytes

    Returns:
        Human-readable size string (e.g., "1.5 MB")
    """
    value = int(value)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def extract_domain(url: str) -> str:
    """Extract domain from a URL.

    Args:
        url: URL string

    Returns:
        Domain name
    """
    from urllib.parse import urlparse
    parsed = urlparse(str(url))
    return parsed.netloc or parsed.path.split('/')[0]


def extract_ip(value: str) -> list[str]:
    """Extract IPv4 addresses from text.

    Args:
        value: Text containing IP addresses

    Returns:
        List of found IP addresses
    """
    pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
    return re.findall(pattern, str(value))


def is_valid_ip(value: str) -> bool:
    """Validate IPv4/IPv6 address format.

    Args:
        value: IP address string

    Returns:
        True if valid IP address format
    """
    import ipaddress
    try:
        ipaddress.ip_address(str(value))
        return True
    except ValueError:
        return False


def is_private_ip(value: str) -> bool:
    """Check if IP is RFC1918/RFC4193 private address.

    Args:
        value: IP address string

    Returns:
        True if private IP address
    """
    import ipaddress
    try:
        return ipaddress.ip_address(str(value)).is_private
    except ValueError:
        return False


def in_cidr(value: str, cidr: str) -> bool:
    """Check if IP is within CIDR range.

    Args:
        value: IP address string
        cidr: CIDR notation (e.g., "10.0.0.0/8")

    Returns:
        True if IP is in the CIDR range
    """
    import ipaddress
    try:
        return ipaddress.ip_address(str(value)) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def file_exists(value: str) -> bool:
    """Check if file exists at path.

    Args:
        value: File path

    Returns:
        True if file exists
    """
    import os
    return os.path.isfile(os.path.expanduser(str(value)))


def in_list(value: Any, items: list) -> bool:
    """Check if value is in list.

    Useful with lines filter: ${{ target | in_list(scope | lines) }}

    Args:
        value: Value to check
        items: List to search in

    Returns:
        True if value is in list
    """
    return str(value) in [str(i).strip() for i in items]


# Dictionary of all safe filters
SAFE_FILTERS: dict[str, Callable[..., Any]] = {
    # String manipulation
    "shell_quote": shell_quote,
    "safe_filename": safe_filename,
    "regex_replace": regex_replace,
    "regex_match": regex_match,
    "truncate": truncate,
    "lines": lines,
    "indent": indent,

    # Encoding/decoding
    "json_encode": json_encode,
    "json_decode": json_decode,
    "base64_encode": base64_encode,
    "base64_decode": base64_decode,
    "url_encode": url_encode,
    "url_decode": url_decode,

    # Data extraction
    "format_bytes": format_bytes,
    "extract_domain": extract_domain,
    "extract_ip": extract_ip,

    # Network validation
    "is_valid_ip": is_valid_ip,
    "is_private_ip": is_private_ip,
    "in_cidr": in_cidr,

    # Filesystem
    "file_exists": file_exists,

    # List operations
    "in_list": in_list,
}
