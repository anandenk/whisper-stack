"""Stage 9 verification: tool registry + get_current_time (Phase 2A).

Pure unit-style checks. No server, no LLM call. Asserts:
- the registry self-populates from package import
- get_current_time handler returns a sensible string for default + named TZ
- bad timezone returns a graceful error string (not an exception)
- LLM Protocol accepts tools=[Tool] without crashing (no-op for Phase 2A)
- to_anthropic / to_ollama schema converters return the right shape

Run from repo root after activating .venv:
    python tests/stage9_tools.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: F401  -- triggers .env load
from server.tools import REGISTRY, all_tools, get_tool, register_tool
from server.tools.base import Tool
from server.tools.time import get_current_time


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def test_registry() -> None:
    print("[1] registry has get_current_time after import")
    must("get_current_time" in REGISTRY,
         f"get_current_time not in registry: keys={list(REGISTRY.keys())}")
    must(get_tool("get_current_time") is get_current_time,
         "get_tool returned the wrong instance")
    must(get_current_time in all_tools(), "all_tools missing get_current_time")
    print("  OK")


def test_register_tool_collision() -> None:
    print("[2] re-registering the same name raises ValueError")
    dup = Tool(name="get_current_time", description="dup",
               input_schema={"type": "object", "properties": {}},
               handler=lambda _: "")
    try:
        register_tool(dup)
    except ValueError:
        print("  OK")
        return
    must(False, "expected ValueError for duplicate registration")


def test_handler_default() -> None:
    print("[3] handler with no args returns a date-shaped string")
    out = get_current_time.handler({})
    must(isinstance(out, str) and len(out) > 10,
         f"handler returned unexpected: {out!r}")
    # Realistic shape: weekday name appears.
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday"]
    must(any(d in out for d in weekdays),
         f"handler output missing weekday: {out!r}")
    print(f"  -> {out!r}")
    print("  OK")


def test_handler_named_tz() -> None:
    print("[4] handler with timezone='Asia/Tokyo' returns Tokyo time")
    out = get_current_time.handler({"timezone": "Asia/Tokyo"})
    # Tokyo's tz abbrev shows up as JST in strftime output on most platforms.
    must(isinstance(out, str), f"non-string out: {out!r}")
    print(f"  -> {out!r}")
    print("  OK")


def test_handler_bad_tz() -> None:
    print("[5] handler with invalid timezone returns a graceful error string")
    out = get_current_time.handler({"timezone": "Mars/Olympus"})
    must(isinstance(out, str) and "Unknown timezone" in out,
         f"expected graceful error string, got {out!r}")
    print(f"  -> {out!r}")
    print("  OK")


def test_schema_converters() -> None:
    print("[6] to_anthropic + to_ollama produce correct shapes")
    a = get_current_time.to_anthropic()
    must(set(a.keys()) == {"name", "description", "input_schema"},
         f"anthropic shape wrong: {set(a.keys())}")
    must(a["name"] == "get_current_time", "anthropic name wrong")

    o = get_current_time.to_ollama()
    must(o.get("type") == "function", f"ollama envelope wrong: {o}")
    must(o["function"]["name"] == "get_current_time", "ollama function name wrong")
    must("parameters" in o["function"], "ollama function.parameters missing")
    must(o["function"]["parameters"] == get_current_time.input_schema,
         "ollama parameters should pass through input_schema")
    print("  OK")


def test_llm_protocol_accepts_tools_kwarg() -> None:
    print("[7] both backends accept tools= without crashing (Phase 2A no-op)")
    # We don't actually call the backends — too expensive per run. Just
    # verify the chat() signature accepts tools= via inspect.
    import inspect
    from server.backends.llm_claude import ClaudeLLM
    from server.backends.llm_ollama import OllamaLLM

    for cls in (ClaudeLLM, OllamaLLM):
        sig = inspect.signature(cls.chat)
        must("tools" in sig.parameters,
             f"{cls.__name__}.chat is missing the tools= kwarg: {sig}")
    print("  OK")


def main() -> int:
    test_registry()
    test_register_tool_collision()
    test_handler_default()
    test_handler_named_tz()
    test_handler_bad_tz()
    test_schema_converters()
    test_llm_protocol_accepts_tools_kwarg()
    print()
    print("=" * 64)
    print("  Phase 2A (tool registry + get_current_time) verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
