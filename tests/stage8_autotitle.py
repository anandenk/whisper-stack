"""Stage 8 verification: auto-titling after the first turn (Milestone 1B).

Sends one /chat request with a session_id to a brand-new stream, then
confirms that:
  - the response carries `session_file` pointing at a slug-suffixed file
  - the file on disk has a `title:` header
  - the previous (no-suffix) filename is gone

Cost: ~$0.001 per run (Haiku titling call + Sonnet reply).

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/stage8_autotitle.py
"""

from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: F401  -- triggers .env load

BASE = "http://127.0.0.1:5000"
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"
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


def main() -> int:
    print(f"Waiting for server at {BASE}/health ...")
    wait_for_health()

    sid = f"autotitle-test-{secrets.token_hex(3)}"
    print(f"Using session_id={sid}")
    print()

    # Force complex route so the reply is short + topical (Sonnet).
    msg = "What's the difference between TCP and UDP?"
    print(f"POST /chat  (msg={msg!r})")
    r = requests.post(
        f"{BASE}/chat",
        json={"message": msg, "session_id": sid, "force_route": "complex"},
        headers=AUTH_HEADERS,
        timeout=60,
    )
    must(r.status_code == 200, f"status {r.status_code}: {r.text}")
    d = r.json()

    session_file = d.get("session_file")
    must(session_file, f"session_file missing from response: {list(d.keys())}")
    print(f"  reply: {d['reply'][:140]!r}...")
    print(f"  session_file: {session_file}")
    print()

    # The auto-titler is synchronous, so by the time /chat responds the
    # rename should have happened. Verify:
    must(session_file.startswith(f"{sid}-"),
         f"session_file should start with {sid!r}: {session_file!r}")
    parts = session_file[len(f"{sid}-"):]  # e.g. "20260504T120000Z-tcp-vs-udp.md"
    parts_no_ext = parts.rsplit(".", 1)[0]
    pieces = parts_no_ext.split("-", 1)
    must(len(pieces) == 2 and pieces[1],
         f"filename should have a slug suffix after the timestamp: {session_file!r}")
    slug = pieces[1]
    print(f"  detected slug: {slug!r}")

    # File on disk must exist with that name and have a `title:` header.
    path = SESSIONS_DIR / session_file
    must(path.exists(), f"session file not on disk at {path}")
    contents = path.read_text(encoding="utf-8")
    must(f"title: {slug}" in contents,
         f"file missing `title: {slug}` header line; head of file:\n"
         f"{contents.splitlines()[:5]}")

    # The pre-rename filename (no slug suffix) must NOT exist — set_title
    # uses Path.replace() which is atomic; no leftover file.
    pre_rename_pattern = list(SESSIONS_DIR.glob(f"{sid}-*.md"))
    must(len(pre_rename_pattern) == 1,
         f"expected exactly 1 file for stream {sid!r}, found "
         f"{[p.name for p in pre_rename_pattern]}")
    must(pre_rename_pattern[0] == path,
         f"glob should resolve to the renamed file: {pre_rename_pattern[0].name}")

    print()
    print("=" * 64)
    print("  Milestone 1B (auto-titling) verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
