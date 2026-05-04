"""Anthropic Claude LLM backend.

Used for the local-mode complex route (Sonnet) AND the cloud-mode simple
route (Haiku). Same class, different `model` arg from the factory.
"""

from __future__ import annotations

import time

from anthropic import Anthropic

from server import config


class ClaudeLLM:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.ANTHROPIC_MODEL
        self._client: Anthropic | None = None
        self._loaded = False

    def _get_client(self) -> Anthropic:
        if self._client is None:
            self._client = Anthropic()
        return self._client

    def is_loaded(self) -> bool:
        return self._loaded

    def chat(self, message: str, max_tokens: int = 1024,
             system_prompt: str | None = None,
             history: list[dict] | None = None,
             tools: list | None = None) -> dict:
        # Phase 2A: tools= accepted but ignored. The actual tool loop lands
        # in Phase 2B; for now this guarantees callers can pass tools= without
        # an error and existing behavior is preserved exactly.
        del tools  # noqa
        client = self._get_client()
        t0 = time.perf_counter()
        # Anthropic expects messages to alternate user/assistant and end on
        # a user turn. `history` is already alternating; appending the new
        # user message preserves that.
        messages: list[dict] = list(history) if history else []
        messages.append({"role": "user", "content": message})
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_prompt:
            # Anthropic puts the system prompt at top-level, not in messages.
            kwargs["system"] = system_prompt
        msg = client.messages.create(**kwargs)
        elapsed = time.perf_counter() - t0
        self._loaded = True
        reply = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        return {
            "reply": reply,
            "model": self.model,
            "latency_s": elapsed,
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "stop_reason": msg.stop_reason,
        }

    def warmup(self) -> None:
        """No-op: Anthropic has no cold-start cost worth pre-paying. The
        client lazily initializes on first chat()."""
        return None
