"""Per-process LLM token + cost accumulator.

In-memory, resets on every server restart. /health exposes a snapshot so
`curl /health | jq .usage` gives a quick cost-since-boot view. Solo-use
visibility — not designed for billing accuracy or long-horizon
analytics; pricing constants are best-effort against public rates and
may drift. The intent is "is something using more tokens than I
expected" eyeballing.

Pricing reference (2026, USD per 1M tokens; update when rates change):

    claude-sonnet-4-6   $3.00 input  /  $15.00 output
    claude-haiku-4-5    $1.00 input  /   $5.00 output

Local models (Ollama / Llama) and STT/TTS calls aren't billed by token,
so they're recorded as call counts only, with cost zero.
"""

from __future__ import annotations

import threading
import time

# Substring-keyed so dated aliases (e.g. "claude-sonnet-4-6-20260101")
# still match. First match wins.
_PRICING_PER_1M_USD: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5":  {"input": 1.00, "output":  5.00},
}
# Substrings that mark a model as local/free.
_LOCAL_MODEL_MARKERS = ("llama",)

_LOCK = threading.Lock()
_BOOT_TIME = time.time()
_COUNTERS: dict[str, dict[str, int]] = {}


def record(model: str | None,
           input_tokens: int | float | None = 0,
           output_tokens: int | float | None = 0,
           route: str | None = None) -> None:
    """Increment counters for a single LLM call. Safe to call with None
    arguments — defensive against backends that didn't surface a metric
    on a particular call."""
    if not model:
        return
    with _LOCK:
        c = _COUNTERS.setdefault(model, {"input": 0, "output": 0, "calls": 0})
        c["input"] += int(input_tokens or 0)
        c["output"] += int(output_tokens or 0)
        c["calls"] += 1


def _model_rates(model: str) -> dict[str, float] | None:
    lower = model.lower()
    for marker, rates in _PRICING_PER_1M_USD.items():
        if marker in lower:
            return rates
    return None


def snapshot() -> dict:
    """Return a dict shaped for embedding in /health responses."""
    with _LOCK:
        per_model_raw = {m: dict(v) for m, v in _COUNTERS.items()}

    total_input = sum(c["input"] for c in per_model_raw.values())
    total_output = sum(c["output"] for c in per_model_raw.values())
    total_calls = sum(c["calls"] for c in per_model_raw.values())

    per_model: dict[str, dict] = {}
    cost_usd = 0.0
    for model, c in per_model_raw.items():
        is_local = any(m in model.lower() for m in _LOCAL_MODEL_MARKERS)
        rates = None if is_local else _model_rates(model)
        model_cost = 0.0
        if rates is not None:
            model_cost = (c["input"] / 1e6 * rates["input"]
                          + c["output"] / 1e6 * rates["output"])
            cost_usd += model_cost
        per_model[model] = {
            **c,
            "estimated_cost_usd": round(model_cost, 6),
            "billed": rates is not None,
        }

    return {
        "uptime_s": round(time.time() - _BOOT_TIME, 1),
        "calls": total_calls,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "estimated_cost_usd": round(cost_usd, 4),
        "per_model": per_model,
    }
