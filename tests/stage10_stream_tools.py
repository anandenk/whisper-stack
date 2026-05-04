"""Stage 10 verification: /converse/stream with tool calls (Phase 3B).

Exercises the streaming tool loop end-to-end. We synthesize a query that's
guaranteed to auto-escalate to the complex route and call the
get_current_time tool — "What time is it in Tokyo right now?" — and assert:

- the route event reports complex + auto_escalated
- at least one tool_call event appears, with a matching tool_result event
- final.tools_used is non-empty and references get_current_time
- the SSE event stream remains well-formed (ready/transcript/route/...
  text*/tool_call/tool_result/text*/audio/final)
- the audio event still decodes to playable PCM at the announced rate

Note: text deltas for tool-using queries can include "thinking text" from
the round that called the tool. final.reply_text is just the LAST round's
text (matches chat_stream's contract — see ClaudeLLM.chat_stream docstring).
We deliberately do NOT assert joined text deltas == final.reply_text here.

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage10_stream_tools.py
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
    events: list[tuple[str, dict]] = []
    cur_event: str | None = None
    cur_data: str | None = None
    for raw in lines_iter:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if line == "":
            if cur_event is not None and cur_data is not None:
                events.append((cur_event, json.loads(cur_data)))
            cur_event = None
            cur_data = None
            continue
        if line.startswith("event: "):
            cur_event = line[len("event: "):]
        elif line.startswith("data: "):
            cur_data = line[len("data: "):]
    if cur_event is not None and cur_data is not None:
        events.append((cur_event, json.loads(cur_data)))
    return events


def main() -> int:
    print(f"Waiting for server at {BASE}/health ...")
    h = wait_for_health()
    must(h.get("status") == "ok", f"unexpected /health: {h}")
    print(f"  -> mode={h.get('mode')!r}\n")

    SAMPLES_DIR.mkdir(exist_ok=True)
    src_text = "What time is it in Tokyo right now?"
    fixture = SAMPLES_DIR / "_stream_tools.wav"
    fixture.write_bytes(synth_to_wav(src_text))
    print(f"Generated fixture: {fixture.name} (src: {src_text!r})\n")

    print("POSTing /converse/stream with tool-likely query and parsing SSE...")
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
        events = parse_sse(r.iter_lines(decode_unicode=False))
    wall = time.perf_counter() - t0

    print(f"  Got {len(events)} events in {wall:.2f}s wall-clock.")
    counts: dict[str, int] = {}
    for ev, _ in events:
        counts[ev] = counts.get(ev, 0) + 1
    print(f"  Event counts: {counts}\n")

    types = [ev for ev, _ in events]
    must("error" not in types, f"unexpected error event: {[d for e,d in events if e=='error']}")
    must(types[0] == "ready", f"first event must be 'ready', got {types[0]!r}")
    must(types[-1] == "final", f"last event must be 'final', got {types[-1]!r}")
    must("transcript" in types, "missing 'transcript' event")
    must("route" in types, "missing 'route' event")
    must("audio" in types, "missing 'audio' event")

    # Tool flow assertions
    must(counts.get("tool_call", 0) >= 1,
         f"expected >=1 tool_call event, got {counts.get('tool_call', 0)}")
    must(counts.get("tool_result", 0) >= 1,
         f"expected >=1 tool_result event, got {counts.get('tool_result', 0)}")
    must(counts.get("tool_call") == counts.get("tool_result"),
         f"tool_call/tool_result mismatch: {counts}")

    # Each tool_call must precede its matching tool_result, and both must
    # appear before `final`.
    final_pos = next(i for i, (e, _) in enumerate(events) if e == "final")
    audio_pos = next(i for i, (e, _) in enumerate(events) if e == "audio")
    for i, (e, _) in enumerate(events):
        if e in ("tool_call", "tool_result"):
            must(i < audio_pos < final_pos,
                 f"tool event at index {i} must precede audio ({audio_pos}) "
                 f"and final ({final_pos})")

    # Auto-escalation: a tool-likely query routed to simple should bump to complex.
    route_ev = next(d for e, d in events if e == "route")
    must(route_ev["route"] == "complex",
         f"tool-likely query should land on complex route, got {route_ev['route']!r}")
    must(route_ev["auto_escalated"] is True,
         f"tool-likely simple query should auto_escalate, got {route_ev}")
    must("claude" in route_ev["model"].lower(),
         f"complex route should run on a Claude model, got {route_ev['model']!r}")

    # tool_call/tool_result content
    tc_events = [d for e, d in events if e == "tool_call"]
    tr_events = [d for e, d in events if e == "tool_result"]
    print(f"  tool_call(s):  {[(d['name'], d.get('input')) for d in tc_events]}")
    print(f"  tool_result(s): {[(d['name'], d.get('output_chars')) for d in tr_events]}")
    must(any(d["name"] == "get_current_time" for d in tc_events),
         f"expected get_current_time call, got {[d['name'] for d in tc_events]}")
    for d in tc_events:
        must("id" in d and "name" in d and "round" in d,
             f"tool_call event missing required fields: {d}")
    for d in tr_events:
        must("id" in d and "name" in d and "output_chars" in d
             and "latency_s" in d and "round" in d,
             f"tool_result event missing required fields: {d}")

    # Audio shape (unchanged from skeleton)
    audio_ev = next(d for e, d in events if e == "audio")
    must(audio_ev.get("sample_rate", 0) > 8000,
         f"audio.sample_rate looks wrong: {audio_ev.get('sample_rate')}")
    pcm = base64.b64decode(audio_ev["pcm_b64"])
    must(len(pcm) > 0 and len(pcm) % 2 == 0,
         f"PCM payload bad length: {len(pcm)} bytes")

    # Final shape + tools_used metadata
    final = next(d for e, d in events if e == "final")
    for k in ("reply_text", "audio_duration_s", "timings", "tools_used", "llm_tokens"):
        must(k in final, f"final event missing {k!r}: {final}")
    must(isinstance(final["tools_used"], list) and len(final["tools_used"]) >= 1,
         f"final.tools_used must be non-empty: {final.get('tools_used')!r}")
    must(any(t.get("name") == "get_current_time" for t in final["tools_used"]),
         f"final.tools_used should reference get_current_time: {final['tools_used']}")
    for t in final["tools_used"]:
        for k in ("name", "input", "output_chars", "latency_s", "round"):
            must(k in t, f"final.tools_used entry missing {k!r}: {t}")

    must(final["reply_text"].strip(),
         f"final.reply_text empty after tool round: {final}")
    # Reply should mention something current-time-ish (Tokyo, JST, time, hour, AM/PM, ...)
    rt = final["reply_text"].lower()
    must(any(s in rt for s in ("tokyo", "jst", "time", "hour", "am", "pm")),
         f"final.reply_text doesn't look like a time answer: {final['reply_text']!r}")

    print(f"\n  reply_text: {final['reply_text']!r}")
    print(f"  llm_tokens: {final['llm_tokens']}")
    print(f"  timings:    {final['timings']}")

    print("\n" + "=" * 64)
    print("  Phase 3B (/converse/stream tool flow) verified.")
    print(f"  events: {len(events)}  text_deltas: {counts.get('text', 0)}  "
          f"tool_calls: {counts.get('tool_call', 0)}  pcm: {len(pcm)} B "
          f"@ {audio_ev['sample_rate']} Hz  total: {final['timings']['total']:.2f}s")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
