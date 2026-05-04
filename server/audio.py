"""Mode-independent audio helpers: raw int16 PCM <-> WAV bytes.

`synth_to_wav` lazily routes through the active TTS backend; in Phase 1 it
imports `server.tts` (which still wraps Piper). Phase 3 swaps that to
`modes.get_tts()` so cloud-mode TTS backends work the same way.
"""

from __future__ import annotations

import io
import wave


def pcm_to_wav(raw: bytes, sample_rate: int) -> bytes:
    """Wrap raw int16 mono PCM as a WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)
    return buf.getvalue()


def synth_to_wav(text: str) -> bytes:
    """Returns full WAV file bytes (mono, 16-bit PCM) for the given text."""
    from server import tts  # lazy to avoid import cycle (tts re-exports from here)
    raw, sr = tts.synth(text)
    return pcm_to_wav(raw, sr)
