"""Acoustic loopback test for the ESP-WROOM-32 voice device.

Plays "hey jarvis" + a known test passage through the workstation
speakers in a loop. The device should wake on the prefix, record the
passage, and POST to /converse. The server saves each upload to
samples/_loopback_turn_<unix-ts>.wav (when LOOPBACK_KEEP_PER_TURN=1).

The goal isn't to test whisper accuracy — it's to determine whether
the audio that reaches the server matches what was played. If the
firmware's mic/VAD/HTTP pipeline corrupts audio, the per-turn WAVs
will be short, silent, or garbled even though the room is quiet and
the stimulus is identical every iteration.

Pacing:

The workstation TTS speaks the reply through the same Realtek output
device that this script uses to drive the device's mic. Two collisions
to avoid:
  - our stimulus colliding with a still-playing reply
  - the reply re-waking the device mid-iteration

We mitigate with a long fixed delay between iteration STARTs
(default 45 s) — long enough for Sonnet's voice-prompted 1-2 sentence
reply at PIPER_LENGTH_SCALE=0.85 to finish (typically ~5-10 s) plus
the device's VAD silence-dwell, plus generous slack.

Usage:

    .venv/Scripts/python.exe tools/loopback_test.py [N_ITERATIONS]

Default N_ITERATIONS=10. Output:
  - notes/loopback-iterations.jsonl  (one line per iteration with timestamps)
  - samples/_loopback_prefix.wav     (cached "hey jarvis" prefix)

Pair the timestamps with samples/_loopback_turn_*.wav (created by the
server) and notes/loopback-monitor.log (from tools/serial_capture.py)
via tools/loopback_analyze.py.
"""

from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from server import config  # noqa: E402
from server import audio_out  # noqa: E402, F401 — import side-effect pins TTS_OUTPUT_DEVICE


PREFIX_WAV = REPO_ROOT / "samples" / "_loopback_prefix.wav"
PASSAGE_WAV = REPO_ROOT / "samples" / "jfk.wav"
ITERATIONS_LOG = REPO_ROOT / "notes" / "loopback-iterations.jsonl"

ITERATION_PERIOD_S = 45.0
PREFIX_TO_PASSAGE_GAP_S = 0.8  # short silence between "hey jarvis" and the passage
PRE_PREFIX_SILENCE_S = 1.5     # quiet window before the wake word


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise RuntimeError(f"{path}: expected int16 WAV, got sampwidth={wf.getsampwidth()}")
        n = wf.getnframes()
        rate = wf.getframerate()
        ch = wf.getnchannels()
        raw = wf.readframes(n)
    pcm = np.frombuffer(raw, dtype=np.int16)
    if ch == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return pcm, rate


def _write_wav(path: Path, pcm: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.astype(np.int16).tobytes())


def _ensure_prefix() -> tuple[np.ndarray, int]:
    """Synthesize 'hey jarvis' via Piper if not already cached."""
    if PREFIX_WAV.exists():
        return _read_wav(PREFIX_WAV)
    print(f"[loopback] synthesizing wake prefix via Piper...", flush=True)
    from server import modes
    tts = modes.get_tts()
    # Piper's "Hi." warmup already happens elsewhere; just synthesize the prefix.
    pcm_bytes, sr = tts.synth("Hey, Jarvis.")
    pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
    _write_wav(PREFIX_WAV, pcm, sr)
    print(f"[loopback] wrote {PREFIX_WAV} ({len(pcm)/sr:.2f} s @ {sr} Hz)", flush=True)
    return pcm, sr


def _play_blocking(pcm: np.ndarray, rate: int) -> None:
    sd.play(pcm, samplerate=rate)
    sd.wait()


def _silence(seconds: float, rate: int) -> None:
    if seconds <= 0:
        return
    n = int(seconds * rate)
    sd.play(np.zeros(n, dtype=np.int16), samplerate=rate)
    sd.wait()


def play_stimulus(prefix_pcm: np.ndarray, prefix_rate: int,
                  passage_pcm: np.ndarray, passage_rate: int) -> dict:
    """Play one stimulus iteration. Returns timestamps for the log."""
    log: dict = {}
    log["t_iteration_start"] = time.time()

    # Quiet lead-in so wake word starts from silence (helps openWakeWord)
    _silence(PRE_PREFIX_SILENCE_S, 16000)

    log["t_prefix_start"] = time.time()
    _play_blocking(prefix_pcm, prefix_rate)
    log["t_prefix_end"] = time.time()

    _silence(PREFIX_TO_PASSAGE_GAP_S, 16000)

    log["t_passage_start"] = time.time()
    _play_blocking(passage_pcm, passage_rate)
    log["t_passage_end"] = time.time()

    return log


def main():
    n_iters = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print(f"[loopback] output device (sd.default.device): {sd.default.device!r} "
          f"(config.TTS_OUTPUT_DEVICE={config.TTS_OUTPUT_DEVICE!r})", flush=True)

    prefix_pcm, prefix_rate = _ensure_prefix()
    print(f"[loopback] prefix: {len(prefix_pcm)/prefix_rate:.2f} s @ {prefix_rate} Hz", flush=True)
    passage_pcm, passage_rate = _read_wav(PASSAGE_WAV)
    print(f"[loopback] passage: {len(passage_pcm)/passage_rate:.2f} s @ {passage_rate} Hz "
          f"({PASSAGE_WAV.name})", flush=True)

    ITERATIONS_LOG.parent.mkdir(parents=True, exist_ok=True)

    print(f"[loopback] starting {n_iters} iterations, period={ITERATION_PERIOD_S}s. "
          f"Total wall-clock ~{n_iters * ITERATION_PERIOD_S:.0f}s.", flush=True)
    print(f"[loopback] log -> {ITERATIONS_LOG}", flush=True)

    with open(ITERATIONS_LOG, "w", encoding="utf-8") as fp:
        for i in range(1, n_iters + 1):
            iter_t0 = time.monotonic()
            print(f"[loopback] === iter {i}/{n_iters} === t={time.time():.3f}", flush=True)
            try:
                log = play_stimulus(prefix_pcm, prefix_rate, passage_pcm, passage_rate)
            except Exception as exc:
                log = {"t_iteration_start": time.time(), "error": repr(exc)}
                print(f"[loopback] iter {i} playback error: {exc!r}", flush=True)
            log["iter"] = i
            fp.write(json.dumps(log) + "\n")
            fp.flush()

            elapsed = time.monotonic() - iter_t0
            remaining = ITERATION_PERIOD_S - elapsed
            if i < n_iters and remaining > 0:
                print(f"[loopback] sleeping {remaining:.1f}s for next iteration "
                      f"(reply playback + idle)", flush=True)
                time.sleep(remaining)

    print(f"[loopback] done. Iterations log: {ITERATIONS_LOG}", flush=True)


if __name__ == "__main__":
    main()
