"""Anthropic Claude LLM backend.

Used for the local-mode complex route (Sonnet) AND the cloud-mode simple
route (Haiku). Same class, different `model` arg from the factory.

Tool use:
Both chat() and chat_stream() run the tool-call loop entirely inside
this backend — route handlers just see a final text reply (with
`tools_used` metadata) for chat(), or a sequence of events terminating
in {"type": "final", ...} for chat_stream(). Loop is capped at
config.TOOL_MAX_ROUNDS to prevent runaway billing.
"""

from __future__ import annotations

import time
from typing import Iterator

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

    def chat_stream(self, message: str, max_tokens: int = 1024,
                    system_prompt: str | None = None,
                    history: list[dict] | None = None,
                    tools: list[Tool] | None = None) -> Iterator[dict]:
        """Streaming counterpart to chat(). See LLM Protocol for the event
        contract.

        Tool loop runs inside the same way as chat(). Within a round,
        text deltas stream as they arrive; tool_call events are yielded
        BEFORE handler execution and tool_result events AFTER. The
        terminating `final` event carries `reply` = the LAST round's
        text only (matches chat()'s "drop intermediate thinking text"
        semantic), so voice callers can TTS final.reply directly.
        """
        client = self._get_client()
        t0 = time.perf_counter()

        messages: list[dict] = list(history) if history else []
        messages.append({"role": "user", "content": message})

        base_kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            base_kwargs["system"] = system_prompt

        tool_index = {t.name: t for t in tools} if tools else {}
        anthropic_tools = [t.to_anthropic() for t in (tools or [])]

        total_input_tokens = 0
        total_output_tokens = 0
        tools_used: list[dict] = []
        last_round_text_parts: list[str] = []
        stop_reason = "end_turn"
        max_rounds = config.TOOL_MAX_ROUNDS

        round_idx = 0
        for round_idx in range(max_rounds):
            kwargs = dict(base_kwargs, messages=messages)
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools

            round_text_parts: list[str] = []
            with client.messages.stream(**kwargs) as stream:
                for delta in stream.text_stream:
                    round_text_parts.append(delta)
                    yield {"type": "text", "delta": delta}
                final_msg = stream.get_final_message()

            total_input_tokens += final_msg.usage.input_tokens
            total_output_tokens += final_msg.usage.output_tokens
            stop_reason = final_msg.stop_reason

            tool_calls = [b for b in final_msg.content
                          if getattr(b, "type", None) == "tool_use"]

            if not tool_calls:
                # Final round — its text IS the reply.
                last_round_text_parts = round_text_parts
                break

            # Tool calls present — preserve assistant's full content (text +
            # tool_use blocks) so the next round sees its own decision, then
            # run handlers and append tool_result blocks.
            messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in final_msg.content],
            })

            tool_results = []
            for tc in tool_calls:
                tc_input = dict(tc.input or {})
                yield {
                    "type": "tool_call",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc_input,
                    "round": round_idx + 1,
                }

                t_call = time.perf_counter()
                tool = tool_index.get(tc.name)
                if tool is None:
                    output = f"Tool {tc.name!r} is not available."
                else:
                    try:
                        output = tool.handler(tc_input)
                    except Exception as exc:
                        output = f"Tool {tc.name!r} raised {type(exc).__name__}: {exc}"
                latency = time.perf_counter() - t_call

                tools_used.append({
                    "name": tc.name,
                    "input": tc_input,
                    "output_chars": len(output),
                    "latency_s": latency,
                    "round": round_idx + 1,
                })
                yield {
                    "type": "tool_result",
                    "id": tc.id,
                    "name": tc.name,
                    "output_chars": len(output),
                    "latency_s": latency,
                    "round": round_idx + 1,
                }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": output,
                })

            messages.append({"role": "user", "content": tool_results})
        else:
            # Loop ran out of rounds without a tool-free reply. Use whatever
            # text the last round produced; if that's empty, surface a
            # graceful apology so the voice user hears SOMETHING.
            last_round_text_parts = round_text_parts if round_text_parts else [
                "I tried to look something up but couldn't finish in time. "
                "Try asking me again."
            ]
            stop_reason = "tool_max_rounds"

        elapsed = time.perf_counter() - t0
        self._loaded = True

        final: dict = {
            "type": "final",
            "reply": "".join(last_round_text_parts).strip(),
            "model": self.model,
            "latency_s": elapsed,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "stop_reason": stop_reason,
        }
        if tools_used:
            final["tools_used"] = tools_used
        yield final

    def warmup(self) -> None:
        """No-op: Anthropic has no cold-start cost worth pre-paying. The
        client lazily initializes on first chat()."""
        return None
