"""
Text splitting strategies for chunking large content.

Provides two V1 strategies:
- LineAwareSplitter: Split on line boundaries
- SlidingWindowSplitter: Overlapping character-based chunks
"""

from abc import ABC, abstractmethod


class TextSplitter(ABC):
    """Base class for text splitting strategies."""

    @abstractmethod
    def split(self, text: str) -> list[str]:
        """Split text into chunks.

        Args:
            text: The input text to split

        Returns:
            List of text chunks
        """
        pass


class LineAwareSplitter(TextSplitter):
    """Split text on line boundaries, respecting max_chars.

    Splits at newline characters, accumulating lines until
    max_chars is reached, then starts a new chunk.

    Args:
        max_chars: Maximum characters per chunk (default: 40000)
        overlap_lines: Number of lines to overlap between chunks (default: 2)

    Example:
        splitter = LineAwareSplitter(max_chars=1000)
        chunks = splitter.split(log_content)
    """

    def __init__(
        self,
        max_chars: int = 40000,
        overlap_lines: int = 2,
    ):
        self.max_chars = max_chars
        self.overlap_lines = overlap_lines

    def split(self, text: str) -> list[str]:
        """Split text on line boundaries."""
        if len(text) <= self.max_chars:
            return [text]

        lines = text.splitlines(keepends=True)
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_size = 0

        for line in lines:
            line_size = len(line)

            # If single line exceeds max, force split it
            if line_size > self.max_chars:
                # Flush current chunk first
                if current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_size = 0

                # Split the long line into max_chars pieces
                for i in range(0, line_size, self.max_chars):
                    chunks.append(line[i:i + self.max_chars])
                continue

            # Check if adding this line would exceed limit
            if current_size + line_size > self.max_chars:
                # Save current chunk
                if current_chunk:
                    chunks.append("".join(current_chunk))

                    # Overlap: keep last N lines for context
                    if self.overlap_lines > 0 and len(current_chunk) > self.overlap_lines:
                        overlap = current_chunk[-self.overlap_lines:]
                        current_chunk = overlap.copy()
                        current_size = sum(len(line) for line in current_chunk)
                    else:
                        current_chunk = []
                        current_size = 0

            current_chunk.append(line)
            current_size += line_size

        # Don't forget the last chunk
        if current_chunk:
            chunks.append("".join(current_chunk))

        return chunks


class SlidingWindowSplitter(TextSplitter):
    """Split text using overlapping character windows.

    Creates chunks of max_chars with overlap_chars overlap
    between consecutive chunks.

    Args:
        max_chars: Maximum characters per chunk (default: 40000)
        overlap_chars: Characters to overlap between chunks (default: 500)

    Example:
        splitter = SlidingWindowSplitter(max_chars=4000, overlap_chars=200)
        chunks = splitter.split(document)
    """

    def __init__(
        self,
        max_chars: int = 40000,
        overlap_chars: int = 500,
    ):
        self.max_chars = max_chars
        self.overlap_chars = min(overlap_chars, max_chars // 4)  # Cap at 25%

    def split(self, text: str) -> list[str]:
        """Split text using sliding window."""
        if len(text) <= self.max_chars:
            return [text]

        chunks: list[str] = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + self.max_chars, text_len)

            # Try to break at word boundary (space or newline)
            if end < text_len:
                # Look backward for a good break point
                for break_offset in range(min(100, self.max_chars // 10)):
                    check_pos = end - break_offset
                    if check_pos > start and text[check_pos] in " \n\t":
                        end = check_pos + 1  # Include the whitespace
                        break

            chunks.append(text[start:end])

            # Advance with overlap
            step = self.max_chars - self.overlap_chars
            if step <= 0:
                step = self.max_chars // 2  # Safety: at least half chunk

            start += step

            # Avoid tiny final chunks
            if text_len - start < self.overlap_chars:
                # Extend last chunk to include remaining
                if chunks:
                    chunks[-1] = text[start - step:]
                break

        return chunks


def get_splitter(
    strategy: str = "sliding_window",
    max_chars: int = 40000,
    overlap: int = 500,
) -> TextSplitter:
    """Factory function to get a splitter by name.

    Args:
        strategy: "line_aware" or "sliding_window"
        max_chars: Maximum characters per chunk
        overlap: Overlap amount (lines for line_aware, chars for sliding_window)

    Returns:
        Configured TextSplitter instance

    Raises:
        ValueError: If strategy is unknown
    """
    if strategy == "line_aware":
        return LineAwareSplitter(max_chars=max_chars, overlap_lines=overlap)
    elif strategy == "sliding_window":
        return SlidingWindowSplitter(max_chars=max_chars, overlap_chars=overlap)
    else:
        raise ValueError(f"Unknown chunking strategy: {strategy}")
