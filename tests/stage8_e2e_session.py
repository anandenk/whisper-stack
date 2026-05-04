"""Stage 8 verification: /converse end-to-end with session memory (Phase D).

Two-turn voice exchange against a running server with the same session_id.
Turn 1 establishes a fact ("my favorite color is amber"), turn 2 asks for
it back. Passes if the second reply contains "amber" — proving the full
loop (STT -> router -> LLM with history -> TTS -> file append) works.

Re-uses Piper to synthesize the source audio so the test can run unattended;
on the workstation the reply audio also plays through the speakers (system
volume controls whether you hear it).

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage8_e2e_session.py
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: F401  -- triggers .env load so API_KEY is in os.environ
from server.audio import synth_to_wav

BASE = "http://127.0.0.1:5000"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"
API_KEY = os.environ.get("API_KEY", "").strip()
AUTH_HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

# Use a unique session_id per run so a previous run's history doesn't bleed
# into this one's expectations.
SESSION_ID = f"e2e-test-{secrets.token_hex(4)}"

TURN_1_TEXT = "Just so you know, my favorite color is amber."
TURN_2_TEXT = "What is my favorite color? Answer in one short sentence."


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def wait_for_health(timeout_s: float = 60.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/health", timeout=2)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(1)
    print(f"FAIL: server not responding on /health within {timeout_s}s", file=sys.stderr)
    print("Did you start it? `python -m server.app`", file=sys.stderr)
    sys.exit(1)


def synth_fixture(text: str, dest: Path) -> None:
    dest.write_bytes(synth_to_wav(text))


def post_converse(audio_path: Path, session_id: str) -> dict:
    with audio_path.open("rb") as fh:
        r = requests.post(
            f"{BASE}/converse",
            files={"audio": (audio_path.name, fh, "audio/wav")},
            data={"session_id": session_id, "play_locally": "false",
                  "force_route": "simple"},
            headers=AUTH_HEADERS,
            timeout=180,
        )
    must(r.status_code == 200, f"/converse status {r.status_code}: {r.text[:300]}")
    return r.json()


def main() -> int:
    print(f"Waiting for server at {BASE}/health ...")
    h = wait_for_health()
    must(h.get("status") == "ok", f"unexpected /health: {h}")
    print(f"  -> mode={h.get('mode')}  models={h.get('models_loaded')}\n")

    SAMPLES_DIR.mkdir(exist_ok=True)
    print(f"Using session_id={SESSION_ID}")
    print()

    # --- TURN 1 ---
    print(f"Turn 1: synth + post {TURN_1_TEXT!r}")
    audio_1 = SAMPLES_DIR / "_session_turn1.wav"
    synth_fixture(TURN_1_TEXT, audio_1)
    d1 = post_converse(audio_1, SESSION_ID)
    must("session_file" in d1,
         f"session_file missing from response: {list(d1.keys())}")
    must(d1["session_file"].startswith(f"{SESSION_ID}-"),
         f"session_file should start with {SESSION_ID!r}, got {d1['session_file']!r}")
    print(f"  transcript: {d1['transcript']!r}")
    print(f"  reply:      {d1['reply_text'][:120]!r}")
    print(f"  session:    {d1['session_file']}")
    print()

    # Let any audio playback finish before the next call cuts it off.
    time.sleep(d1.get("audio_duration_s", 0) + 0.5)

    # --- TURN 2 ---
    print(f"Turn 2: synth + post {TURN_2_TEXT!r}")
    audio_2 = SAMPLES_DIR / "_session_turn2.wav"
    synth_fixture(TURN_2_TEXT, audio_2)
    d2 = post_converse(audio_2, SESSION_ID)
    must(d2.get("session_file") == d1["session_file"],
         f"session_file should be stable: {d1['session_file']} vs {d2.get('session_file')}")
    print(f"  transcript: {d2['transcript']!r}")
    print(f"  reply:      {d2['reply_text']!r}")
    print(f"  session:    {d2['session_file']}")
    print()

    # The actual memory assertion: turn 2 reply must reference "amber".
    must("amber" in d2["reply_text"].lower(),
         f"history not consumed by /converse — turn 2 missing 'amber': {d2['reply_text']!r}")

    # Verify the session file landed on disk with both turns and the right text.
    session_path = SESSIONS_DIR / d1["session_file"]
    must(session_path.exists(), f"expected session file at {session_path}")
    contents = session_path.read_text(encoding="utf-8")
    must(contents.count("## turn") == 2,
         f"expected 2 turns in session file, found {contents.count('## turn')}")
    must("amber" in contents.lower(),
         "session file should contain 'amber' from turn 1's transcript")
    print(f"  OK: session file has 2 turns and contains the established fact")

    # Sanity on the wav decode + audio shape (carry-over from stage7).
    wav = base64.b64decode(d2["audio_base64"])
    must(wav.startswith(b"RIFF"), "reply audio_base64 doesn't decode to WAV")
    must(d2["audio_duration_s"] > 0.1, f"reply audio too short: {d2['audio_duration_s']}")

    print()
    print("=" * 64)
    print("  Phase D end-to-end session memory verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
