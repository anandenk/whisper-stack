"""Stage smoke test: Gemini 2.5 Flash via google-genai SDK.

Verifies the four behaviors we rely on in the GeminiLLM backend:
    1. Basic chat       — API key works, model responds
    2. Function calling — custom tool dispatch shape (used for `time`)
    3. google_search    — built-in grounding (replaces Tavily-based web_search)
    4. Streaming        — generate_content_stream yields chunks (used by /converse/stream)

Note on rate limiting: Gemini 2.5 Flash free tier is 5 requests/minute.
We sleep ~14 s between tests to stay under that cap. Total run time ~60 s.

Also note on Gemini 2.5: `google_search` and custom `function_declarations`
CANNOT be combined in one request (returns 400 INVALID_ARGUMENT). The backend
will register only one tool type per call, picked by query shape — that's a
runtime decision, not something to smoke-test here.

Run from repo root after activating .venv:
    python tests/stage_gemini.py

Exits 0 on success, 1 on any failure.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)

from google import genai
from google.genai import types

MODEL = "gemini-2.5-flash"


def _get_function_call(resp):
    """Return the first function_call part on a response, or None."""
    parts = resp.candidates[0].content.parts or []
    for p in parts:
        fc = getattr(p, "function_call", None)
        if fc and fc.name:
            return fc
    return None


def test_basic_chat(client) -> bool:
    t0 = time.perf_counter()
    resp = client.models.generate_content(
        model=MODEL,
        contents="In one short sentence, confirm you are Gemini and say hello.",
    )
    dt = time.perf_counter() - t0
    text = (resp.text or "").strip()
    print(f"  reply ({dt:.2f}s): {text}")
    return bool(text)


def test_function_calling(client) -> bool:
    get_time_decl = {
        "name": "get_current_time",
        "description": "Returns the current local time as a human-readable string.",
        "parameters": {"type": "object", "properties": {}},
    }
    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=[get_time_decl])],
    )
    prompt = "What time is it right now? Call the tool."
    resp = client.models.generate_content(model=MODEL, contents=prompt, config=config)

    fc = _get_function_call(resp)
    if fc is None:
        print(f"  no function_call in response — got text: {(resp.text or '')[:100]!r}")
        return False
    print(f"  function called: {fc.name}(args={dict(fc.args) if fc.args else {}})")

    fr = types.Part.from_function_response(
        name=fc.name,
        response={"result": time.strftime("%I:%M %p")},
    )
    final = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Content(role="user", parts=[types.Part(text=prompt)]),
            resp.candidates[0].content,
            types.Content(role="user", parts=[fr]),
        ],
        config=config,
    )
    final_text = (final.text or "").strip()
    print(f"  final reply: {final_text}")
    return bool(final_text)


def test_google_search(client) -> bool:
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    resp = client.models.generate_content(
        model=MODEL,
        contents="What's a current news headline today? One short sentence.",
        config=config,
    )
    text = (resp.text or "").strip()
    print(f"  reply: {text[:180]}")
    gm = getattr(resp.candidates[0], "grounding_metadata", None)
    if gm is not None:
        queries = getattr(gm, "web_search_queries", None) or []
        if queries:
            print(f"  searched: {list(queries)[:3]}")
    return bool(text)


def test_streaming(client) -> bool:
    t0 = time.perf_counter()
    stream = client.models.generate_content_stream(
        model=MODEL,
        contents="Count from one to five in plain prose, with a short phrase about each number.",
    )
    chunks = 0
    first_chunk_t = None
    full = []
    for chunk in stream:
        piece = chunk.text or ""
        if piece:
            if first_chunk_t is None:
                first_chunk_t = time.perf_counter() - t0
            full.append(piece)
            chunks += 1
    joined = "".join(full).strip()
    ttft = f"{first_chunk_t:.2f}s" if first_chunk_t is not None else "n/a"
    print(f"  chunks: {chunks}  ttft: {ttft}")
    print(f"  full:   {joined[:140]}")
    return chunks >= 2 and bool(joined)


def main() -> int:
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        print("FAIL: GOOGLE_API_KEY missing from environment")
        print("      Put it in .env at the repo root (load_dotenv override=True is on).")
        return 1
    print(f"model: {MODEL}\n")

    client = genai.Client(api_key=key)

    tests = [
        ("1. basic chat",                  test_basic_chat),
        ("2. function calling (time tool)", test_function_calling),
        ("3. google_search grounding",      test_google_search),
        ("4. streaming",                    test_streaming),
    ]
    results: list[tuple[str, bool, str]] = []
    # Free tier is 5 RPM. Pace the test loop to stay under it: 4 tests
    # plus an occasional retry, spaced ~22s apart, fits comfortably.
    # 5xx errors are transient Google-side congestion — retry once.
    def _is_5xx(exc: Exception) -> bool:
        s = str(exc)
        return any(code in s for code in ("500", "502", "503", "504"))

    for i, (name, fn) in enumerate(tests):
        if i > 0:
            print(f"... sleeping 22s to respect 5 RPM free-tier limit ...\n")
            time.sleep(22)
        print(f"--- {name} ---")
        for attempt in (1, 2):
            try:
                ok = fn(client)
                results.append((name, ok, ""))
                break
            except Exception as e:
                if attempt == 1 and _is_5xx(e):
                    print(f"  transient {type(e).__name__} (5xx), retrying in 6s...")
                    time.sleep(6)
                    continue
                results.append((name, False, f"{type(e).__name__}: {e}"))
                print(f"  EXCEPTION: {type(e).__name__}: {e}")
                break
        print()

    print("=" * 50)
    all_ok = True
    for name, ok, err in results:
        mark = "OK  " if ok else "FAIL"
        suffix = f" — {err}" if err else ""
        print(f"  {mark}  {name}{suffix}")
        if not ok:
            all_ok = False
    print("=" * 50)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
