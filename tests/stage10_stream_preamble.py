"""Regression: chat_stream tool loop with preamble text + tool_use in one round.

Background: when Sonnet emits text BEFORE calling a tool in a streaming
response (e.g. "Let me check the current time..." -> tool_use), the
streaming SDK's content blocks carry an extra `parsed_output` field on
text blocks. If we serialize those blocks with `model_dump()` and send
them back as the assistant turn for round 2, Anthropic rejects the
request:

    BadRequestError: 400 - messages.N.content.0.text.parsed_output:
                            Extra inputs are not permitted

This was a real production bug — surfaced by a multi-turn web-client
session asking "What is the weather like in Mongolia right now?", which
auto-escalates to Sonnet. Sonnet decides to call get_current_time to
gauge the season, emits "Let me check the current time..." as preamble,
then tool_use. Round 2 fails the API call.

Fix: ClaudeLLM._serialize_assistant_content() whitelists known wire-
format fields per block type (text -> {type, text}, tool_use ->
{type, id, name, input}), dropping SDK-internal additions.

This test forces the preamble shape with a phrasing that reliably elicits
it ("First explain ... then call the tool"). If it ever stops eliciting
the preamble (model behavior changes), the test will still PASS — it
just won't exercise the bug. To verify the bug-trigger is still firing,
the test asserts the chat_stream event sequence contains text events
BEFORE the tool_call event (i.e. there was preamble in round 1).

Doesn't go through the HTTP server — calls ClaudeLLM directly. Doesn't
need the server to be running.

Run from repo root after activating .venv:
    python tests/stage10_stream_preamble.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: F401  -- triggers .env load
from server.backends.llm_claude import ClaudeLLM
from server.tools import all_tools


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    print("Calling ClaudeLLM.chat_stream with a query that elicits preamble + tool_use...")
    llm = ClaudeLLM()
    events: list[dict] = []
    for ev in llm.chat_stream(
        "What time is it? First explain in one short sentence what you "
        "are about to do, then call the tool.",
        max_tokens=200,
        tools=all_tools(),
    ):
        events.append(ev)

    types = [e["type"] for e in events]
    print(f"  event sequence: {types}")

    must("final" in types,
         f"No 'final' event — chat_stream raised before completing round 2 "
         f"(parsed_output bug?). Sequence: {types}")
    must("tool_call" in types and "tool_result" in types,
         f"No tool_call/tool_result events — query didn't trigger tool use, "
         f"so the bug-trigger conditions weren't met. Sequence: {types}")

    # Confirm preamble-text-before-tool: there should be at least one 'text'
    # event BEFORE the first 'tool_call'. If not, the model went straight to
    # tool_use and we didn't actually exercise the parsed_output code path.
    first_tool_call_idx = next(i for i, t in enumerate(types) if t == "tool_call")
    text_before_tool = sum(1 for t in types[:first_tool_call_idx] if t == "text")
    must(text_before_tool >= 1,
         f"No text events before first tool_call — model went straight to "
         f"tool_use, so the bug trigger didn't fire. Sequence: {types}. "
         f"Try a different prompt to force preamble.")

    final = next(e for e in events if e["type"] == "final")
    print(f"  preamble text deltas: {text_before_tool}")
    print(f"  final.reply: {final['reply']!r}")
    print(f"  tools_used: {final.get('tools_used', [])}")

    must(final["reply"].strip(),
         f"final.reply empty after multi-round tool flow: {final}")
    must(final.get("tools_used") and any(
        t["name"] == "get_current_time" for t in final["tools_used"]),
        f"final.tools_used missing get_current_time: {final}")

    print()
    print("=" * 64)
    print("  Regression: preamble-text + tool_use in one round verified.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
