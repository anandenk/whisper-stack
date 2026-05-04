"""Back-compat shim — STT singleton wrapping WhisperLocalSTT.

Phase 2 keeps existing call sites (`stt.transcribe(...)`, `stt.warmup()`,
`stt.is_loaded()`) working. Phase 3 deletes this file and routes app.py
through `modes.get_stt()` directly.
"""

from __future__ import annotations

from pathlib import Path

from server.backends.stt_whisper import WhisperLocalSTT

_singleton = WhisperLocalSTT()


def is_loaded() -> bool:
    return _singleton.is_loaded()


def transcribe(audio_path: str | Path) -> dict:
    return _singleton.transcribe(audio_path)


def warmup() -> None:
    _singleton.warmup()
