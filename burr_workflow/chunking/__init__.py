"""
Chunking module for processing large content.

Provides text splitting and result aggregation strategies
for handling content that exceeds LLM context windows.

Usage:
    from burr_workflow.chunking import get_splitter, get_aggregator

    # Split large text
    splitter = get_splitter("line_aware", max_chars=40000)
    chunks = splitter.split(large_log)

    # Aggregate results
    aggregator = get_aggregator("merge_structured")
    merged = await aggregator.aggregate(chunk_results)
"""

from .aggregator import (
    Aggregator,
    ConcatenateAggregator,
    MergeStructuredAggregator,
    get_aggregator,
)
from .splitter import (
    LineAwareSplitter,
    SlidingWindowSplitter,
    TextSplitter,
    get_splitter,
)

__all__ = [
    # Splitters
    "TextSplitter",
    "LineAwareSplitter",
    "SlidingWindowSplitter",
    "get_splitter",
    # Aggregators
    "Aggregator",
    "MergeStructuredAggregator",
    "ConcatenateAggregator",
    "get_aggregator",
]
