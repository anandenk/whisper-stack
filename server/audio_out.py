"""Local audio playback helpers (sounddevice).

In local mode these synth + play through the system default output. In cloud
mode (Phase 3 wires `LOCAL_PLAYBACK_AVAILABLE`) the play paths become no-ops
since a VPS has no audio device.

Both helpers source `synth()` lazily from `server.tts` in Phase 1; Phase 3
re-routes through `modes.get_tts()`.
"""

from __future__ import annotations

import time

import numpy as np
import sounddevice as sd

from server import config

# Toggled by the modes factory at process boot. Phase 1 default = True; Phase 3
# sets this to False when MODE=cloud so misconfigured clients sending
# play_locally=true don't crash a server that has no audio device.
LOCAL_PLAYBACK_AVAILABLE = True


def synth_play_nonblock(text: str) -> tuple[bytes, int, float]:
    """Synth + start non-blocking playback. Returns (raw_pcm, sample_rate, synth_time_s).

    Caller does NOT wait for playback — playback runs in the audio backend thread.
    Note: a subsequent sd.play() call cuts off any in-flight stream. Single user only.

    On a cloud server (no audio device) the play() call is suppressed but the
    PCM is still returned so the route handler can base64-encode it for the client.
    """
    from server import tts  # lazy

    t0 = time.perf_counter()
    raw, sr = tts.synth(text)
    synth_s = time.perf_counter() - t0
    if LOCAL_PLAYBACK_AVAILABLE:
        arr = np.frombuffer(raw, dtype=np.int16)
        sd.play(arr, sr)
    return raw, sr, synth_s


def synth_and_play(text: str) -> dict:
    """Synthesize + play through the system default output. Blocks until done."""
    from server import tts  # lazy

    t0 = time.perf_counter()
    raw, sr = tts.synth(text)
    synth_s = time.perf_counter() - t0

    arr = np.frombuffer(raw, dtype=np.int16)
    duration_s = len(arr) / sr if sr > 0 else 0.0

    play_s = 0.0
    if LOCAL_PLAYBACK_AVAILABLE:
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
