"""
System detection utilities for LLM command-line tools.

Provides cross-platform detection of:
- Shell type and version (bash, zsh, fish, PowerShell, cmd)
- Operating system with Linux distribution name
- Hybrid environments (WSL, Git Bash, Cygwin)
- Available package managers

Functions:
    detect_shell() - Returns (shell_name, version) tuple
    detect_os() - Returns OS string (e.g., "Linux (Ubuntu)", "macOS", "Windows")
    detect_environment() - Returns environment type ("native", "wsl", "gitbash", "cygwin")
    detect_package_managers() - Returns list of available package managers
    get_system_context() - Returns dict with all detection results
"""

__version__ = "1.0"

import os
import platform
import shutil


def detect_shell():
    """Detect current shell cross-platform (using env vars only)"""
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


def detect_os():
    """Detect OS - simplified version info"""
    os_type = platform.system()

    if os_type == "Linux":
        # Just get distro name, skip version/kernel details
        try:
            with open('/etc/os-release') as f:
                for line in f:
                    if line.startswith('NAME='):
                        distro = line.split('=')[1].strip().strip('"')
                        return f"Linux ({distro})"
        except:
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


def detect_environment():
    """Detect hybrid environments (WSL, Git Bash, etc.)"""
    os_name = platform.system()

    # WSL detection
    if os_name == "Linux":
        if os.getenv("WSL_DISTRO_NAME"):
            return "wsl"
        try:
            with open('/proc/version', 'r') as f:
                if 'microsoft' in f.read().lower():
                    return "wsl"
        except:
            pass

    # Git Bash / MSYS
    if os.getenv("MSYSTEM"):
        return "gitbash"

    # Cygwin
    if os.getenv("CYGWIN"):
        return "cygwin"

    return "native"


def detect_package_managers():
    """Detect available package managers"""
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


def get_system_context():
    """Get all system detection results as a dictionary.

    Returns:
        dict with keys: shell, shell_version, os, environment, package_managers
    """
    shell_name, shell_version = detect_shell()
    return {
        'shell': shell_name,
        'shell_version': shell_version,
        'os': detect_os(),
        'environment': detect_environment(),
        'package_managers': detect_package_managers(),
    }
