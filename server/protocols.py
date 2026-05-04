"""Structural interfaces for the four pluggable backends (STT, LLM, TTS, Embedder).

Mode-independent. Cloud-mode and local-mode implementations live under
`server/backends/*`. The factory in `server/modes.py` instantiates the right
one based on the `MODE` env var at process boot.

These are typing.Protocols on purpose — backend classes don't subclass them,
they just match the shape. Method signatures use plain dicts where the
existing code already does (no point introducing dataclasses for this migration).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np


class STT(Protocol):
    def transcribe(self, audio_path: str | Path) -> dict:
        """Returns {transcript, duration_s, latency_s, model, language}.

        `audio_path` is a path the implementation may read. Flask writes the
        upload to a temp file and passes the path — keeps the contract simple
        and matches both faster-whisper AND Deepgram (both accept file uploads).
        `language` may be None for cloud providers that don't detect it.
        """
        ...

    def warmup(self) -> None: ...

    def is_loaded(self) -> bool: ...


class LLM(Protocol):
    def chat(self, message: str, max_tokens: int = 1024,
             system_prompt: str | None = None,
             history: list[dict] | None = None,
             tools: list | None = None) -> dict:
        """Returns {reply, model, latency_s, input_tokens, output_tokens, stop_reason}.

        `tps` (tokens-per-second) is optional — present when the backend
        reports it (Ollama does), absent otherwise (Anthropic does not).

        `history` is a chronological list of {role, content} dicts representing
        prior turns (NOT including the current user `message`). When None or
        empty, behaves identically to a stateless one-shot. Both backends
        pass-through the same shape — Anthropic's messages array and Ollama's
        /api/chat both accept it natively.

        `tools` is a list of `server.tools.Tool` instances the model is
        allowed to call during this request. The backend runs the tool loop
        internally — request emerges from `chat()` only when the model
        produces a final text reply. When `tools` is None or empty, no
        tools are exposed and `chat()` behaves identically to today.
        Returns also include an optional `tools_used: list[dict]` describing
        what got called.
        """
        ...

    def warmup(self) -> None: ...

    def is_loaded(self) -> bool: ...


class TTS(Protocol):
    def synth(self, text: str) -> tuple[bytes, int]:
        """Returns (raw int16 mono PCM bytes, sample_rate_hz).

        IMPORTANT: bytes MUST be raw int16 mono PCM, not WAV/MP3/Opus.
        Cloud providers return MP3 by default — implementations MUST request
        PCM. We standardize on int16 mono so `pcm_to_wav` and sounddevice
        playback work without per-backend special cases. Sample rate may
        differ (Piper 22050, OpenAI tts-1 24000) — callers must not hardcode it.
        """
        ...

    def warmup(self) -> None: ...

    def is_loaded(self) -> bool: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """Returns (n, dim) float32 array. Dim is implementation-defined
        (Ollama nomic-embed-text = 768, MiniLM-L6 = 384). Vectors are NOT
        required to be L2-normalized — the router normalizes."""
        ...

    def warmup(self) -> None: ...

    def is_loaded(self) -> bool: ...
