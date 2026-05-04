"""Back-compat shim — TTS singleton wrapping PiperTTS.

Phase 2 keeps existing call sites (`tts.synth(...)`, `tts.warmup()`,
`tts.is_loaded()`) working. Pure helpers continue to be re-exported from
their new homes.

Phase 3 deletes this file and routes app.py through `modes.get_tts()` directly.
"""

from __future__ import annotations

from server.backends.tts_piper import PiperTTS

# Re-exports for back-compat (these moved out in Phase 1).
from server.markdown import to_speakable_text  # noqa: F401
from server.audio import pcm_to_wav, synth_to_wav  # noqa: F401
from server.audio_out import synth_and_play, synth_play_nonblock  # noqa: F401

_singleton = PiperTTS()


def is_loaded() -> bool:
    return _singleton.is_loaded()


def synth(text: str) -> tuple[bytes, int]:
    return _singleton.synth(text)


def warmup() -> None:
    _singleton.warmup()
