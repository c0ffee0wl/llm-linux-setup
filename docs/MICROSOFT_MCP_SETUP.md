# Azure MCP and Codex CLI Setup Guide

This guide covers the installation and configuration of Codex CLI, Azure MCP Server, Lokka (Microsoft 365 MCP), and Microsoft Learn MCP for enhanced AI-powered development workflows.

## Quick Start Guide

Follow these steps to quickly set up all three MCP servers in Codex CLI.

### Prerequisites

- ✅ Run `./install-llm-tools.sh` with Azure OpenAI configured
- ✅ Codex CLI automatically installed and configured
- ✅ Azure OpenAI credentials set in `~/.profile`

### Step 1: Install Azure CLI

```bash
sudo apt-get update
sudo apt-get install -y azure-cli
```

### Step 2: Login to Azure

**Interactive login (opens browser):**
```bash
az login
```

**Or for device code flow (remote/headless systems):**
```bash
az login --use-device-code
```

**Verify authentication:**
```bash
az account show
```

You should see your Azure subscription details.

### Step 3: Configure Azure MCP in Codex

```bash
codex mcp add azure -- npx -y @azure/mcp@latest server start
```

This enables access to 40+ Azure services directly from Codex.

### Step 4: Configure Lokka (Microsoft 365 MCP) in Codex

**Interactive authentication (simplest):**
```bash
codex mcp add lokka -- npx -y @merill/lokka
```

When you first use Lokka in Codex, it will open your browser to authenticate with Microsoft 365.

**For production use with Entra ID app registration**, see the [App Registration Guide](#app-registration-guide) section below.

### Step 5: Configure Microsoft Learn MCP in Codex

```bash
# Install mcp-remote proxy
npm install -g mcp-remote

# Add Microsoft Learn MCP
codex mcp add microsoft-learn -- npx -y mcp-remote https://learn.microsoft.com/api/mcp
```

This provides Codex with access to trusted Microsoft documentation.

### Step 6: Verify MCP Servers

```bash
codex mcp list
```

You should see all three servers listed:
- `azure`
- `lokka`
- `microsoft-learn`

### Step 7: Test in Codex

```bash
# Load Azure environment variables (if not already loaded)
source ~/.profile

# Start Codex
codex

# Try example prompts:
# - "List my Azure resource groups"
# - "Show my Microsoft 365 users"
# - "Find documentation on Azure Functions"
```

### Troubleshooting

**Azure authentication fails:**
```bash
# Check login status
az account show

# Re-login if needed
az login
```

**MCP server not working:**
```bash
# Test individual servers
npx -y @azure/mcp@latest server start  # Should show server info
npx -y @merill/lokka                   # Should show Lokka info

# Check Codex logs for errors
codex mcp test azure
```

**Environment variables not loaded:**
```bash
# Verify variables exist
grep AZURE ~/.profile

# Reload profile
source ~/.profile
```

---

## Table of Contents

- [Quick Start Guide](#quick-start-guide)
- [Codex CLI](#codex-cli)
  - [Overview](#overview)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [VS Code Extension](#vs-code-extension)
- [Configuring MCP Servers in Codex](#configuring-mcp-servers-in-codex)
  - [Configuration Methods](#configuration-methods)
  - [Configuration File Structure](#configuration-file-structure)
- [Azure MCP Server](#azure-mcp-server)
  - [Overview](#azure-mcp-overview)
  - [Installation](#azure-mcp-installation)
  - [Authentication](#azure-mcp-authentication)
  - [Codex CLI Configuration](#azure-mcp-codex-configuration)
- [Lokka (Microsoft 365 MCP)](#lokka-microsoft-365-mcp)
  - [Overview](#lokka-overview)
  - [Authentication Methods](#lokka-authentication-methods)
  - [Codex CLI Configuration](#lokka-codex-configuration)
  - [App Registration Guide](#app-registration-guide)
- [Microsoft Learn MCP](#microsoft-learn-mcp)
  - [Overview](#microsoft-learn-overview)
  - [Codex CLI Configuration](#microsoft-learn-codex-configuration)
- [Testing and Verification](#testing-and-verification)
- [Integration with llm-linux-setup](#integration-with-llm-linux-setup)

---

## Codex CLI

### Overview

**Codex CLI** is an open-source AI coding agent built by OpenAI that runs locally in your terminal. It provides terminal-based AI coding assistance with support for Azure OpenAI.

**Key Features:**
- Automatically creates pull requests
- Refactors files and writes tests
- Executes asynchronous coding tasks
- Integrates with GitHub Actions for CI/CD
- Operates within enterprise security boundaries
- Your source code never leaves your environment

**Official Documentation:**
- **Quickstart**: https://developers.openai.com/codex/quickstart
- **MCP Integration**: https://developers.openai.com/codex/mcp/

**Prerequisites:**
- Node.js 20+ (already ensured by llm-linux-setup)
- Minimum: 4GB RAM (8GB recommended)
- Platform: macOS 12+, Ubuntu 20.04+, Debian 10+, or Windows 11 via WSL2
- ChatGPT Plus/Pro/Business/Edu/Enterprise account OR OpenAI API key

### Installation

Codex CLI is **automatically installed** by llm-linux-setup when Azure OpenAI is configured.

**Manual installation methods:**

```bash
# Via npm (global)
npm install -g @openai/codex

# Via Homebrew (macOS)
brew install codex
```

**Verify installation:**
```bash
codex --version
```

### Configuration

When Azure OpenAI is configured, llm-linux-setup automatically creates `~/.codex/config.toml`:

```toml
[model]
name = "gpt-5-codex"
model_provider = "azure"

[model_providers.azure]
base_url = "https://YOUR-RESOURCE.openai.azure.com/openai/v1/"
env_key = "AZURE_OPENAI_API_KEY"
```

**Environment Variables (automatically added to `~/.profile`):**

```bash
export AZURE_OPENAI_API_KEY="your-api-key"
export AZURE_RESOURCE_NAME="your-resource-name"
```

**To load environment variables in your current session:**
```bash
source ~/.profile
```

### VS Code Extension

Codex is also available as a **Visual Studio Code extension**, providing IDE-integrated AI assistance.

**Installation:**
1. Open VS Code
2. Go to Extensions (Ctrl+Shift+X or Cmd+Shift+X)
3. Search for "Codex"
4. Click Install

**Supported IDEs:**
- Visual Studio Code
- VS Code Insiders
- Cursor
- Windsurf

**Features:**
- Inline AI suggestions
- Delegate tasks to cloud agent
- Review proposed changes as diffs
- Create PRs directly from the IDE

**Pro Tip:** Create git checkpoints before and after each Codex task to easily revert if needed.

---

## Configuring MCP Servers in Codex

The Model Context Protocol (MCP) allows Codex to connect to external tools and services for enhanced capabilities.

**Official Documentation**: https://developers.openai.com/codex/mcp/

### Configuration Methods

Codex supports two approaches for MCP server setup:

#### 1. CLI Command (Recommended)

Use `codex mcp` commands for streamlined setup:

```bash
codex mcp add <server-name> --env VAR1=VALUE1 -- <command>
```

**Example:**
```bash
codex mcp add context7 -- npx -y @upstash/context7-mcp
```

**View all available commands:**
```bash
codex mcp --help
```

#### 2. Direct File Editing

Manually edit `~/.codex/config.toml` to add MCP servers.

### Configuration File Structure

The `config.toml` uses table syntax for each server: `[mcp_servers.server-name]`

#### STDIO Server Parameters
- `command` (required): Launch command
- `args` (optional): Command arguments array
- `env` (optional): Environment variables table

#### HTTP Server Parameters
- `url` (required): Server endpoint
- `bearer_token` (optional): Authentication header value

#### Global Settings
- `startup_timeout_sec`: Server initialization timeout
- `tool_timeout_sec`: Tool execution timeout
- `experimental_use_rmcp_client`: Enables RMCP client and OAuth support

**Example Configuration:**

```toml
[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[mcp_servers.context7.env]
MY_ENV_VAR = "MY_ENV_VALUE"

[mcp_servers.figma]
url = "https://mcp.figma.com/mcp"

experimental_use_rmcp_client = true
```

---

## Azure MCP Server

### Azure MCP Overview

The **Azure MCP Server** is Microsoft's official MCP implementation, providing seamless integration with 40+ Azure services.

**Capabilities:**
- Query Azure resources and configurations
- Interact with Azure AI services, storage, compute
- Manage Azure Communication Services (SMS, email)
- Generate Azure CLI commands
- Manage databases and analyze data
- Unified access to Azure operations

**Repository**: https://github.com/microsoft/mcp/tree/main/servers/Azure.Mcp.Server

### Azure MCP Installation

**Prerequisites:**
- Node.js 20+ (already installed by llm-linux-setup)
- Azure account with appropriate permissions
- Azure CLI (for authentication)

**Installation:**

```bash
# Global install (recommended for Linux)
npm install -g @azure/mcp

# Or use with npx (auto-updates each run)
npx -y @azure/mcp@latest server start
```

**Current Version**: 0.6.0 (Public Preview)

**Package**: https://www.npmjs.com/package/@azure/mcp

### Azure MCP Authentication

Azure MCP requires authentication to Azure before running.

#### Method 1: Azure CLI Authentication (Recommended)

**Install Azure CLI:**
```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

**Login to Azure:**
```bash
# Interactive login (opens browser)
az login

# Device code flow (for remote/headless systems)
az login --use-device-code

# Verify authentication
az account show
```

**List available subscriptions:**
```bash
az account list
```

**Set default subscription (if needed):**
```bash
az account set --subscription "Subscription Name or ID"
```

#### Method 2: Service Principal (For Automation)

Create a service principal and use environment variables:

```bash
# Create service principal
az ad sp create-for-rbac --name "MyMCPServicePrincipal" --role Contributor
```

**Required Environment Variables:**
- `AZURE_TENANT_ID`: Your Azure AD tenant ID
- `AZURE_CLIENT_ID`: Service principal application ID
- `AZURE_CLIENT_SECRET`: Service principal password

#### Method 3: Managed Identity

Available when running within Azure (VMs, App Service, etc.). No additional configuration needed.

### Azure MCP Codex Configuration

#### Option 1: CLI Command (with Azure CLI auth)

```bash
codex mcp add azure -- npx -y @azure/mcp@latest server start
```

#### Option 2: Manual config.toml (with Azure CLI auth)

```toml
[mcp_servers.azure]
command = "npx"
args = ["-y", "@azure/mcp@latest", "server", "start"]
```

#### Option 3: CLI Command (with Service Principal)

```bash
codex mcp add azure \
  --env AZURE_TENANT_ID=your-tenant-id \
  --env AZURE_CLIENT_ID=your-client-id \
  --env AZURE_CLIENT_SECRET=your-client-secret \
  -- npx -y @azure/mcp@latest server start
```

#### Option 4: Manual config.toml (with Service Principal)

```toml
[mcp_servers.azure]
command = "npx"
args = ["-y", "@azure/mcp@latest", "server", "start"]

[mcp_servers.azure.env]
AZURE_TENANT_ID = "your-tenant-id"
AZURE_CLIENT_ID = "your-client-id"
AZURE_CLIENT_SECRET = "your-client-secret"
```

**Verification:**
```bash
# Test Azure authentication
az account show

# List MCP servers in Codex
codex mcp list
```

---

## Lokka (Microsoft 365 MCP)

### Lokka Overview

**Lokka** is an MCP server by Merill Fernando that enables natural language interaction with Microsoft 365 and Azure resources through Microsoft Graph and Azure RM APIs.

**Capabilities:**
- Query and manage Microsoft 365 environments
- Create security groups with dynamic rules
- Analyze conditional access policies
- Manage Intune device configurations
- Review Azure billing data
- Execute administrative tasks through natural language

**Repository**: https://github.com/merill/lokka

**Package**: https://www.npmjs.com/package/@merill/lokka

**Current Version**: 0.1.7

### Lokka Authentication Methods

Lokka supports multiple authentication approaches:

#### 1. Interactive Authentication (Simplest - Recommended for Personal Use)

Uses browser-based login with the default Lokka app. No app registration required.

#### 2. Client Credentials (App-Only with Client Secret)

For service principal authentication with app registration.

#### 3. Certificate-Based Authentication (Recommended for Production)

Uses PEM-encoded certificate for enhanced security.

#### 4. Token-Based Authentication

External token management for advanced scenarios.

### Lokka Codex Configuration

#### Option 1: Interactive Auth (Simplest)

**CLI Command:**
```bash
codex mcp add lokka -- npx -y @merill/lokka
```

**Manual config.toml:**
```toml
[mcp_servers.lokka]
command = "npx"
args = ["-y", "@merill/lokka"]
```

#### Option 2: Client Credentials (App-Only)

**CLI Command:**
```bash
codex mcp add lokka \
  --env TENANT_ID=your-tenant-id \
  --env CLIENT_ID=your-client-id \
  --env CLIENT_SECRET=your-client-secret \
  -- npx -y @merill/lokka
```

**Manual config.toml:**
```toml
[mcp_servers.lokka]
command = "npx"
args = ["-y", "@merill/lokka"]

[mcp_servers.lokka.env]
TENANT_ID = "your-tenant-id"
CLIENT_ID = "your-client-id"
CLIENT_SECRET = "your-client-secret"
```

#### Option 3: Certificate-Based Auth

**CLI Command:**
```bash
codex mcp add lokka \
  --env TENANT_ID=your-tenant-id \
  --env CLIENT_ID=your-client-id \
  --env CERTIFICATE_PATH=/path/to/cert.pem \
  -- npx -y @merill/lokka
```

**Manual config.toml:**
```toml
[mcp_servers.lokka]
command = "npx"
args = ["-y", "@merill/lokka"]

[mcp_servers.lokka.env]
TENANT_ID = "your-tenant-id"
CLIENT_ID = "your-client-id"
CERTIFICATE_PATH = "/path/to/cert.pem"
```

#### Option 4: Token-Based Auth

**CLI Command:**
```bash
codex mcp add lokka \
  --env USE_CLIENT_TOKEN=true \
  -- npx -y @merill/lokka
```

**Manual config.toml:**
```toml
[mcp_servers.lokka]
command = "npx"
args = ["-y", "@merill/lokka"]

[mcp_servers.lokka.env]
USE_CLIENT_TOKEN = "true"
```

### App Registration Guide

For **Client Credentials** or **Certificate-Based** authentication, you need to register an app in Microsoft Entra (Azure AD):

#### Step 1: Register Application

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Microsoft Entra ID** (formerly Azure Active Directory)
3. Select **App registrations** → **New registration**
4. Enter application name (e.g., "Lokka MCP")
5. Select **Accounts in this organizational directory only**
6. Click **Register**

#### Step 2: Note IDs

After registration, note these values:
- **Application (client) ID**: Your `CLIENT_ID`
- **Directory (tenant) ID**: Your `TENANT_ID`

#### Step 3: Create Client Secret (for Client Credentials auth)

1. In your app registration, go to **Certificates & secrets**
2. Click **New client secret**
3. Add description and expiration period
4. Click **Add**
5. **Copy the secret value immediately** (shown only once): Your `CLIENT_SECRET`

#### Step 4: Upload Certificate (for Certificate-Based auth)

1. In your app registration, go to **Certificates & secrets**
2. Click **Upload certificate**
3. Select your `.cer` or `.pem` file
4. Click **Add**
5. Use the corresponding private key file as `CERTIFICATE_PATH`

#### Step 5: Configure API Permissions

Grant appropriate Microsoft Graph permissions based on your needs:

**Recommended Microsoft Graph API Permissions:**

| Permission | Type | Purpose |
|------------|------|---------|
| `User.Read.All` | Application | Read all users |
| `Group.Read.All` | Application | Read all groups |
| `Device.Read.All` | Application | Read all devices |
| `Policy.Read.All` | Application | Read conditional access policies |
| `DeviceManagementConfiguration.Read.All` | Application | Read Intune configurations |
| `AuditLog.Read.All` | Application | Read audit logs |

**To add permissions:**
1. Go to **API permissions**
2. Click **Add a permission** → **Microsoft Graph**
3. Select **Application permissions**
4. Search and add the permissions above
5. Click **Grant admin consent** (requires admin privileges)

#### Step 6: Configure Azure Resource Manager Permissions (Optional)

For Azure subscription operations:

1. Go to your **Azure subscription**
2. Select **Access control (IAM)**
3. Click **Add role assignment**
4. Select appropriate role (Reader, Contributor, etc.)
5. Assign to your registered application

---

## Microsoft Learn MCP

### Microsoft Learn Overview

The **Microsoft Learn MCP Server** is an official Microsoft remote MCP server providing AI agents with access to trusted, up-to-date Microsoft documentation and code samples.

**Capabilities:**
- Semantic search across Microsoft technical documentation
- Fetch complete documentation pages in markdown
- Discover official Microsoft/Azure code samples
- Language-filtered code examples
- Real-time, trusted Microsoft documentation

**Repository**: https://github.com/microsoftdocs/mcp

**Endpoint**: `https://learn.microsoft.com/api/mcp`

**Authentication**: None required (public endpoint)

**Content Scope:**
- ✅ Publicly available documentation
- ✅ Official code samples
- ✅ Technical reference materials
- ❌ Training modules (excluded)
- ❌ Learning paths (excluded)
- ❌ Instructor-led courses (excluded)
- ❌ Exams (excluded)

**Update Frequency:**
- Incremental updates per content change
- Complete refresh once daily

### Microsoft Learn Codex Configuration

Microsoft Learn MCP is an **HTTP server**, not a STDIO server, so configuration differs slightly.

#### Option 1: Direct TOML Edit (Native Remote Support)

For clients with native remote MCP support over Streamable HTTP:

```toml
[mcp_servers.microsoft-learn]
url = "https://learn.microsoft.com/api/mcp"

experimental_use_rmcp_client = true
```

**Note**: You must manually edit `~/.codex/config.toml` for HTTP servers, as `codex mcp add` is designed for STDIO servers.

#### Option 2: Using mcp-remote Proxy (STDIO Wrapper)

For clients without native remote support, use the `mcp-remote` proxy:

**Install mcp-remote:**
```bash
npm install -g mcp-remote
```

**CLI Command:**
```bash
codex mcp add microsoft-learn -- npx -y mcp-remote https://learn.microsoft.com/api/mcp
```

**Manual config.toml:**
```toml
[mcp_servers.microsoft-learn]
command = "npx"
args = ["-y", "mcp-remote", "https://learn.microsoft.com/api/mcp"]
```

**Verification:**

The endpoint is designed exclusively for MCP clients using Streamable HTTP. Direct browser access will return `405 Method Not Allowed`.

To test:
```bash
codex mcp list
```

---

## Testing and Verification

### Test MCP Server Configuration

**List configured servers:**
```bash
codex mcp list
```

**Test server startup (if supported):**
```bash
codex mcp test <server-name>
```

### Check Logs

If a server fails to start, check Codex logs for errors:

```bash
# Check Codex config
cat ~/.codex/config.toml

# Test individual components
npx -y @azure/mcp@latest server start  # Test Azure MCP
npx -y @merill/lokka                   # Test Lokka
```

### Verify Azure Authentication

```bash
# Check Azure login status
az account show

# List available Azure resources (test Azure MCP access)
az account list
```

### Use MCP Servers in Codex

Start a Codex session and reference MCP tools:

```bash
codex

# Example prompts:
# "List my Azure resource groups" (Azure MCP)
# "Show my Microsoft 365 users" (Lokka)
# "Find documentation on Azure Functions" (Microsoft Learn MCP)
```

---

## Integration with llm-linux-setup

### Automatic Codex CLI Configuration

When Azure OpenAI is configured in llm-linux-setup, the installation script automatically:

1. **Installs Codex CLI** via npm (Phase 6)
2. **Creates `~/.codex/config.toml`** with Azure OpenAI settings
3. **Exports environment variables** to `~/.profile`:
   - `AZURE_OPENAI_API_KEY`
   - `AZURE_RESOURCE_NAME`

### Environment Variables

The script extracts credentials from existing llm configuration:
- **API Key**: Retrieved from `llm keys get azure`
- **Resource URL**: Extracted from `extra-openai-models.yaml`
- **Resource Name**: Parsed from the URL

**To load environment variables:**
```bash
source ~/.profile
```

### Relationship to Existing Setup

- **llm CLI**: Uses `extra-openai-models.yaml` for model configuration
- **aichat**: Uses `~/.config/aichat/config.yaml` for RAG and chat
- **Codex CLI**: Uses `~/.codex/config.toml` + environment variables
- **All three tools** share the same Azure OpenAI credentials (managed via `llm keys`)

### Claude Code Router Integration

If you're using Claude Code Router (installed in Phase 7), you can also configure it with Azure OpenAI:

```json
{
  "providers": [
    {
      "name": "azure",
      "type": "azure-openai",
      "endpoint": "https://your-resource.openai.azure.com",
      "apiKey": "your-api-key",
      "deployments": {
        "gpt-4.1": "gpt-4.1",
        "gpt-4.1-mini": "gpt-4.1-mini"
      }
    }
  ]
}
```

---

## Additional MCP Client Configurations

While this guide focuses on Codex CLI, MCP servers can be used with other compatible clients:

### Claude Desktop Configuration

Add to `~/.config/Claude/claude_desktop_config.json` (Linux):

```json
{
  "mcpServers": {
    "azure": {
      "command": "npx",
      "args": ["-y", "@azure/mcp@latest", "server", "start"]
    },
    "lokka": {
      "command": "npx",
      "args": ["-y", "@merill/lokka"]
    },
    "microsoft-learn": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://learn.microsoft.com/api/mcp"]
    }
  }
}
```

### Other MCP Clients

Any MCP-compatible client can use these servers by following the appropriate configuration format (STDIO or HTTP).

---

## Summary

This guide covered:

- ✅ **Codex CLI**: Automatic installation and Azure OpenAI configuration
- ✅ **VS Code Extension**: IDE-integrated AI coding assistance
- ✅ **MCP Configuration**: CLI commands and manual TOML editing
- ✅ **Azure MCP Server**: 40+ Azure services integration with authentication
- ✅ **Lokka**: Microsoft 365 management with multiple auth methods
- ✅ **Microsoft Learn MCP**: Access to trusted Microsoft documentation
- ✅ **Integration**: Seamless integration with llm-linux-setup

**Next Steps:**
1. Ensure Azure is configured: Run `./install-llm-tools.sh`
2. Authenticate to Azure: `az login`
3. Configure MCP servers: `codex mcp add <server> -- <command>`
4. Test in Codex: `codex` and try MCP-powered prompts

**Resources:**
- Codex Quickstart: https://developers.openai.com/codex/quickstart
- Codex MCP Guide: https://developers.openai.com/codex/mcp/
- Azure MCP: https://github.com/microsoft/mcp/tree/main/servers/Azure.Mcp.Server
- Lokka: https://github.com/merill/lokka
- Microsoft Learn MCP: https://github.com/microsoftdocs/mcp

For issues or questions, consult the official documentation or repository issue trackers.
