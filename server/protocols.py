"""Structural interfaces for the three pluggable backends (STT, LLM, TTS).

Implementations live under `server/backends/*`. The factory in
`server/modes.py` instantiates them as singletons.

These are typing.Protocols on purpose — backend classes don't subclass them,
they just match the shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, Protocol


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

    def chat_stream(self, message: str, max_tokens: int = 1024,
                    system_prompt: str | None = None,
                    history: list[dict] | None = None,
                    tools: list | None = None) -> Iterator[dict]:
        """Streaming variant of chat(). Yields a sequence of event dicts and
        terminates with a single 'final' event carrying the same fields
        chat() returns.

        Event shapes:
          - {"type": "text", "delta": str}
              A chunk of generated text. Multiple events per round; clients
              concatenate to display the model's output as it forms.
          - {"type": "tool_call", "id": str, "name": str, "input": dict, "round": int}
              Yielded BEFORE the handler runs.
          - {"type": "tool_result", "id": str, "name": str, "output_chars": int,
             "latency_s": float, "round": int}
              Yielded AFTER the handler runs.
          - {"type": "final", "reply": str, "model": str, "latency_s": float,
             "input_tokens": int, "output_tokens": int, "stop_reason": str,
             "tools_used": list[dict] | absent}
              Always the last event. `reply` is the LAST round's text only
              — text from earlier rounds (before tool_use) is "thinking"
              that the model didn't intend as the final answer. Voice
              callers should TTS `final.reply`, not the concatenation of
              all `text` deltas.

        Backends that don't support streaming yet raise NotImplementedError
        on first iteration; route handlers fall back to chat().
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

    def synth_stream(self, text_iter: Iterable[str]) -> Iterator[tuple[bytes, int]]:
        """Streaming counterpart to synth(). Caller passes an iterable of
        complete sentences; impl yields (pcm_chunk, sample_rate_hz)
        tuples as PCM becomes available.

        "Per sentence" is loose — implementations MAY further chunk audio
        within a sentence to keep first-byte latency low (Piper's
        `voice.synthesize()` already yields multiple chunks per input,
        which we forward directly). Sample rate is reported per chunk
        for symmetry with synth(); within a single backend instance it
        is constant.

        The text_iter is consumed lazily — pull-driven, so the caller
        can interleave text generation and audio emission without
        buffering the full reply.
        """
        ...

    def warmup(self) -> None: ...

    def is_loaded(self) -> bool: ...


