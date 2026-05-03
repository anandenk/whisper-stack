"""faster-whisper wrapper: singleton model, lazy-loaded.

The CTranslate2 DLL bootstrap MUST run before `from faster_whisper import ...`,
otherwise the encoder fails on Windows with "cublas64_12.dll not found".
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

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE,
        )
    return _model


def is_loaded() -> bool:
    return _model is not None


def transcribe(audio_path: str | Path) -> dict:
    model = get_model()
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


def warmup() -> None:
    """Load model and run one transcription so PTX JIT compile happens at startup, not on the first user request."""
    if config.WARMUP_AUDIO.exists():
        transcribe(config.WARMUP_AUDIO)
    else:
        get_model()
