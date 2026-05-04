"""Stage 9 verification: Ollama tool-call loop end-to-end (Phase 2C).

Same shape as stage9_tools_claude.py but for OllamaLLM. Llama 3.1 8B's
tool calling is noticeably less reliable than Sonnet's — the test is
deliberately lenient: the model is expected to call the tool but the
exact reply phrasing isn't asserted on. This is also why router
auto-escalation (Phase 2E) bumps "tool-likely" queries to Sonnet
in production.

Free; local Ollama only.

Run from repo root after activating .venv:
    python tests/stage9_tools_ollama.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: F401
from server.backends.llm_ollama import OllamaLLM
from server.tools import get_tool


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def test_no_tools_no_change() -> None:
    print("[1] tools=None preserves stateless one-shot behavior")
    llm = OllamaLLM()
    d = llm.chat("Reply with the single word OK.", max_tokens=8)
    must(isinstance(d["reply"], str) and d["reply"], f"empty reply: {d}")
    must("tools_used" not in d,
         f"tools_used should not be set when tools= is None: {d}")
    print(f"  reply: {d['reply']!r}")
    print("  OK")


def test_get_current_time_called() -> None:
    print("[2] Llama calls get_current_time when asked the date")
    llm = OllamaLLM()
    tool = get_tool("get_current_time")
    d = llm.chat(
        "What is the current year? You have a tool for this; please use it.",
        max_tokens=128,
        tools=[tool],
    )
    print(f"  reply:      {d['reply']!r}")
    print(f"  tools_used: {[u['name'] for u in (d.get('tools_used') or [])]}")
    print(f"  in/out:     {d['input_tokens']}/{d['output_tokens']}  "
          f"latency={d['latency_s']:.2f}s")

    used = d.get("tools_used") or []
    must(any(u["name"] == "get_current_time" for u in used),
         f"Llama did not call get_current_time: {used}\n"
         f"This is a known limitation of Llama 3.1 8B — auto-escalation "
         f"to Sonnet (Phase 2E) routes around it in production.")
    current_year = str(datetime.now().year)
    must(current_year in d["reply"],
         f"reply missing current year {current_year}: {d['reply']!r}")
    print("  OK")


def main() -> int:
    test_no_tools_no_change()
    print()
    test_get_current_time_called()
    print()
    print("=" * 64)
    print("  Phase 2C (Ollama tool loop) verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
