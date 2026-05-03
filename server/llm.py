"""LLM clients — Anthropic (complex route) and Ollama (simple route).

Both functions return the same shape so the /chat handler can treat them
interchangeably: {reply, model, latency_s, input_tokens, output_tokens, stop_reason}.
chat_ollama additionally returns `tps` for observability.
"""

from __future__ import annotations

import time

import requests
from anthropic import Anthropic

from server import config

_anthropic: Anthropic | None = None


def _client() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic()
    return _anthropic


def chat_claude(message: str, max_tokens: int = 1024, system_prompt: str | None = None) -> dict:
    client = _client()
    t0 = time.perf_counter()
    kwargs: dict = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": message}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    msg = client.messages.create(**kwargs)
    elapsed = time.perf_counter() - t0
    reply = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    return {
        "reply": reply,
        "model": config.ANTHROPIC_MODEL,
        "latency_s": elapsed,
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "stop_reason": msg.stop_reason,
    }


def warmup_ollama() -> None:
    """Trigger Ollama model load so first user simple-route call isn't a 5s cold start."""
    chat_ollama("hi", max_tokens=4)


def chat_ollama(message: str, max_tokens: int = 512, system_prompt: str | None = None) -> dict:
    t0 = time.perf_counter()
    payload: dict = {
        "model": config.OLLAMA_MODEL,
        "prompt": message,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": max_tokens},
    }
    if system_prompt:
        payload["system"] = system_prompt
    r = requests.post(
        config.OLLAMA_GENERATE_URL,
        json=payload,
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    elapsed = time.perf_counter() - t0

    eval_count = data.get("eval_count", 0)
    eval_dur_ns = data.get("eval_duration", 1)
    tps = eval_count / (eval_dur_ns / 1e9) if eval_dur_ns > 0 else 0.0

    return {
        "reply": data.get("response", "").strip(),
        "model": config.OLLAMA_MODEL,
        "latency_s": elapsed,
        "input_tokens": data.get("prompt_eval_count", 0),
        "output_tokens": eval_count,
        "stop_reason": data.get("done_reason", "stop"),
        "tps": tps,
    }
