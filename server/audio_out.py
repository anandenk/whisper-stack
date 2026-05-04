"""Local audio playback helpers (sounddevice).

`LOCAL_PLAYBACK_AVAILABLE` is set from `config.MODE` at import time. In cloud
mode (where the VPS has no audio device) playback paths become no-ops — the
PCM bytes are still returned so the route handler can base64-encode them for
the client to play.
"""

from __future__ import annotations

import time

import numpy as np
import sounddevice as sd

from server import config

LOCAL_PLAYBACK_AVAILABLE = config.MODE == "local"


def synth_play_nonblock(text: str) -> tuple[bytes, int, float]:
    """Synth + start non-blocking playback. Returns (raw_pcm, sample_rate, synth_time_s).

    Caller does NOT wait for playback — playback runs in the audio backend thread.
    Note: a subsequent sd.play() call cuts off any in-flight stream. Single user only.
    """
    from server import modes  # lazy

    t0 = time.perf_counter()
    raw, sr = modes.get_tts().synth(text)
    synth_s = time.perf_counter() - t0
    if LOCAL_PLAYBACK_AVAILABLE:
        arr = np.frombuffer(raw, dtype=np.int16)
        sd.play(arr, sr)
    return raw, sr, synth_s


def synth_and_play(text: str) -> dict:
    """Synthesize + play through the system default output. Blocks until done.

    In cloud mode the play step is a no-op (no audio device); synth still runs
    so /speak can report timings."""
    from server import modes  # lazy

    t0 = time.perf_counter()
    raw, sr = modes.get_tts().synth(text)
    synth_s = time.perf_counter() - t0

    arr = np.frombuffer(raw, dtype=np.int16)
    duration_s = len(arr) / sr if sr > 0 else 0.0

    play_s = 0.0
    if LOCAL_PLAYBACK_AVAILABLE:
        play_t0 = time.perf_counter()
        sd.play(arr, sr)
        sd.wait()
        play_s = time.perf_counter() - play_t0

    voice_label = config.PIPER_VOICE if config.MODE == "local" else config.OPENAI_TTS_VOICE
    return {
        "synth_s": synth_s,
        "play_s": play_s,
        "duration_s": duration_s,
        "sample_rate": sr,
        "voice": voice_label,
    }
