"""llm-tools-core - Core utilities for llm-assistant and llm-inlineassistant.

This package provides shared utilities with zero external dependencies:
- PromptDetector: Shell prompt detection (regex and Unicode markers)
- hash_blocks, filter_new_blocks: Block-level content hashing
- ConsoleHelper: Rich console output formatting
- get_config_dir, get_temp_dir, get_logs_db_path: XDG directory helpers
"""

from .prompt_detection import PromptDetector
from .hashing import hash_blocks, filter_new_blocks
from .console import ConsoleHelper
from .xdg import get_config_dir, get_temp_dir, get_logs_db_path

__all__ = [
    # Prompt detection
    "PromptDetector",
    # Hashing
    "hash_blocks",
    "filter_new_blocks",
    # Console output
    "ConsoleHelper",
    # XDG directories
    "get_config_dir",
    "get_temp_dir",
    "get_logs_db_path",
]

__version__ = "1.0.0"
