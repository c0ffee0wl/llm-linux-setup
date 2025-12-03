# Microsoft MCP and Claude Code Setup Guide

This guide covers the installation and configuration of Claude Code (primary) and Codex CLI (alternative) with Microsoft MCP servers: Azure MCP Server, Lokka (Microsoft 365 MCP), and Microsoft Learn MCP for enhanced AI-powered development workflows.

**Claude Code** is positioned as the primary method due to its native HTTP support, OAuth 2.0 authentication, team-sharable project configurations, and superior developer experience. **Codex CLI** is provided as an alternative for users preferring TOML-based configuration.

## Prerequisites and Permissions

### Azure RBAC Roles for MCP Servers

Before configuring MCP servers, ensure proper Azure role assignments for secure, read-only access.

#### Azure MCP Server Required Roles

**Minimum Required Role:**
- **`Reader`**: Provides read-only access to Azure resources (subscriptions, resource groups, services)

**Enhanced Security Monitoring:**
- **`Security Reader`**: Provides read access to security-related information and recommendations

**⚠️ SECURITY WARNING:**
- **NEVER assign roles with write privileges** (e.g., `Contributor`, `Owner`, or any `*.Write*` roles)
- MCP connections should be read-only for safety
- Write access creates security risks if the AI agent is compromised

**Role Assignment Commands:**

```bash
# Assign Reader role at subscription level (replace placeholders)
az role assignment create \
  --assignee <your-email@domain.com> \
  --role "Reader" \
  --scope "/subscriptions/<your-subscription-id>"

# Assign Security Reader role for enhanced security queries
az role assignment create \
  --assignee <your-email@domain.com> \
  --role "Security Reader" \
  --scope "/subscriptions/<your-subscription-id>"

# Verify role assignments
az role assignment list --assignee <your-email@domain.com> --output table
```

**Scoping Options:**
- **Subscription level**: `--scope "/subscriptions/<subscription-id>"` (broadest)
- **Resource group level**: `--scope "/subscriptions/<subscription-id>/resourceGroups/<rg-name>"` (recommended)
- **Resource level**: `--scope "/subscriptions/<subscription-id>/resourceGroups/<rg-name>/providers/<resource-type>/<resource-name>"` (most restrictive)

#### Lokka (Microsoft 365) Permissions

**Interactive Authentication:**
- Uses your personal Microsoft 365 credentials
- Inherits your existing Graph API permissions
- No additional setup required for personal use

**App-Only Authentication (Production):**
- Requires app registration in Microsoft Entra ID
- Must explicitly grant Microsoft Graph API permissions

**Recommended Graph API Permissions (Read-Only):**

| Permission | Type | Purpose |
|------------|------|---------|
| `User.Read.All` | Application | Read all users |
| `Group.Read.All` | Application | Read all groups |
| `Device.Read.All` | Application | Read all devices |
| `Policy.Read.All` | Application | Read conditional access policies |
| `DeviceManagementConfiguration.Read.All` | Application | Read Intune configurations |
| `AuditLog.Read.All` | Application | Read audit logs |

**⚠️ SECURITY WARNING:**
- **AVOID granting write permissions** (e.g., `.ReadWrite.All`) unless absolutely necessary
- Read-only permissions are sufficient for monitoring and queries
- Write permissions allow the AI to modify your M365 tenant

**To assign permissions:**
1. Go to [Azure Portal](https://portal.azure.com) → Microsoft Entra ID → App registrations
2. Select your app → API permissions → Add a permission → Microsoft Graph
3. Choose "Application permissions" (not Delegated)
4. Search for and add the permissions above
5. Click "Grant admin consent" (requires admin privileges)

---

## Quick Start Guide

Follow these steps to quickly set up all three MCP servers in **Claude Code** (primary method).

**Note**: This guide focuses on Claude Code configuration, which provides native HTTP support, OAuth 2.0, and team-sharable project configurations. For Codex CLI configuration (alternative method), see the [Codex CLI](#codex-cli-alternative-method) section below.

### Prerequisites

- ✅ Run `./install-llm-tools.sh` with Azure OpenAI configured
- ✅ Claude Code and Codex CLI automatically installed and configured
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

### Step 3: Install MCP Server Packages

Install all MCP server packages globally for better performance and offline availability.

```bash
# Install Azure MCP globally
sudo npm install -g @azure/mcp

# Install Lokka globally (for Microsoft 365)
sudo npm install -g @merill/lokka
```

**Why install globally first:**
- Packages are cached and immediately available
- Faster MCP server startup (no download on first use)
- Works better in offline or restricted network environments
- Avoids npm permission issues with npx

**Note**: Microsoft Learn MCP does not require installation as Claude Code supports native HTTP connections to remote MCP servers.

### Step 4: Configure Azure MCP in Claude Code

```bash
claude mcp add --transport stdio azure -- npx -y @azure/mcp@latest server start
```

This enables access to 40+ Azure services directly from Claude Code.

### Step 5: Configure Lokka (Microsoft 365 MCP) in Claude Code

**Interactive authentication (simplest):**
```bash
claude mcp add --transport stdio lokka -- npx -y @merill/lokka
```

When you first use Lokka in Claude Code, it will open your browser to authenticate with Microsoft 365.

**For production use with Entra ID app registration**, see the [App Registration Guide](#app-registration-guide) section below.

### Step 6: Configure Microsoft Learn MCP in Claude Code

**Native HTTP method (recommended):**
```bash
claude mcp add --transport http microsoft-learn https://learn.microsoft.com/api/mcp
```

This provides Claude Code with access to trusted Microsoft documentation using native HTTP transport (no proxy required).

### Step 7: Verify MCP Servers

```bash
claude mcp list
```

You should see all three servers listed:
- `azure`
- `lokka`
- `microsoft-learn`

You can also check server status and authentication within Claude Code using the `/mcp` command in the interactive interface.

### Step 8: Test in Claude Code and Learn Effective Prompting

```bash
# Load Azure environment variables (if not already loaded)
source ~/.profile

# Start Claude Code
claude
```

Now you can interact with Claude Code using natural language prompts and **@ mentions** to leverage your configured MCP servers. Here's how to effectively use each server:

#### Azure MCP Server Prompting

Use natural language to query your Azure infrastructure. Be specific with resource names when you know them.

**Example Prompts:**
```
"What indexes do I have in my Azure AI Search service 'mysvc'?"
"List all websites in my subscription"
"Show me the storage containers in resource group 'my-rg'"
"What is the status of my App Services?"
"List the databases in my subscription"
```

**Tips:**
- Frame as direct questions about your infrastructure
- Be specific with resource names for faster results
- Queries respect your Azure RBAC permissions (Reader, Security Reader)

#### Lokka (Microsoft 365) MCP Prompting

Use conversational language for Microsoft 365 administrative queries.

**Example Prompts:**
```
"Find all conditional access policies in my tenant"
"Show me users in the Marketing security group"
"List all Intune device compliance policies"
"What are the current license assignments?"
"Show me inactive user accounts"
```

**Tips:**
- Use natural, conversational language
- Queries respect your Microsoft Graph API permissions
- Great for security, compliance, and administrative tasks

#### Microsoft Learn MCP Prompting

Request current Microsoft documentation and best practices.

**Example Prompts:**
```
"How do I create an Azure storage account using az cli?"
"Show me the latest documentation on Azure Functions"
"What's the best practice for configuring Azure App Service?"
"Explain Azure Key Vault secret management"
"How do I set up Azure Application Insights?"
```

**Tips:**
- Mention specific Microsoft products/services in your query
- Request "current" or "detailed" information beyond training data
- Useful for setup guides, configuration, and best practices

#### General Best Practices

- **Be explicit and specific**: Clear requests get better responses
- **Use natural language**: Conversational queries work better than technical syntax
- **Use @ mentions**: Reference MCP resources directly (e.g., `@azure:resource-groups`, `@lokka:users`)
- **One task per prompt**: Focus on a single primary action
- **Iterate if needed**: Refine prompts based on initial responses
- **Check server status**: Use `claude mcp list` or `/mcp` command to verify enabled servers

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

# Check server details and status
claude mcp get azure
claude mcp get lokka
claude mcp get microsoft-learn

# Check authentication status in Claude Code
# Use /mcp command in the interactive interface
```

**Environment variables not loaded:**
```bash
# Verify variables exist
grep AZURE ~/.profile

# Reload profile
source ~/.profile
```

### Alternative: Using Codex CLI

If you prefer using Codex CLI instead of Claude Code, follow the same steps but use `codex mcp add` instead of `claude mcp add`. Note that Codex CLI:
- Uses TOML configuration instead of JSON
- Requires `mcp-remote` proxy for Microsoft Learn MCP (no native HTTP support)
- Stores configuration in `~/.codex/config.toml` instead of `.mcp.json`
- Does not support OAuth 2.0 or team-sharable project configurations

See the [Codex CLI](#codex-cli-alternative-method) section for detailed instructions.

---

## Table of Contents

- [Prerequisites and Permissions](#prerequisites-and-permissions)
- [Quick Start Guide](#quick-start-guide)
- [Claude Code (Primary Method)](#claude-code-primary-method)
  - [Overview](#claude-code-overview)
  - [Installation](#claude-code-installation)
  - [MCP Configuration](#claude-code-mcp-configuration)
  - [Configuration File Structure](#claude-code-configuration-file-structure)
  - [VS Code Extension](#claude-code-vs-code-extension)
- [Codex CLI (Alternative Method)](#codex-cli-alternative-method)
  - [Overview](#codex-overview)
  - [Installation](#codex-installation)
  - [Configuration](#codex-configuration)
  - [VS Code Extension](#codex-vs-code-extension)
- [Configuring MCP Servers](#configuring-mcp-servers)
  - [Claude Code Configuration Methods](#claude-code-configuration-methods)
  - [Codex CLI Configuration Methods](#codex-cli-configuration-methods)
- [Azure MCP Server](#azure-mcp-server)
  - [Overview](#azure-mcp-overview)
  - [Installation](#azure-mcp-installation)
  - [Authentication](#azure-mcp-authentication)
  - [Claude Code Configuration](#azure-mcp-claude-code-configuration)
  - [Codex CLI Configuration](#azure-mcp-codex-configuration)
- [Lokka (Microsoft 365 MCP)](#lokka-microsoft-365-mcp)
  - [Overview](#lokka-overview)
  - [Authentication Methods](#lokka-authentication-methods)
  - [Claude Code Configuration](#lokka-claude-code-configuration)
  - [Codex CLI Configuration](#lokka-codex-configuration)
  - [App Registration Guide](#app-registration-guide)
- [Microsoft Learn MCP](#microsoft-learn-mcp)
  - [Overview](#microsoft-learn-overview)
  - [Claude Code Configuration](#microsoft-learn-claude-code-configuration)
  - [Codex CLI Configuration](#microsoft-learn-codex-configuration)
- [Testing and Verification](#testing-and-verification)
- [Integration with llm-linux-setup](#integration-with-llm-linux-setup)

---

## Claude Code (Primary Method)

### Overview {#claude-code-overview}

**Claude Code** is Anthropic's official AI coding agent that runs locally in your terminal. It provides advanced terminal-based AI coding assistance with native Model Context Protocol (MCP) support.

**Key Features:**
- Native HTTP and STDIO MCP transport support
- OAuth 2.0 authentication for third-party services
- Team-sharable project configurations (`.mcp.json`)
- Environment variable expansion with defaults (`${VAR:-default}`)
- @ mentions for MCP resources
- Cross-platform support (macOS, Windows, Linux)
- Enterprise-ready with managed configuration policies

**Official Documentation:**
- **Main Site**: https://code.claude.com/
- **MCP Integration**: https://code.claude.com/docs/en/mcp

**Prerequisites:**
- Node.js 20+ (already ensured by llm-linux-setup)
- Minimum: 4GB RAM (8GB recommended)
- Platform: macOS 12+, Windows 11+, Ubuntu 20.04+, Debian 10+

### Installation {#claude-code-installation}

Claude Code is **automatically installed** by llm-linux-setup when Azure OpenAI is configured.

**Installation method:**
- Installed via npm in Phase 7: `npm install -g @anthropic-ai/claude-code`
- Uses Azure OpenAI credentials from `~/.profile`

**Verify installation:**
```bash
claude --version
```

### MCP Configuration {#claude-code-mcp-configuration}

Claude Code supports multiple MCP server configuration approaches with superior flexibility compared to alternatives.

**CLI Commands (Recommended):**

```bash
# STDIO servers (local processes)
claude mcp add --transport stdio <name> --env KEY=value -- <command> [args]

# HTTP servers (remote endpoints)
claude mcp add --transport http <name> <url>

# HTTP with authentication
claude mcp add --transport http <name> <url> --header "Authorization: Bearer token"

# Server management
claude mcp list              # List all configured servers
claude mcp get <name>        # Get server details
claude mcp remove <name>     # Remove a server
```

**Example - Azure MCP Server:**
```bash
claude mcp add --transport stdio azure -- npx -y @azure/mcp@latest server start
```

**Example - Lokka with environment variables:**
```bash
claude mcp add --transport stdio lokka \
  --env TENANT_ID=your-tenant-id \
  --env CLIENT_ID=your-client-id \
  --env CLIENT_SECRET=your-client-secret \
  -- npx -y @merill/lokka
```

**Example - Microsoft Learn MCP (native HTTP):**
```bash
claude mcp add --transport http microsoft-learn https://learn.microsoft.com/api/mcp
```

**Scope System:**
- **Local**: User-specific, private configuration (default)
- **Project**: `.mcp.json` at repository root, version-controlled, team-shared
- **User**: Cross-project availability on your machine
- **Enterprise**: System-level `managed-mcp.json` with policy controls

**In-App Management:**
- Use `/mcp` command in Claude Code to check status and manage authentication
- OAuth 2.0 flows handled automatically for supported services

### Configuration File Structure {#claude-code-configuration-file-structure}

Claude Code uses JSON configuration (`.mcp.json`) with support for environment variable expansion.

**Basic Structure:**

```json
{
  "mcpServers": {
    "server-name": {
      "type": "stdio|http|sse",
      "command": "executable-path",
      "args": ["arg1", "arg2"],
      "env": {
        "VAR_NAME": "value",
        "API_KEY": "${API_KEY}",
        "BASE_URL": "${API_BASE:-https://default.com}"
      }
    }
  }
}
```

**STDIO Server Example (Azure MCP):**

```json
{
  "mcpServers": {
    "azure": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@azure/mcp@latest", "server", "start"]
    }
  }
}
```

**HTTP Server Example (Microsoft Learn MCP):**

```json
{
  "mcpServers": {
    "microsoft-learn": {
      "type": "http",
      "url": "https://learn.microsoft.com/api/mcp"
    }
  }
}
```

**Environment Variable Expansion:**
- `${VAR}` - Uses environment variable value
- `${VAR:-default}` - Falls back to default if unset
- Supported in: `command`, `args`, `env`, `url`, `headers`

**Complete Example (All Three Microsoft MCP Servers):**

```json
{
  "mcpServers": {
    "azure": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@azure/mcp@latest", "server", "start"]
    },
    "lokka": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@merill/lokka"],
      "env": {
        "TENANT_ID": "${TENANT_ID}",
        "CLIENT_ID": "${CLIENT_ID}",
        "CLIENT_SECRET": "${CLIENT_SECRET}"
      }
    },
    "microsoft-learn": {
      "type": "http",
      "url": "https://learn.microsoft.com/api/mcp"
    }
  }
}
```

**Windows-Specific Note:**
STDIO servers on Windows require `cmd /c` wrapper:
```bash
claude mcp add --transport stdio my-server -- cmd /c npx -y @some/package
```

### VS Code Extension {#claude-code-vs-code-extension}

Claude Code is also available as a **Visual Studio Code extension** (note: this is the same extension as Codex).

**Installation:**
1. Open VS Code
2. Go to Extensions (Ctrl+Shift+X or Cmd+Shift+X)
3. Search for "Claude Code" or "Codex"
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
- MCP integration (if configured)

**Pro Tip:** Create git checkpoints before and after each Claude Code task to easily revert if needed.

---

## Codex CLI (Alternative Method)

### Overview {#codex-overview}

**Codex CLI** is an open-source AI coding agent built by OpenAI that runs locally in your terminal. It provides terminal-based AI coding assistance with support for Azure OpenAI.

**Note**: This section describes Codex CLI as an alternative to Claude Code for users preferring TOML-based configuration.

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

### Installation {#codex-installation}

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

### Configuration {#codex-configuration}

When Azure OpenAI is configured, llm-linux-setup automatically creates `~/.codex/config.toml`:

```toml
[model]
name = "gpt-5.1-codex"
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

### VS Code Extension {#codex-vs-code-extension}

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


## Configuring MCP Servers

The Model Context Protocol (MCP) allows AI coding agents to connect to external tools and services for enhanced capabilities.

This section covers MCP configuration for both Claude Code (primary) and Codex CLI (alternative).

### Claude Code Configuration Methods {#claude-code-configuration-methods}

**Claude Code** provides superior MCP configuration with native HTTP support, OAuth 2.0, and team-sharable configurations.

**Official Documentation**: https://code.claude.com/docs/en/mcp

#### Configuration Approaches

Claude Code supports three configuration approaches:

**1. CLI Commands (Recommended)**

Use `claude mcp add` commands for streamlined setup:

```bash
# STDIO servers
claude mcp add --transport stdio <name> --env KEY=value -- <command> [args]

# HTTP servers
claude mcp add --transport http <name> <url>

# HTTP with authentication
claude mcp add --transport http <name> <url> --header "Authorization: Bearer token"
```

**2. Manual .mcp.json Editing**

Create or edit `.mcp.json` in your project root for team-shared configuration:

```json
{
  "mcpServers": {
    "server-name": {
      "type": "stdio|http",
      "command": "path/to/command",
      "args": ["arg1", "arg2"],
      "env": {
        "VAR": "${VAR:-default}"
      }
    }
  }
}
```

**3. In-App Management**

Use `/mcp` command within Claude Code to:
- Check server status
- Manage OAuth authentication
- Troubleshoot connection issues

#### Key Features

- **Native HTTP support**: No proxy needed for remote MCP servers
- **Environment variable expansion**: `${VAR:-default}` syntax
- **OAuth 2.0**: Built-in authentication for supported services
- **Scope system**: Local, project, user, and enterprise configurations
- **Team sharing**: `.mcp.json` can be version-controlled

For detailed configuration examples, see the [Claude Code](#claude-code-primary-method) section above.

---

### Codex CLI Configuration Methods {#codex-cli-configuration-methods}

**Codex CLI** provides TOML-based MCP configuration as an alternative for users preferring that format.

**Official Documentation**: https://developers.openai.com/codex/mcp/

#### Configuration Approaches

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

### Azure MCP Claude Code Configuration {#azure-mcp-claude-code-configuration}

**Recommended**: Use Claude Code for Azure MCP configuration to benefit from native features and superior developer experience.

#### Option 1: CLI Command (with Azure CLI auth)

```bash
claude mcp add --transport stdio azure -- npx -y @azure/mcp@latest server start
```

This is the simplest method - uses your existing `az login` credentials automatically.

#### Option 2: Manual .mcp.json (with Azure CLI auth)

Create or edit `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "azure": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@azure/mcp@latest", "server", "start"]
    }
  }
}
```

#### Option 3: CLI Command (with Service Principal)

```bash
claude mcp add --transport stdio azure \
  --env AZURE_TENANT_ID=your-tenant-id \
  --env AZURE_CLIENT_ID=your-client-id \
  --env AZURE_CLIENT_SECRET=your-client-secret \
  -- npx -y @azure/mcp@latest server start
```

#### Option 4: Manual .mcp.json (with Service Principal and variable expansion)

```json
{
  "mcpServers": {
    "azure": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@azure/mcp@latest", "server", "start"],
      "env": {
        "AZURE_TENANT_ID": "${AZURE_TENANT_ID}",
        "CLIENT_ID": "${AZURE_CLIENT_ID}",
        "AZURE_CLIENT_SECRET": "${AZURE_CLIENT_SECRET}"
      }
    }
  }
}
```

**Verification:**
```bash
# Test Azure authentication
az account show

# List MCP servers in Claude Code
claude mcp list

# Get server details
claude mcp get azure

# Check status in Claude Code interface
# Use /mcp command
```

---

### Azure MCP Codex Configuration {#azure-mcp-codex-configuration}

**Alternative**: Use Codex CLI if you prefer TOML-based configuration.

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

### Lokka Claude Code Configuration {#lokka-claude-code-configuration}

**Recommended**: Use Claude Code for Lokka configuration to benefit from environment variable expansion and superior developer experience.

#### Option 1: Interactive Auth (Simplest)

**CLI Command:**
```bash
claude mcp add --transport stdio lokka -- npx -y @merill/lokka
```

When you first use Lokka in Claude Code, it will open your browser to authenticate with Microsoft 365.

**Manual .mcp.json:**
```json
{
  "mcpServers": {
    "lokka": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@merill/lokka"]
    }
  }
}
```

#### Option 2: Client Credentials (App-Only)

**CLI Command:**
```bash
claude mcp add --transport stdio lokka \
  --env TENANT_ID=your-tenant-id \
  --env CLIENT_ID=your-client-id \
  --env CLIENT_SECRET=your-client-secret \
  -- npx -y @merill/lokka
```

**Manual .mcp.json (with variable expansion):**
```json
{
  "mcpServers": {
    "lokka": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@merill/lokka"],
      "env": {
        "TENANT_ID": "${TENANT_ID}",
        "CLIENT_ID": "${CLIENT_ID}",
        "CLIENT_SECRET": "${CLIENT_SECRET}"
      }
    }
  }
}
```

#### Option 3: Certificate-Based Auth

**CLI Command:**
```bash
claude mcp add --transport stdio lokka \
  --env TENANT_ID=your-tenant-id \
  --env CLIENT_ID=your-client-id \
  --env CERTIFICATE_PATH=/path/to/cert.pem \
  -- npx -y @merill/lokka
```

**Manual .mcp.json (with variable expansion):**
```json
{
  "mcpServers": {
    "lokka": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@merill/lokka"],
      "env": {
        "TENANT_ID": "${TENANT_ID}",
        "CLIENT_ID": "${CLIENT_ID}",
        "CERTIFICATE_PATH": "${CERTIFICATE_PATH:-/path/to/cert.pem}"
      }
    }
  }
}
```

#### Option 4: Token-Based Auth

**CLI Command:**
```bash
claude mcp add --transport stdio lokka \
  --env USE_CLIENT_TOKEN=true \
  -- npx -y @merill/lokka
```

**Manual .mcp.json:**
```json
{
  "mcpServers": {
    "lokka": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@merill/lokka"],
      "env": {
        "USE_CLIENT_TOKEN": "true"
      }
    }
  }
}
```

**Verification:**
```bash
# List MCP servers
claude mcp list

# Get server details
claude mcp get lokka

# Check authentication status in Claude Code
# Use /mcp command to manage OAuth if needed
```

---

### Lokka Codex Configuration {#lokka-codex-configuration}

**Alternative**: Use Codex CLI if you prefer TOML-based configuration.

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

### Microsoft Learn Claude Code Configuration {#microsoft-learn-claude-code-configuration}

**Recommended**: Claude Code provides **native HTTP support**, eliminating the need for proxy tools.

#### Native HTTP Method (Recommended)

**CLI Command:**
```bash
claude mcp add --transport http microsoft-learn https://learn.microsoft.com/api/mcp
```

This is the simplest and most efficient method - no proxy required!

**Manual .mcp.json:**
```json
{
  "mcpServers": {
    "microsoft-learn": {
      "type": "http",
      "url": "https://learn.microsoft.com/api/mcp"
    }
  }
}
```

#### Alternative: STDIO via mcp-remote Proxy

If you prefer using the mcp-remote proxy (not recommended):

**CLI Command:**
```bash
claude mcp add --transport stdio microsoft-learn -- npx -y mcp-remote https://learn.microsoft.com/api/mcp
```

**Manual .mcp.json:**
```json
{
  "mcpServers": {
    "microsoft-learn": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://learn.microsoft.com/api/mcp"]
    }
  }
}
```

**Verification:**
```bash
# List MCP servers
claude mcp list

# Get server details
claude mcp get microsoft-learn

# The endpoint is designed for MCP clients using Streamable HTTP
# Direct browser access will return 405 Method Not Allowed
```

**Why Native HTTP is Better:**
- ✅ No additional dependencies (no mcp-remote needed)
- ✅ Direct connection to remote server
- ✅ Faster and more reliable
- ✅ Simpler configuration

---

### Microsoft Learn Codex Configuration {#microsoft-learn-codex-configuration}

**Alternative**: Codex CLI requires either manual TOML editing or the mcp-remote proxy since it has limited native HTTP support.

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

### Claude Code Testing (Primary)

**List configured servers:**
```bash
claude mcp list
```

**Get server details:**
```bash
claude mcp get <server-name>

# Examples:
claude mcp get azure
claude mcp get lokka
claude mcp get microsoft-learn
```

**Check status in Claude Code interface:**
- Launch Claude Code: `claude`
- Use `/mcp` command to check server status and authentication
- Use @ mentions to test MCP resources: `@azure:resource-groups`, `@lokka:users`

**Test individual components:**
```bash
# Test Azure MCP directly
npx -y @azure/mcp@latest server start

# Test Lokka directly
npx -y @merill/lokka
```

**Example Claude Code prompts:**
```bash
claude

# In Claude Code:
> List my Azure resource groups
> @azure:subscriptions
> Find all conditional access policies in my M365 tenant
> @microsoft-learn:azure-functions
```

---

### Codex CLI Testing (Alternative)

**List configured servers:**
```bash
codex mcp list
```

**Test server startup (if supported):**
```bash
codex mcp test <server-name>
```

**Check configuration:**
```bash
cat ~/.codex/config.toml
```

**Example Codex prompts:**
```bash
codex

# Example prompts:
# "List my Azure resource groups" (Azure MCP)
# "Show my Microsoft 365 users" (Lokka)
# "Find documentation on Azure Functions" (Microsoft Learn MCP)
```

---

### Verify Azure Authentication

This applies to both Claude Code and Codex CLI:

```bash
# Check Azure login status
az account show

# List available Azure resources (test Azure MCP access)
az account list
```

---

## Integration with llm-linux-setup

### Automatic Claude Code and Codex CLI Configuration

When Azure OpenAI is configured in llm-linux-setup, the installation script automatically:

1. **Installs Claude Code** via npm (Phase 7): `npm install -g @anthropic-ai/claude-code`
2. **Installs Codex CLI** via npm (Phase 7): `npm install -g @openai/codex`
3. **Creates `~/.codex/config.toml`** with Azure OpenAI settings (Codex only)
4. **Exports environment variables** to `~/.profile`:
   - `AZURE_OPENAI_API_KEY`
   - `AZURE_RESOURCE_NAME`

**Note**: Claude Code and Codex CLI are both automatically installed when you run `./install-llm-tools.sh` with Azure OpenAI configured. No manual installation is required.

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
- **Claude Code**: Uses Azure OpenAI credentials from `~/.profile` environment variables
- **Codex CLI**: Uses `~/.codex/config.toml` + environment variables
- **All tools** share the same Azure OpenAI credentials (managed via `llm keys`)

### MCP Server Configuration

MCP server configuration is done post-installation:
- **Claude Code**: Use `claude mcp add` CLI commands or create `.mcp.json` files
- **Codex CLI**: Use `codex mcp add` CLI commands or edit `~/.codex/config.toml`

See the respective configuration sections above for detailed instructions.

---

## Summary

This guide covered:

- ✅ **Claude Code**: Primary MCP method with native HTTP support, OAuth 2.0, and team-sharable configurations
- ✅ **Codex CLI**: Alternative TOML-based configuration for users preferring that approach
- ✅ **Automatic Installation**: Both tools installed by llm-linux-setup
- ✅ **Shared Credentials**: Both use Azure OpenAI credentials from `~/.profile`
- ✅ **VS Code Extension**: IDE-integrated AI coding assistance (shared between Claude Code and Codex)
- ✅ **MCP Configuration**: CLI commands and manual file editing (Claude Code JSON, Codex TOML)
- ✅ **Azure MCP Server**: 40+ Azure services integration with authentication
- ✅ **Lokka**: Microsoft 365 management with multiple auth methods
- ✅ **Microsoft Learn MCP**: Access to trusted Microsoft documentation
- ✅ **Integration**: Seamless integration with llm-linux-setup

**Next Steps:**

**For Claude Code (Recommended):**
1. Verify installation: `claude --version`
2. Load environment: `source ~/.profile`
3. Authenticate to Azure: `az login`
4. Configure MCP servers:
   - Azure MCP: `claude mcp add --transport stdio azure -- npx -y @azure/mcp@latest server start`
   - Lokka: `claude mcp add --transport stdio lokka -- npx -y @merill/lokka`
   - Microsoft Learn: `claude mcp add --transport http microsoft-learn https://learn.microsoft.com/api/mcp`
5. Verify: `claude mcp list`
6. Test: `claude` and use @ mentions for MCP resources (`@azure:subscriptions`, `@lokka:users`)

**For Codex CLI (Alternative):**
1. Verify installation: `codex --version`
2. Load environment: `source ~/.profile`
3. Authenticate to Azure: `az login`
4. Configure MCP servers: `codex mcp add <server> -- <command>` (see Codex CLI sections for examples)
5. Verify: `codex mcp list`
6. Test: `codex` and use natural language queries

**Key Advantages of Claude Code:**
- ✅ Native HTTP support (no proxy needed for Microsoft Learn MCP)
- ✅ OAuth 2.0 authentication for third-party services
- ✅ Team-sharable project configurations (`.mcp.json` in version control)
- ✅ Environment variable expansion with defaults (`${VAR:-default}`)
- ✅ @ mentions for MCP resources
- ✅ `/mcp` command for status and authentication management

**Resources:**
- Claude Code: https://code.claude.com/
- Claude Code MCP Guide: https://code.claude.com/docs/en/mcp
- Codex Quickstart: https://developers.openai.com/codex/quickstart
- Codex MCP Guide: https://developers.openai.com/codex/mcp/
- Azure MCP: https://github.com/microsoft/mcp/tree/main/servers/Azure.Mcp.Server
- Lokka: https://github.com/merill/lokka
- Microsoft Learn MCP: https://github.com/microsoftdocs/mcp

For issues or questions, consult the official documentation or repository issue trackers.
