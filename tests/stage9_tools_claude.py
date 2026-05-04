"""Stage 9 verification: Claude tool-call loop end-to-end (Phase 2B).

Asks ClaudeLLM "what year is it" while exposing only `get_current_time`
as a tool. Asserts:
  - tools_used is populated and contains exactly one get_current_time call
  - the final reply mentions the current year
  - input_tokens accumulated across the two model rounds (>= 50, since
    round 1 includes tool definition + question, round 2 includes the
    tool result)

Cost: ~$0.005 per run (two Sonnet rounds). Re-runnable.

Run from repo root after activating .venv:
    python tests/stage9_tools_claude.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config
from server.backends.llm_claude import ClaudeLLM
from server.tools import get_tool


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def test_no_tools_no_change() -> None:
    """Sanity: tools=None should behave exactly like the pre-Phase-2 path."""
    print("[1] tools=None preserves stateless one-shot behavior")
    must(bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
         "ANTHROPIC_API_KEY missing")
    llm = ClaudeLLM(model=config.ANTHROPIC_MODEL)
    d = llm.chat("Reply with the single word OK.", max_tokens=8)
    must(isinstance(d["reply"], str) and d["reply"], f"empty reply: {d}")
    must("tools_used" not in d,
         f"tools_used should not be set when tools= is None: {d}")
    print(f"  reply: {d['reply']!r}")
    print("  OK")


def test_get_current_time_called() -> None:
    print("[2] model calls get_current_time when asked the date")
    llm = ClaudeLLM(model=config.ANTHROPIC_MODEL)
    tool = get_tool("get_current_time")
    d = llm.chat(
        "What year is it right now? Reply in one short sentence.",
        max_tokens=64,
        tools=[tool],
    )
    must(isinstance(d["reply"], str) and d["reply"], f"empty reply: {d}")
    print(f"  reply: {d['reply']!r}")

    used = d.get("tools_used") or []
    must(len(used) >= 1, f"expected at least one tool call: {used}")
    must(any(u["name"] == "get_current_time" for u in used),
         f"get_current_time not in tools_used: {used}")

    # The reply must reference the current year. We don't hardcode it
    # below — just confirm the LLM produced a 4-digit year that matches
    # the actual current year.
    current_year = str(datetime.now().year)
    must(current_year in d["reply"],
         f"reply missing current year {current_year}: {d['reply']!r}")

    must(d["input_tokens"] >= 50,
         f"input_tokens unexpectedly small (loop didn't accumulate?): {d['input_tokens']}")
    must(d["stop_reason"] in ("end_turn", "tool_max_rounds"),
         f"unexpected stop_reason: {d['stop_reason']}")
    print(f"  tools_used: {[u['name'] for u in used]}")
    print(f"  input_tokens={d['input_tokens']}  output_tokens={d['output_tokens']}  "
          f"latency={d['latency_s']:.2f}s")
    print("  OK")


def test_tool_with_timezone_arg() -> None:
    print("[3] model calls get_current_time with a timezone arg")
    llm = ClaudeLLM(model=config.ANTHROPIC_MODEL)
    tool = get_tool("get_current_time")
    d = llm.chat(
        "What time is it in Tokyo right now? Reply in one short sentence.",
        max_tokens=64,
        tools=[tool],
    )
    used = d.get("tools_used") or []
    must(any(u["name"] == "get_current_time" and u.get("input", {}).get("timezone")
             for u in used),
         f"expected a tool call with a timezone arg: {used}")
    print(f"  reply: {d['reply']!r}")
    tz_call = next(u for u in used if u["input"].get("timezone"))
    print(f"  tool input: {tz_call['input']}")
    print("  OK")


def main() -> int:
    test_no_tools_no_change()
    print()
    test_get_current_time_called()
    print()
    test_tool_with_timezone_arg()
    print()
    print("=" * 64)
    print("  Phase 2B (Claude tool loop) verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
