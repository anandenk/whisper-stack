"""Stage 11 verification: POST /listen + openWakeWord (M4 server-side scope).

Smokes the new /listen endpoint along three axes:

  1. Negative wire-format checks: bad Content-Type and bad rate/channels
     return 415 with a clear error.
  2. EOF handling: a client that sends some silence then closes the
     stream gets {"wake": false, "reason": "client_closed"} — the path
     the device firmware will hit when it cancels (e.g. on shutdown).
  3. Best-effort detection: synthesize a "hey jarvis" utterance via
     Piper, resample to 16 kHz, stream it through chunked-transfer +
     trailing silence. Log the response. Detection is NOT asserted —
     openWakeWord is trained on real-speech and TTS-synthesized
     "hey jarvis" doesn't reliably cross threshold. Real-mic detection
     is verified manually at the firmware bring-up stage.

Why no idle-timeout HTTP test: the cap is 10 minutes per spec, and the
server is a separate process so we can't monkeypatch IDLE_TIMEOUT_S
across the boundary. The cap is exercised in-process below
(test_idle_timeout_inproc) which drives wakeword.stream_predict
directly with a synthetic reader and a small monkey-patched timeout.

Requires the server to be running with openwakeword installed and
models downloaded:
    python -m server.app
    .venv/Scripts/python.exe -m pip install openwakeword
    .venv/Scripts/python.exe -c "import openwakeword; \
        openwakeword.utils.download_models()"

Run from repo root after activating .venv:
    python tests/stage11_listen.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: F401  -- triggers .env load so API_KEY is in os.environ

BASE = "http://127.0.0.1:5000"
API_KEY = os.environ.get("API_KEY", "").strip()
AUTH = {"X-API-Key": API_KEY} if API_KEY else {}

# Frame shape openWakeWord wants. The client may send any chunk size — the
# server rebuffers — but we send 80 ms chunks here both because that's what
# the firmware will do and because it makes the chunked-transfer cadence
# resemble real-world.
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * 2


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def wait_for_health(timeout_s: float = 60.0) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/health", timeout=2)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException as exc:
            last = exc
        time.sleep(1)
    print(f"FAIL: server did not respond on /health within {timeout_s}s. Last: {last!r}",
          file=sys.stderr)
    sys.exit(1)


def resample_int16(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear-interpolation resampler. Rough but adequate for wake-word —
    "hey jarvis" is robust to mild aliasing, and the test's job is the
    wire format, not detection accuracy."""
    if src_sr == dst_sr:
        return samples.astype(np.int16, copy=False)
    ratio = dst_sr / src_sr
    dst_len = int(len(samples) * ratio)
    src_idx = np.arange(dst_len) / ratio
    f = samples.astype(np.float32)
    floor_idx = np.clip(np.floor(src_idx).astype(np.int64), 0, len(f) - 2)
    frac = src_idx - floor_idx
    dst = f[floor_idx] * (1.0 - frac) + f[floor_idx + 1] * frac
    return np.clip(dst, -32768, 32767).astype(np.int16)


def chunks_of(data: bytes, size: int):
    """Yield successive `size`-byte chunks. requests sends chunked transfer
    when data= is a generator (no Content-Length)."""
    for i in range(0, len(data), size):
        yield data[i:i + size]


def synth_hey_jarvis_pcm16k() -> bytes:
    """Synthesize 'hey jarvis' through the active TTS, resample to 16 kHz
    mono int16, return raw PCM bytes (NOT WAV-wrapped — that's what /listen
    consumes)."""
    from server import modes
    raw_bytes, sr = modes.get_tts().synth("hey jarvis")
    samples = np.frombuffer(raw_bytes, dtype=np.int16)
    if sr != 16000:
        samples = resample_int16(samples, sr, 16000)
    return samples.tobytes()


def silence_pcm16k(duration_s: float) -> bytes:
    n = int(16000 * duration_s)
    return np.zeros(n, dtype=np.int16).tobytes()


# ---------------------------------------------------------------------------
# 1. negative wire-format checks
# ---------------------------------------------------------------------------

def test_rejects_bad_content_type() -> None:
    print("[1] /listen rejects non-audio/l16 Content-Type")
    r = requests.post(f"{BASE}/listen",
                      data=b"\x00" * 1280,
                      headers={"Content-Type": "audio/wav", **AUTH},
                      timeout=10)
    must(r.status_code == 415,
         f"expected 415 for audio/wav, got {r.status_code}: {r.text[:200]}")
    body = r.json()
    must("error" in body and "audio/l16" in body["error"],
         f"error message should mention audio/l16: {body}")
    print(f"  OK ({r.status_code} {body['error']!r})")


def test_rejects_bad_rate_channels() -> None:
    print("[2] /listen rejects rate != 16000 / channels != 1")
    # 44.1k mono — wrong rate
    r = requests.post(f"{BASE}/listen",
                      data=b"\x00" * 1280,
                      headers={"Content-Type": "audio/l16; rate=44100; channels=1",
                               **AUTH},
                      timeout=10)
    must(r.status_code == 415,
         f"expected 415 for rate=44100, got {r.status_code}: {r.text[:200]}")
    body = r.json()
    must("rate=44100" in body.get("error", ""),
         f"error should name the bad rate: {body}")
    # 16k stereo — wrong channels
    r = requests.post(f"{BASE}/listen",
                      data=b"\x00" * 1280,
                      headers={"Content-Type": "audio/l16; rate=16000; channels=2",
                               **AUTH},
                      timeout=10)
    must(r.status_code == 415,
         f"expected 415 for channels=2, got {r.status_code}: {r.text[:200]}")
    print("  OK")


# ---------------------------------------------------------------------------
# 2. EOF handling
# ---------------------------------------------------------------------------

def test_client_close_returns_client_closed() -> None:
    print("[3] /listen returns wake=false reason=client_closed when stream ends")
    pcm = silence_pcm16k(0.5)  # half a second of silence — no wake
    r = requests.post(
        f"{BASE}/listen",
        data=chunks_of(pcm, FRAME_BYTES),  # generator → chunked transfer
        headers={
            "Content-Type": "audio/l16; rate=16000; channels=1",
            **AUTH,
        },
        timeout=30,
    )
    must(r.status_code == 200,
         f"expected 200 on graceful close, got {r.status_code}: {r.text[:200]}")
    body = r.json()
    must(body.get("wake") is False, f"wake should be false: {body}")
    must(body.get("reason") == "client_closed",
         f"reason should be client_closed: {body}")
    print(f"  OK ({body})")


# ---------------------------------------------------------------------------
# 3. best-effort detection (logs only — does not assert wake==True)
# ---------------------------------------------------------------------------

def test_synth_hey_jarvis_best_effort() -> dict:
    print("[4] /listen with TTS 'hey jarvis' (best-effort; logs the score)")
    print("    NOTE: openWakeWord is trained on real speech; TTS-synthesized")
    print("    'hey jarvis' may not cross threshold. Real-mic detection is")
    print("    verified at firmware bring-up, not here.")
    pcm = synth_hey_jarvis_pcm16k()
    pcm += silence_pcm16k(0.5)  # trailing silence so the model has flush room
    r = requests.post(
        f"{BASE}/listen",
        data=chunks_of(pcm, FRAME_BYTES),
        headers={
            "Content-Type": "audio/l16; rate=16000; channels=1",
            **AUTH,
        },
        timeout=60,
    )
    must(r.status_code == 200,
         f"expected 200, got {r.status_code}: {r.text[:200]}")
    body = r.json()
    must("wake" in body, f"response missing 'wake' key: {body}")
    if body["wake"]:
        must("score" in body and "model" in body,
             f"detection result missing keys: {body}")
        print(f"  detection FIRED: {body}")
    else:
        must(body.get("reason") in ("client_closed", "idle_timeout"),
             f"unexpected non-detection reason: {body}")
        print(f"  no detection: {body}")
    return body


# ---------------------------------------------------------------------------
# 4. in-process idle-timeout test (drives wakeword.stream_predict directly)
# ---------------------------------------------------------------------------

def test_idle_timeout_inproc() -> None:
    print("[5] wakeword.stream_predict idle-timeout path (in-process)")
    from server import wakeword

    if not wakeword.is_available():
        print("  SKIP: openwakeword not installed")
        return

    # Monkeypatch the cap down so the test runs in seconds, not 10 min.
    # We're driving the function directly here, not through HTTP, so
    # the patch sticks for this call.
    original = wakeword.IDLE_TIMEOUT_S
    wakeword.IDLE_TIMEOUT_S = 0.5
    try:
        # A reader that always returns a small silence chunk — mimics a
        # client that streams forever without speaking. Should hit the
        # deadline check before any wake fires.
        def reader(_n: int) -> bytes:
            time.sleep(0.05)  # don't busy-loop the lock
            return np.zeros(160, dtype=np.int16).tobytes()  # 10 ms of silence

        result = wakeword.stream_predict(reader)
    finally:
        wakeword.IDLE_TIMEOUT_S = original

    must(result.get("wake") is False, f"wake should be false: {result}")
    must(result.get("reason") == "idle_timeout",
         f"reason should be idle_timeout: {result}")
    print(f"  OK ({result})")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"Waiting for server at {BASE}/health ...")
    h = wait_for_health()
    must(h.get("status") == "ok", f"unexpected /health: {h}")
    print(f"  -> mode={h.get('mode')!r}\n")

    test_rejects_bad_content_type()
    test_rejects_bad_rate_channels()
    test_client_close_returns_client_closed()
    detection = test_synth_hey_jarvis_best_effort()
    test_idle_timeout_inproc()

    # Re-fetch /health and confirm the wake_detections counter is present.
    # Increment-by-one is conditional on detection actually firing in [4],
    # which we don't assert, so we only check the field exists.
    h2 = requests.get(f"{BASE}/health", timeout=5).json()
    usage_block = h2.get("usage", {})
    must("wake_detections" in usage_block,
         f"/health.usage missing wake_detections: {usage_block}")
    print(f"\n  /health.usage.wake_detections = {usage_block['wake_detections']}")

    print("=" * 64)
    print("  Stage 11 (POST /listen wire format) verified.")
    print(f"  detection-best-effort: wake={detection.get('wake')} "
          f"score={detection.get('score', 'n/a')}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
