# llm-azure-embeddings

LLM plugin for Azure OpenAI embeddings with custom endpoint support.

## Installation

```bash
llm install -e /opt/llm-linux-setup/llm-azure-embeddings
```

Or for development:
```bash
cd llm-azure-embeddings
llm install -e .
```

## Configuration

Create a configuration file at `~/.config/io.datasette.llm/azure-embeddings-models.yaml`:

```yaml
- model_id: azure/text-embedding-3-small
  model_name: text-embedding-3-small
  api_base: https://YOUR_RESOURCE.openai.azure.com/openai/v1/
  api_key_name: azure
  aliases:
    - azure-3-small

- model_id: azure/text-embedding-3-large
  model_name: text-embedding-3-large
  api_base: https://YOUR_RESOURCE.openai.azure.com/openai/v1/
  api_key_name: azure
  aliases:
    - azure-3-large

- model_id: azure/text-embedding-ada-002
  model_name: text-embedding-ada-002
  api_base: https://YOUR_RESOURCE.openai.azure.com/openai/v1/
  api_key_name: azure
  aliases:
    - azure-ada
```

### Configuration Fields

- **model_id** (required): Identifier for the model (e.g., `azure/text-embedding-3-small`)
- **model_name** (required): Name of the deployed model in Azure OpenAI
- **api_base** (required): Your Azure OpenAI endpoint URL
- **api_key_name** (optional): Name of the API key in llm keys (default: `azure`)
- **dimensions** (optional): Dimension size for embeddings (for models that support it)
- **chunk_size** (optional): Maximum tokens per chunk (default: `2000`)
  - Texts exceeding this limit are automatically split into chunks
  - Each chunk is embedded separately and averaged
  - Lower values = more chunks, better for very large texts
  - Higher values = fewer chunks, faster processing
- **aliases** (optional): List of alternative names for the model

## Set API Key

Set your Azure OpenAI API key:

```bash
llm keys set azure
# Enter your Azure OpenAI API key when prompted
```

Or use a different key name if you specified `api_key_name` in the config:

```bash
llm keys set myazurekey
```

## Usage

### List Available Models

```bash
llm embed-models
```

You should see your Azure models listed, e.g.:
- `azure/text-embedding-3-small`
- `azure-3-small` (alias)

### Generate Embeddings

```bash
# Single embedding
llm embed -m azure/text-embedding-3-small -c "Hello world"

# Using an alias
llm embed -m azure-3-small -c "Hello world"

# Embed multiple items
echo -e "Hello\nWorld\nTest" | llm embed-multi azure/text-embedding-3-small -

# Set as default
llm embed-models default azure/text-embedding-3-small
llm embed -c "Hello world"  # Uses default model
```

### Create a Collection

```bash
# Create a collection from files
llm embed-multi files --files "*.txt" --model azure/text-embedding-3-small --store

# Search the collection
llm similar files -c "search query"
```

## How It Works

This plugin:
1. Reads Azure OpenAI embedding configurations from `azure-embeddings-models.yaml`
2. Registers each model with llm's embedding system
3. Uses the OpenAI Python SDK with custom `base_url` for Azure endpoints
4. Automatically handles texts exceeding token limits via chunking
5. Supports all standard llm embedding features (collections, similarity search, etc.)

## Automatic Chunking for Large Texts

The plugin automatically handles texts that exceed the chunk size limit:

**How it works:**
- Texts are tokenized using tiktoken (OpenAI's tokenizer)
- If a text exceeds `chunk_size`, it's split into overlapping chunks
- Each chunk is embedded separately
- The chunk embeddings are averaged to produce a single embedding vector
- Default chunk size: 2000 tokens
- Default chunk overlap: 200 tokens (configurable via code)

**Example:**
```bash
# This works even if the file is 10,000+ tokens
llm embed -m azure/text-embedding-3-small -c "$(cat large-document.txt)"

# You'll see a message like:
# Info: Text with 10249 tokens split into 6 chunks
```

**Configuration:**
```yaml
- model_id: azure/text-embedding-3-small
  model_name: text-embedding-3-small
  api_base: https://YOUR_RESOURCE.openai.azure.com/openai/v1/
  api_key_name: azure
  chunk_size: 2000  # Adjust based on your needs (default: 2000)
```

**Choosing a chunk size:**
- **2000 tokens** (default): Good balance for most use cases
- **Lower (e.g., 1000)**: More chunks, better for very large texts, more granular
- **Higher (e.g., 4000)**: Fewer chunks, faster processing, more context per chunk
- **Note**: OpenAI models have a maximum of ~8192 tokens, but smaller chunks often work better

## Differences from Standard OpenAI

- Uses `api_base` parameter to point to Azure OpenAI endpoints
- Uses separate API key (configured via `api_key_name`)
- `model_name` refers to your Azure deployment name, not OpenAI's model names

## Example Workflow

```bash
# 1. Install plugin
llm install -e /opt/llm-linux-setup/llm-azure-embeddings

# 2. Create config file
cat > ~/.config/io.datasette.llm/azure-embeddings-models.yaml <<EOF
- model_id: azure/text-embedding-3-small
  model_name: text-embedding-3-small
  api_base: https://myresource.openai.azure.com/openai/v1/
  api_key_name: azure
EOF

# 3. Set API key
llm keys set azure

# 4. Test it
llm embed -m azure/text-embedding-3-small -c "Test embedding"

# 5. Create a collection
llm embed-multi docs \
  --files "*.md" \
  --model azure/text-embedding-3-small \
  --store

# 6. Search the collection
llm similar docs -c "how do I configure embeddings?"
```

## Troubleshooting

### Model not found

Make sure:
1. Plugin is installed: `llm plugins | grep azure`
2. Config file exists: `ls ~/.config/io.datasette.llm/azure-embeddings-models.yaml`
3. Config is valid YAML (check indentation)

### Token limit errors (FIXED in v0.1.0+)

**Error:** `This model's maximum context length is 8192 tokens, however you requested 10249 tokens`

**Solution:** Update to the latest version of the plugin. It now automatically chunks large texts:
```bash
# Reinstall the plugin
llm install -U /opt/llm-linux-setup/llm-azure-embeddings
```

The plugin will now automatically:
- Split texts exceeding the token limit into chunks
- Embed each chunk separately
- Average the embeddings into a single vector
- Display info about chunking: `Info: Text with 10249 tokens split into 2 chunks`

### API key errors

```bash
# Check if key is set
llm keys path azure

# Re-set the key
llm keys set azure
```

### Connection errors

Verify your `api_base` URL:
- Should end with `/openai/v1/`
- Format: `https://YOUR_RESOURCE.openai.azure.com/openai/v1/`
- Check Azure portal for correct endpoint

## License

Apache-2.0
