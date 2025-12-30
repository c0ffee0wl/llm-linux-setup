"""System detection utilities for LLM command-line tools.

Provides cross-platform detection of:
- Shell type and version (bash, zsh, fish, PowerShell, cmd)
- Operating system with Linux distribution name
- Hybrid environments (WSL, Git Bash, Cygwin)
- Available package managers

Used by:
- llm-assistant (system prompt context)
- llm-inlineassistant (system prompt context)
- Any tool that needs system-aware prompts

Functions:
    detect_shell() - Returns (shell_name, version) tuple
    detect_os() - Returns OS string (e.g., "Linux (Ubuntu)", "macOS", "Windows")
    detect_environment() - Returns environment type ("native", "wsl", "gitbash", "cygwin")
    detect_package_managers() - Returns list of available package managers
    get_system_context() - Returns dict with all detection results
"""

import os
import platform
import shutil
from typing import Dict, List, Tuple


def detect_shell() -> Tuple[str, str]:
    """Detect current shell cross-platform (using env vars only).

    Returns:
        Tuple of (shell_name, version). Version may be empty string if unknown.

    Examples:
        >>> detect_shell()  # On Linux with bash
        ('bash', '')
        >>> detect_shell()  # On Windows with PowerShell 7
        ('pwsh', '7')
    """
    system = platform.system()

    # Check for PowerShell first (cross-platform)
    if os.getenv("PSModulePath"):
        if system == "Windows":
            # On Windows, distinguish PowerShell 5.1 vs 7+
            # PSModulePath contains "WindowsPowerShell" in PS5, but just "PowerShell" in PS7
            ps_module_path = os.getenv("PSModulePath", "")
            if "WindowsPowerShell" in ps_module_path:
                return "powershell", "5"  # Windows PowerShell 5.1

            # Secondary check: PS7 adds "PowerShell\7" to PATH
            path = os.getenv("Path", "")
            if "PowerShell\\7" in path or "PowerShell/7" in path:
                return "pwsh", "7"

            # Tertiary fallback: check which executable is available
            if shutil.which("pwsh"):
                return "pwsh", "7"

            # If PSModulePath exists but no WindowsPowerShell, assume PS7
            return "pwsh", "7"
        else:
            # On Linux/macOS, PowerShell is always pwsh 7+
            # (PowerShell 5.1 is Windows-only)
            return "pwsh", "7"

    # Windows-specific shells
    if system == "Windows":
        # Check for Git Bash/MSYS/Cygwin on Windows
        shell = os.getenv("SHELL")
        if shell:
            shell_name = os.path.basename(shell)
            return shell_name, ""

        # Fall back to cmd.exe
        return "cmd", ""

    # Unix-like systems: $SHELL is reliable
    shell_name = os.path.basename(os.getenv("SHELL") or "sh")
    return shell_name, ""


def detect_os() -> str:
    """Detect OS - simplified version info.

    Returns:
        OS string like "Linux (Ubuntu)", "macOS", or "Windows".

    Examples:
        >>> detect_os()  # On Ubuntu
        'Linux (Ubuntu)'
        >>> detect_os()  # On macOS
        'macOS'
    """
    os_type = platform.system()

    if os_type == "Linux":
        # Just get distro name, skip version/kernel details
        try:
            with open('/etc/os-release') as f:
                for line in f:
                    if line.startswith('NAME='):
                        distro = line.split('=')[1].strip().strip('"')
                        return f"Linux ({distro})"
        except Exception:
            pass
        return "Linux"

    elif os_type == "Darwin":
        # Just "macOS" - version rarely matters for commands
        return "macOS"

    elif os_type == "Windows":
        # Simple: just Windows (no build numbers)
        return "Windows"

    else:
        return os_type


def detect_environment() -> str:
    """Detect hybrid environments (WSL, Git Bash, etc.).

    Returns:
        One of: "native", "wsl", "gitbash", "cygwin"

    Examples:
        >>> detect_environment()  # On native Linux
        'native'
        >>> detect_environment()  # On WSL
        'wsl'
    """
    os_name = platform.system()

    # WSL detection
    if os_name == "Linux":
        if os.getenv("WSL_DISTRO_NAME"):
            return "wsl"
        try:
            with open('/proc/version', 'r') as f:
                if 'microsoft' in f.read().lower():
                    return "wsl"
        except Exception:
            pass

    # Git Bash / MSYS
    if os.getenv("MSYSTEM"):
        return "gitbash"

    # Cygwin
    if os.getenv("CYGWIN"):
        return "cygwin"

    return "native"


def detect_package_managers() -> List[str]:
    """Detect available package managers.

    Checks for common package managers across different systems:
    - Language: uv, pipx, pip, npm, cargo, gem
    - Linux: apt, dnf, yum, pacman, zypper, apk, snap, flatpak
    - macOS: brew, port
    - Windows: choco, scoop, winget
    - Alternative: nix, guix

    Returns:
        List of available package manager names.

    Examples:
        >>> detect_package_managers()  # On Ubuntu with Python tools
        ['uv', 'pipx', 'pip', 'npm', 'apt', 'snap']
    """
    managers = []

    # Check common package managers
    for pm in ['uv', 'pipx', 'pip', 'npm', 'cargo', 'gem',  # Language
               'apt', 'dnf', 'yum', 'pacman', 'zypper', 'apk',  # Linux
               'snap', 'flatpak',  # Universal Linux
               'brew', 'port',  # macOS
               'choco', 'scoop', 'winget',  # Windows
               'nix', 'guix']:  # Alternative
        if shutil.which(pm):
            managers.append(pm)

    return managers


def get_system_context() -> Dict[str, any]:
    """Get all system detection results as a dictionary.

    Combines all detection functions into a single dict for easy
    inclusion in system prompts.

    Returns:
        Dict with keys: shell, shell_version, os, environment, package_managers

    Examples:
        >>> ctx = get_system_context()
        >>> ctx['os']
        'Linux (Ubuntu)'
        >>> ctx['shell']
        'bash'
    """
    shell_name, shell_version = detect_shell()
    return {
        'shell': shell_name,
        'shell_version': shell_version,
        'os': detect_os(),
        'environment': detect_environment(),
        'package_managers': detect_package_managers(),
    }
