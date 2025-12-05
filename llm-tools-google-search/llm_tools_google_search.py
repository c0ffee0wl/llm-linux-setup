"""
LLM tools for Google Search using Vertex/Gemini with Google Search grounding.

Provides a google_search tool that leverages Vertex AI or Gemini's google_search
option to perform web searches, making search available to any LLM model.
"""
import json

import llm


# Model priority: Vertex first (enterprise), then standard Gemini API
SEARCH_MODELS = [
    "vertex/gemini-2.5-flash",
    "gemini-2.5-flash",
]


def _get_search_model():
    """Get the first available search model (prefers Vertex)."""
    for model_id in SEARCH_MODELS:
        try:
            return llm.get_model(model_id)
        except llm.UnknownModelError:
            continue
    return None


def google_search(query: str, max_results: int = 5) -> str:
    """
    Search the web using Google Search. ONLY use when explicitly requested.

    IMPORTANT: Do NOT use this tool unless the user explicitly asks to:
    - "search for...", "look up...", "find online...", "google..."
    - "what's the latest...", "check the web for..."

    Do NOT use for:
    - General questions you can answer from training data
    - Topics where your knowledge is likely current enough
    - Anything the user didn't explicitly ask to search for

    Args:
        query: The search query - be specific for better results
        max_results: Maximum number of source URLs to return (default: 5)

    Returns:
        JSON with search results including synthesized answer and source URLs
    """
    model = _get_search_model()

    if model is None:
        return json.dumps({
            "error": "Vertex/Gemini not configured. Run: install-llm-tools.sh --gemini",
            "query": query,
            "results": "",
            "sources": []
        }, indent=2)

    # Craft prompt that encourages grounded search results
    search_prompt = f"""Search the web for: {query}

Provide a comprehensive answer based on current web search results.
Include specific facts, numbers, and dates where relevant.
Cite your sources."""

    try:
        # Call model with google_search enabled
        response = model.prompt(
            search_prompt,
            google_search=True
        )

        result_text = response.text()

        # Extract grounding metadata if available
        sources = []
        try:
            response_json = response.response_json
            if response_json:
                for candidate in response_json.get('candidates', []):
                    grounding = candidate.get('groundingMetadata', {})
                    # Extract from groundingChunks
                    for chunk in grounding.get('groundingChunks', []):
                        web_info = chunk.get('web', {})
                        if web_info:
                            source = {
                                'title': web_info.get('title', ''),
                                'uri': web_info.get('uri', '')
                            }
                            if source not in sources:
                                sources.append(source)
                    # Check searchEntryPoint for search suggestions
                    search_entry = grounding.get('searchEntryPoint', {})
                    if search_entry and not sources:
                        # Fallback: use rendered content URL if no other sources
                        rendered = search_entry.get('renderedContent', '')
                        if rendered:
                            sources.append({
                                'title': 'Google Search',
                                'uri': f'https://www.google.com/search?q={query.replace(" ", "+")}'
                            })
        except Exception:
            # If we can't extract grounding metadata, continue without it
            pass

        return json.dumps({
            "query": query,
            "results": result_text,
            "sources": sources[:max_results],
            "model": model.model_id
        }, indent=2)

    except Exception as e:
        return json.dumps({
            "error": str(e),
            "query": query,
            "results": "",
            "sources": []
        }, indent=2)


@llm.hookimpl
def register_tools(register):
    """Register Google Search tool."""
    register(google_search)
