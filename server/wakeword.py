"""Server-side openWakeWord predict loop, used by POST /listen.

The chunked-transfer body comes from a client (the ESP-WROOM-32 firmware
in IDLE state, or tests/stage11_listen.py) streaming raw int16 LE mono
@ 16 kHz. We frame to 1280 samples (openWakeWord's preferred 80 ms
frame) and call model.predict() per frame. On the first frame that
crosses WAKEWORD_THRESHOLD we close out with a detection result. After
IDLE_TIMEOUT_S of bytes-but-no-detection we close out with idle_timeout
so the device re-arms cleanly without leaking a long-lived socket
through any router NAT.

A process-level Model is reused across requests under a lock — only
one /listen at a time runs predictions. Single-device deployments
don't need more, and shared-mutable model state is the failure mode
this module is designed to avoid. v2 can grow a pool here if
multi-device becomes a real shape.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from server import config

# 1280 samples @ 16 kHz = 80 ms — openWakeWord's preferred frame.
# Anything else and the model rebuffers internally; matching the
# preferred shape keeps detection latency at exactly one frame.
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * 2  # int16 LE

# Per-spec: cap an open /listen at 10 minutes so the device re-arms
# cleanly. Tunable via env for tests + edge deployments. Module-level
# (not a config knob) because the firmware contract bakes 10 min in.
IDLE_TIMEOUT_S = 10 * 60

# Read from the request stream in 4 KiB chunks. Smaller than a frame
# (2560 B) so we don't sit on a half-frame across a slow link, but
# big enough to amortize syscall overhead. Bounded reads also let
# the deadline check fire reasonably promptly during idle stretches.
READ_CHUNK_BYTES = 4096

_LOCK = threading.Lock()
_MODEL = None
_MODEL_NAME: str | None = None


def is_available() -> bool:
    """True iff openwakeword can be imported. Route returns 503 when False
    so a server without the optional dep gives a clear error instead of a
    500 stacktrace."""
    try:
        import openwakeword  # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_model():
    """Lazy-load the singleton Model. Caller must hold _LOCK."""
    global _MODEL, _MODEL_NAME
    if _MODEL is not None:
        return _MODEL
    # Lazy import: keep the dep optional. Server boots without
    # openwakeword installed — only /listen needs it.
    from openwakeword.model import Model
    _MODEL = Model(wakeword_models=[config.WAKEWORD_MODEL])
    _MODEL_NAME = config.WAKEWORD_MODEL
    return _MODEL


def stream_predict(read_chunk: Callable[[int], bytes]) -> dict:
    """Run wake-word inference over a stream of bytes until detection,
    timeout, or client EOF. Returns a JSON-shaped result dict in every
    case — never raises for the normal control flow.

    read_chunk: callable taking max_bytes and returning up to that many
        bytes (b"" on EOF). Wraps Flask's request.stream.read for the
        route handler; tests pass a synthetic byte source's read method.

    Result shapes:
      detection:    {"wake": True,  "score": float, "model": str}
      idle timeout: {"wake": False, "reason": "idle_timeout"}
      client EOF:   {"wake": False, "reason": "client_closed"}
    """
    import numpy as np

    with _LOCK:
        model = _ensure_model()
        # Reset between arms so leftover scores from the previous
        # request can't carry into this one as a phantom detection.
        model.reset()
        threshold = float(config.WAKEWORD_THRESHOLD)

        buf = bytearray()
        deadline = time.monotonic() + IDLE_TIMEOUT_S

        while True:
            if time.monotonic() >= deadline:
                return {"wake": False, "reason": "idle_timeout"}

            chunk = read_chunk(READ_CHUNK_BYTES)
            if not chunk:
                # Client closed the connection. Treat as graceful per the
                # /listen spec — the firmware uses TCP close to cancel.
                return {"wake": False, "reason": "client_closed"}
            buf.extend(chunk)

            # Drain whole frames. A slow uploader may take several reads
            # to accumulate one frame; a fast one may hand us several
            # frames at once. Either is fine.
            while len(buf) >= FRAME_BYTES:
                frame = bytes(buf[:FRAME_BYTES])
                del buf[:FRAME_BYTES]
                samples = np.frombuffer(frame, dtype=np.int16)
                scores = model.predict(samples)
                score = float(scores.get(_MODEL_NAME, 0.0))
                if score >= threshold:
                    return {
                        "wake": True,
                        "score": score,
                        "model": _MODEL_NAME,
                    }
