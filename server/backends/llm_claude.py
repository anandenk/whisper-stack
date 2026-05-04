"""Anthropic Claude LLM backend.

Used for the local-mode complex route (Sonnet) AND the cloud-mode simple
route (Haiku). Same class, different `model` arg from the factory.

Tool use:
The chat() method runs the tool-call loop entirely inside this backend —
the route handler just sees a final text reply (with `tools_used` metadata).
Loop is capped at config.TOOL_MAX_ROUNDS to prevent runaway billing.
"""

from __future__ import annotations

import time

from anthropic import Anthropic

from server import config
from server.tools.base import Tool


class ClaudeLLM:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.ANTHROPIC_MODEL
        self._client: Anthropic | None = None
        self._loaded = False

    def _get_client(self) -> Anthropic:
        if self._client is None:
            self._client = Anthropic()
        return self._client

    def is_loaded(self) -> bool:
        return self._loaded

    def chat(self, message: str, max_tokens: int = 1024,
             system_prompt: str | None = None,
             history: list[dict] | None = None,
             tools: list[Tool] | None = None) -> dict:
        client = self._get_client()
        t0 = time.perf_counter()

        # Anthropic expects messages to alternate user/assistant and end on
        # a user turn. `history` is already alternating; appending the new
        # user message preserves that.
        messages: list[dict] = list(history) if history else []
        messages.append({"role": "user", "content": message})

        base_kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            # Anthropic puts the system prompt at top-level, not in messages.
            base_kwargs["system"] = system_prompt

        # Tool-call loop. When `tools` is None or empty, this runs exactly
        # once and returns the model's text reply — same behavior as before.
        tool_index = {t.name: t for t in tools} if tools else {}
        anthropic_tools = [t.to_anthropic() for t in (tools or [])]

        total_input_tokens = 0
        total_output_tokens = 0
        tools_used: list[dict] = []
        final_reply = ""
        stop_reason = "end_turn"
        max_rounds = config.TOOL_MAX_ROUNDS

        for round_idx in range(max_rounds):
            kwargs = dict(base_kwargs, messages=messages)
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools

            msg = client.messages.create(**kwargs)
            total_input_tokens += msg.usage.input_tokens
            total_output_tokens += msg.usage.output_tokens
            stop_reason = msg.stop_reason

            # Pull text + tool_use blocks out of the response.
            text_chunks = []
            tool_calls = []
            for block in msg.content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text_chunks.append(block.text)
                elif btype == "tool_use":
                    tool_calls.append(block)

            # No tool calls → final reply, exit loop.
            if not tool_calls:
                final_reply = "".join(text_chunks).strip()
                break

            # Tool calls present → run handlers, append result messages, loop.
            messages.append({"role": "assistant", "content": [b.model_dump() for b in msg.content]})

            tool_results = []
            for tc in tool_calls:
                t_call = time.perf_counter()
                tool = tool_index.get(tc.name)
                if tool is None:
                    output = f"Tool {tc.name!r} is not available."
                else:
                    try:
                        output = tool.handler(dict(tc.input or {}))
                    except Exception as exc:
                        # Surface the error AS a tool_result so the model can
                        # decide whether to retry or apologize. Don't crash
                        # the route.
                        output = f"Tool {tc.name!r} raised {type(exc).__name__}: {exc}"
                latency = time.perf_counter() - t_call
                tools_used.append({
                    "name": tc.name,
                    "input": dict(tc.input or {}),
                    "output_chars": len(output),
                    "latency_s": latency,
                    "round": round_idx + 1,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": output,
                })

            messages.append({"role": "user", "content": tool_results})
        else:
            # for-else: ran out of rounds without breaking out. Use whatever
            # text we have, mark stop_reason so the caller can see we capped.
            final_reply = "".join(text_chunks).strip() if text_chunks else (
                "I tried to look something up but couldn't finish in time. "
                "Try asking me again."
            )
            stop_reason = "tool_max_rounds"

        elapsed = time.perf_counter() - t0
        self._loaded = True

        result = {
            "reply": final_reply,
            "model": self.model,
            "latency_s": elapsed,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "stop_reason": stop_reason,
        }
        if tools_used:
            result["tools_used"] = tools_used
        return result

    def warmup(self) -> None:
        """No-op: Anthropic has no cold-start cost worth pre-paying. The
        client lazily initializes on first chat()."""
        return None
