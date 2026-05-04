"""Piper TTS backend (local mode)."""

from __future__ import annotations

from typing import Iterable, Iterator

from piper import PiperVoice, SynthesisConfig

from server import config


class PiperTTS:
    """Singleton-style Piper voice. The voice is shared across instances of
    this class via the class-level cache to avoid double-loading."""

    _voice: PiperVoice | None = None

    def _get_voice(self) -> PiperVoice:
        if PiperTTS._voice is None:
            PiperTTS._voice = PiperVoice.load(str(config.PIPER_MODEL_PATH))
        return PiperTTS._voice

    def _syn_config(self) -> SynthesisConfig:
        # Built fresh per call so a hot-reloaded config takes effect on the
        # next synth without a process restart.
        return SynthesisConfig(length_scale=config.PIPER_LENGTH_SCALE)

    def is_loaded(self) -> bool:
        return PiperTTS._voice is not None

    def synth(self, text: str) -> tuple[bytes, int]:
        """Synthesize text. Returns (raw int16 PCM mono bytes, sample_rate_hz)."""
        voice = self._get_voice()
        chunks = list(voice.synthesize(text, syn_config=self._syn_config()))
        if not chunks:
            return b"", voice.config.sample_rate
        raw = b"".join(c.audio_int16_bytes for c in chunks)
        return raw, chunks[0].sample_rate

    def synth_stream(self, text_iter: Iterable[str]) -> Iterator[tuple[bytes, int]]:
        """Per-sentence streaming synth. Pulls one sentence from `text_iter`,
        synthesizes it (Piper itself yields multiple PCM chunks per
        sentence — we forward each unchanged), then pulls the next.
        Empty / whitespace-only sentences are skipped so a stray flush
        doesn't fire a zero-length voice call."""
        voice = self._get_voice()
        syn_config = self._syn_config()
        for text in text_iter:
            if not text or not text.strip():
                continue
            for chunk in voice.synthesize(text, syn_config=syn_config):
                yield chunk.audio_int16_bytes, chunk.sample_rate

    def warmup(self) -> None:
        """Load voice and synth a tiny string so the first user call is warm."""
        self.synth("Hi.")
