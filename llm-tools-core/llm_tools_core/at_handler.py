"""@ reference handler for llm-assistant and llm-guiassistant.

Provides unified @ reference parsing, autocomplete, and resolution.
Supports file paths, URLs, PDFs, YouTube videos, and directories.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class Completion:
    """An autocomplete suggestion."""
    text: str  # Full @ reference to insert
    description: str  # Description shown in dropdown
    type: str  # 'file', 'directory', 'prefix', 'hint'


@dataclass
class ResolvedReference:
    """A resolved @ reference."""
    original: str  # Original @ reference text
    type: str  # 'fragment' or 'attachment'
    content: Optional[str]  # Content for fragments
    path: Optional[str]  # Path for attachments
    loader: Optional[str]  # Loader used (pdf, yt, site, etc.)
    error: Optional[str]  # Error message if resolution failed


class AtHandler:
    """Handle @ references in user input."""

    # Supported prefixes and their descriptions
    PREFIXES: Dict[str, str] = {
        "pdf": "Load PDF file",
        "yt": "Load YouTube transcript",
        "arxiv": "Load arXiv paper",
        "dir": "List directory contents",
        "file": "Load local file",
    }

    # URL prefixes (handled specially)
    URL_PREFIXES = ("http://", "https://")

    # Image extensions for attachment detection
    IMAGE_EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg"
    }

    # Text file extensions that should be loaded as fragments
    TEXT_EXTENSIONS = {
        ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".html", ".css", ".sh", ".bash", ".zsh", ".fish", ".toml",
        ".xml", ".csv", ".sql", ".c", ".h", ".cpp", ".hpp", ".java",
        ".go", ".rs", ".rb", ".php", ".pl", ".lua", ".vim", ".conf",
        ".ini", ".cfg", ".log", ".rst", ".tex", ".r", ".m", ".swift",
    }

    # Regex to match @ references
    # Matches: @prefix:path, @url, @path/to/file, @file.ext
    REFERENCE_PATTERN = re.compile(
        r'@((?:pdf|yt|arxiv|dir|file):(?:[^\s]+)|'  # @prefix:path
        r'https?://[^\s]+|'  # @http(s)://url
        r'[^\s@]+)'  # @path or @file
    )

    def __init__(self, cwd: Optional[str] = None):
        """Initialize handler.

        Args:
            cwd: Current working directory for relative paths (supports ~ expansion)
        """
        self.cwd = Path(cwd).expanduser() if cwd else Path.cwd()

    def parse_references(self, text: str) -> List[str]:
        """Extract @ references from text.

        Args:
            text: User input text

        Returns:
            List of @ references found (without the @)
        """
        return self.REFERENCE_PATTERN.findall(text)

    def strip_references(self, text: str) -> str:
        """Remove @ references from text.

        Args:
            text: User input text

        Returns:
            Text with @ references removed
        """
        return self.REFERENCE_PATTERN.sub('', text).strip()

    def is_image(self, path: str) -> bool:
        """Check if a path points to an image file.

        Args:
            path: File path or name

        Returns:
            True if the file has an image extension
        """
        ext = Path(path).suffix.lower()
        return ext in self.IMAGE_EXTENSIONS

    def is_text_file(self, path: str) -> bool:
        """Check if a path points to a text file.

        Args:
            path: File path or name

        Returns:
            True if the file has a text extension
        """
        ext = Path(path).suffix.lower()
        return ext in self.TEXT_EXTENSIONS

    def get_completions(
        self,
        prefix: str,
        cwd: Optional[str] = None
    ) -> List[Completion]:
        """Get autocomplete suggestions for a partial @ reference.

        Args:
            prefix: The partial reference after @ (e.g., "pdf:", "pdf:re", "/home/")
            cwd: Override current working directory

        Returns:
            List of completion suggestions
        """
        working_dir = Path(cwd).expanduser() if cwd else self.cwd
        completions = []

        # Handle empty prefix - show available prefixes
        if not prefix:
            for name, desc in self.PREFIXES.items():
                completions.append(Completion(
                    text=f"@{name}:",
                    description=desc,
                    type="prefix"
                ))
            completions.append(Completion(
                text="@http://",
                description="Load web page",
                type="prefix"
            ))
            completions.append(Completion(
                text="@https://",
                description="Load web page (HTTPS)",
                type="prefix"
            ))
            return completions

        # Handle prefix completions
        if prefix.endswith(":") or (":" not in prefix and not prefix.startswith(("http://", "https://", "/", ".", "~"))):
            # Check if this is a partial prefix
            partial = prefix.rstrip(":")
            for name, desc in self.PREFIXES.items():
                if name.startswith(partial):
                    completions.append(Completion(
                        text=f"@{name}:",
                        description=desc,
                        type="prefix"
                    ))

            # If exact prefix match with colon, continue to path completion
            if ":" in prefix and prefix.split(":")[0] in self.PREFIXES:
                pass
            elif completions:
                return completions

        # Handle prefix:path patterns
        if ":" in prefix and not prefix.startswith(("http://", "https://")):
            prefix_name, path_part = prefix.split(":", 1)

            if prefix_name == "pdf":
                return self._get_file_completions(path_part, working_dir, filter_ext=".pdf", prefix_format="@pdf:")
            elif prefix_name == "yt":
                # YouTube URLs - just show a hint
                if not path_part:
                    return [Completion(
                        text="@yt:https://youtube.com/watch?v=...",
                        description="Paste YouTube video URL",
                        type="hint"
                    )]
                return []  # No autocomplete for YouTube URLs
            elif prefix_name == "arxiv":
                # arXiv paper IDs - just show a hint
                if not path_part:
                    return [Completion(
                        text="@arxiv:2310.06825",
                        description="Enter arXiv paper ID (e.g., 2310.06825)",
                        type="hint"
                    )]
                return []  # No autocomplete for arXiv IDs
            elif prefix_name == "dir":
                return self._get_directory_completions(path_part, working_dir)
            elif prefix_name == "file":
                return self._get_file_completions(path_part, working_dir, prefix_format="@file:")

        # Handle URL prefixes
        if prefix.startswith(("http://", "https://")):
            # No autocomplete for URLs
            return []

        # Handle file/directory paths
        return self._get_file_completions(prefix, working_dir)

    def _get_file_completions(
        self,
        partial_path: str,
        working_dir: Path,
        filter_ext: Optional[str] = None,
        prefix_format: str = "@"
    ) -> List[Completion]:
        """Get file completions for a partial path.

        Args:
            partial_path: Partial path to complete
            working_dir: Base directory for relative paths
            filter_ext: Optional extension filter (e.g., ".pdf")
            prefix_format: Prefix for completion text (e.g., "@" or "@file:")

        Returns:
            List of file completions
        """
        completions = []

        # Determine the directory to list and the prefix to match
        if not partial_path:
            dir_to_list = working_dir
            name_prefix = ""
        else:
            # Expand ~ in path (e.g., @~/Documents or @~user/file)
            partial = Path(partial_path).expanduser()
            if partial_path.endswith("/"):
                # Use expanded path directly if absolute, else relative to working_dir
                dir_to_list = partial if partial.is_absolute() else working_dir / partial
                name_prefix = ""
            else:
                if partial.is_absolute():
                    dir_to_list = partial.parent
                else:
                    dir_to_list = working_dir / partial.parent if partial.parent != Path(".") else working_dir
                name_prefix = partial.name

        try:
            if not dir_to_list.exists() or not dir_to_list.is_dir():
                return []

            for item in sorted(dir_to_list.iterdir()):
                if name_prefix and not item.name.lower().startswith(name_prefix.lower()):
                    continue

                # Skip hidden files
                if item.name.startswith("."):
                    continue

                # Build the relative path
                rel_path = item.relative_to(working_dir) if item.is_relative_to(working_dir) else item

                if item.is_dir():
                    completions.append(Completion(
                        text=f"{prefix_format}{rel_path}/",
                        description="Directory",
                        type="directory"
                    ))
                elif item.is_file():
                    # Apply extension filter if specified
                    if filter_ext and item.suffix.lower() != filter_ext:
                        continue

                    # Determine file type
                    if self.is_image(item.name):
                        file_type = "Image"
                    elif filter_ext:
                        file_type = filter_ext.upper().lstrip(".")
                    elif self.is_text_file(item.name):
                        file_type = "Text"
                    else:
                        file_type = "File"

                    completions.append(Completion(
                        text=f"{prefix_format}{rel_path}",
                        description=file_type,
                        type="file"
                    ))
        except PermissionError:
            pass

        return completions[:20]  # Limit results

    def _get_directory_completions(
        self,
        partial_path: str,
        working_dir: Path
    ) -> List[Completion]:
        """Get directory completions for @dir: prefix.

        Args:
            partial_path: Partial path to complete
            working_dir: Base directory for relative paths

        Returns:
            List of directory completions
        """
        completions = []

        # Determine the directory to list
        if not partial_path:
            dir_to_list = working_dir
            name_prefix = ""
        else:
            # Expand ~ in path (e.g., @dir:~/Documents)
            partial = Path(partial_path).expanduser()
            if partial_path.endswith("/"):
                # Use expanded path directly if absolute, else relative to working_dir
                dir_to_list = partial if partial.is_absolute() else working_dir / partial
                name_prefix = ""
            else:
                if partial.is_absolute():
                    dir_to_list = partial.parent
                else:
                    dir_to_list = working_dir / partial.parent if partial.parent != Path(".") else working_dir
                name_prefix = partial.name

        try:
            if not dir_to_list.exists() or not dir_to_list.is_dir():
                return []

            for item in sorted(dir_to_list.iterdir()):
                if not item.is_dir():
                    continue
                if name_prefix and not item.name.lower().startswith(name_prefix.lower()):
                    continue
                if item.name.startswith("."):
                    continue

                rel_path = item.relative_to(working_dir) if item.is_relative_to(working_dir) else item
                completions.append(Completion(
                    text=f"@dir:{rel_path}/",
                    description="Directory",
                    type="directory"
                ))
        except PermissionError:
            pass

        return completions[:20]

    def resolve(
        self,
        reference: str,
        cwd: Optional[str] = None
    ) -> ResolvedReference:
        """Resolve a @ reference to its content.

        Args:
            reference: The reference without @ (e.g., "pdf:report.pdf", "file.txt")
            cwd: Override current working directory

        Returns:
            ResolvedReference with content or error
        """
        working_dir = Path(cwd).expanduser() if cwd else self.cwd

        # Handle prefix:path patterns
        if ":" in reference and not reference.startswith(("http://", "https://")):
            prefix_name, path = reference.split(":", 1)

            if prefix_name == "pdf":
                return self._resolve_pdf(path, working_dir)
            elif prefix_name == "yt":
                return self._resolve_youtube(path)
            elif prefix_name == "arxiv":
                return self._resolve_arxiv(path)
            elif prefix_name == "dir":
                return self._resolve_directory(path, working_dir)
            elif prefix_name == "file":
                return self._resolve_file(path, working_dir)
            else:
                return ResolvedReference(
                    original=f"@{reference}",
                    type="fragment",
                    content=None,
                    path=None,
                    loader=None,
                    error=f"Unknown prefix: {prefix_name}"
                )

        # Handle URLs
        if reference.startswith(("http://", "https://")):
            return self._resolve_url(reference)

        # Handle file paths
        return self._resolve_file(reference, working_dir)

    def _resolve_pdf(self, path: str, working_dir: Path) -> ResolvedReference:
        """Resolve a PDF file reference."""
        try:
            # Try to use llm's PDF fragment loader
            from llm.default_plugins.loaders.pdf import load_pdf

            # Expand ~ in path and resolve relative to working_dir
            expanded_path = Path(path).expanduser()
            file_path = expanded_path if expanded_path.is_absolute() else working_dir / path
            if not file_path.exists():
                return ResolvedReference(
                    original=f"@pdf:{path}",
                    type="fragment",
                    content=None,
                    path=str(file_path),
                    loader="pdf",
                    error=f"File not found: {file_path}"
                )

            fragments = list(load_pdf(file_path))
            if fragments:
                content = "\n\n".join(f.content for f in fragments if f.content)
                return ResolvedReference(
                    original=f"@pdf:{path}",
                    type="fragment",
                    content=content,
                    path=str(file_path),
                    loader="pdf",
                    error=None
                )
            else:
                return ResolvedReference(
                    original=f"@pdf:{path}",
                    type="fragment",
                    content=None,
                    path=str(file_path),
                    loader="pdf",
                    error="No content extracted from PDF"
                )
        except ImportError:
            return ResolvedReference(
                original=f"@pdf:{path}",
                type="fragment",
                content=None,
                path=None,
                loader="pdf",
                error="PDF loader not available"
            )
        except Exception as e:
            return ResolvedReference(
                original=f"@pdf:{path}",
                type="fragment",
                content=None,
                path=None,
                loader="pdf",
                error=str(e)
            )

    def _resolve_youtube(self, url: str) -> ResolvedReference:
        """Resolve a YouTube video reference."""
        try:
            # Try to use llm's YouTube fragment loader
            from llm_fragments_youtube_transcript import YouTubeFragmentLoader

            loader = YouTubeFragmentLoader()
            fragments = list(loader.load(url))
            if fragments:
                content = "\n\n".join(f.content for f in fragments if f.content)
                return ResolvedReference(
                    original=f"@yt:{url}",
                    type="fragment",
                    content=content,
                    path=None,
                    loader="yt",
                    error=None
                )
            else:
                return ResolvedReference(
                    original=f"@yt:{url}",
                    type="fragment",
                    content=None,
                    path=None,
                    loader="yt",
                    error="No transcript available"
                )
        except ImportError:
            return ResolvedReference(
                original=f"@yt:{url}",
                type="fragment",
                content=None,
                path=None,
                loader="yt",
                error="YouTube loader not available"
            )
        except Exception as e:
            return ResolvedReference(
                original=f"@yt:{url}",
                type="fragment",
                content=None,
                path=None,
                loader="yt",
                error=str(e)
            )

    def _resolve_arxiv(self, paper_id: str) -> ResolvedReference:
        """Resolve an arXiv paper reference."""
        try:
            # Try to use llm-arxiv fragment loader
            from llm_arxiv import ArxivFragmentLoader

            loader = ArxivFragmentLoader()
            fragments = list(loader.load(paper_id))
            if fragments:
                content = "\n\n".join(f.content for f in fragments if f.content)
                return ResolvedReference(
                    original=f"@arxiv:{paper_id}",
                    type="fragment",
                    content=content,
                    path=None,
                    loader="arxiv",
                    error=None
                )
            else:
                return ResolvedReference(
                    original=f"@arxiv:{paper_id}",
                    type="fragment",
                    content=None,
                    path=None,
                    loader="arxiv",
                    error="No content extracted from arXiv paper"
                )
        except ImportError:
            return ResolvedReference(
                original=f"@arxiv:{paper_id}",
                type="fragment",
                content=None,
                path=None,
                loader="arxiv",
                error="arXiv loader not available (install llm-arxiv)"
            )
        except Exception as e:
            return ResolvedReference(
                original=f"@arxiv:{paper_id}",
                type="fragment",
                content=None,
                path=None,
                loader="arxiv",
                error=str(e)
            )

    def _resolve_directory(self, path: str, working_dir: Path) -> ResolvedReference:
        """Resolve a directory reference to a listing."""
        try:
            # Expand ~ in path
            if path:
                expanded_path = Path(path).expanduser()
                dir_path = expanded_path if expanded_path.is_absolute() else working_dir / path
            else:
                dir_path = working_dir
            if not dir_path.exists():
                return ResolvedReference(
                    original=f"@dir:{path}",
                    type="fragment",
                    content=None,
                    path=str(dir_path),
                    loader="dir",
                    error=f"Directory not found: {dir_path}"
                )

            if not dir_path.is_dir():
                return ResolvedReference(
                    original=f"@dir:{path}",
                    type="fragment",
                    content=None,
                    path=str(dir_path),
                    loader="dir",
                    error=f"Not a directory: {dir_path}"
                )

            # Build directory listing
            items = []
            for item in sorted(dir_path.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    items.append(f"  {item.name}/")
                else:
                    size = item.stat().st_size
                    if size < 1024:
                        size_str = f"{size}B"
                    elif size < 1024 * 1024:
                        size_str = f"{size // 1024}K"
                    else:
                        size_str = f"{size // (1024 * 1024)}M"
                    items.append(f"  {item.name} ({size_str})")

            content = f"Directory: {dir_path}\n\n" + "\n".join(items)
            return ResolvedReference(
                original=f"@dir:{path}",
                type="fragment",
                content=content,
                path=str(dir_path),
                loader="dir",
                error=None
            )
        except Exception as e:
            return ResolvedReference(
                original=f"@dir:{path}",
                type="fragment",
                content=None,
                path=None,
                loader="dir",
                error=str(e)
            )

    def _resolve_url(self, url: str) -> ResolvedReference:
        """Resolve a URL reference."""
        try:
            # Try to use llm's site fragment loader
            from llm.default_plugins.loaders.site import load_site

            fragments = list(load_site(url))
            if fragments:
                content = "\n\n".join(f.content for f in fragments if f.content)
                return ResolvedReference(
                    original=f"@{url}",
                    type="fragment",
                    content=content,
                    path=None,
                    loader="site",
                    error=None
                )
            else:
                return ResolvedReference(
                    original=f"@{url}",
                    type="fragment",
                    content=None,
                    path=None,
                    loader="site",
                    error="No content fetched from URL"
                )
        except ImportError:
            return ResolvedReference(
                original=f"@{url}",
                type="fragment",
                content=None,
                path=None,
                loader="site",
                error="Site loader not available"
            )
        except Exception as e:
            return ResolvedReference(
                original=f"@{url}",
                type="fragment",
                content=None,
                path=None,
                loader="site",
                error=str(e)
            )

    def _resolve_file(self, path: str, working_dir: Path) -> ResolvedReference:
        """Resolve a file reference."""
        # Expand ~ in path
        expanded_path = Path(path).expanduser()
        file_path = expanded_path if expanded_path.is_absolute() else working_dir / path
        if not file_path.exists():
            return ResolvedReference(
                original=f"@{path}",
                type="fragment",
                content=None,
                path=str(file_path),
                loader=None,
                error=f"File not found: {file_path}"
            )

        # Handle images as attachments
        if self.is_image(path):
            return ResolvedReference(
                original=f"@{path}",
                type="attachment",
                content=None,
                path=str(file_path),
                loader=None,
                error=None
            )

        # Handle text files as fragments
        try:
            content = file_path.read_text()
            return ResolvedReference(
                original=f"@{path}",
                type="fragment",
                content=content,
                path=str(file_path),
                loader="file",
                error=None
            )
        except Exception as e:
            return ResolvedReference(
                original=f"@{path}",
                type="fragment",
                content=None,
                path=str(file_path),
                loader="file",
                error=f"Could not read file: {e}"
            )
