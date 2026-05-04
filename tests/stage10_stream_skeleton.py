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

    # Expected event sequence on a non-empty utterance after Phase 3D:
    #   ready, transcript, route, then text/audio events interleaved
    #   (sentence-boundary streaming TTS), final last.
    types = [ev for ev, _ in events]
    must(types[0] == "ready", f"first event must be 'ready', got {types[0]!r}")
    must("transcript" in types, "missing 'transcript' event")
    must("route" in types, "missing 'route' event")
    must(types.count("text") >= 1, f"expected >=1 'text' events, got {types.count('text')}")
    must(types.count("audio") >= 1, f"expected >=1 'audio' event, got {types.count('audio')}")
    must(types[-1] == "final", f"last event must be 'final', got {types[-1]!r}")
    must("error" not in types, f"unexpected error event: {[d for e,d in events if e=='error']}")

    # Order constraints:
    #   - header events (ready/transcript/route) precede the first text/audio
    #   - first text comes before any audio (a sentence must complete before
    #     it can be synthesized)
    #   - all audio events precede `final`
    pos_first = {}
    for i, (e, _) in enumerate(events):
        pos_first.setdefault(e, i)
    first_text = pos_first["text"]
    first_audio = pos_first["audio"]
    last_audio = max(i for i, (e, _) in enumerate(events) if e == "audio")
    final_pos = pos_first["final"]
    must(pos_first["ready"] < pos_first["transcript"] < pos_first["route"] < first_text,
         "events out of order before text deltas")
    must(first_text < first_audio,
         f"first text ({first_text}) must precede first audio ({first_audio})")
    must(last_audio < final_pos,
         f"last audio ({last_audio}) must precede final ({final_pos})")

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

    # Per-chunk shape + cross-chunk consistency: every audio event has a
    # sample_rate, all events agree on it, and the concatenated PCM length
    # matches the announced final.audio_duration_s.
    audio_events = [d for e, d in events if e == "audio"]
    sample_rates = {a["sample_rate"] for a in audio_events}
    must(len(sample_rates) == 1,
         f"all audio events should share one sample_rate, got {sample_rates}")
    sr = next(iter(sample_rates))
    must(sr > 8000, f"audio sample_rate looks wrong: {sr}")
    total_pcm_len = 0
    for a in audio_events:
        must("pcm_b64" in a, f"audio event missing pcm_b64: {a}")
        pcm = base64.b64decode(a["pcm_b64"])
        must(len(pcm) > 0 and len(pcm) % 2 == 0,
             f"PCM chunk bad length: {len(pcm)} bytes")
        total_pcm_len += len(pcm)

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

    # Sanity: announced PCM duration across ALL audio chunks matches final.
    expected_dur = (total_pcm_len // 2) / sr
    must(abs(expected_dur - final["audio_duration_s"]) < 0.01,
         f"audio_duration_s mismatch: header={final['audio_duration_s']:.3f} "
         f"computed-from-chunks={expected_dur:.3f}")

    print("=" * 64)
    print("  Phase 3D (/converse/stream SSE skeleton) verified.")
    print(f"  events: {len(events)}  text_deltas: {len(text_deltas)}  "
          f"audio_chunks: {len(audio_events)}  "
          f"pcm: {total_pcm_len} B @ {sr} Hz  "
          f"({final['audio_duration_s']:.2f}s)  total: {final['timings']['total']:.2f}s")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
