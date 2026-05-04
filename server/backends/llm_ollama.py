"""Ollama LLM backend (local-mode simple route)."""

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
             system_prompt: str | None = None) -> dict:
        t0 = time.perf_counter()
        payload: dict = {
            "model": self.model,
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
        self._loaded = True

        eval_count = data.get("eval_count", 0)
        eval_dur_ns = data.get("eval_duration", 1)
        tps = eval_count / (eval_dur_ns / 1e9) if eval_dur_ns > 0 else 0.0

        return {
            "reply": data.get("response", "").strip(),
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
