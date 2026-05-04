"""Stage 9 verification: router auto-escalation on tool-likely queries (Phase 2E).

Hits a running server's /chat with three queries:

  1. 'What time is it in Tokyo right now?'  (no force_route)
       -> should auto-escalate to complex, call get_current_time,
          reply mentions Tokyo time
  2. 'Hello.' (no force_route)
       -> should stay simple, NOT escalate, no tools called
  3. 'What time is it in Tokyo right now?' with force_route=simple
       -> should NOT escalate (explicit user override wins)

Cost: ~$0.005 per run (one Sonnet round-trip for case 1).

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage9_autoescalation.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: F401  -- triggers .env load

BASE = "http://127.0.0.1:5000"
API_KEY = os.environ.get("API_KEY", "").strip()
AUTH_HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def wait_for_health(timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if requests.get(f"{BASE}/health", timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    print("FAIL: server didn't come up", file=sys.stderr)
    sys.exit(1)


def post_chat(message: str, force_route: str | None = None) -> dict:
    body = {"message": message}
    if force_route is not None:
        body["force_route"] = force_route
    r = requests.post(f"{BASE}/chat", json=body, headers=AUTH_HEADERS, timeout=60)
    must(r.status_code == 200, f"status {r.status_code}: {r.text[:300]}")
    return r.json()


def test_tool_likely_auto_escalates() -> None:
    print("[1] tool-likely query auto-escalates to complex + calls a tool")
    d = post_chat("What time is it in Tokyo right now?")
    print(f"  route={d['route']}  auto_escalated={d.get('auto_escalated')}  "
          f"model={d.get('model')!r}")
    must(d["route"] == "complex",
         f"expected route=complex after auto-escalation, got {d['route']}")
    must(d.get("auto_escalated") is True,
         f"auto_escalated should be True: {d}")
    used = d.get("tools_used") or []
    must(any(u["name"] == "get_current_time" for u in used),
         f"expected get_current_time call in tools_used: {used}")
    print(f"  reply: {d['reply']!r}")
    print(f"  tools_used: {[u['name'] for u in used]}")
    print("  OK")


def test_normal_query_stays_simple() -> None:
    print("[2] non-tool-likely query stays on the embedding-router decision")
    d = post_chat("Hello.")
    must(d.get("auto_escalated") is False,
         f"auto_escalated should be False on a vanilla greeting: {d}")
    must(not d.get("tools_used"),
         f"no tool calls expected on a vanilla greeting: {d.get('tools_used')}")
    print(f"  route={d['route']}  auto_escalated={d.get('auto_escalated')}")
    print(f"  reply: {d['reply'][:80]!r}")
    print("  OK")


def test_force_route_overrides_escalation() -> None:
    print("[3] explicit force_route=simple overrides auto-escalation")
    d = post_chat("What time is it in Tokyo right now?", force_route="simple")
    must(d["route"] == "simple",
         f"force_route=simple should pin the route: {d['route']}")
    must(d.get("auto_escalated") is False,
         f"auto_escalated must be False when force_route is set: {d}")
    print(f"  route={d['route']}  auto_escalated={d.get('auto_escalated')}")
    # Llama may or may not call the tool (its tool calling is unreliable —
    # the whole point of auto-escalation). We just don't assert on it here.
    print(f"  reply: {d['reply'][:80]!r}")
    print(f"  tools_used: {[u['name'] for u in (d.get('tools_used') or [])]}")
    print("  OK")


def main() -> int:
    print(f"Waiting for server at {BASE}/health ...")
    wait_for_health()
    print()
    test_tool_likely_auto_escalates()
    print()
    test_normal_query_stays_simple()
    print()
    test_force_route_overrides_escalation()
    print()
    print("=" * 64)
    print("  Phase 2E (auto-escalation) verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
