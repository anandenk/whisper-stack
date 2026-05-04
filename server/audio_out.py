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

# Pin the output device when configured. TTS_OUTPUT_DEVICE accepts either:
#   * an integer index (most reliable on Windows where the same device name
#     appears under MME / DirectSound / WASAPI host APIs and substring
#     matching becomes ambiguous), or
#   * a substring of the device name — sounddevice raises ValueError when
#     multiple devices match, so the substring must be unique across BOTH
#     input and output device names.
# Run `python -c "import sounddevice as sd; print(sd.query_devices())"` to
# enumerate. Failure here is non-fatal: we fall back to the system default
# and warn so the user sees the misconfiguration on boot rather than at
# first /speak call.
if LOCAL_PLAYBACK_AVAILABLE and config.TTS_OUTPUT_DEVICE:
    raw = config.TTS_OUTPUT_DEVICE
    try:
        target = int(raw) if raw.lstrip("-").isdigit() else raw
        sd.default.device = (None, target)
        _idx = sd.default.device[1]
        _picked = sd.query_devices(_idx)["name"]
        print(f"TTS output device pinned: {raw!r} -> [{_idx}] {_picked!r}",
              flush=True)
    except Exception as exc:
        print(f"WARNING: TTS_OUTPUT_DEVICE={raw!r} did not resolve to a "
              f"unique output device ({exc!r}). Falling back to system "
              f"default. Try a more specific substring or an integer index "
              f"from `python -c \"import sounddevice as sd; print(sd.query_devices())\"`.",
              flush=True)


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
