"""web_search — Tavily-backed real-time web search.

Registered ONLY when config.TAVILY_API_KEY is set. The model doesn't see
this tool when it's unconfigured, so it can't try-and-fail.

Tavily is purpose-built for AI agents: returns clean snippets + an
optional summary "answer" field that's pre-condensed for LLM consumption.
Free tier covers 1k queries/month, which is enough for solo use forever.
Paid is $0.005/query above that.
"""

from __future__ import annotations

import requests

from server import config
from server.tools import register_tool
from server.tools.base import Tool


_TAVILY_URL = "https://api.tavily.com/search"


def _format_results(data: dict) -> str:
    """Render Tavily's JSON response as text the model can read back.

    Format optimized for short voice replies — answer first, then a few
    titled snippets, no URLs to clutter the model's context window
    unless the user asks for them.
    """
    answer = (data.get("answer") or "").strip()
    results = data.get("results") or []
    parts: list[str] = []
    if answer:
        parts.append(f"Quick answer: {answer}")
    for i, r in enumerate(results[:5], start=1):
        title = (r.get("title") or "").strip()
        content = (r.get("content") or "").strip()
        # Trim long snippets — voice replies don't benefit from huge contexts.
        if len(content) > 400:
            content = content[:400].rstrip() + "..."
        if title and content:
            parts.append(f"{i}. {title}\n   {content}")
        elif content:
            parts.append(f"{i}. {content}")
    if not parts:
        return "No results found."
    return "\n\n".join(parts)


def _handler(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "web_search requires a non-empty 'query' argument."
    max_results = int(args.get("max_results") or 5)
    max_results = max(1, min(max_results, 10))

    payload = {
        "api_key": config.TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "max_results": max_results,
        "include_raw_content": False,
    }
    try:
        r = requests.post(_TAVILY_URL, json=payload, timeout=15)
    except requests.RequestException as exc:
        return f"web_search failed (network): {exc}"
    if r.status_code != 200:
        # Tavily error bodies are usually small JSON; surface what we can.
        try:
            body = r.json()
        except Exception:
            body = r.text[:200]
        return f"web_search failed (HTTP {r.status_code}): {body}"
    try:
        data = r.json()
    except Exception:
        return f"web_search returned non-JSON body: {r.text[:200]}"
    return _format_results(data)


web_search = Tool(
    name="web_search",
    description=(
        "Search the live web for current information. Use this for anything "
        "that depends on recent events, current prices, weather, news, "
        "people's ages, or anything else that may have changed since your "
        "training cutoff."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query, in plain English.",
            },
            "max_results": {
                "type": "integer",
                "description": "Optional cap on result count (1-10, default 5).",
            },
        },
        "required": ["query"],
    },
    handler=_handler,
)

# Register only when Tavily is configured. Otherwise the model never sees
# this tool — keeps "what's the weather" requests honest about whether we
# have the capability.
if config.TAVILY_API_KEY:
    register_tool(web_search)
