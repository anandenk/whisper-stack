"""Back-compat shim — re-exports OllamaLLM/ClaudeLLM as module-level functions.

Phase 2 keeps existing call sites (`llm.chat_ollama(...)`, `llm.chat_claude(...)`,
`llm.warmup_ollama()`) working. Phase 3 deletes this file and routes app.py
through `modes.get_llm("simple"|"complex")` directly.
"""

from __future__ import annotations

from server.backends.llm_claude import ClaudeLLM
from server.backends.llm_ollama import OllamaLLM

_ollama = OllamaLLM()
_claude = ClaudeLLM()


def chat_ollama(message: str, max_tokens: int = 512,
                system_prompt: str | None = None) -> dict:
    return _ollama.chat(message, max_tokens=max_tokens, system_prompt=system_prompt)


def chat_claude(message: str, max_tokens: int = 1024,
                system_prompt: str | None = None) -> dict:
    return _claude.chat(message, max_tokens=max_tokens, system_prompt=system_prompt)


def warmup_ollama() -> None:
    _ollama.warmup()
