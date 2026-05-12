"""Tool registry — what the model can call during a chat() turn.

Public API:
- Tool dataclass (see .base)
- REGISTRY: name -> Tool
- register_tool(tool): adds to registry; raises if name collides
- all_tools(): returns the list of currently-registered Tools
- get_tool(name): registry lookup, raises KeyError if missing

Tools are added via their module's import — see `time.py` for the pattern.
The default ship-set is registered when this package first imports.
"""

from __future__ import annotations

from server.tools.base import Tool

REGISTRY: dict[str, Tool] = {}


def register_tool(tool: Tool) -> Tool:
    if tool.name in REGISTRY:
        raise ValueError(f"tool name already registered: {tool.name!r}")
    REGISTRY[tool.name] = tool
    return tool


def all_tools() -> list[Tool]:
    return list(REGISTRY.values())


def get_tool(name: str) -> Tool:
    return REGISTRY[name]


# Side-effect import registers the `time` tool. Web search lives inside the
# GeminiLLM backend now via the built-in google_search grounding — no longer
# a custom Tool that needs registering.
from server.tools import time as _time  # noqa: F401, E402
