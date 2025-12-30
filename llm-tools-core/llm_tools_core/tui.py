"""TUI command detection utilities.

Provides detection of Terminal User Interface (TUI) applications
that require screenshot capture instead of text capture.

Used by:
- llm-assistant (determines capture mode for command output)
- Any tool that needs to distinguish TUI vs text output
"""

import os
from typing import Set

# TUI commands that require screenshot capture instead of text capture
# These programs use full-screen curses/TUI interfaces that don't preserve
# well as plain text.
TUI_COMMANDS: Set[str] = {
    # System monitors
    'htop', 'top', 'btop', 'gtop', 'glances', 'atop', 'nmon',
    'iotop', 'iftop', 'nethogs', 'bmon', 'vnstat', 'procs',
    # Editors
    'vim', 'vi', 'nvim', 'nano', 'emacs', 'helix', 'micro',
    'joe', 'pico', 'jed', 'ne', 'mg', 'kakoune', 'kak',
    # Pagers
    'less', 'more', 'most', 'bat',
    # File managers
    'mc', 'ranger', 'nnn', 'lf', 'vifm', 'fff', 'broot',
    'ncdu', 'duf', 'dust',
    # Terminal multiplexers
    'tmux', 'screen', 'byobu', 'zellij',
    # Git TUIs
    'tig', 'lazygit', 'gitui',
    # Container/K8s TUIs
    'k9s', 'lazydocker', 'dive', 'ctop',
    # Fuzzy finders (when run standalone)
    'fzf', 'sk', 'peco',
    # Periodic execution
    'watch',
    # Audio
    'alsamixer', 'pulsemixer',
    # Email/IRC
    'mutt', 'neomutt', 'aerc',
    'weechat', 'irssi',
    # Music players
    'cmus', 'ncmpcpp', 'moc', 'mocp',
    # Web browsers
    'lynx', 'w3m', 'links', 'elinks',
    # Task management
    'taskwarrior-tui', 'taskell',
    # Calendar
    'calcurse', 'khal',
}


def is_tui_command(command: str) -> bool:
    """Detect if a command will launch a TUI application.

    Handles piped commands by checking the rightmost command,
    since that's what actually displays in the terminal.

    Args:
        command: Full command string (e.g., "htop -d 5" or "git log | less")

    Returns:
        True if command is a known TUI application

    Examples:
        >>> is_tui_command("htop")
        True
        >>> is_tui_command("htop -d 5")
        True
        >>> is_tui_command("git log | less")
        True
        >>> is_tui_command("ls -la")
        False
        >>> is_tui_command("cat file | head")
        False
    """
    if not command.strip():
        return False

    # For piped commands, check the rightmost command (that's what displays)
    # e.g., "cat file | less" -> check "less"
    # e.g., "git log | head" -> check "head" (not TUI)
    if '|' in command:
        parts = command.split('|')
        command = parts[-1].strip()

    # Extract the base command (first word)
    base_cmd = command.split()[0] if command.split() else ""

    # Remove path if present (e.g., /usr/bin/htop -> htop)
    # Use lowercase for case-insensitive matching
    base_cmd = os.path.basename(base_cmd).lower()

    return base_cmd in TUI_COMMANDS
