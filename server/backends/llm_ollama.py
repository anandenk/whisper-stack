"""Ollama LLM backend (local-mode simple route).

Uses `/api/chat` (messages array) rather than `/api/generate` (single prompt)
so multi-turn `history` flows through natively. Response shape changes
slightly from /api/generate: reply is at `data["message"]["content"]`
instead of `data["response"]`. Token counts and timings are at the same
top-level keys.

Tool use:
Mirrors the ClaudeLLM tool loop — same `tools_used` metadata and
TOOL_MAX_ROUNDS cap. Llama 3.1 8B is less reliable at tool calling than
Sonnet (sometimes hallucinates args, sometimes refuses to call when it
should); auto-escalation in router.py routes "tool-likely" queries to
the complex (Sonnet) backend instead.
"""

from __future__ import annotations

import json
import time
from typing import Iterator

import requests

from server import config
from server.tools.base import Tool


class OllamaLLM:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.OLLAMA_MODEL
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def chat(self, message: str, max_tokens: int = 512,
             system_prompt: str | None = None,
             history: list[dict] | None = None,
             tools: list[Tool] | None = None) -> dict:
        # /api/chat takes a messages array. System prompt is a `system` role
        # entry at the front (NOT a top-level field — that's /api/generate's
        # convention). History is already alternating user/assistant pairs;
        # the new user turn goes at the end.
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        tool_index = {t.name: t for t in tools} if tools else {}
        ollama_tools = [t.to_ollama() for t in (tools or [])]

        total_input_tokens = 0
        total_output_tokens = 0
        total_eval_dur_ns = 0
        total_eval_count = 0
        tools_used: list[dict] = []
        final_reply = ""
        stop_reason = "stop"
        max_rounds = config.TOOL_MAX_ROUNDS

        t0 = time.perf_counter()
        for round_idx in range(max_rounds):
            payload: dict = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": max_tokens},
            }
            if ollama_tools:
                payload["tools"] = ollama_tools

            r = requests.post(config.OLLAMA_CHAT_URL, json=payload, timeout=120)
            r.raise_for_status()
            data = r.json()

            total_input_tokens += data.get("prompt_eval_count", 0)
            total_eval_count += data.get("eval_count", 0)
            total_output_tokens = total_eval_count
            total_eval_dur_ns += data.get("eval_duration", 0) or 0
            stop_reason = data.get("done_reason", "stop")

            msg = data.get("message") or {}
            content = (msg.get("content") or "").strip()
            tool_calls = msg.get("tool_calls") or []

            # No tool calls → final reply, exit loop.
            if not tool_calls:
                final_reply = content
                break

            # Tool calls present. Preserve the assistant message so the model
            # sees its own decision in the next round, then append a `tool`
            # role message per result.
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn = (tc or {}).get("function") or {}
                name = fn.get("name") or ""
                # `arguments` may be a dict (newer Ollama) or a JSON string.
                # Tolerate both.
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}

                t_call = time.perf_counter()
                tool = tool_index.get(name)
                if tool is None:
                    output = f"Tool {name!r} is not available."
                else:
                    try:
                        output = tool.handler(args)
                    except Exception as exc:
                        output = f"Tool {name!r} raised {type(exc).__name__}: {exc}"
                latency = time.perf_counter() - t_call
                tools_used.append({
                    "name": name,
                    "input": args,
                    "output_chars": len(output),
                    "latency_s": latency,
                    "round": round_idx + 1,
                })
                # Ollama links tool results to the call by NAME (not id —
                # /api/chat doesn't return one). Include it for safety.
                messages.append({
                    "role": "tool",
                    "name": name,
                    "content": output,
                })
        else:
            # for-else: ran out of rounds without breaking out.
            final_reply = content if content else (
                "I tried to look something up but couldn't finish in time. "
                "Try asking me again."
            )
            stop_reason = "tool_max_rounds"

        elapsed = time.perf_counter() - t0
        self._loaded = True

        tps = (total_eval_count / (total_eval_dur_ns / 1e9)
               if total_eval_dur_ns > 0 else 0.0)

        result = {
            "reply": final_reply,
            "model": self.model,
            "latency_s": elapsed,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "stop_reason": stop_reason,
            "tps": tps,
        }
        if tools_used:
            result["tools_used"] = tools_used
        return result

    def chat_stream(self, message: str, max_tokens: int = 512,
                    system_prompt: str | None = None,
                    history: list[dict] | None = None,
                    tools: list[Tool] | None = None) -> Iterator[dict]:
        """Streaming counterpart to chat() over /api/chat with stream=true.

        Each NDJSON line carries `message.content` (incremental text) and
        `done` (terminal flag). Tool calls appear in `message.tool_calls`
        (typically on the done line, but the parser tolerates them on
        any line). Tool loop matches chat() — same TOOL_MAX_ROUNDS cap,
        same `last_round_text` semantic for the final reply.

        Note: /converse/stream and /chat both withhold tools from the
        simple route (Llama 3.1 8B misbehaves with tools mid-conversation
        and auto-escalation already moves tool-likely queries to Sonnet),
        so the tool loop here is exercised only by direct callers.
        """
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        tool_index = {t.name: t for t in tools} if tools else {}
        ollama_tools = [t.to_ollama() for t in (tools or [])]

        total_input_tokens = 0
        total_eval_count = 0
        total_eval_dur_ns = 0
        tools_used: list[dict] = []
        last_round_text_parts: list[str] = []
        stop_reason = "stop"
        max_rounds = config.TOOL_MAX_ROUNDS

        t0 = time.perf_counter()
        round_text_parts: list[str] = []
        for round_idx in range(max_rounds):
            payload: dict = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.7, "num_predict": max_tokens},
            }
            if ollama_tools:
                payload["tools"] = ollama_tools

            round_text_parts = []
            round_assistant_text = ""
            round_tool_calls: list[dict] = []

            with requests.post(config.OLLAMA_CHAT_URL, json=payload,
                               stream=True, timeout=120) as r:
                r.raise_for_status()
                for raw in r.iter_lines(decode_unicode=False):
                    if not raw:
                        continue
                    data = json.loads(raw)
                    msg = data.get("message") or {}
                    content = msg.get("content") or ""
                    if content:
                        round_text_parts.append(content)
                        round_assistant_text += content
                        yield {"type": "text", "delta": content}
                    tcs = msg.get("tool_calls") or []
                    if tcs:
                        round_tool_calls.extend(tcs)
                    if data.get("done"):
                        total_input_tokens += data.get("prompt_eval_count", 0) or 0
                        eval_count = data.get("eval_count", 0) or 0
                        total_eval_count += eval_count
                        total_eval_dur_ns += data.get("eval_duration", 0) or 0
                        stop_reason = data.get("done_reason", "stop")
                        break

            if not round_tool_calls:
                last_round_text_parts = round_text_parts
                break

            # Tool calls present — preserve assistant message + tool_calls
            # so the model sees its own decision next round, then thread
            # tool result messages by NAME (Ollama's /api/chat doesn't
            # return tool_use ids).
            messages.append({
                "role": "assistant",
                "content": round_assistant_text,
                "tool_calls": round_tool_calls,
            })

            for tc in round_tool_calls:
                fn = (tc or {}).get("function") or {}
                name = fn.get("name") or ""
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}

                yield {
                    "type": "tool_call",
                    "id": None,
                    "name": name,
                    "input": args,
                    "round": round_idx + 1,
                }

                t_call = time.perf_counter()
                tool = tool_index.get(name)
                if tool is None:
                    output = f"Tool {name!r} is not available."
                else:
                    try:
                        output = tool.handler(args)
                    except Exception as exc:
                        output = f"Tool {name!r} raised {type(exc).__name__}: {exc}"
                latency = time.perf_counter() - t_call

                tools_used.append({
                    "name": name,
                    "input": args,
                    "output_chars": len(output),
                    "latency_s": latency,
                    "round": round_idx + 1,
                })
                yield {
                    "type": "tool_result",
                    "id": None,
                    "name": name,
                    "output_chars": len(output),
                    "latency_s": latency,
                    "round": round_idx + 1,
                }
                messages.append({
                    "role": "tool",
                    "name": name,
                    "content": output,
                })
        else:
            last_round_text_parts = round_text_parts if round_text_parts else [
                "I tried to look something up but couldn't finish in time. "
                "Try asking me again."
            ]
            stop_reason = "tool_max_rounds"

        elapsed = time.perf_counter() - t0
        self._loaded = True

        tps = (total_eval_count / (total_eval_dur_ns / 1e9)
               if total_eval_dur_ns > 0 else 0.0)

        final: dict = {
            "type": "final",
            "reply": "".join(last_round_text_parts).strip(),
            "model": self.model,
            "latency_s": elapsed,
            "input_tokens": total_input_tokens,
            "output_tokens": total_eval_count,
            "stop_reason": stop_reason,
            "tps": tps,
        }
        if tools_used:
            final["tools_used"] = tools_used
        yield final

    def warmup(self) -> None:
        """Trigger model load so first user simple-route call isn't a cold start."""
        self.chat("hi", max_tokens=4)
