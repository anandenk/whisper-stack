"""Streaming text/audio plumbing for /converse/stream.

The shape of the streaming pipeline is:

    LLM text deltas  --->  SentenceChunker  --->  TTS.synth_stream  --->  audio events

Each text delta from the LLM lands in the chunker's buffer; complete
sentences (terminated by `.`, `!`, or `?` followed by whitespace) are
emitted as the buffer crosses a boundary. The route handler hands those
sentences to TTS.synth_stream for per-sentence synthesis, and yields the
resulting PCM chunks as SSE `audio` events. The trailing fragment with
no terminating punctuation is flushed at LLM end-of-stream.

Tool-use interaction:
The route resets the chunker on a `tool_call` event so any partial
sentence from "thinking text" before a tool call is dropped (we can't
unspeak audio chunks already emitted, but we at least avoid completing
the sentence). Sonnet under VOICE_SYSTEM_PROMPT typically goes straight
to tool_use without preamble, so this rarely fires in practice.

Boundary regex:
The plan-spec'd boundary is `[.!?][\\s\\n]+` (punctuation followed by
whitespace). We deliberately do NOT match end-of-string mid-stream —
it would split "Mr" from " Smith" (well, false positive on "Mr." too,
but waiting for whitespace at least keeps the model's mid-token output
intact). flush() handles the unterminated tail at stream end.
"""

from __future__ import annotations

import re

# Match one-or-more punctuation followed by one-or-more whitespace.
# Multiple terminal punctuation ("?!") collapses to a single boundary.
_BOUNDARY_RE = re.compile(r"[.!?]+\s+")


class SentenceChunker:
    """Stream-friendly text -> sentence segmenter.

    `feed(delta)` accepts one chunk at a time and returns the list of
    complete sentences that just became available. `flush()` returns
    whatever residual (no trailing punctuation) is left in the buffer
    so the final sentence isn't dropped at end-of-stream. `reset()`
    discards the buffer (used when a tool_call invalidates an
    in-progress "thinking sentence").
    """

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        self._buf += delta
        out: list[str] = []
        while True:
            m = _BOUNDARY_RE.search(self._buf)
            if not m:
                break
            end = m.end()
            sentence = self._buf[:end].strip()
            self._buf = self._buf[end:]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        leftover = self._buf.strip()
        self._buf = ""
        return [leftover] if leftover else []

    def reset(self) -> None:
        self._buf = ""

    @property
    def buffer(self) -> str:
        """Read-only view of the unflushed buffer; useful for tests."""
        return self._buf
