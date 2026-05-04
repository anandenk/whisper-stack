"""faster-whisper STT backend (local mode).

The CTranslate2 DLL bootstrap MUST run at module import time, BEFORE
`from faster_whisper import ...`, otherwise the encoder fails on Windows
with "cublas64_12.dll not found". Side-effecting block stays at module top
on purpose — moving it into __init__ would re-introduce the failure when
faster_whisper is imported above.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
if _nvidia_root.exists():
    for _bin in _nvidia_root.glob("*/bin"):
        os.add_dll_directory(str(_bin))

from faster_whisper import WhisperModel  # noqa: E402

from server import config  # noqa: E402


class WhisperLocalSTT:
    """Singleton-style faster-whisper wrapper. The model is shared across
    instances of this class via the class-level cache so re-instantiating
    in tests doesn't reload the model."""

    _model: WhisperModel | None = None

    def _get_model(self) -> WhisperModel:
        if WhisperLocalSTT._model is None:
            WhisperLocalSTT._model = WhisperModel(
                config.WHISPER_MODEL,
                device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE,
            )
        return WhisperLocalSTT._model

    def is_loaded(self) -> bool:
        return WhisperLocalSTT._model is not None

    def transcribe(self, audio_path: str | Path) -> dict:
        model = self._get_model()
        t0 = time.perf_counter()
        segments, info = model.transcribe(str(audio_path), beam_size=5)
        segments = list(segments)
        elapsed = time.perf_counter() - t0
        text = " ".join(s.text.strip() for s in segments).strip()
        return {
            "transcript": text,
            "duration_s": float(info.duration),
            "latency_s": elapsed,
            "model": config.WHISPER_MODEL,
            "language": info.language,
        }

    def warmup(self) -> None:
        """Load model + run one transcription so PTX JIT compile happens at
        startup, not on the first user request."""
        if config.WARMUP_AUDIO.exists():
            self.transcribe(config.WARMUP_AUDIO)
        else:
            self._get_model()
