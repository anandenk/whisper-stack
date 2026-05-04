"""Ollama LLM backend (local-mode simple route).

Uses `/api/chat` (messages array) rather than `/api/generate` (single prompt)
so multi-turn `history` flows through natively. Response shape changes
slightly from /api/generate: reply is at `data["message"]["content"]`
instead of `data["response"]`. Token counts and timings are at the same
top-level keys.
"""

from __future__ import annotations

import time

import requests

from server import config


class OllamaLLM:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.OLLAMA_MODEL
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def chat(self, message: str, max_tokens: int = 512,
             system_prompt: str | None = None,
             history: list[dict] | None = None) -> dict:
        # /api/chat takes a messages array. System prompt is a `system` role
        # entry at the front (NOT a top-level field — that's /api/generate's
        # convention). History is already alternating user/assistant pairs;
        # the new user turn goes at the end.
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": max_tokens},
        }

        t0 = time.perf_counter()
        r = requests.post(config.OLLAMA_CHAT_URL, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        elapsed = time.perf_counter() - t0
        self._loaded = True

        eval_count = data.get("eval_count", 0)
        eval_dur_ns = data.get("eval_duration", 1)
        tps = eval_count / (eval_dur_ns / 1e9) if eval_dur_ns > 0 else 0.0

        reply = (data.get("message") or {}).get("content", "").strip()

        return {
            "reply": reply,
            "model": self.model,
            "latency_s": elapsed,
            "input_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": eval_count,
            "stop_reason": data.get("done_reason", "stop"),
            "tps": tps,
        }

    def warmup(self) -> None:
        """Trigger model load so first user simple-route call isn't a cold start."""
        self.chat("hi", max_tokens=4)
