"""Piper TTS wrapper. Singleton voice; synth + play through default output."""

from __future__ import annotations

import io
import re
import time
import wave

import numpy as np
import sounddevice as sd
from piper import PiperVoice

from server import config


# Markdown -> speakable plain text.
# LLMs slip in markdown even when told not to; Piper reads punctuation literally
# ("**2**" becomes "asterisk asterisk 2 asterisk asterisk"). Strip before synth.
_RE_TRIPLE_BACKTICK = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_RE_LATEX_BLOCK = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_RE_LATEX_INLINE = re.compile(r"\$[^$\n]+?\$")
_RE_LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+\*?(?:\{[^}]*\})*")
_RE_BOLD_ASTERISK = re.compile(r"\*\*\*([^\*\n]+)\*\*\*|\*\*([^\*\n]+)\*\*")
_RE_BOLD_UNDERSCORE = re.compile(r"___([^_\n]+)___|__([^_\n]+)__")
_RE_ITALIC_ASTERISK = re.compile(r"\*([^\*\n]+)\*")
_RE_ITALIC_UNDERSCORE = re.compile(r"\b_([^_\n]+)_\b")
_RE_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_HEADER = re.compile(r"^#+\s+", re.MULTILINE)
_RE_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_RE_HORIZONTAL_RULE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
_RE_BLANK_LINES = re.compile(r"\n\s*\n+")
_RE_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")


def to_speakable_text(text: str) -> str:
    """Strip markdown / LaTeX so the TTS engine reads prose, not punctuation.

    Order matters: drop block-level constructs (code fences, math blocks) before
    inline ones, and strip LaTeX commands BEFORE bold/italic so a stray backslash
    sequence inside math doesn't survive.
    """
    text = _RE_TRIPLE_BACKTICK.sub(lambda m: m.group(1) or "", text)
    text = _RE_INLINE_CODE.sub(r"\1", text)
    text = _RE_LATEX_BLOCK.sub(" ", text)
    text = _RE_LATEX_INLINE.sub(" ", text)
    text = _RE_LATEX_COMMAND.sub(" ", text)
    text = _RE_BOLD_ASTERISK.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _RE_BOLD_UNDERSCORE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _RE_ITALIC_ASTERISK.sub(r"\1", text)
    text = _RE_ITALIC_UNDERSCORE.sub(r"\1", text)
    text = _RE_LINK.sub(r"\1", text)
    text = _RE_HEADER.sub("", text)
    text = _RE_BULLET.sub("", text)
    text = _RE_HORIZONTAL_RULE.sub("", text)
    text = _RE_BLANK_LINES.sub("\n", text)
    text = _RE_RUN_OF_SPACES.sub(" ", text)
    return text.strip()

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
    """Returns full WAV file bytes (mono, 16-bit PCM)."""
    raw, sr = synth(text)
    return pcm_to_wav(raw, sr)


def synth_play_nonblock(text: str) -> tuple[bytes, int, float]:
    """Synth + start non-blocking playback. Returns (raw_pcm, sample_rate, synth_time_s).

    Caller does NOT wait for playback — playback runs in the audio backend thread.
    Note: a subsequent sd.play() call cuts off any in-flight stream. Single user only.
    """
    t0 = time.perf_counter()
    raw, sr = synth(text)
    synth_s = time.perf_counter() - t0
    arr = np.frombuffer(raw, dtype=np.int16)
    sd.play(arr, sr)
    return raw, sr, synth_s


def synth_and_play(text: str) -> dict:
    """Synthesize + play through the system default output. Blocks until done."""
    t0 = time.perf_counter()
    raw, sr = synth(text)
    synth_s = time.perf_counter() - t0

    arr = np.frombuffer(raw, dtype=np.int16)
    duration_s = len(arr) / sr if sr > 0 else 0.0

    play_t0 = time.perf_counter()
    sd.play(arr, sr)
    sd.wait()
    play_s = time.perf_counter() - play_t0

    return {
        "synth_s": synth_s,
        "play_s": play_s,
        "duration_s": duration_s,
        "sample_rate": sr,
        "voice": config.PIPER_VOICE,
    }


def warmup() -> None:
    """Load voice and synth a tiny string so the first user call is warm."""
    synth("Hi.")
