"""Piper TTS backend (local mode)."""

from __future__ import annotations

from piper import PiperVoice

from server import config


class PiperTTS:
    """Singleton-style Piper voice. The voice is shared across instances of
    this class via the class-level cache to avoid double-loading."""

    _voice: PiperVoice | None = None

    def _get_voice(self) -> PiperVoice:
        if PiperTTS._voice is None:
            PiperTTS._voice = PiperVoice.load(str(config.PIPER_MODEL_PATH))
        return PiperTTS._voice

    def is_loaded(self) -> bool:
        return PiperTTS._voice is not None

    def synth(self, text: str) -> tuple[bytes, int]:
        """Synthesize text. Returns (raw int16 PCM mono bytes, sample_rate_hz)."""
        voice = self._get_voice()
        chunks = list(voice.synthesize(text))
        if not chunks:
            return b"", voice.config.sample_rate
        raw = b"".join(c.audio_int16_bytes for c in chunks)
        return raw, chunks[0].sample_rate

    def warmup(self) -> None:
        """Load voice and synth a tiny string so the first user call is warm."""
        self.synth("Hi.")
