"""Stage 7 verification: end-to-end /converse pipeline.

Generates 3 source audio files via Piper (factual, complex, casual chat), POSTs each to
/converse, asserts the response shape (NOT the route — that's stage 5's job to calibrate),
and prints a per-stage timing breakdown plus which stage dominates each interaction.

You'll hear three reply audios play through your default output as the test runs.

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage7_e2e.py
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import tts

BASE = "http://127.0.0.1:5000"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
API_KEY = os.environ.get("API_KEY", "").strip()
AUTH_HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

# (label, prompt-text-to-synth, expected_route_hint)
# expected_route_hint is for human-readable output only — NOT asserted, since
# router calibration is the user's choice (see stage 5 design notes).
QUERIES = [
    ("factual",  "Could you tell me, briefly, what is the capital of France?",                    "simple"),
    ("complex",  "In one sentence, explain why a Python function might raise a NoneType error.",  "complex"),
    ("chat",     "Hi there, how are you doing today?",                                            "either"),
]


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
    print(f"FAIL: server did not respond on /health within {timeout_s}s. Last: {last!r}", file=sys.stderr)
    print("Did you start it? `python -m server.app`", file=sys.stderr)
    sys.exit(1)


def synth_fixture(text: str, dest: Path) -> None:
    wav_bytes = tts.synth_to_wav(text)
    dest.write_bytes(wav_bytes)


def post_converse(audio_path: Path) -> dict:
    with audio_path.open("rb") as fh:
        r = requests.post(
            f"{BASE}/converse",
            files={"audio": (audio_path.name, fh, "audio/wav")},
            headers=AUTH_HEADERS,
            timeout=120,
        )
    must(r.status_code == 200, f"/converse status {r.status_code}: {r.text[:300]}")
    return r.json()


def main() -> int:
    print(f"Waiting for server at {BASE}/health ...")
    h = wait_for_health()
    must(h.get("status") == "ok", f"unexpected /health: {h}")
    print(f"  -> {h}\n")

    SAMPLES_DIR.mkdir(exist_ok=True)
    print(f"Generating {len(QUERIES)} fixture audio files via Piper...")
    fixtures = []
    for label, text, hint in QUERIES:
        path = SAMPLES_DIR / f"_converse_{label}.wav"
        synth_fixture(text, path)
        size_kb = path.stat().st_size / 1024
        fixtures.append((label, text, hint, path))
        print(f"  {path.name}  ({size_kb:.0f} KB)  src: {text!r}")
    print()

    results = []
    for label, src_text, hint, path in fixtures:
        print(f"--- {label}  (expected route hint: {hint}) ---")
        print(f"  src text: {src_text!r}")
        wall_t0 = time.perf_counter()
        d = post_converse(path)
        wall = time.perf_counter() - wall_t0

        # Wiring assertions — NOT semantic assertions on the route
        for k in ("transcript", "route", "model", "reply_text", "audio_base64",
                  "audio_sample_rate", "audio_duration_s", "timings", "router_scores"):
            must(k in d, f"missing key {k!r} in response: {list(d.keys())}")
        must(d["route"] in ("simple", "complex"), f"bad route: {d['route']!r}")
        # model name should match the route taken
        if d["route"] == "simple":
            must("llama" in d["model"].lower(), f"simple route but model={d['model']!r}")
        else:
            must("claude" in d["model"].lower(), f"complex route but model={d['model']!r}")
        # audio_base64 should be non-empty + decode successfully
        wav = base64.b64decode(d["audio_base64"])
        must(wav.startswith(b"RIFF"), "audio_base64 doesn't decode to a WAV (no RIFF header)")
        must(d["audio_duration_s"] > 0.1, f"reply audio too short: {d['audio_duration_s']:.2f}s")
        # Timings present + roughly add up
        for tk in ("stt", "route", "llm", "tts", "total"):
            must(tk in d["timings"], f"missing timing {tk!r}")
        sub_sum = d["timings"]["stt"] + d["timings"]["route"] + d["timings"]["llm"] + d["timings"]["tts"]
        # total should be >= sum of sub-stages, with some overhead. Allow 2s slack.
        must(d["timings"]["total"] >= sub_sum - 0.05,
             f"total={d['timings']['total']:.2f} < sub_sum={sub_sum:.2f}")

        print(f"  transcript:  {d['transcript']!r}")
        print(f"  route:       {d['route']}  (model={d['model']})")
        print(f"  reply:       {d['reply_text'][:200]}{'...' if len(d['reply_text']) > 200 else ''}")
        t = d["timings"]
        print(f"  timings (s): stt={t['stt']:.2f}  route={t['route']:.3f}  llm={t['llm']:.2f}  tts={t['tts']:.2f}  total={t['total']:.2f}  http_wall={wall:.2f}")
        # Identify the dominant stage
        named = {"stt": t["stt"], "llm": t["llm"], "tts": t["tts"]}
        dom = max(named, key=named.get)
        print(f"  dominant stage: {dom}  ({named[dom]:.2f}s, {named[dom]/t['total']*100:.0f}% of total)")
        print(f"  reply audio: {d['audio_duration_s']:.2f}s @ {d['audio_sample_rate']} Hz  (~{len(wav)/1024:.0f} KB WAV)")
        print()

        results.append((label, d, wall))
        # Allow the previous reply to finish playing before the next /converse
        # cuts it off via sd.play (single audio stream).
        time.sleep(d["audio_duration_s"] + 0.5)

    print("=" * 64)
    print("  All stage 7 wiring checks passed.")
    print()
    print("  Per-query summary:")
    for label, d, wall in results:
        t = d["timings"]
        print(f"    {label:<8} route={d['route']:<8} stt={t['stt']:.2f}s  llm={t['llm']:.2f}s  tts={t['tts']:.2f}s  total={t['total']:.2f}s")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
