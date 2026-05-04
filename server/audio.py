"""Mode-independent audio helpers: raw int16 PCM <-> WAV bytes.

`synth_to_wav` routes through whichever TTS backend the active MODE selected.
"""

from __future__ import annotations

import io
import wave


def pcm_to_wav(raw: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """Wrap raw int16 PCM as a WAV file. Defaults to mono — pass channels=2
    for stereo."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)
    return buf.getvalue()


def synth_to_wav(text: str) -> bytes:
    """Returns full WAV file bytes (mono, 16-bit PCM) for the given text,
    using the active TTS backend."""
    from server import modes  # lazy: keep config import-time cheap
    raw, sr = modes.get_tts().synth(text)
    return pcm_to_wav(raw, sr)
