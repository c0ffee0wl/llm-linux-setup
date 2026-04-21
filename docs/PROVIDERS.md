# Provider Setup

## Choosing a Provider

Azure OpenAI if your org requires compliance, SLAs, or data residency. Google Gemini for personal use (free tier, no credit card).

## Azure OpenAI

Uses Azure-hosted OpenAI models (not direct OpenAI API).

| Aspect | Detail |
|--------|--------|
| Model ID prefix | `azure/` (e.g., `azure/gpt-4.1-mini`, `azure/o4-mini`) |
| API key name | `azure` (not `openai`) |
| API base URL | Your Azure resource (e.g., `https://your-resource.openai.azure.com`) |

### Configuration Files

| File | Purpose |
|------|---------|
| `~/.config/io.datasette.llm/extra-openai-models.yaml` | Azure chat model definitions |
| `~/.config/io.datasette.llm/azure-embeddings-models.yaml` | Azure embedding models |

**Example model definition:**

```yaml
- model_id: azure/gpt-4.1-mini
  model_name: gpt-4.1-mini
  api_base: https://your-resource.openai.azure.com
  api_key_name: azure
```

### Available Models

- `azure/gpt-4.1` - GPT-4.1 (most capable)
- `azure/gpt-4.1-mini` - GPT-4.1 Mini (balanced, **default**)
- `azure/gpt-4.1-nano` - GPT-4.1 Nano (fast, cost-effective)
- `azure/o4-mini` - O4 Mini (advanced reasoning)
- `azure/gpt-5.4-mini`, `azure/gpt-5.4-nano`, `azure/gpt-5.4` - GPT-5.4 models (registration required for `gpt-5.4`)

**Note**: Model IDs shown above are examples from a specific Azure deployment. Your available models depend on your Azure Foundry configuration. Use `llm models` to see your configured models.

### Limitations

**PDF Attachments Not Supported:**
Azure OpenAI models support image attachments but NOT PDF attachments.

**Error you'll see:**

```
Error code: 400 - {'error': {'message': "Invalid Value: 'file'.
This model does not support file content types.", 'type': 'invalid_request_error'}}
```

**Workarounds for PDF attachments:**

1. **For text extraction from PDFs:** Use the `pdf:` fragment

   ```bash
   llm -f pdf:document.pdf "summarize the text"
   ```

2. **For PDF visual analysis:** Use non-Azure models

   ```bash
   llm -m gpt-4o "analyze this PDF" -a document.pdf
   llm -m gemini-2.5-flash "describe" -a poster.pdf
   llm -m claude-sonnet-4.5 "analyze" -a document.pdf
   ```

### When to Use Azure

- **Enterprise/workplace requirements** - Compliance, SLAs, data residency
- **Organizational policies** - Centralized billing, governance
- **Private deployments** - Models hosted in your Azure subscription

## Google Gemini

For personal projects, learning, and hobbyist use.

**Get your API key:**

- Visit [Google AI Studio](https://ai.google.dev/gemini-api/docs/api-key)
- Sign up (free, no credit card required)
- Generate an API key from the dashboard

**Temperature Note:** Gemini supports temperature values from 0 to 2.0, while most models use 0 to 1.0. Be mindful when setting temperature values.

## Switching Providers

```bash
# Switch to Gemini
./install-llm-tools.sh --gemini

# Switch to Azure
./install-llm-tools.sh --azure
```

The script will reconfigure the selected provider.

## Managing Models

**List Available Models**

```bash
# List all models (shows Azure, Gemini, and other configured models)
llm models

# Find default model
llm models | grep -i default

# List Azure models
llm models | grep azure

# List Gemini models
llm models | grep gemini

# Get detailed Gemini model info
llm gemini models
```

**Set Default Model**

```bash
# Set default model for all commands
llm models default azure/gpt-4.1-mini

# Alternative: using environment variable
export LLM_MODEL=azure/gpt-4.1
llm "Your prompt"  # Uses gpt-4.1
```

**Use Specific Models**

```bash
# Override default with -m flag
llm "Ten names for cheesecakes" -m azure/gpt-4.1-mini

# Use different models for different tasks
llm -m azure/gpt-4.1 "Enterprise compliance analysis"
llm -m gemini-2.5-flash "Personal coding question"
llm -m gemini-2.5-flash "Describe this image" -a photo.jpg
```

**Default Model Recommendation:**

`azure/gpt-4.1-mini` is the default. Fast and cheap enough for daily use. Switch to `azure/gpt-4.1` when you need more reasoning horsepower:

```bash
# Switch to gpt-4.1 for complex tasks
llm models default azure/gpt-4.1

# Or use it for a single query with -m flag
llm -m azure/gpt-4.1 "Complex analysis task..."
```

## Model-Specific Parameters

Pass provider-specific parameters with `-o`.

**Gemini and Vertex AI Models**

Google's Gemini models (via `llm-gemini` and `llm-vertex` plugins) support advanced features like code execution:

```bash
# Enable code execution for computational tasks
command llm -m gemini-2.5-flash -o code_execution 1 \
  "write and execute python to calculate fibonacci sequence up to n=20"

# Use Vertex AI with code execution
command llm -m vertex/gemini-2.5-flash -o code_execution 1 \
  "write and execute python to generate a 80x40 ascii art fractal"

# Combine with other options
command llm -m gemini-2.5-flash \
  -o code_execution 1 \
  -o temperature 0.7 \
  "solve this math problem step by step with code"
```

**Code Execution Feature**: Gemini writes Python, runs it in a sandbox, and uses the output in its answer. Good for:
- Mathematical calculations
- Data analysis and visualization
- Algorithm implementation and testing
- Generating dynamic content (ASCII art, graphs, etc.)

**Reasoning Models (o-series)**

OpenAI's reasoning models support effort control:

```bash
# High reasoning effort for complex problems
command llm -m openai/o4-mini \
  -o reasoning_effort high \
  "design a distributed caching system with these requirements..."

# Medium effort for balanced performance
command llm -m azure/o4-mini \
  -o reasoning_effort medium \
  "analyze this algorithm's time complexity"

# Low effort for simpler reasoning tasks
command llm -m openai/o4-mini \
  -o reasoning_effort low \
  "explain this code pattern"
```

**Common Model Options**

Standard options supported across most models:

```bash
# Control randomness (0.0 = deterministic, 2.0 = very creative)
llm -m gemini-2.5-flash -o temperature 0.9 "creative story prompt"

# Limit output length
llm -m azure/gpt-4.1 -o max_tokens 500 "brief summary needed"

# Combine multiple options
llm -m claude-sonnet-4.5 \
  -o temperature 0.3 \
  -o max_tokens 2000 \
  "technical documentation request"
```

**Temperature Range Note**: Gemini models support temperature values from 0 to 2.0, while most other models use 0 to 1.0. Check your model's documentation for valid ranges.

For complete parameter documentation:
- [Gemini Models](https://github.com/simonw/llm-gemini#code-execution)
- [Vertex AI](https://github.com/c0ffee0wl/llm-vertex)
- [OpenAI Models](https://platform.openai.com/docs/api-reference/chat/create)

## Managing API Keys

**Configure Azure OpenAI Key:**

```bash
# Set Azure key interactively
llm keys set azure

# View configured keys
llm keys

# View key storage path
llm keys path
```

**Configure Gemini Key:**

```bash
# Set Gemini key interactively
llm keys set gemini

# Verify Gemini key is working
llm -m gemini-2.5-flash "test prompt"
```

**Get API Keys:**

- **Azure OpenAI**: Obtained from your Azure Foundry portal/deployment
- **Gemini**: Free from [Google AI Studio](https://ai.google.dev/gemini-api/docs/api-key) (no credit card required)
- **OpenAI**: From [OpenAI platform](https://platform.openai.com/api-keys) (requires payment)
- **Anthropic**: From [Anthropic console](https://console.anthropic.com/) (requires payment)
