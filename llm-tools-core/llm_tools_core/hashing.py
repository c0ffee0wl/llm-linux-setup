"""Block-level hashing for context deduplication.

This module provides content hashing functions used by:
- llm-assistant (context compression, terminal content deduplication)
- llm-inlineassistant (asciinema context deduplication)
- llm-guiassistant (GUI context deduplication)

The hash functions enable incremental context - only sending new command
outputs instead of re-sending everything on each prompt.
"""
import hashlib
from typing import Dict, List, Set, Tuple


def hash_blocks(blocks: List[str]) -> Set[str]:
    """
    Compute SHA256 hashes for a list of content blocks.

    Used for deduplication - comparing previous capture vs current capture
    to avoid resending unchanged content.

    Args:
        blocks: List of content blocks (e.g., command + output blocks)

    Returns:
        Set of SHA256 hex digests for all blocks

    Example:
        >>> blocks = ["$ ls\\nfile1.txt\\nfile2.txt", "$ pwd\\n/home/user"]
        >>> hashes = hash_blocks(blocks)
        >>> len(hashes)
        2
    """
    return {
        hashlib.sha256(block.strip().encode()).hexdigest()
        for block in blocks
        if block.strip()
    }


def filter_new_blocks(
    blocks: List[str],
    prev_hashes: Set[str]
) -> Tuple[List[str], Set[str]]:
    """
    Filter blocks to only return those not seen in previous capture.

    This enables incremental context - only sending new command outputs
    instead of re-sending everything on each prompt.

    Args:
        blocks: List of current content blocks
        prev_hashes: Set of hashes from previous capture (or empty set)

    Returns:
        Tuple of (new_blocks, current_hashes):
        - new_blocks: Blocks that weren't in prev_hashes
        - current_hashes: All current block hashes (for next comparison)

    Example:
        >>> prev = hash_blocks(["$ ls\\nfile1.txt"])
        >>> current = ["$ ls\\nfile1.txt", "$ pwd\\n/home/user"]
        >>> new, hashes = filter_new_blocks(current, prev)
        >>> len(new)  # Only pwd block is new
        1
    """
    current_hashes = set()
    new_blocks = []

    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue

        block_hash = hashlib.sha256(stripped.encode()).hexdigest()
        current_hashes.add(block_hash)

        if block_hash not in prev_hashes:
            new_blocks.append(block)

    return new_blocks, current_hashes


def hash_window(win: Dict) -> str:
    """Hash a single window for GUI context deduplication.

    Creates a short hash based on window properties (app_class, title, pid).
    Used to detect when windows appear/disappear/change.

    Args:
        win: Window dict with app_class, window_title, pid keys

    Returns:
        16-character hex hash string

    Example:
        >>> win = {'app_class': 'firefox', 'window_title': 'GitHub', 'pid': 1234}
        >>> hash_window(win)
        'a1b2c3d4e5f67890'
    """
    key = f"{win.get('app_class')}|{win.get('window_title')}|{win.get('pid')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def hash_gui_context(ctx: Dict) -> Tuple[str, Set[str]]:
    """Hash full GUI context for deduplication.

    Generates hashes for the focused window and all visible windows,
    enabling detection of desktop state changes between queries.

    Args:
        ctx: Context dict from gather_context() with 'focused' and 'visible_windows' keys

    Returns:
        Tuple of (focused_hash, set_of_window_hashes)

    Example:
        >>> ctx = gather_context()
        >>> focused_hash, window_hashes = hash_gui_context(ctx)
        >>> focused_hash
        'a1b2c3d4e5f67890'
        >>> len(window_hashes)
        5
    """
    focused = ctx.get('focused', {})
    focused_hash = hash_window(focused) if focused else ""
    window_hashes = {hash_window(w) for w in ctx.get('visible_windows', [])}
    return focused_hash, window_hashes
