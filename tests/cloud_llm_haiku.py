"""Cloud-mode smoke test: Claude Haiku via ClaudeLLM backend.

Hits the Anthropic API directly; no Flask server needed. Verifies the
default ANTHROPIC_HAIKU_MODEL alias still resolves and that the chat()
shape matches the LLM protocol (so router → /chat → /converse keep working
when MODE=cloud routes 'simple' to Haiku).

Cost: ~$0.001/run.

Run from repo root after activating .venv:
    python tests/cloud_llm_haiku.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config
from server.backends.llm_claude import ClaudeLLM


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        print("(this is a cloud-API test — a network/auth failure is not a "
              "logic bug, but a model_not_found 400 means the alias drifted "
              "and needs the dated form)", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    must(bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
         "ANTHROPIC_API_KEY missing from .env / environment")
    model = config.ANTHROPIC_HAIKU_MODEL
    print(f"Smoke-calling Haiku via ClaudeLLM(model={model!r})...")

    llm = ClaudeLLM(model=model)
    must(not llm.is_loaded(), "is_loaded() should be False before first call")

    d = llm.chat("Reply with the single word OK and nothing else.", max_tokens=8)
    for k in ("reply", "model", "latency_s", "input_tokens", "output_tokens", "stop_reason"):
        must(k in d, f"missing key {k!r} in result: {d}")
    must(isinstance(d["reply"], str) and d["reply"], f"empty reply: {d}")
    must(d["model"] == model, f"model mismatch: {d['model']!r} vs {model!r}")
    must(llm.is_loaded(), "is_loaded() should be True after first call")

    print(f"  reply        = {d['reply']!r}")
    print(f"  model        = {d['model']}")
    print(f"  latency      = {d['latency_s']:.2f}s")
    print(f"  tokens in/out= {d['input_tokens']}/{d['output_tokens']}")
    print(f"  stop_reason  = {d['stop_reason']}")
    print()
    print("Haiku alias verified — cloud-mode simple route is wired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
