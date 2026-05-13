"""Backend factory — singletons for the three runtime backends.

After the Gemini migration: one LLM (Gemini 2.5 Flash, cloud), one STT
(faster-whisper, local GPU), one TTS (Piper, local). No mode branching,
no per-request swap.

Usage:
    from server import modes
    text = modes.get_stt().transcribe(path)["transcript"]
    reply = modes.get_llm().chat(text)["reply"]
    raw, sr = modes.get_tts().synth(reply)
"""

from __future__ import annotations

import functools

from server.protocols import LLM, STT, TTS


@functools.lru_cache(maxsize=1)
def get_stt() -> STT:
    from server.backends.stt_whisper import WhisperLocalSTT
    return WhisperLocalSTT()


@functools.lru_cache(maxsize=1)
def get_llm() -> LLM:
    from server.backends.llm_gemini import GeminiLLM
    return GeminiLLM()


@functools.lru_cache(maxsize=1)
def get_tts() -> TTS:
    from server.backends.tts_kokoro import KokoroTTS
    return KokoroTTS()
