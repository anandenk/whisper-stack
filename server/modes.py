"""Backend factory — one process serves one MODE, picked at boot.

Five lru_cache'd factory functions, each returning a singleton for the
configured backend. Backend-specific imports live INSIDE the factory branches
so cloud-mode boot doesn't pay the ~250 MB torch import for sentence-transformers
when faster_whisper isn't even installable on a Linux VPS, and vice versa.

Usage:
    from server import modes
    text = modes.get_stt().transcribe(path)["transcript"]
    reply = modes.get_llm("simple").chat(text)["reply"]
    raw, sr = modes.get_tts().synth(reply)

The factories raise NotImplementedError for cloud branches not yet wired
(Phases 5/7/8). Local mode is fully functional today.
"""

from __future__ import annotations

import functools
from typing import Literal

from server import config
from server.protocols import LLM, STT, TTS, Embedder


@functools.lru_cache(maxsize=1)
def get_stt() -> STT:
    if config.MODE == "local":
        from server.backends.stt_whisper import WhisperLocalSTT
        return WhisperLocalSTT()
    raise NotImplementedError(
        "cloud STT (Deepgram) not yet implemented — Phase 5 of two-mode-plan"
    )


@functools.lru_cache(maxsize=2)
def get_llm(route: Literal["simple", "complex"]) -> LLM:
    if route == "complex":
        # Same Sonnet model in both modes.
        from server.backends.llm_claude import ClaudeLLM
        return ClaudeLLM(model=config.ANTHROPIC_MODEL)
    if config.MODE == "local":
        from server.backends.llm_ollama import OllamaLLM
        return OllamaLLM()
    # cloud-mode simple = Claude Haiku
    from server.backends.llm_claude import ClaudeLLM
    return ClaudeLLM(model=config.ANTHROPIC_HAIKU_MODEL)


@functools.lru_cache(maxsize=1)
def get_tts() -> TTS:
    if config.MODE == "local":
        from server.backends.tts_piper import PiperTTS
        return PiperTTS()
    raise NotImplementedError(
        "cloud TTS (OpenAI) not yet implemented — Phase 7 of two-mode-plan"
    )


@functools.lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    if config.MODE == "local":
        from server.backends.embed_ollama import OllamaEmbedder
        return OllamaEmbedder()
    from server.backends.embed_st import SentenceTransformerEmbedder
    return SentenceTransformerEmbedder()
