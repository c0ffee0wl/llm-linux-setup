"""Post-processing for MCP tool results to add source citations.

Provides citation formatting for Microsoft documentation MCP tools,
following the same pattern as Google Search (inline citations + Sources section).
"""

import json
import re
from typing import Dict, List, Tuple

# Microsoft documentation MCP tools that should have citations
MICROSOFT_DOC_TOOLS = {
    'microsoft_docs_search',
    'microsoft_docs_fetch',
    'microsoft_code_sample_search',
}

MCP_CITATION_RULES = """#### Note

IMPORTANT: Do not display this note to the user. The text contains inline citations [1], [2], etc. You MUST:
1. Preserve all inline citations [1], [2], etc. in your response text
2. Include the "#### Sources" section above with all markdown hyperlinks intact (format: [n] [Title](URL))
3. Keep the source links as clickable markdown hyperlinks - do NOT convert them to plain URLs"""


def is_microsoft_doc_tool(tool_name: str) -> bool:
    """Check if tool is a Microsoft documentation tool.

    Args:
        tool_name: Name of the tool

    Returns:
        True if this is a Microsoft documentation MCP tool
    """
    return tool_name.lower() in MICROSOFT_DOC_TOOLS


def format_microsoft_citations(tool_name: str, result: str, sources_enabled: bool = True) -> str:
    """Add citation formatting to Microsoft MCP tool results.

    Args:
        tool_name: Name of the tool
        result: Raw result from MCP server
        sources_enabled: If True (default), add inline citations and citation rules note.
                        If False, no inline citations but sources section still included.

    Returns:
        Formatted result with citations if applicable
    """
    if not is_microsoft_doc_tool(tool_name):
        return result

    tool_name_lower = tool_name.lower()

    if tool_name_lower == 'microsoft_docs_search':
        return _format_search_results(result, sources_enabled)
    elif tool_name_lower == 'microsoft_docs_fetch':
        return _format_fetch_result(result, sources_enabled)
    elif tool_name_lower == 'microsoft_code_sample_search':
        return _format_code_sample_results(result, sources_enabled)

    return result


def _format_search_results(result: str, sources_enabled: bool) -> str:
    """Format microsoft_docs_search results with citations.

    Handles deduplication: if same URL appears multiple times, use same citation number.
    Sources section always included (for reference), but:
    - sources_enabled=True: inline citations + citation rules note
    - sources_enabled=False: no inline citations, no note (matches Google Search)
    """
    try:
        data = json.loads(result)
        if not isinstance(data, dict) or 'results' not in data:
            return result
    except (json.JSONDecodeError, TypeError):
        return result

    results = data.get('results', [])
    if not results:
        return result

    # Build URL-to-citation mapping for deduplication
    # Key: normalized URL, Value: (citation_number, title)
    url_to_citation: Dict[str, Tuple[int, str]] = {}
    all_sources: List[Tuple[int, str, str]] = []  # (num, title, url)
    next_citation = 1

    lines = []
    for item in results:
        title = item.get('title', 'Untitled')
        url = item.get('url', '').strip()
        snippet = item.get('snippet', item.get('description', ''))

        if url:
            # Check for duplicate URL (deduplication always applies)
            normalized_url = url.rstrip('/')
            if normalized_url in url_to_citation:
                cite_num = url_to_citation[normalized_url][0]
            else:
                cite_num = next_citation
                url_to_citation[normalized_url] = (cite_num, title)
                all_sources.append((cite_num, title, url))
                next_citation += 1

            # Only add inline citation marker if sources enabled
            if sources_enabled:
                lines.append(f"**{title}** [{cite_num}]")
            else:
                lines.append(f"**{title}**")
        else:
            lines.append(f"**{title}**")

        if snippet:
            lines.append(snippet)
        lines.append("")

    output = "\n".join(lines)

    # Always add sources section (for reference), but note only when enabled
    if all_sources:
        all_sources.sort(key=lambda x: x[0])
        output += _format_sources_section(all_sources, include_note=sources_enabled)

    return output


def _format_fetch_result(result: str, sources_enabled: bool) -> str:
    """Format microsoft_docs_fetch results with citations.

    Fetch returns a single document, so always citation [1].
    Sources section always included, inline citation only when enabled.
    """
    url_match = re.search(r'https://learn\.microsoft\.com[^\s\])"\']*', result)
    if not url_match:
        return result

    url = url_match.group(0).rstrip('/')
    title = _extract_title_from_content(result)

    # Add citation marker only when sources enabled
    if sources_enabled:
        result = result.rstrip() + " [1]"

    # Always add sources section for reference
    result += "\n\n" + _format_sources_section([(1, title, url)], include_note=sources_enabled)

    return result


def _format_code_sample_results(result: str, sources_enabled: bool) -> str:
    """Format microsoft_code_sample_search results with citations.

    Same behavior as search results: deduplication, sources always shown.
    """
    try:
        data = json.loads(result)
        if not isinstance(data, dict):
            return result
    except (json.JSONDecodeError, TypeError):
        return result

    results = data.get('results', data.get('samples', []))
    if not results:
        return result

    # Same deduplication logic as search results
    url_to_citation: Dict[str, Tuple[int, str]] = {}
    all_sources: List[Tuple[int, str, str]] = []
    next_citation = 1

    lines = []
    for item in results:
        title = item.get('title', item.get('name', 'Code Sample'))
        url = item.get('url', item.get('link', '')).strip()
        description = item.get('description', '')

        if url:
            normalized_url = url.rstrip('/')
            if normalized_url in url_to_citation:
                cite_num = url_to_citation[normalized_url][0]
            else:
                cite_num = next_citation
                url_to_citation[normalized_url] = (cite_num, title)
                all_sources.append((cite_num, title, url))
                next_citation += 1

            if sources_enabled:
                lines.append(f"**{title}** [{cite_num}]")
            else:
                lines.append(f"**{title}**")
        else:
            lines.append(f"**{title}**")

        if description:
            lines.append(description)
        lines.append("")

    output = "\n".join(lines)

    if all_sources:
        all_sources.sort(key=lambda x: x[0])
        output += _format_sources_section(all_sources, include_note=sources_enabled)

    return output


def _format_sources_section(sources: List[Tuple[int, str, str]], include_note: bool = True) -> str:
    """Format sources as markdown hyperlinks.

    Args:
        sources: List of (citation_number, title, url) tuples, already sorted by number
        include_note: Whether to append citation rules note

    Returns:
        Markdown formatted sources section
    """
    if not sources:
        return ""

    lines = ["\n#### Sources\n"]
    for num, title, url in sources:
        if url:
            lines.append(f"[{num}] [{title}]({url})")
        else:
            lines.append(f"[{num}] {title}")
        lines.append("")

    if include_note:
        lines.append(MCP_CITATION_RULES)

    return "\n".join(lines)


def _extract_title_from_content(content: str) -> str:
    """Extract title from markdown content.

    Looks for H1 header first, then falls back to first non-empty line.
    """
    h1_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if h1_match:
        return h1_match.group(1).strip()

    for line in content.split('\n'):
        line = line.strip()
        if line and not line.startswith('http'):
            return line[:100]

    return "Microsoft Documentation"
