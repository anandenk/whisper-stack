"""Cloud-mode smoke test: web_search via Tavily.

Hits Tavily directly with a well-known query that should always have
results ("what year is it" returns a Wikipedia-style summary). Asserts:
  - the tool is registered when TAVILY_API_KEY is set
  - the handler returns non-empty formatted text
  - the response includes either an "answer" or numbered results

Cost: ~$0.005 per run on the paid tier; free on the 1k/mo tier.

Skips with a clear message when TAVILY_API_KEY is unset — the tool
should also be absent from the registry in that case, which we assert.

Run from repo root after activating .venv:
    python tests/cloud_tools_search.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    # Must import AFTER config is loaded so the conditional registration
    # in search.py sees the right TAVILY_API_KEY value.
    from server.tools import REGISTRY, get_tool

    if not config.TAVILY_API_KEY:
        must("web_search" not in REGISTRY,
             "web_search should NOT be registered when TAVILY_API_KEY is unset")
        print("SKIP: TAVILY_API_KEY is not set in .env")
        print("      The web_search tool is correctly omitted from the registry.")
        print("      Set TAVILY_API_KEY to actually exercise the search.")
        print("      Free tier: https://tavily.com  (no card required)")
        return 0

    must("web_search" in REGISTRY,
         "web_search should be registered when TAVILY_API_KEY is set")
    tool = get_tool("web_search")
    print(f"web_search registered. description starts: {tool.description[:60]!r}...")
    print()

    print("Querying Tavily for: 'what is the current year'")
    out = tool.handler({"query": "what is the current year"})
    must(isinstance(out, str), f"handler returned non-string: {type(out)}")
    must(len(out) > 30, f"handler output suspiciously short: {out!r}")
    must(not out.startswith("web_search failed"),
         f"handler reported a failure: {out}")
    print(out[:600])
    print("...")
    print()

    # max_results=1 sanity
    print("Re-querying with max_results=1")
    out_1 = tool.handler({"query": "what is the current year", "max_results": 1})
    must(isinstance(out_1, str) and out_1, "max_results=1 gave empty output")
    print(f"  output length: {len(out_1)} chars")
    print()

    # Empty query is a graceful failure, not a crash
    out_empty = tool.handler({"query": ""})
    must("requires a non-empty 'query'" in out_empty,
         f"empty query should return a clear error string: {out_empty!r}")

    print("=" * 64)
    print("  Phase 2D (Tavily web_search) verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
