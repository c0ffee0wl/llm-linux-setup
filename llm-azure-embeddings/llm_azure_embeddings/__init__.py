"""
LLM plugin for Azure OpenAI embeddings with custom endpoints
"""
import llm
import openai
from pathlib import Path
from typing import Iterable, Iterator, List, Union
import yaml
import tiktoken
import sys


@llm.hookimpl
def register_embedding_models(register):
    """Register Azure OpenAI embedding models from YAML configuration"""
    config_path = llm.user_dir() / "azure-embeddings-models.yaml"

    if not config_path.exists():
        # Return early if no config file exists
        return

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)

        if not config:
            return

        # Register each model from the config
        for model_config in config:
            model_id = model_config.get("model_id")
            if not model_id:
                continue

            # Create the model instance
            model = AzureOpenAIEmbeddingModel(
                model_id=model_id,
                model_name=model_config.get("model_name", model_id),
                api_base=model_config.get("api_base"),
                api_key_name=model_config.get("api_key_name", "azure"),
                dimensions=model_config.get("dimensions"),
                chunk_size=model_config.get("chunk_size", 2000),
            )

            # Register with optional aliases
            aliases = model_config.get("aliases", [])
            if aliases:
                register(model, aliases=tuple(aliases))
            else:
                register(model)

    except Exception as e:
        # Log error but don't crash if config is malformed
        import sys
        print(f"Warning: Failed to load Azure embeddings config: {e}", file=sys.stderr)


class AzureOpenAIEmbeddingModel(llm.EmbeddingModel):
    """
    Embedding model for Azure OpenAI with custom endpoint support

    Supports the same parameters as standard OpenAI embeddings but allows
    custom api_base for Azure OpenAI deployments.
    """

    # Default batch size for embeddings
    batch_size = 100

    def __init__(
        self,
        model_id: str,
        model_name: str,
        api_base: str = None,
        api_key_name: str = "azure",
        dimensions: int = None,
        chunk_size: int = 2000,
        chunk_overlap: int = 200,
    ):
        """
        Initialize Azure OpenAI embedding model

        Args:
            model_id: Identifier for the model (e.g., "azure/text-embedding-3-small")
            model_name: Name of the deployed model in Azure
            api_base: Azure OpenAI endpoint URL
            api_key_name: Name of the API key in llm keys (default: "azure")
            dimensions: Optional dimension size for embeddings
            chunk_size: Maximum tokens per chunk (default: 2000)
            chunk_overlap: Token overlap between chunks (default: 200)
        """
        self.model_id = model_id
        self.model_name = model_name
        self.api_base = api_base
        self.api_key_name = api_key_name
        self.dimensions = dimensions
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # Set needs_key for llm's key management
        self.needs_key = api_key_name
        self.key_env_var = f"{api_key_name.upper()}_API_KEY"

        # Initialize tokenizer (cl100k_base is used by text-embedding-3-* models)
        try:
            self.encoding = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            print(f"Warning: Failed to load tokenizer: {e}", file=sys.stderr)
            self.encoding = None

    def count_tokens(self, text: str) -> int:
        """
        Count the number of tokens in a text string

        Args:
            text: The text to count tokens for

        Returns:
            Number of tokens in the text
        """
        if self.encoding is None:
            # Fallback: rough estimate (4 chars per token)
            return len(text) // 4

        return len(self.encoding.encode(text))

    def chunk_text(self, text: str) -> List[str]:
        """
        Split text into chunks that fit within chunk_size limit

        Args:
            text: The text to chunk

        Returns:
            List of text chunks
        """
        if self.encoding is None:
            # Fallback: character-based chunking
            chunk_chars = self.chunk_size * 4  # Rough estimate
            overlap_size = self.chunk_overlap * 4
            chunks = []
            start = 0
            while start < len(text):
                end = start + chunk_chars
                chunks.append(text[start:end])
                start = end - overlap_size
            return chunks if chunks else [text]

        # Tokenize the text
        tokens = self.encoding.encode(text)

        # If it fits, return as-is
        if len(tokens) <= self.chunk_size:
            return [text]

        # Split into overlapping chunks
        chunks = []
        start_idx = 0

        while start_idx < len(tokens):
            # Get chunk of tokens
            end_idx = min(start_idx + self.chunk_size, len(tokens))
            chunk_tokens = tokens[start_idx:end_idx]

            # Decode back to text
            chunk_text = self.encoding.decode(chunk_tokens)
            chunks.append(chunk_text)

            # Move to next chunk with overlap
            if end_idx >= len(tokens):
                break
            start_idx = end_idx - self.chunk_overlap

        return chunks

    def average_embeddings(self, embeddings: List[List[float]]) -> List[float]:
        """
        Average multiple embeddings into a single embedding

        Args:
            embeddings: List of embedding vectors

        Returns:
            Averaged embedding vector
        """
        if not embeddings:
            return []

        if len(embeddings) == 1:
            return embeddings[0]

        # Calculate component-wise average
        num_embeddings = len(embeddings)
        embedding_dim = len(embeddings[0])

        averaged = [0.0] * embedding_dim
        for embedding in embeddings:
            for i, value in enumerate(embedding):
                averaged[i] += value / num_embeddings

        return averaged

    def embed_batch(self, items: Iterable[Union[str, bytes]]) -> Iterator[List[float]]:
        """
        Generate embeddings for a batch of items

        Automatically chunks items that exceed chunk_size and averages their embeddings.

        Args:
            items: Iterable of strings or bytes to embed

        Returns:
            Iterator over lists of floating point embedding vectors
        """
        # Get API key
        api_key = self.get_key()

        # Build kwargs for OpenAI client
        client_kwargs = {
            "api_key": api_key,
        }

        # Add custom endpoint if specified
        if self.api_base:
            client_kwargs["base_url"] = self.api_base

        # Create OpenAI client
        client = openai.OpenAI(**client_kwargs)

        # Process each item
        results = []
        for item in items:
            # Convert bytes to string if needed
            text = item.decode("utf-8") if isinstance(item, bytes) else item

            # Count tokens and chunk if necessary
            token_count = self.count_tokens(text)

            if token_count <= self.chunk_size:
                # Item fits within limit - process normally
                chunk_texts = [text]
            else:
                # Item exceeds limit - chunk it
                chunk_texts = self.chunk_text(text)
                print(
                    f"Info: Text with {token_count} tokens split into {len(chunk_texts)} chunks",
                    file=sys.stderr,
                )

            # Generate embeddings for each chunk
            chunk_embeddings = []
            for chunk in chunk_texts:
                # Build embedding request parameters
                embed_kwargs = {
                    "input": [chunk],
                    "model": self.model_name,
                }

                # Add dimensions if specified
                if self.dimensions:
                    embed_kwargs["dimensions"] = self.dimensions

                # Make API request
                response = client.embeddings.create(**embed_kwargs)
                embedding = [float(v) for v in response.data[0].embedding]
                chunk_embeddings.append(embedding)

            # Average embeddings if we had to chunk
            if len(chunk_embeddings) > 1:
                final_embedding = self.average_embeddings(chunk_embeddings)
            else:
                final_embedding = chunk_embeddings[0]

            results.append(final_embedding)

        # Return iterator over embedding vectors
        return iter(results)
