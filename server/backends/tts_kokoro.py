"""Kokoro TTS backend (local, ONNX runtime).

Replaces Piper because Piper's voices (en_GB-alan-medium,
en_GB-jenny_dioco-medium) sounded robotic on direct A/B. Kokoro 82M
(Apache-2.0) ships a stock af_bella voice that's noticeably warmer.

We use the `kokoro-onnx` package, not the official `kokoro` PyTorch
package, because `kokoro` hard-pins numpy==1.26.4 which has no Python
3.13 wheel — pip falls back to a source build that needs MSVC.
`kokoro-onnx` is pure-Python on top of onnxruntime, bundles its own
phonemizer (no system espeak-ng install required), and produces the
same model output. Trade: no GPU acceleration via this path. Synth is
~1.5x realtime on CPU at the workstation; warmup hides the model
load.

Output: float32 numpy in [-1, 1] at 24000 Hz, single chunk per call.
Convert to int16 PCM bytes for the TTS protocol.
"""
from __future__ import annotations

from typing import Iterable, Iterator

import numpy as np
from kokoro_onnx import Kokoro

from server import config


class KokoroTTS:
    """Shared Kokoro session across all instances via class-level cache."""

    _kokoro: Kokoro | None = None

    def _get_kokoro(self) -> Kokoro:
        if KokoroTTS._kokoro is None:
            KokoroTTS._kokoro = Kokoro(
                str(config.KOKORO_MODEL_PATH),
                str(config.KOKORO_VOICES_PATH),
            )
        return KokoroTTS._kokoro

    @staticmethod
    def _to_int16_bytes(audio: np.ndarray) -> bytes:
        return (audio * 32767.0).clip(-32768, 32767).astype(np.int16).tobytes()

    def is_loaded(self) -> bool:
        return KokoroTTS._kokoro is not None

    def synth(self, text: str) -> tuple[bytes, int]:
        k = self._get_kokoro()
        samples, sr = k.create(
            text,
            voice=config.KOKORO_VOICE,
            speed=config.KOKORO_SPEED,
            lang=config.KOKORO_LANG,
        )
        return self._to_int16_bytes(samples), sr

    def synth_stream(self, text_iter: Iterable[str]) -> Iterator[tuple[bytes, int]]:
        """Per-sentence streaming synth. Kokoro's `create()` returns the
        whole sentence as one array; we emit one PCM chunk per sentence.
        Kokoro also has `create_stream` but it's an async generator,
        which doesn't fit the sync iterator protocol cleanly — one chunk
        per sentence is the practical streaming granularity anyway, since
        the caller drives this from a SentenceChunker that already breaks
        the reply at clause boundaries."""
        k = self._get_kokoro()
        for text in text_iter:
            if not text or not text.strip():
                continue
            samples, sr = k.create(
                text,
                voice=config.KOKORO_VOICE,
                speed=config.KOKORO_SPEED,
                lang=config.KOKORO_LANG,
            )
            yield self._to_int16_bytes(samples), sr

    def warmup(self) -> None:
        """Load model + voice, synth a tiny string so the first user call
        is hot. Cold load is ~0.7 s; warm synth is ONNX inference only."""
        self.synth("Hi.")
