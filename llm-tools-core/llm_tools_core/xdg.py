"""XDG Base Directory Specification helpers.

This module provides XDG-compliant directory management used by:
- llm-assistant (config, logs, temp files)
- llm-inlineassistant (config, logs, temp files)

All functions take an app_name parameter to support multiple applications
using the same utility functions with different directory names.
"""
import os
from pathlib import Path


def get_config_dir(app_name: str) -> Path:
    """Get application config directory using XDG spec.

    Args:
        app_name: Application name (e.g., "llm-assistant")

    Returns:
        Path to XDG_CONFIG_HOME/app_name or ~/.config/app_name
    """
    xdg_config = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config:
        base = Path(xdg_config)
    else:
        base = Path.home() / '.config'
    return base / app_name


def get_temp_dir(app_name: str) -> Path:
    """Get application temp directory with user isolation.

    Args:
        app_name: Application name (e.g., "llm-assistant")

    Returns:
        Path to TMPDIR/app_name/{uid} or /tmp/app_name/{uid}
    """
    tmpdir = os.environ.get('TMPDIR') or os.environ.get('TMP') or os.environ.get('TEMP')
    if tmpdir:
        base = Path(tmpdir)
    else:
        base = Path('/tmp')
    return base / app_name / str(os.getuid())


def get_logs_db_path(app_name: str) -> Path:
    """Get path to application's conversation database.

    Args:
        app_name: Application name (e.g., "llm-assistant")

    Returns:
        Path to XDG_CONFIG_HOME/app_name/logs.db

    This is separate from llm CLI's logs.db to keep assistant conversations
    isolated from regular llm usage.
    """
    return get_config_dir(app_name) / 'logs.db'
