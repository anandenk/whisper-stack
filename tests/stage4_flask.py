"""Stage 4 verification: hit /health, /transcribe, /chat against a running server.

Requires the server to be running first:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage4_flask.py
"""

from __future__ import annotations

import os
import secrets
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

    # ----- Phase C: session_id wiring on /chat -----
    # Three sequential calls with the same session_id; turn 2 establishes a
    # fact, turn 3 asks for it back. Forces 'simple' route so we test the
    # OllamaLLM /api/chat history path through the whole stack.
    #
    # Per-run unique session_id so back-to-back runs within the idle window
    # (10 min default) don't accumulate turns in the same file and break the
    # "exactly 3 turns" assertion below.
    sid = f"stage4-test-{secrets.token_hex(3)}"
    print(f"POST /chat  (session-aware probe via session_id={sid!r})")

    # Turn 1: plain greeting — no fact yet.
    r = requests.post(
        f"{BASE}/chat",
        json={"message": "Hello.", "session_id": sid, "force_route": "simple"},
        headers=AUTH_HEADERS, timeout=60,
    )
    must(r.status_code == 200, f"turn 1 status {r.status_code}: {r.text}")
    d1 = r.json()
    must("session_file" in d1, f"session_file missing in response: {d1.keys()}")
    must(d1["session_file"].startswith(f"{sid}-"),
         f"session_file should start with '{sid}-', got {d1['session_file']!r}")
    print(f"  turn 1 reply: {d1['reply'][:80]!r}  session_file={d1['session_file']}")

    # Turn 2: establish a fact.
    r = requests.post(
        f"{BASE}/chat",
        json={"message": "My favorite color is teal.",
              "session_id": sid, "force_route": "simple"},
        headers=AUTH_HEADERS, timeout=60,
    )
    must(r.status_code == 200, f"turn 2 status {r.status_code}: {r.text}")
    d2 = r.json()
    must(d2["session_file"] == d1["session_file"],
         f"session_file should be stable across turns: {d1['session_file']} vs {d2['session_file']}")
    print(f"  turn 2 reply: {d2['reply'][:80]!r}")

    # Turn 3: recall the fact.
    r = requests.post(
        f"{BASE}/chat",
        json={"message": "What's my favorite color? Reply in one short sentence.",
              "session_id": sid, "force_route": "simple"},
        headers=AUTH_HEADERS, timeout=60,
    )
    must(r.status_code == 200, f"turn 3 status {r.status_code}: {r.text}")
    d3 = r.json()
    print(f"  turn 3 reply: {d3['reply']!r}")
    must("teal" in d3["reply"].lower(),
         f"history not consumed — turn 3 reply missing 'teal': {d3['reply']!r}")

    # Verify the file actually landed on disk with all three turns.
    sessions_dir = Path(__file__).resolve().parent.parent / "sessions"
    session_path = sessions_dir / d1["session_file"]
    must(session_path.exists(), f"expected session file at {session_path}")
    contents = session_path.read_text(encoding="utf-8")
    must(contents.count("## turn") == 3,
         f"expected 3 turns in session file, found "
         f"{contents.count('## turn')} (file: {session_path.name})")
    must("My favorite color is teal." in contents,
         "turn 2 user text not in session file")
    print(f"  OK: session file has 3 turns: {session_path.name}")
    print()

    print("POST /chat with bad session_id  -> expect 400")
    r = requests.post(
        f"{BASE}/chat",
        json={"message": "hi", "session_id": "../../etc/passwd"},
        headers=AUTH_HEADERS, timeout=10,
    )
    must(r.status_code == 400,
         f"expected 400 for malformed session_id, got {r.status_code}: {r.text}")
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
