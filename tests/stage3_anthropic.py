"""Stage 3 verification: Anthropic API smoke test.

Loads ANTHROPIC_API_KEY from .env at the repo root, calls claude-sonnet-4-6
with a single short prompt, prints the reply, latency, and token counts.

Run from repo root after activating .venv:
    python tests/stage3_anthropic.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)

from anthropic import Anthropic, APIStatusError, AuthenticationError

MODEL = "claude-sonnet-4-6"
PROMPT = "In one short sentence, say hello and confirm you are Claude."


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("ERROR: ANTHROPIC_API_KEY not found in environment or .env", file=sys.stderr)
        return 1
    if key == "sk-ant-..." or not key.startswith("sk-ant-"):
        print("ERROR: ANTHROPIC_API_KEY looks like the placeholder, not a real key", file=sys.stderr)
        return 1

    print(f"Model:   {MODEL}")
    print(f"Prompt:  {PROMPT}")
    print()

    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env

    t0 = time.perf_counter()
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=128,
            messages=[{"role": "user", "content": PROMPT}],
        )
    except AuthenticationError as exc:
        print(f"AUTH FAILED: {exc}", file=sys.stderr)
        print("Check that ANTHROPIC_API_KEY is correct.", file=sys.stderr)
        return 2
    except APIStatusError as exc:
        print(f"API ERROR ({exc.status_code}): {exc.message}", file=sys.stderr)
        if exc.status_code == 400 and "credit" in str(exc).lower():
            print("This usually means your account has no credits — top up at console.anthropic.com.", file=sys.stderr)
        return 3
    elapsed = time.perf_counter() - t0

    reply = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()

    print("=" * 64)
    print(f"  Reply:           {reply}")
    print(f"  Stop reason:     {msg.stop_reason}")
    print(f"  Input tokens:    {msg.usage.input_tokens}")
    print(f"  Output tokens:   {msg.usage.output_tokens}")
    print(f"  Latency:         {elapsed:.2f}s")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
