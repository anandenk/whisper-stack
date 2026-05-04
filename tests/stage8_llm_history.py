"""Stage 8 verification: LLM backends carry conversation history (Phase B).

Smoke-tests both ClaudeLLM (Anthropic API) and OllamaLLM (/api/chat) by
giving each a 2-turn history that establishes a fact ("my favorite color
is blue") and then asking a question that can only be answered correctly
if the history was actually consumed ("what's my favorite color?").

A passing test = the model's reply contains "blue". This is a deliberately
loose assertion — the model can phrase the reply however it wants, but
the substring "blue" needs to be there.

Cost: ~$0.001 per Anthropic call. Ollama side is free. Re-running OK.

Run from repo root after activating .venv:
    python tests/stage8_llm_history.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config
from server.backends.llm_claude import ClaudeLLM
from server.backends.llm_ollama import OllamaLLM

HISTORY = [
    {"role": "user", "content": "My favorite color is blue."},
    {"role": "assistant", "content": "Got it — blue is a great choice."},
]
QUESTION = "What's my favorite color? Reply in one short sentence."


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def assert_chat_shape(d: dict, expect_model: str) -> None:
    for k in ("reply", "model", "latency_s", "input_tokens", "output_tokens", "stop_reason"):
        must(k in d, f"missing key {k!r} in result: {list(d.keys())}")
    must(d["model"] == expect_model, f"model mismatch: {d['model']!r} vs {expect_model!r}")
    must(isinstance(d["reply"], str) and d["reply"], f"empty reply: {d}")


def test_claude() -> None:
    print("[1] ClaudeLLM with 2-turn history")
    must(bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
         "ANTHROPIC_API_KEY missing from env")
    llm = ClaudeLLM(model=config.ANTHROPIC_MODEL)

    # Stateless control: should NOT know the color.
    control = llm.chat(QUESTION, max_tokens=64)
    assert_chat_shape(control, config.ANTHROPIC_MODEL)
    print(f"  control (no history) reply: {control['reply']!r}")

    # With history: should know.
    d = llm.chat(QUESTION, max_tokens=64, history=HISTORY)
    assert_chat_shape(d, config.ANTHROPIC_MODEL)
    print(f"  with history reply:         {d['reply']!r}")
    must("blue" in d["reply"].lower(),
         f"history not consumed — reply missing 'blue': {d['reply']!r}")
    print(f"  latency={d['latency_s']:.2f}s  in={d['input_tokens']}  out={d['output_tokens']}")
    print("  OK")


def test_ollama() -> None:
    print("[2] OllamaLLM with 2-turn history (via /api/chat)")
    llm = OllamaLLM()

    control = llm.chat(QUESTION, max_tokens=64)
    assert_chat_shape(control, config.OLLAMA_MODEL)
    must("tps" in control, "Ollama result should report tps")
    print(f"  control (no history) reply: {control['reply']!r}")

    d = llm.chat(QUESTION, max_tokens=64, history=HISTORY)
    assert_chat_shape(d, config.OLLAMA_MODEL)
    must("tps" in d, "Ollama result should report tps")
    print(f"  with history reply:         {d['reply']!r}")
    must("blue" in d["reply"].lower(),
         f"history not consumed — reply missing 'blue': {d['reply']!r}")
    print(f"  latency={d['latency_s']:.2f}s  in={d['input_tokens']}  "
          f"out={d['output_tokens']}  tps={d['tps']:.1f}")
    print("  OK")


def test_system_prompt_with_history() -> None:
    """Verify system_prompt + history compose correctly on both backends."""
    print("[3] system_prompt + history on both backends")
    sysprompt = "You are a terse assistant. Reply in 5 words or fewer."

    claude = ClaudeLLM(model=config.ANTHROPIC_MODEL)
    d = claude.chat(QUESTION, max_tokens=32,
                    system_prompt=sysprompt, history=HISTORY)
    must("blue" in d["reply"].lower(),
         f"Claude: history+system, missing 'blue': {d['reply']!r}")
    print(f"  Claude: {d['reply']!r}")

    ollama = OllamaLLM()
    d = ollama.chat(QUESTION, max_tokens=32,
                    system_prompt=sysprompt, history=HISTORY)
    must("blue" in d["reply"].lower(),
         f"Ollama: history+system, missing 'blue': {d['reply']!r}")
    print(f"  Ollama: {d['reply']!r}")
    print("  OK")


def main() -> int:
    test_claude()
    print()
    test_ollama()
    print()
    test_system_prompt_with_history()
    print()
    print("=" * 64)
    print("  All Phase B (LLM history) checks passed.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
