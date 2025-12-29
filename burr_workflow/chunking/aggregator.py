"""
Result aggregation strategies for chunked processing.

Provides two V1 strategies:
- MergeStructuredAggregator: Deep merge JSON objects/arrays
- ConcatenateAggregator: Join text with separator
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocols import LLMClient


class Aggregator(ABC):
    """Base class for result aggregation strategies."""

    @abstractmethod
    async def aggregate(
        self,
        results: list[Any],
        llm_client: Optional["LLMClient"] = None,
    ) -> Any:
        """Aggregate multiple chunk results into one.

        Args:
            results: List of results from each chunk
            llm_client: Optional LLM client for AI-assisted aggregation

        Returns:
            Aggregated result
        """
        pass


class MergeStructuredAggregator(Aggregator):
    """Deep merge JSON objects and arrays.

    For objects: Merges keys, with later chunks overwriting earlier.
    For arrays: Concatenates and deduplicates.

    Useful for llm/extract where each chunk extracts structured data.

    Example:
        chunk1 = {"ports": [22, 80], "services": {"ssh": "OpenSSH"}}
        chunk2 = {"ports": [443], "services": {"http": "nginx"}}
        merged = {"ports": [22, 80, 443], "services": {"ssh": "OpenSSH", "http": "nginx"}}
    """

    def __init__(self, deduplicate_arrays: bool = True):
        self.deduplicate_arrays = deduplicate_arrays

    async def aggregate(
        self,
        results: list[Any],
        llm_client: Optional["LLMClient"] = None,
    ) -> Any:
        """Merge structured results."""
        if not results:
            return {}

        # Filter out None/empty results
        valid_results = [r for r in results if r]
        if not valid_results:
            return {}

        # If single result, return as-is
        if len(valid_results) == 1:
            return valid_results[0]

        # Determine merge strategy based on first result type
        first = valid_results[0]

        if isinstance(first, dict):
            return self._merge_dicts(valid_results)
        elif isinstance(first, list):
            return self._merge_lists(valid_results)
        else:
            # For scalars, return the last non-empty value
            return valid_results[-1]

    def _merge_dicts(self, dicts: list[dict]) -> dict:
        """Deep merge dictionaries."""
        result: dict[str, Any] = {}

        for d in dicts:
            if not isinstance(d, dict):
                continue
            for key, value in d.items():
                if key in result:
                    existing = result[key]
                    if isinstance(existing, dict) and isinstance(value, dict):
                        result[key] = self._merge_dicts([existing, value])
                    elif isinstance(existing, list) and isinstance(value, list):
                        result[key] = self._merge_lists([existing, value])
                    else:
                        # Overwrite with newer value
                        result[key] = value
                else:
                    result[key] = value

        return result

    def _merge_lists(self, lists: list[list]) -> list:
        """Merge and optionally deduplicate lists."""
        combined: list[Any] = []
        seen: set[Any] = set()

        for lst in lists:
            if not isinstance(lst, list):
                continue
            for item in lst:
                # Try to deduplicate hashable items
                if self.deduplicate_arrays:
                    try:
                        if item in seen:
                            continue
                        seen.add(item)
                    except TypeError:
                        # Unhashable (e.g., dict) - always include
                        pass
                combined.append(item)

        return combined


class ConcatenateAggregator(Aggregator):
    """Join text results with a separator.

    Useful for llm/generate where each chunk produces text.

    Args:
        separator: String to join chunks with (default: "\\n\\n")
        strip_chunks: Whether to strip whitespace from each chunk
    """

    def __init__(
        self,
        separator: str = "\n\n",
        strip_chunks: bool = True,
    ):
        self.separator = separator
        self.strip_chunks = strip_chunks

    async def aggregate(
        self,
        results: list[Any],
        llm_client: Optional["LLMClient"] = None,
    ) -> str:
        """Concatenate text results."""
        if not results:
            return ""

        # Convert to strings
        text_parts: list[str] = []
        for r in results:
            if r is None:
                continue
            text = str(r)
            if self.strip_chunks:
                text = text.strip()
            if text:
                text_parts.append(text)

        return self.separator.join(text_parts)


def get_aggregator(
    strategy: str = "concatenate",
    **kwargs: Any,
) -> Aggregator:
    """Factory function to get an aggregator by name.

    Args:
        strategy: "merge_structured" or "concatenate"
        **kwargs: Additional arguments for the aggregator

    Returns:
        Configured Aggregator instance

    Raises:
        ValueError: If strategy is unknown
    """
    if strategy == "merge_structured":
        return MergeStructuredAggregator(
            deduplicate_arrays=kwargs.get("deduplicate_arrays", True)
        )
    elif strategy == "concatenate":
        return ConcatenateAggregator(
            separator=kwargs.get("separator", "\n\n"),
            strip_chunks=kwargs.get("strip_chunks", True),
        )
    else:
        raise ValueError(f"Unknown aggregation strategy: {strategy}")
