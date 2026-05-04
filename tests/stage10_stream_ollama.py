"""Stage 10 verification: /converse/stream simple-route (Ollama) streaming (Phase 3C).

Phase 3C wires `OllamaLLM.chat_stream` over `/api/chat` with `stream=true`
NDJSON parsing. Before 3C, the simple-route path through /converse/stream
hit OllamaLLM's NotImplementedError stub and fell back to
non-streaming chat() (one big text delta). After 3C, the stream is
real — many small text deltas as Llama emits tokens.

This test pins down that distinction. Synthesizes a non-tool, simple-route
query, asserts:

- the route lands on simple (NOT auto-escalated)
- model name contains "llama" (we hit the Ollama backend, not Claude)
- text deltas count >= 2 (proves real streaming, not the chat() fallback)
- final.reply_text equals the joined text deltas modulo whitespace
- no tool_call / tool_result events fire (simple route doesn't get tools)

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage10_stream_ollama.py
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
    must(h.get("mode") == "local",
         f"this test only meaningful in MODE=local (cloud's simple route is Haiku, "
         f"not Llama). Got mode={h.get('mode')!r}")
    print(f"  -> mode={h.get('mode')!r}\n")

    SAMPLES_DIR.mkdir(exist_ok=True)
    # Phrasing that the embedding router consistently classifies simple +
    # is non-tool-likely so it doesn't auto-escalate. A short factual
    # question with no temporal markers fits the bill.
    src_text = "Tell me a fun fact about octopuses in one sentence."
    fixture = SAMPLES_DIR / "_stream_ollama.wav"
    fixture.write_bytes(synth_to_wav(src_text))
    print(f"Generated fixture: {fixture.name} (src: {src_text!r})\n")

    print("POSTing /converse/stream and parsing SSE...")
    t0 = time.perf_counter()
    with fixture.open("rb") as fh:
        r = requests.post(
            f"{BASE}/converse/stream",
            files={"audio": (fixture.name, fh, "audio/wav")},
            headers=AUTH_HEADERS,
            data={"force_route": "simple"},  # belt-and-suspenders: pin to simple
            stream=True,
            timeout=120,
        )
        must(r.status_code == 200, f"stream status {r.status_code}: {r.text[:300]}")
        events = parse_sse(r.iter_lines(decode_unicode=False))
    wall = time.perf_counter() - t0

    counts: dict[str, int] = {}
    for ev, _ in events:
        counts[ev] = counts.get(ev, 0) + 1
    print(f"  Got {len(events)} events in {wall:.2f}s wall-clock.")
    print(f"  Event counts: {counts}\n")

    types = [ev for ev, _ in events]
    must("error" not in types, f"unexpected error event: {[d for e,d in events if e=='error']}")
    must(types[0] == "ready", f"first event must be 'ready', got {types[0]!r}")
    must(types[-1] == "final", f"last event must be 'final', got {types[-1]!r}")
    must("audio" in types, "missing 'audio' event")

    # Simple-route assertions
    route_ev = next(d for e, d in events if e == "route")
    must(route_ev["route"] == "simple",
         f"forced simple route should land on simple, got {route_ev['route']!r}")
    must(route_ev["force_routed"] is True,
         f"force_routed should be True, got {route_ev}")
    must(route_ev["auto_escalated"] is False,
         f"force_route=simple should NOT auto_escalate, got {route_ev}")
    must("llama" in route_ev["model"].lower(),
         f"simple route should be on a llama model, got {route_ev['model']!r}")

    # No tools should fire — /converse/stream withholds tools from simple.
    must(counts.get("tool_call", 0) == 0,
         f"simple route should not emit tool_call, got {counts.get('tool_call')}")
    must(counts.get("tool_result", 0) == 0,
         f"simple route should not emit tool_result, got {counts.get('tool_result')}")

    # The whole point of 3C: real streaming, not the chat() fallback.
    # Llama emits tokens in many small chunks; even a one-sentence reply
    # gets >2 deltas. The fallback path emits exactly 1 delta (the whole
    # reply), so >=2 cleanly distinguishes them.
    text_deltas = [d["delta"] for e, d in events if e == "text"]
    print(f"  text deltas: {len(text_deltas)} (first few: {text_deltas[:5]!r})")
    must(len(text_deltas) >= 2,
         f"simple route should emit >=2 text deltas (real Ollama streaming, "
         f"not chat() fallback), got {len(text_deltas)}: {text_deltas!r}")

    joined = "".join(text_deltas)
    final = next(d for e, d in events if e == "final")
    must(final["reply_text"].strip() == joined.strip(),
         f"final.reply_text != joined deltas (single-round invariant):\n"
         f"  reply_text: {final['reply_text']!r}\n  joined: {joined!r}")
    must(len(final["reply_text"].strip()) > 10,
         f"reply text suspiciously short: {final['reply_text']!r}")

    # tools_used must be empty/absent for the simple route.
    must(not final.get("tools_used"),
         f"final.tools_used should be empty for simple route, got {final.get('tools_used')!r}")

    # Audio shape (unchanged from skeleton)
    audio_ev = next(d for e, d in events if e == "audio")
    pcm = base64.b64decode(audio_ev["pcm_b64"])
    must(audio_ev.get("sample_rate", 0) > 8000 and len(pcm) > 0 and len(pcm) % 2 == 0,
         f"audio shape wrong: sr={audio_ev.get('sample_rate')} pcm_len={len(pcm)}")

    print(f"\n  reply_text: {final['reply_text']!r}")
    print(f"  llm_tokens: {final['llm_tokens']}")
    print(f"  timings:    {final['timings']}")

    print("\n" + "=" * 64)
    print("  Phase 3C (OllamaLLM.chat_stream via /converse/stream) verified.")
    print(f"  events: {len(events)}  text_deltas: {len(text_deltas)}  "
          f"pcm: {len(pcm)} B @ {audio_ev['sample_rate']} Hz  "
          f"total: {final['timings']['total']:.2f}s")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
