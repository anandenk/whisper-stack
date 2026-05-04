"""Stage 10 verification: streaming TTS via /converse/stream (Phase 3D).

Phase 3D wires SentenceChunker + TTS.synth_stream into /converse/stream
so audio events arrive WHILE the LLM is still streaming text. Before 3D,
TTS ran once over the joined reply and emitted exactly one audio event;
after 3D, audio events interleave with text events and Piper emits one
chunk per phoneme batch (typically several chunks per sentence).

This test deliberately uses a query that produces a multi-sentence reply
("Tell me three short fun facts about octopuses, one per sentence.") so
the chunker fires multiple times. We assert:

- the reply lands on the simple route (no auto-escalation, no tools)
- text deltas and audio events INTERLEAVE: the first audio event arrives
  AFTER the first text event, but BEFORE the last text event (proving
  audio synth started before LLM streaming finished)
- audio events count >= 2 (multiple sentences trigger multiple synth
  calls; even one sentence Piper-synthesizes to multiple chunks, so
  this is a strong but achievable bar)
- all audio events share a single sample_rate
- final.audio_duration_s matches the concatenated PCM duration to within 10 ms

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage10_stream_audio.py
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


def parse_sse_with_arrival(lines_iter) -> list[tuple[str, dict, float]]:
    """Like parse_sse but tags each event with a wall-clock arrival timestamp.

    Useful for asserting interleave: did the first audio event land before
    the last text event? Times are seconds-since-test-start, derived from
    perf_counter when the line was read.
    """
    events: list[tuple[str, dict, float]] = []
    cur_event: str | None = None
    cur_data: str | None = None
    t0 = time.perf_counter()
    for raw in lines_iter:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if line == "":
            if cur_event is not None and cur_data is not None:
                events.append((cur_event, json.loads(cur_data),
                               time.perf_counter() - t0))
            cur_event = None
            cur_data = None
            continue
        if line.startswith("event: "):
            cur_event = line[len("event: "):]
        elif line.startswith("data: "):
            cur_data = line[len("data: "):]
    if cur_event is not None and cur_data is not None:
        events.append((cur_event, json.loads(cur_data),
                       time.perf_counter() - t0))
    return events


def main() -> int:
    print(f"Waiting for server at {BASE}/health ...")
    h = wait_for_health()
    must(h.get("status") == "ok", f"unexpected /health: {h}")
    print(f"  -> mode={h.get('mode')!r}\n")

    SAMPLES_DIR.mkdir(exist_ok=True)
    src_text = ("Tell me three short fun facts about octopuses, one per "
                "sentence, plain prose only.")
    fixture = SAMPLES_DIR / "_stream_audio.wav"
    fixture.write_bytes(synth_to_wav(src_text))
    print(f"Generated fixture: {fixture.name} (src: {src_text!r})\n")

    print("POSTing /converse/stream and parsing SSE...")
    t0 = time.perf_counter()
    with fixture.open("rb") as fh:
        r = requests.post(
            f"{BASE}/converse/stream",
            files={"audio": (fixture.name, fh, "audio/wav")},
            headers=AUTH_HEADERS,
            data={"force_route": "simple"},  # pin to Ollama; multi-sentence reply
            stream=True,
            timeout=180,
        )
        must(r.status_code == 200, f"stream status {r.status_code}: {r.text[:300]}")
        events = parse_sse_with_arrival(r.iter_lines(decode_unicode=False))
    wall = time.perf_counter() - t0

    counts: dict[str, int] = {}
    for ev, _, _ in events:
        counts[ev] = counts.get(ev, 0) + 1
    print(f"  Got {len(events)} events in {wall:.2f}s wall-clock.")
    print(f"  Event counts: {counts}\n")

    types = [ev for ev, _, _ in events]
    must("error" not in types, f"unexpected error event")
    must(types[0] == "ready", f"first event must be 'ready', got {types[0]!r}")
    must(types[-1] == "final", f"last event must be 'final', got {types[-1]!r}")
    must(counts.get("audio", 0) >= 2,
         f"streaming TTS should emit >=2 audio chunks, got {counts.get('audio', 0)}")
    must(counts.get("text", 0) >= 2,
         f"expected multiple text deltas, got {counts.get('text', 0)}")
    must(counts.get("tool_call", 0) == 0,
         f"force_route=simple should not emit tool_call, got {counts.get('tool_call')}")

    # Interleave check — the heart of Phase 3D:
    # - first audio comes AFTER first text (a sentence has to complete first)
    # - first audio comes BEFORE last text (some sentences synth'd while
    #   the LLM was still emitting later sentences)
    text_indices = [i for i, (e, _, _) in enumerate(events) if e == "text"]
    audio_indices = [i for i, (e, _, _) in enumerate(events) if e == "audio"]
    first_text_i = text_indices[0]
    last_text_i = text_indices[-1]
    first_audio_i = audio_indices[0]
    last_audio_i = audio_indices[-1]
    final_i = next(i for i, (e, _, _) in enumerate(events) if e == "final")
    must(first_text_i < first_audio_i,
         f"first text ({first_text_i}) must precede first audio ({first_audio_i})")
    must(first_audio_i < last_text_i,
         f"first audio ({first_audio_i}) must precede last text ({last_text_i}) "
         "— that's the interleave invariant the streaming TTS pipeline buys")
    must(last_audio_i < final_i,
         f"last audio ({last_audio_i}) must precede final ({final_i})")

    # All audio events agree on a single sample rate
    sample_rates = {events[i][1]["sample_rate"] for i in audio_indices}
    must(len(sample_rates) == 1,
         f"all audio events should share one sample_rate, got {sample_rates}")
    sr = next(iter(sample_rates))
    must(sr > 8000, f"audio sample_rate looks wrong: {sr}")

    # Concatenated PCM length matches final.audio_duration_s within 10 ms
    total_pcm_len = sum(len(base64.b64decode(events[i][1]["pcm_b64"]))
                        for i in audio_indices)
    final = next(d for e, d, _ in events if e == "final")
    expected_dur = (total_pcm_len // 2) / sr
    must(abs(expected_dur - final["audio_duration_s"]) < 0.01,
         f"audio_duration_s mismatch: header={final['audio_duration_s']:.3f}s "
         f"computed-from-chunks={expected_dur:.3f}s")

    # Sanity: timings.tts is the accumulated synth wall-clock, should be
    # in [0, total]. Doesn't have to equal anything in particular —
    # just shouldn't be negative or absurdly larger than total.
    must(0.0 <= final["timings"]["tts"] <= final["timings"]["total"] + 0.5,
         f"timings.tts out of range: {final['timings']}")

    # Print arrival timeline summary so a human can eyeball the interleave
    first_audio_t = events[first_audio_i][2]
    last_text_t = events[last_text_i][2]
    print(f"  arrival timeline (s from request):")
    print(f"    first text:  {events[first_text_i][2]:.2f}")
    print(f"    first audio: {first_audio_t:.2f}")
    print(f"    last text:   {last_text_t:.2f}")
    print(f"    last audio:  {events[last_audio_i][2]:.2f}")
    print(f"    final:       {events[final_i][2]:.2f}")
    print(f"  -> first audio arrived {last_text_t - first_audio_t:.2f}s before "
          f"the last text delta (interleave headroom)")

    print(f"\n  reply_text: {final['reply_text']!r}")
    print(f"  audio_chunks: {len(audio_indices)}  pcm: {total_pcm_len} B @ {sr} Hz  "
          f"duration: {final['audio_duration_s']:.2f}s")
    print(f"  timings: {final['timings']}")

    print("\n" + "=" * 64)
    print("  Phase 3D (sentence-boundary streaming TTS) verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
