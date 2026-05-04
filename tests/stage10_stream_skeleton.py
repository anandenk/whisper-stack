"""Stage 10 verification: /converse/stream SSE wire format.

Originally written for Phase 3A (the stubbed-LLM skeleton). After Phase 3B
the LLM is real (ClaudeLLM.chat_stream); this test now validates the
wire format end-to-end without asserting stub-specific behavior. The
fixture is a non-tool query ("capital of France") so the response stays
in a single round and `final.reply_text` equals the joined text deltas
— that invariant breaks for tool-using queries (intermediate "thinking
text" doesn't end up in final.reply), which is what stage10_stream_tools
exercises.

This test:
- synthesizes a short fixture utterance via Piper (same trick as stage 7)
- POSTs it to /converse/stream with `stream=True`
- parses the SSE event stream line-by-line as it arrives
- asserts the events arrive in the expected order with the expected shapes
- asserts the audio event decodes to playable PCM at the announced rate

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage10_stream_skeleton.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: F401 -- triggers .env load so API_KEY is in os.environ
from server.audio import synth_to_wav

BASE = "http://127.0.0.1:5000"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
API_KEY = os.environ.get("API_KEY", "").strip()
AUTH_HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}


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
    sys.exit(1)


def parse_sse(lines_iter) -> list[tuple[str, dict]]:
    """Collect (event, data_dict) pairs from a line iterator.

    Minimal SSE parser — single-line `data:` only (matches what the server
    emits via _sse_event). Yields one tuple per blank-line-terminated frame.
    """
    events: list[tuple[str, dict]] = []
    cur_event: str | None = None
    cur_data: str | None = None
    for raw in lines_iter:
        # requests.iter_lines() yields bytes; decode to str
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if line == "":  # frame boundary
            if cur_event is not None and cur_data is not None:
                events.append((cur_event, json.loads(cur_data)))
            cur_event = None
            cur_data = None
            continue
        if line.startswith("event: "):
            cur_event = line[len("event: "):]
        elif line.startswith("data: "):
            cur_data = line[len("data: "):]
    # Trailing frame without a blank line (shouldn't happen, but guard)
    if cur_event is not None and cur_data is not None:
        events.append((cur_event, json.loads(cur_data)))
    return events


def main() -> int:
    print(f"Waiting for server at {BASE}/health ...")
    h = wait_for_health()
    must(h.get("status") == "ok", f"unexpected /health: {h}")
    print(f"  -> mode={h.get('mode')!r}\n")

    SAMPLES_DIR.mkdir(exist_ok=True)
    src_text = "What is the capital of France?"
    fixture = SAMPLES_DIR / "_stream_skeleton.wav"
    fixture.write_bytes(synth_to_wav(src_text))
    print(f"Generated fixture: {fixture.name} (src: {src_text!r})\n")

    print("POSTing /converse/stream and parsing SSE...")
    t0 = time.perf_counter()
    with fixture.open("rb") as fh:
        r = requests.post(
            f"{BASE}/converse/stream",
            files={"audio": (fixture.name, fh, "audio/wav")},
            headers=AUTH_HEADERS,
            stream=True,
            timeout=120,
        )
        must(r.status_code == 200, f"stream status {r.status_code}: {r.text[:300]}")
        must(r.headers.get("Content-Type", "").startswith("text/event-stream"),
             f"unexpected Content-Type: {r.headers.get('Content-Type')!r}")
        events = parse_sse(r.iter_lines(decode_unicode=False))
    wall = time.perf_counter() - t0

    print(f"  Got {len(events)} events in {wall:.2f}s wall-clock.")
    for ev, data in events:
        snippet = json.dumps(data)
        if len(snippet) > 100:
            snippet = snippet[:100] + "..."
        print(f"    {ev:<10} {snippet}")
    print()

    # The expected event sequence for Phase 3A on a non-empty utterance:
    #   ready, transcript, route, text (>=1), audio, final
    types = [ev for ev, _ in events]
    must(types[0] == "ready", f"first event must be 'ready', got {types[0]!r}")
    must("transcript" in types, "missing 'transcript' event")
    must("route" in types, "missing 'route' event")
    must(types.count("text") >= 1, f"expected >=1 'text' events, got {types.count('text')}")
    must(types.count("audio") == 1, f"expected exactly 1 'audio' event, got {types.count('audio')}")
    must(types[-1] == "final", f"last event must be 'final', got {types[-1]!r}")
    must("error" not in types, f"unexpected error event: {[d for e,d in events if e=='error']}")

    # Order: ready < transcript < route < text* < audio < final
    pos = {ev: i for i, (ev, _) in enumerate(events)}
    first_text = next(i for i, (e, _) in enumerate(events) if e == "text")
    last_text = max(i for i, (e, _) in enumerate(events) if e == "text")
    must(pos["ready"] < pos["transcript"] < pos["route"] < first_text,
         "events out of order before text deltas")
    must(last_text < pos["audio"] < pos["final"],
         "audio/final must come after all text deltas")

    # Shape checks per event
    ready = next(d for e, d in events if e == "ready")
    must("mode" in ready and ready["mode"] in ("local", "cloud"),
         f"ready.mode wrong: {ready}")

    transcript_ev = next(d for e, d in events if e == "transcript")
    must("transcript" in transcript_ev and transcript_ev["transcript"].strip(),
         f"transcript event missing/empty: {transcript_ev}")
    must("latency_s" in transcript_ev, f"transcript event missing latency_s: {transcript_ev}")

    route_ev = next(d for e, d in events if e == "route")
    for k in ("route", "force_routed", "auto_escalated", "model", "router_scores"):
        must(k in route_ev, f"route event missing {k!r}: {route_ev}")
    must(route_ev["route"] in ("simple", "complex"), f"bad route: {route_ev['route']}")
    must(isinstance(route_ev["model"], str) and len(route_ev["model"]) > 0,
         f"route.model must be a non-empty string, got {route_ev['model']!r}")

    text_deltas = [d["delta"] for e, d in events if e == "text"]
    joined = "".join(text_deltas)
    must(len(joined) > 5, f"joined text too short: {joined!r}")

    audio_ev = next(d for e, d in events if e == "audio")
    must("sample_rate" in audio_ev and audio_ev["sample_rate"] > 8000,
         f"audio event sample_rate looks wrong: {audio_ev.get('sample_rate')}")
    must("pcm_b64" in audio_ev, "audio event missing pcm_b64")
    pcm = base64.b64decode(audio_ev["pcm_b64"])
    must(len(pcm) > 0 and len(pcm) % 2 == 0,
         f"PCM payload bad length: {len(pcm)} bytes")

    final = next(d for e, d in events if e == "final")
    for k in ("reply_text", "audio_duration_s", "timings", "tools_used", "llm_tokens"):
        must(k in final, f"final event missing {k!r}: {final}")
    # For a non-tool query the LLM runs in a single round, so the joined
    # text deltas equal final.reply_text modulo leading/trailing whitespace
    # (chat_stream strips the final reply). Tool-using queries can break
    # this — see stage10_stream_tools.
    must(final["reply_text"].strip() == joined.strip(),
         f"final.reply_text != joined deltas (single-round invariant):\n"
         f"  reply_text: {final['reply_text']!r}\n  joined: {joined!r}")
    for tk in ("stt", "route", "llm", "tts", "total"):
        must(tk in final["timings"], f"final.timings missing {tk!r}")
    must(final["audio_duration_s"] > 0.1,
         f"reply audio too short: {final['audio_duration_s']:.2f}s")

    # Sanity: announced PCM duration matches the announced sample_rate
    expected_dur = (len(pcm) // 2) / audio_ev["sample_rate"]
    must(abs(expected_dur - final["audio_duration_s"]) < 0.01,
         f"audio_duration_s mismatch: header={final['audio_duration_s']:.3f} "
         f"computed={expected_dur:.3f}")

    print("=" * 64)
    print("  Phase 3A (/converse/stream SSE skeleton) verified.")
    print(f"  events: {len(events)}  text_deltas: {len(text_deltas)}  "
          f"pcm: {len(pcm)} B @ {audio_ev['sample_rate']} Hz  "
          f"({final['audio_duration_s']:.2f}s)  total: {final['timings']['total']:.2f}s")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
