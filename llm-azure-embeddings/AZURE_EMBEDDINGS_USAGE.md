# Azure OpenAI Embeddings - Usage Guide

This guide explains how to use Azure OpenAI embeddings with the `llm` CLI tool.

## Quick Start

The Azure embeddings plugin has been integrated into the installation script and will be automatically installed when you run `./install-llm-tools.sh`.

## Configuration

### 1. Create Configuration File

Create a YAML configuration file at `~/.config/io.datasette.llm/azure-embeddings-models.yaml`:

```yaml
- model_id: azure/text-embedding-3-small
  model_name: text-embedding-3-small
  api_base: https://YOUR_RESOURCE.openai.azure.com/openai/v1/
  api_key_name: azure
  aliases:
    - azure-embed-small

- model_id: azure/text-embedding-3-large
  model_name: text-embedding-3-large
  api_base: https://YOUR_RESOURCE.openai.azure.com/openai/v1/
  api_key_name: azure
  aliases:
    - azure-embed-large
```

**Configuration Parameters:**
- `model_id` (required): Unique identifier for the model
- `model_name` (required): Your Azure OpenAI deployment name
- `api_base` (required): Your Azure endpoint URL
- `api_key_name` (optional): API key name, default: "azure"
- `dimensions` (optional): Custom dimension size (for supported models)
- `chunk_size` (optional): Maximum tokens per chunk, default: 2000
  - Texts exceeding this limit are automatically split into chunks
  - Each chunk is embedded separately and averaged
  - Lower values = more chunks, higher values = fewer chunks
- `aliases` (optional): Alternative names for convenience

### 2. Set API Key

If you've already configured Azure OpenAI for chat models, the same key will be used:

```bash
llm keys set azure
```

### 3. Verify Installation

Check that the plugin is loaded:

```bash
llm plugins | grep azure
```

List available embedding models:

```bash
llm embed-models
```

You should see your Azure models listed (e.g., `azure/text-embedding-3-small`).

## Usage Examples

### Basic Embedding

Generate an embedding for a single text:

```bash
llm embed -m azure/text-embedding-3-small -c "Hello, world!"
```

Using an alias:

```bash
llm embed -m azure-embed-small -c "Hello, world!"
```

### Batch Embeddings

Embed multiple lines of text:

```bash
echo -e "First line\nSecond line\nThird line" | llm embed-multi azure/text-embedding-3-small -
```

### Collections

Create an embedding collection from files:

```bash
# Create collection from markdown files
llm embed-multi docs \
  --files "*.md" \
  --model azure/text-embedding-3-small \
  --store

# Search the collection
llm similar docs -c "how do I configure embeddings?"
```

### Set Default Model

Set a default embedding model to avoid specifying it each time:

```bash
llm embed-models default azure/text-embedding-3-small

# Now you can omit -m flag
llm embed -c "Test"
```

### Handling Large Texts

The plugin **automatically handles texts that exceed the chunk size limit**:

**How it works:**
- Texts are tokenized using tiktoken (OpenAI's official tokenizer)
- If a text exceeds `chunk_size`, it's split into overlapping chunks (200 token overlap)
- Each chunk is embedded separately
- Chunk embeddings are averaged to produce a single vector

**Example with large text:**
```bash
# This works even if the file has 10,000+ tokens
llm embed -m azure/text-embedding-3-small -c "$(cat large-document.txt)"

# Output:
# Info: Text with 10249 tokens split into 6 chunks
# [embedding vector...]
```

**Configure the chunk size:**
```yaml
- model_id: azure/text-embedding-3-small
  model_name: text-embedding-3-small
  api_base: https://YOUR_RESOURCE.openai.azure.com/openai/v1/
  api_key_name: azure
  chunk_size: 2000  # Adjust as needed (default: 2000)
```

**Choosing the right chunk size:**
- **2000 tokens** (default): Good balance for most use cases
- **Lower (e.g., 1000)**: More chunks, better for very large texts
- **Higher (e.g., 4000)**: Fewer chunks, faster processing, more context per chunk
- **Maximum**: OpenAI models support up to ~8192 tokens, but smaller chunks often work better

## Advanced Configuration

### Custom Dimensions

Some models support custom output dimensions for smaller embeddings:

```yaml
- model_id: azure/text-embedding-3-small-512
  model_name: text-embedding-3-small
  api_base: https://YOUR_RESOURCE.openai.azure.com/openai/v1/
  api_key_name: azure
  dimensions: 512
  aliases:
    - azure-small-512
```

### Multiple Azure Resources

You can configure embeddings from different Azure resources:

```yaml
- model_id: azure-us/text-embedding-3-small
  model_name: text-embedding-3-small
  api_base: https://us-resource.openai.azure.com/openai/v1/
  api_key_name: azure_us

- model_id: azure-eu/text-embedding-3-small
  model_name: text-embedding-3-small
  api_base: https://eu-resource.openai.azure.com/openai/v1/
  api_key_name: azure_eu
```

Then set separate keys:

```bash
llm keys set azure_us
llm keys set azure_eu
```

## Troubleshooting

### Token Limit Errors (FIXED in v0.1.0+)

**Error:**
```
openai.BadRequestError: Error code: 400 - {'error': {'message': "This model's maximum
context length is 8192 tokens, however you requested 10249 tokens..."}}
```

**Solution:**
Update to the latest version of the plugin - it now automatically chunks large texts:

```bash
# Reinstall the plugin
llm install -U /opt/llm-linux-setup/llm-azure-embeddings

# Or reinstall via the installation script
cd /opt/llm-linux-setup
./install-llm-tools.sh
```

After updating:
- Texts exceeding the token limit are automatically split into chunks
- Each chunk is embedded separately
- Embeddings are averaged into a single vector
- You'll see: `Info: Text with 10249 tokens split into 2 chunks`

### Plugin Not Found

If `llm embed-models` doesn't show your Azure models:

1. Verify plugin is installed:
   ```bash
   llm plugins | grep azure-embeddings
   ```

2. Check configuration file exists:
   ```bash
   ls ~/.config/io.datasette.llm/azure-embeddings-models.yaml
   ```

3. Verify YAML syntax (proper indentation, valid format)

### API Key Errors

If you get authentication errors:

```bash
# Check if key is set
llm keys path azure

# Re-set the key
llm keys set azure
```

### Connection Errors

Verify your `api_base` URL format:
- Must end with `/openai/v1/`
- Format: `https://YOUR_RESOURCE.openai.azure.com/openai/v1/`
- Check Azure portal for correct endpoint

### Model Not Found

Make sure `model_name` matches your **Azure deployment name** exactly (not OpenAI's model name).

Check your Azure OpenAI deployments in the Azure portal.

## Comparison: Chat vs Embeddings Configuration

| Feature | Chat Models | Embedding Models |
|---------|-------------|------------------|
| Config File | `extra-openai-models.yaml` | `azure-embeddings-models.yaml` |
| Built-in Support | Yes (via YAML) | Requires plugin |
| Configuration | Automatic | Manual YAML |
| API Key | Same `azure` key | Same `azure` key |
| Endpoint Format | Same | Same |

## Differences from Standard OpenAI

- Uses `api_base` to point to Azure endpoints
- `model_name` refers to Azure deployment name
- Separate API key (configurable via `api_key_name`)

## Example Workflow

```bash
# 1. Ensure plugin is installed (happens automatically via install script)
llm plugins | grep azure-embeddings

# 2. Create config (copy example and edit)
cp /opt/llm-linux-setup/llm-azure-embeddings/azure-embeddings-models.yaml.example \
   ~/.config/io.datasette.llm/azure-embeddings-models.yaml

# 3. Edit with your Azure details
nano ~/.config/io.datasette.llm/azure-embeddings-models.yaml

# 4. Set API key (if not already set)
llm keys set azure

# 5. Test embedding
llm embed -m azure/text-embedding-3-small -c "Test"

# 6. Create a collection
llm embed-multi docs --files "*.md" --model azure/text-embedding-3-small --store

# 7. Search
llm similar docs -c "search query"
```

## Related Documentation

- Main README: `/opt/llm-linux-setup/README.md`
- Plugin README: `/opt/llm-linux-setup/llm-azure-embeddings/README.md`
- Example Config: `/opt/llm-linux-setup/llm-azure-embeddings/azure-embeddings-models.yaml.example`
- Official llm docs: https://llm.datasette.io/en/stable/embeddings/
