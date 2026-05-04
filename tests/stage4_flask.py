"""Stage 4 verification: hit /health, /transcribe, /chat against a running server.

Requires the server to be running first:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage4_flask.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from server import config  # noqa: F401  -- triggers .env load so API_KEY is in os.environ

BASE = "http://127.0.0.1:5000"
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "jfk.wav"
API_KEY = os.environ.get("API_KEY", "").strip()
AUTH_HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def wait_for_health(timeout_s: float = 60.0) -> dict:
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/health", timeout=2)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException as exc:
            last_err = exc
        time.sleep(1)
    print(f"FAIL: server did not respond on /health within {timeout_s}s. Last error: {last_err!r}", file=sys.stderr)
    print(f"Did you start it? `python -m server.app`", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    must(SAMPLE.exists(), f"sample audio missing at {SAMPLE}")

    print(f"Waiting for server at {BASE}/health ...")
    h = wait_for_health()
    must(h.get("status") == "ok", f"unexpected /health body: {h}")
    print(f"  -> {h}")
    print()

    print(f"POST /transcribe  (audio=@samples/{SAMPLE.name})")
    with SAMPLE.open("rb") as fh:
        r = requests.post(
            f"{BASE}/transcribe",
            files={"audio": (SAMPLE.name, fh, "audio/wav")},
            headers=AUTH_HEADERS,
            timeout=60,
        )
    must(r.status_code == 200, f"status {r.status_code}: {r.text}")
    t = r.json()
    for k in ("transcript", "duration_s", "latency_s", "model"):
        must(k in t, f"missing key {k!r} in response: {t}")
    must("Americans" in t["transcript"], f"unexpected transcript: {t['transcript']!r}")
    print(f"  transcript: {t['transcript']}")
    print(f"  duration={t['duration_s']:.2f}s  latency={t['latency_s']:.2f}s  model={t['model']}")
    print()

    # Use a query that routes complex so we exercise the Claude path end-to-end.
    msg = "Write a one-line Python function that adds two numbers."
    print(f"POST /chat  (message={msg!r})")
    r = requests.post(f"{BASE}/chat", json={"message": msg}, headers=AUTH_HEADERS, timeout=30)
    must(r.status_code == 200, f"status {r.status_code}: {r.text}")
    c = r.json()
    for k in ("reply", "model", "latency_s", "input_tokens", "output_tokens"):
        must(k in c, f"missing key {k!r} in response: {c}")
    must(isinstance(c["reply"], str) and c["reply"], f"empty reply: {c}")
    print(f"  reply: {c['reply']}")
    print(f"  model={c['model']}  latency={c['latency_s']:.2f}s  in={c['input_tokens']}  out={c['output_tokens']}")
    print()

    print("POST /chat with no body  -> expect 400")
    r = requests.post(f"{BASE}/chat", json={}, headers=AUTH_HEADERS, timeout=10)
    must(r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}")
    print(f"  -> 400 {r.json()}")
    print()

    print("POST /transcribe with no file  -> expect 400")
    r = requests.post(f"{BASE}/transcribe", headers=AUTH_HEADERS, timeout=10)
    must(r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}")
    print(f"  -> 400 {r.json()}")
    print()

    print("=" * 64)
    print("  All stage 4 checks passed.")
    print()
    print("  Manual probes:")
    print(f"    curl {BASE}/health")
    print(f"    curl -F 'audio=@samples/{SAMPLE.name}' {BASE}/transcribe")
    print(f"    curl -H 'Content-Type: application/json' -d '{{\"message\":\"hello\"}}' {BASE}/chat")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
