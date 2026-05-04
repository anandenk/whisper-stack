"""Piper TTS wrapper. Singleton voice; module-level synth().

Pure helpers (`pcm_to_wav`, `synth_to_wav`, `to_speakable_text`) and local
playback helpers (`synth_and_play`, `synth_play_nonblock`) moved out so cloud
mode can reuse them. They're re-exported here for back-compat with existing
callers — Phase 3 deletes this shim and routes all callers through `modes.get_tts()`.
"""

from __future__ import annotations

from piper import PiperVoice

from server import config

# Re-exports for back-compat. Phase 3 deletes this module; callers move to
# server.markdown / server.audio / server.audio_out / modes.get_tts().
from server.markdown import to_speakable_text  # noqa: F401
from server.audio import pcm_to_wav, synth_to_wav  # noqa: F401
from server.audio_out import synth_and_play, synth_play_nonblock  # noqa: F401

_voice: PiperVoice | None = None


def get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        _voice = PiperVoice.load(str(config.PIPER_MODEL_PATH))
    return _voice


def is_loaded() -> bool:
    return _voice is not None


def synth(text: str) -> tuple[bytes, int]:
    """Synthesize text. Returns (raw int16 PCM mono bytes, sample_rate_hz)."""
    voice = get_voice()
    chunks = list(voice.synthesize(text))
    if not chunks:
        return b"", voice.config.sample_rate
    raw = b"".join(c.audio_int16_bytes for c in chunks)
    return raw, chunks[0].sample_rate


def warmup() -> None:
    """Load voice and synth a tiny string so the first user call is warm."""
    synth("Hi.")
