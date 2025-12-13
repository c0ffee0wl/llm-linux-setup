#Requires -Version 5.1
<#
.SYNOPSIS
    Windows Preparation Script for Remote Management

.DESCRIPTION
    Prepares a fresh Windows installation for remote management by:
    - Setting ExecutionPolicy to RemoteSigned
    - Optionally running Andrew Taylor's debloat script
    - Enabling Remote Desktop (RDP) with NLA (Private networks only)
    - Installing and configuring OpenSSH Server (Private networks only)
    - Setting PowerShell as default SSH shell
    - Installing Chocolatey and essential packages

.EXAMPLE
    .\Prepare-Windows.ps1
    Run the preparation script with all prompts

.EXAMPLE
    Set-ExecutionPolicy Bypass -Scope Process -Force; [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor 3072; $ProgressPreference='SilentlyContinue'; irm "https://raw.githubusercontent.com/c0ffee0wl/llm-linux-setup/main/integration/Prepare-Windows.ps1" | iex
    Run directly from GitHub (requires Administrator PowerShell)

.NOTES
    Author: c0ffee0wl
    Version: 1.0
    Requires: Windows 10/11/Server, Administrator privileges

    This script is designed to be run once on a fresh Windows installation
    to prepare it for remote management. It can be safely re-run (idempotent).
#>

[CmdletBinding()]
param()

# ============================================================================
# Script Configuration
# ============================================================================

$ErrorActionPreference = "Stop"

# ============================================================================
# Helper Functions
# ============================================================================

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-ErrorLog {
    param([string]$Message)
    Write-Host "[ERROR] " -ForegroundColor Red -NoNewline
    Write-Host $Message
}

function Write-WarningLog {
    param([string]$Message)
    Write-Host "[WARNING] " -ForegroundColor Yellow -NoNewline
    Write-Host $Message
}

function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-CommandExists {
    param([string]$Command)
    [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

function Test-PythonAvailable {
    <#
    .SYNOPSIS
        Tests if Python is actually available and working
    .DESCRIPTION
        On Windows 11, 'python' command may exist as an App Execution Alias that opens
        the Microsoft Store instead of running Python. This function verifies Python
        is actually installed and working by checking version output.
    #>
    try {
        $version = python --version 2>&1
        return ($version -match "Python \d+\.\d+")
    } catch {
        return $false
    }
}

function Test-ServiceExists {
    <#
    .SYNOPSIS
        Checks if a Windows service exists
    .PARAMETER ServiceName
        The name of the service to check
    #>
    param([string]$ServiceName)
    $null -ne (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)
}

function Refresh-EnvironmentPath {
    <#
    .SYNOPSIS
        Refreshes the PATH environment variable from Machine and User scopes
    .DESCRIPTION
        Combines Machine and User PATH variables to update the current session's PATH.
        Useful after installing new tools via Chocolatey or other installers.
    #>
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
}

# ============================================================================
# Administrator Check
# ============================================================================

Write-Log "Windows Preparation Script"
Write-Log "=========================="
Write-Host ""

if (-not (Test-Administrator)) {
    Write-ErrorLog "This script requires Administrator privileges."
    Write-Host ""
    Write-Host "Please run PowerShell as Administrator and try again:" -ForegroundColor Yellow
    Write-Host "  1. Right-click on PowerShell" -ForegroundColor Gray
    Write-Host "  2. Select 'Run as administrator'" -ForegroundColor Gray
    Write-Host "  3. Navigate to the script directory" -ForegroundColor Gray
    Write-Host "  4. Run: .\Prepare-Windows.ps1" -ForegroundColor Gray
    exit 1
}

Write-Log "Running with Administrator privileges"
Write-Host ""

# ============================================================================
# Execution Policy Configuration
# ============================================================================

Write-Log "Configuring PowerShell Execution Policy..."

$currentPolicy = Get-ExecutionPolicy -Scope LocalMachine

if ($currentPolicy -eq "RemoteSigned" -or $currentPolicy -eq "Unrestricted" -or $currentPolicy -eq "Bypass") {
    Write-Log "Execution Policy is already set to $currentPolicy (acceptable)"
} else {
    Write-Log "Current policy: $currentPolicy - Setting to RemoteSigned..."
    try {
        Set-ExecutionPolicy RemoteSigned -Scope LocalMachine -Force
        Write-Log "Execution Policy set to RemoteSigned"
    } catch {
        Write-WarningLog "Failed to set Execution Policy: $_"
        Write-WarningLog "You may need to set this via Group Policy"
    }
}

Write-Host ""

# ============================================================================
# Windows Debloat (Optional)
# ============================================================================

Write-Log "Windows Debloat Configuration"
Write-Host ""
Write-Host "Andrew Taylor's debloat script removes bloatware from Windows 10/11:" -ForegroundColor Cyan
Write-Host "  - Removes pre-installed AppX packages (Candy Crush, etc.)" -ForegroundColor Gray
Write-Host "  - Disables Cortana" -ForegroundColor Gray
Write-Host "  - Removes vendor bloat (McAfee, HP, Dell, Lenovo)" -ForegroundColor Gray
Write-Host ""
Write-Host "Reference: https://github.com/andrew-s-taylor/public/tree/main/De-Bloat" -ForegroundColor Gray
Write-Host ""

$runDebloat = Read-Host "Run Windows debloat script? (Y/n)"

if ([string]::IsNullOrEmpty($runDebloat) -or $runDebloat -eq 'Y' -or $runDebloat -eq 'y') {
    Write-Log "Running Windows debloat script..."

    $debloatUrl = "https://raw.githubusercontent.com/andrew-s-taylor/public/main/De-Bloat/RemoveBloat.ps1"

    try {
        Write-Log "Downloading debloat script from GitHub..."

        # Set TLS 1.2 for HTTPS
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072

        # Download script
        $debloatScript = (New-Object System.Net.WebClient).DownloadString($debloatUrl)

        Write-Log "Executing debloat script (this may take several minutes)..."
        Write-Log "Note: Some errors on non-English Windows are expected and can be ignored."

        # Save script to temp file and execute with error handling disabled
        # This ensures the script continues even when .NET methods throw exceptions
        $tempScript = Join-Path $env:TEMP "RemoveBloat_$(Get-Random).ps1"
        $debloatScript | Out-File -FilePath $tempScript -Encoding UTF8

        # Execute in separate process with error action preference set to continue
        # The -Command wrapper ensures errors don't terminate execution
        & powershell.exe -ExecutionPolicy Bypass -NoProfile -Command "& {`$ErrorActionPreference='SilentlyContinue'; . '$tempScript'}"

        # Cleanup temp file
        Remove-Item -Path $tempScript -Force -ErrorAction SilentlyContinue

        Write-Log "Windows debloat completed (check above for any warnings)"
    } catch {
        Write-WarningLog "Failed to download/run debloat script: $_"
        Write-WarningLog "You can run it manually later from: $debloatUrl"
    }
} else {
    Write-Log "Skipping Windows debloat"
}

Write-Host ""

# ============================================================================
# Windows Firewall Status (informational only - firewall remains enabled)
# ============================================================================

Write-Log "Checking Windows Firewall status..."

# Log status of all profiles for reference (firewall stays enabled on all profiles)
$domainProfile = Get-NetFirewallProfile -Profile Domain
$privateProfile = Get-NetFirewallProfile -Profile Private
$publicProfile = Get-NetFirewallProfile -Profile Public
Write-Log "Firewall status: Domain=$($domainProfile.Enabled), Private=$($privateProfile.Enabled), Public=$($publicProfile.Enabled)"

Write-Host ""

# ============================================================================
# Enable Remote Desktop (RDP)
# ============================================================================

Write-Log "Configuring Remote Desktop..."

# Registry path for RDP settings
$rdpRegPath = "HKLM:\System\CurrentControlSet\Control\Terminal Server"

# Check current RDP status
$currentValue = Get-ItemProperty -Path $rdpRegPath -Name "fDenyTSConnections" -ErrorAction SilentlyContinue

if ($currentValue.fDenyTSConnections -eq 0) {
    Write-Log "Remote Desktop is already enabled"
} else {
    Write-Log "Enabling Remote Desktop..."

    try {
        # Enable RDP via registry
        Set-ItemProperty -Path $rdpRegPath -Name "fDenyTSConnections" -Value 0

        Write-Log "Remote Desktop enabled via registry"
    } catch {
        Write-WarningLog "Failed to enable RDP via registry: $_"
    }
}

# Enable firewall rule for RDP (Private profile only for security)
Write-Log "Configuring RDP firewall rules for Private profile only..."

try {
    # Use rule name pattern - this is language-independent (rule names don't change across locales)
    # Built-in rules: RemoteDesktop-UserMode-In-TCP, RemoteDesktop-UserMode-In-UDP, etc.
    # Enable the rules first
    Enable-NetFirewallRule -Name "RemoteDesktop-UserMode-In-TCP" -ErrorAction SilentlyContinue
    Enable-NetFirewallRule -Name "RemoteDesktop-UserMode-In-UDP" -ErrorAction SilentlyContinue
    # Restrict to Private profile only (trusted networks)
    Set-NetFirewallRule -Name "RemoteDesktop-UserMode-In-TCP" -Profile Private -ErrorAction SilentlyContinue
    Set-NetFirewallRule -Name "RemoteDesktop-UserMode-In-UDP" -Profile Private -ErrorAction SilentlyContinue
    Write-Log "RDP firewall rules configured (Private only)"
} catch {
    Write-WarningLog "Failed to configure RDP firewall rules: $_"
    Write-WarningLog "You may need to manually enable the 'Remote Desktop' firewall rule"
}

# Enable Network Level Authentication (NLA) for security
Write-Log "Enabling Network Level Authentication (NLA)..."

try {
    $nlaRegPath = "HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
    Set-ItemProperty -Path $nlaRegPath -Name "UserAuthentication" -Value 1
    Write-Log "NLA enabled for RDP connections"
} catch {
    Write-WarningLog "Failed to enable NLA: $_"
}

Write-Host ""

# ============================================================================
# Install & Configure OpenSSH Server
# ============================================================================

Write-Log "Configuring OpenSSH Server..."

# Check if OpenSSH Server is installed
$sshCapability = Get-WindowsCapability -Online | Where-Object { $_.Name -like "OpenSSH.Server*" }

if ($sshCapability.State -eq "Installed") {
    Write-Log "OpenSSH Server is already installed"
} else {
    Write-Log "Installing OpenSSH Server from Windows Features (this may take a few minutes)..."

    try {
        Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
        Write-Log "OpenSSH Server installed successfully"
    } catch {
        Write-WarningLog "Failed to install OpenSSH Server: $_"
        Write-Host ""
        Write-Host "If you're on an isolated network or using WSUS, you may need to:" -ForegroundColor Yellow
        Write-Host "  1. Download the OpenSSH package manually" -ForegroundColor Gray
        Write-Host "  2. Or configure Windows Update to use online sources temporarily" -ForegroundColor Gray
        # Continue script execution - SSH is not critical
    }
}

# Configure OpenSSH service
if (Test-ServiceExists "sshd") {
    # Set service to start automatically
    Write-Log "Configuring sshd service to start automatically..."

    try {
        Set-Service -Name sshd -StartupType 'Automatic'
        Write-Log "sshd service configured for automatic startup"
    } catch {
        Write-WarningLog "Failed to configure sshd startup type: $_"
    }

    # Configure firewall rule for SSH (Private profile only for security)
    Write-Log "Configuring SSH firewall rule for Private profile only..."

    # Check for any existing SSH/OpenSSH firewall rules (port 22 inbound)
    $sshFirewallRule = Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like "*SSH*" -or $_.Name -like "*OpenSSH*"
    }

    if ($sshFirewallRule) {
        # Ensure existing rules are enabled and restricted to Private profile
        $sshFirewallRule | Enable-NetFirewallRule -ErrorAction SilentlyContinue
        $sshFirewallRule | Set-NetFirewallRule -Profile Private -ErrorAction SilentlyContinue
        Write-Log "SSH firewall rule configured (Private only)"
    } else {
        try {
            New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" `
                -DisplayName "OpenSSH Server (TCP)" `
                -Protocol TCP `
                -Action Allow `
                -Direction Inbound `
                -LocalPort 22 `
                -Profile Private `
                -ErrorAction Stop | Out-Null
            Write-Log "SSH firewall rule created (Private only)"
        } catch {
            Write-WarningLog "Failed to create SSH firewall rule: $_"
        }
    }

    # Set PowerShell as default shell for SSH
    Write-Log "Setting PowerShell as default SSH shell..."

    $sshRegPath = "HKLM:\SOFTWARE\OpenSSH"

    # Create OpenSSH registry key if it doesn't exist
    if (-not (Test-Path $sshRegPath)) {
        try {
            New-Item -Path $sshRegPath -Force | Out-Null
            Write-Log "Created OpenSSH registry key"
        } catch {
            Write-WarningLog "Failed to create OpenSSH registry key: $_"
        }
    }

    # Use Windows PowerShell as default shell
    $defaultShellPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    $shellName = "Windows PowerShell (powershell.exe)"

    try {
        New-ItemProperty -Path $sshRegPath `
            -Name DefaultShell `
            -Value $defaultShellPath `
            -PropertyType String `
            -Force | Out-Null
        Write-Log "Default SSH shell set to: $shellName"
    } catch {
        Write-WarningLog "Failed to set default SSH shell: $_"
    }

    # Start the service if not running
    $sshdService = Get-Service -Name sshd

    if ($sshdService.Status -ne 'Running') {
        Write-Log "Starting sshd service..."

        try {
            Start-Service sshd
            Write-Log "sshd service started"
        } catch {
            Write-WarningLog "Failed to start sshd service: $_"
        }
    } else {
        Write-Log "sshd service is already running"
    }

} else {
    Write-WarningLog "sshd service not found - OpenSSH Server may not have installed correctly"
}

Write-Host ""

# ============================================================================
# Install Chocolatey
# ============================================================================

Write-Log "Checking Chocolatey installation..."

if (Test-CommandExists "choco") {
    Write-Log "Chocolatey is already installed"
} else {
    Write-Log "Installing Chocolatey..."

    try {
        # Set TLS 1.2 for HTTPS downloads
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072

        # Set execution policy for this process (needed for Chocolatey install script)
        Set-ExecutionPolicy Bypass -Scope Process -Force

        # Download and execute Chocolatey installation script
        Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

        # Refresh environment variables
        Refresh-EnvironmentPath

        Write-Log "Chocolatey installed successfully"
    } catch {
        Write-ErrorLog "Failed to install Chocolatey: $_"
        Write-Host ""
        Write-Host "Please install Chocolatey manually:" -ForegroundColor Yellow
        Write-Host "  https://chocolatey.org/install" -ForegroundColor Gray
        exit 1
    }
}

Write-Host ""

# ============================================================================
# Install Chocolatey Packages
# ============================================================================

Write-Log "Installing Chocolatey packages..."
Write-Host ""

# Define packages to install
# Format: @{ Name = "choco-package"; Command = "command-to-check"; SkipCheck = $false }
# If Command is empty, skip existence check
# If SkipCheck is true, always reinstall (useful for Python with Windows 11 store alias)
$packages = @(
    @{ Name = "python312"; Command = "python"; SkipCheck = $true },
    @{ Name = "powershell-core"; Command = "pwsh"; SkipCheck = $false },
    @{ Name = "notepadplusplus"; Command = ""; SkipCheck = $false },
    @{ Name = "vcredist140"; Command = ""; SkipCheck = $false },
    @{ Name = "vscode"; Command = "code"; SkipCheck = $false }
)

foreach ($package in $packages) {
    $packageName = $package.Name
    $commandName = $package.Command
    $skipCheck = $package.SkipCheck

    # Special handling for Python - use Test-PythonAvailable instead of Test-CommandExists
    if ($packageName -eq "python312") {
        if (Test-PythonAvailable) {
            Write-Log "$packageName is already installed and working"
            continue
        } else {
            Write-Log "Python not available or Windows Store alias detected - installing $packageName..."
        }
    }
    # Check if already installed (if command check is provided and not skipping)
    elseif ($commandName -and -not $skipCheck -and (Test-CommandExists $commandName)) {
        Write-Log "$packageName is already installed (found $commandName)"
        continue
    }
    # For packages without command check, use choco list to check if installed
    elseif (-not $commandName -and -not $skipCheck) {
        $chocoList = & choco list --local-only --exact $packageName 2>$null
        if ($chocoList -match $packageName) {
            Write-Log "$packageName is already installed (via Chocolatey)"
            continue
        }
    }

    Write-Log "Installing $packageName..."

    # Temporarily relax error handling for choco operations
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        & choco install $packageName -y

        if ($LASTEXITCODE -eq 0) {
            Write-Log "$packageName installed successfully"
        } else {
            Write-WarningLog "$packageName installation returned exit code: $LASTEXITCODE"
        }
    } catch {
        Write-WarningLog "Failed to install $packageName : $_"
    }

    $ErrorActionPreference = $previousErrorAction
}

# Refresh PATH to pick up newly installed tools
Refresh-EnvironmentPath

Write-Host ""

# ============================================================================
# COMPLETE
# ============================================================================

Write-Host ""
Write-Log "============================================="
Write-Log "Windows Preparation Complete!"
Write-Log "============================================="
Write-Host ""

Write-Log "Configured features:"
Write-Host "  - Execution Policy: RemoteSigned" -ForegroundColor Cyan
Write-Host "  - Windows Firewall: Enabled on all profiles" -ForegroundColor Cyan
Write-Host "  - Remote Desktop (RDP): Enabled with NLA (Private networks only)" -ForegroundColor Cyan
Write-Host "  - OpenSSH Server: Installed and running (Private networks only)" -ForegroundColor Cyan
Write-Host "  - Default SSH Shell: Windows PowerShell" -ForegroundColor Cyan
Write-Host ""

Write-Log "Installed packages via Chocolatey:"
Write-Host "  - Python 3.12" -ForegroundColor Cyan
Write-Host "  - PowerShell Core (pwsh)" -ForegroundColor Cyan
Write-Host "  - Notepad++" -ForegroundColor Cyan
Write-Host "  - Visual C++ Redistributable" -ForegroundColor Cyan
Write-Host "  - Visual Studio Code" -ForegroundColor Cyan
Write-Host ""

Write-Log "Remote access information:"

# Get IP addresses
$ipAddresses = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.InterfaceAlias -notlike "*Loopback*" -and
    $_.PrefixOrigin -ne "WellKnown" -and
    $_.IPAddress -notlike "169.254.*"
}

foreach ($ip in $ipAddresses) {
    Write-Host ""
    Write-Host "  Interface: $($ip.InterfaceAlias)" -ForegroundColor Gray
    Write-Host "  RDP: " -ForegroundColor Cyan -NoNewline
    Write-Host "mstsc /v:$($ip.IPAddress)"
    Write-Host "  SSH: " -ForegroundColor Cyan -NoNewline
    Write-Host "ssh $env:USERNAME@$($ip.IPAddress)"
}

Write-Host ""
Write-Log "Next steps:"
Write-Host "  1. Test RDP connection from another machine" -ForegroundColor Gray
Write-Host "  2. Test SSH connection: ssh $env:USERNAME@<ip-address>" -ForegroundColor Gray
Write-Host "  3. Run Install-LlmTools.ps1 to install LLM tools" -ForegroundColor Gray
Write-Host ""
