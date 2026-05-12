"""Backend factory — singletons for the three runtime backends.

After the Gemini migration there is one LLM (Gemini 2.5 Flash) and STT/TTS
remain local. The MODE-branching that used to live here for the LLM is gone;
get_stt/get_tts still gate on config.MODE because commit 5 will be the one
to fully drop the obsolete branches.

Usage:
    from server import modes
    text = modes.get_stt().transcribe(path)["transcript"]
    reply = modes.get_llm().chat(text)["reply"]
    raw, sr = modes.get_tts().synth(reply)
"""

from __future__ import annotations

import functools

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


@functools.lru_cache(maxsize=1)
def get_llm() -> LLM:
    from server.backends.llm_gemini import GeminiLLM
    return GeminiLLM()


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
