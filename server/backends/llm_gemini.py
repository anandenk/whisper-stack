"""Gemini 2.5 Flash LLM backend.

Uses the unified google-genai SDK. Mirrors the chat() + chat_stream() shape
used by ClaudeLLM / OllamaLLM so app.py route handlers don't care which
backend is in use.

Two tool modes, MUTUALLY EXCLUSIVE per request:

  * `tools=[Tool, ...]`     custom function calling (dispatch loop runs
                            here, capped at config.TOOL_MAX_ROUNDS)
  * `enable_search=True`    google_search grounding (no dispatch loop;
                            one API call, Google runs the search inside it)

Gemini 2.5 explicitly forbids combining google_search with function_declarations
in the same request (returns 400 INVALID_ARGUMENT). The caller picks one per
turn. If both are passed, tools wins and a warning is logged.

History shape matches the other backends: list[{"role": "user"|"assistant",
"content": str}]. The backend translates "assistant" → Gemini's "model" role
internally.
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

from google import genai
from google.genai import types

from server import config
from server.tools.base import Tool

log = logging.getLogger(__name__)


def _tool_to_declaration(tool: Tool) -> dict:
    """Convert a Tool to a Gemini function declaration dict."""
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.input_schema,
    }


def _function_call_from_parts(parts) -> tuple[str, dict, str | None] | None:
    """Find the first function_call part on a Content; return (name, args, id) or None."""
    if not parts:
        return None
    for p in parts:
        fc = getattr(p, "function_call", None)
        if fc and fc.name:
            return fc.name, (dict(fc.args) if fc.args else {}), getattr(fc, "id", None)
    return None


def _stop_reason(resp) -> str:
    """Best-effort stop_reason string from a Gemini response.

    candidates[0].finish_reason is a FinishReason enum (e.g.
    FinishReason.STOP). Use `.name` so we get "stop" not "finishreason.stop".
    """
    if not resp.candidates:
        return "stop"
    fr = getattr(resp.candidates[0], "finish_reason", None)
    if fr is None:
        return "stop"
    return str(getattr(fr, "name", fr)).lower()


def _maybe_inline_brevity_directive(message: str, enable_search: bool) -> str:
    """Inline a brevity instruction when google_search grounding is on.

    Gemini's `google_search` mode reliably ignores `system_instruction`'s
    "answer in one sentence" rule because the grounding path treats the
    system prompt as advice and the search results as the authority on
    content. Inline instructions (in the user message) survive that
    override. Without this nudge a "what's the weather" voice query
    returns a four-sentence forecast with day/night breakdowns; with
    it the same query returns a single sentence.
    """
    if not enable_search:
        return message
    return (
        f"{message}\n\n"
        "Answer this in exactly one short spoken sentence. Pick one unit "
        "of measurement (no parenthetical conversions). No lists, no "
        "multi-period breakdowns."
    )


def _accumulate_usage(resp) -> tuple[int, int]:
    """Pull (input_tokens, output_tokens) out of a response's usage_metadata."""
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return 0, 0
    inp = getattr(um, "prompt_token_count", 0) or 0
    out = getattr(um, "candidates_token_count", 0) or 0
    return inp, out


class GeminiLLM:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.GEMINI_MODEL
        self._client: genai.Client | None = None
        self._loaded = False

    def _get_client(self) -> genai.Client:
        if self._client is None:
            if not config.GOOGLE_API_KEY:
                raise RuntimeError(
                    "GOOGLE_API_KEY is empty — put it in .env (load_dotenv "
                    "override=True is already applied in server.config)."
                )
            self._client = genai.Client(api_key=config.GOOGLE_API_KEY)
        return self._client

    def is_loaded(self) -> bool:
        return self._loaded

    def _build_config(self, max_tokens: int, system_prompt: str | None,
                      tools: list[Tool] | None,
                      enable_search: bool) -> types.GenerateContentConfig:
        # Gemini 2.5 Flash has "thinking" enabled by default; internal
        # reasoning tokens count against max_output_tokens. For voice the
        # replies are short (1–2 sentences) and we cap max_output_tokens
        # at CONVERSE_MAX_TOKENS, so thinking will silently eat the budget
        # on harder queries (grounded search, "what did you mean" follow-ups)
        # and the visible reply gets truncated to e.g. "Currently," or
        # "My". Setting thinking_budget=0 disables thinking entirely.
        #
        # temperature=0.4 keeps the model adhering to the system prompt's
        # "one sentence" rule. At Gemini's default ~1.0 it ignores the rule
        # for grounded queries and dumps a multi-period forecast.
        kwargs: dict = {
            "max_output_tokens": max_tokens,
            "temperature": 0.4,
            "thinking_config": types.ThinkingConfig(thinking_budget=0),
        }
        if system_prompt:
            kwargs["system_instruction"] = system_prompt

        if tools:
            decls = [_tool_to_declaration(t) for t in tools]
            kwargs["tools"] = [types.Tool(function_declarations=decls)]
            if enable_search:
                log.warning(
                    "GeminiLLM: enable_search=True ignored because custom "
                    "tools are present (Gemini 2.5 cannot combine them)."
                )
        elif enable_search:
            kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]

        return types.GenerateContentConfig(**kwargs)

    def _build_initial_contents(self, message: str,
                                history: list[dict] | None) -> list:
        contents: list = []
        if history:
            for turn in history:
                role_in = (turn.get("role") or "").lower()
                gemini_role = "model" if role_in == "assistant" else "user"
                contents.append(types.Content(
                    role=gemini_role,
                    parts=[types.Part(text=turn.get("content", ""))],
                ))
        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=message)],
        ))
        return contents

    def _dispatch_tool(self, tool_index: dict[str, Tool], name: str,
                       args: dict) -> tuple[str, float]:
        """Run a tool handler; return (output_text, latency_s)."""
        t_call = time.perf_counter()
        tool = tool_index.get(name)
        if tool is None:
            output = f"Tool {name!r} is not available."
        else:
            try:
                output = tool.handler(args)
            except Exception as exc:  # noqa: BLE001
                output = f"Tool {name!r} raised {type(exc).__name__}: {exc}"
        return output, time.perf_counter() - t_call

    def chat(self, message: str, max_tokens: int = 1024,
             system_prompt: str | None = None,
             history: list[dict] | None = None,
             tools: list[Tool] | None = None,
             enable_search: bool = False) -> dict:
        client = self._get_client()
        t0 = time.perf_counter()

        contents = self._build_initial_contents(
            _maybe_inline_brevity_directive(message, enable_search),
            history,
        )
        cfg = self._build_config(max_tokens, system_prompt, tools, enable_search)
        tool_index = {t.name: t for t in tools} if tools else {}

        total_input_tokens = 0
        total_output_tokens = 0
        tools_used: list[dict] = []
        final_reply = ""
        stop_reason = "stop"
        max_rounds = config.TOOL_MAX_ROUNDS

        for round_idx in range(max_rounds):
            resp = client.models.generate_content(
                model=self.model, contents=contents, config=cfg,
            )
            inp, out = _accumulate_usage(resp)
            total_input_tokens += inp
            total_output_tokens += out
            stop_reason = _stop_reason(resp)

            fc_info = _function_call_from_parts(
                resp.candidates[0].content.parts if resp.candidates else None,
            )

            if fc_info is None:
                final_reply = (resp.text or "").strip()
                break

            name, args, fc_id = fc_info
            output, latency = self._dispatch_tool(tool_index, name, args)
            tools_used.append({
                "name": name, "input": args, "output_chars": len(output),
                "latency_s": latency, "round": round_idx + 1,
            })

            # Thread the model's function-call turn + our function-response
            # back into the conversation for the next round.
            contents.append(resp.candidates[0].content)
            fr_kwargs: dict = {"name": name, "response": {"result": output}}
            if fc_id:
                fr_kwargs["id"] = fc_id
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(**fr_kwargs)],
            ))
        else:
            # for-else: ran out of rounds without producing a tool-free reply.
            final_reply = final_reply or (
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
                    tools: list[Tool] | None = None,
                    enable_search: bool = False) -> Iterator[dict]:
        """Streaming counterpart to chat().

        Text deltas stream as they arrive. Function calls appear only at the
        end of a streamed round (Gemini does not stream function-call args
        token-by-token), so tool_call events fire after the stream drains.
        The final round's text is what gets emitted as `reply` in the
        terminating `final` event — matching the ClaudeLLM convention so
        voice callers TTS the right thing.
        """
        client = self._get_client()
        t0 = time.perf_counter()

        contents = self._build_initial_contents(
            _maybe_inline_brevity_directive(message, enable_search),
            history,
        )
        cfg = self._build_config(max_tokens, system_prompt, tools, enable_search)
        tool_index = {t.name: t for t in tools} if tools else {}

        total_input_tokens = 0
        total_output_tokens = 0
        tools_used: list[dict] = []
        last_round_text_parts: list[str] = []
        stop_reason = "stop"
        max_rounds = config.TOOL_MAX_ROUNDS

        for round_idx in range(max_rounds):
            round_text_parts: list[str] = []
            assistant_content = None
            fc_info: tuple[str, dict, str | None] | None = None
            # Per-round running totals. Gemini's streaming usage_metadata is
            # CUMULATIVE within the round, not per-chunk delta, so we
            # overwrite with the latest non-zero values instead of summing.
            # Earlier code summed and reported ~3x the real token count,
            # which broke /converse's estimated_cost_usd reporting.
            round_input_tokens = 0
            round_output_tokens = 0

            stream = client.models.generate_content_stream(
                model=self.model, contents=contents, config=cfg,
            )
            for chunk in stream:
                piece = chunk.text or ""
                if piece:
                    round_text_parts.append(piece)
                    yield {"type": "text", "delta": piece}

                # The last chunk carries the cumulative usage_metadata. We
                # take whatever the most recent chunk reports as the round's
                # total (the SDK's running cumulative pattern).
                inp, out = _accumulate_usage(chunk)
                if inp:
                    round_input_tokens = inp
                if out:
                    round_output_tokens = out
                sr = _stop_reason(chunk)
                if sr:
                    stop_reason = sr

                if chunk.candidates and chunk.candidates[0].content:
                    parts = chunk.candidates[0].content.parts
                    fc_here = _function_call_from_parts(parts)
                    if fc_here is not None:
                        fc_info = fc_here
                        assistant_content = chunk.candidates[0].content

            # Roll the round's final cumulative totals into the
            # cross-round totals. Multi-round tool dispatch will sum
            # correctly because each round's totals are independent
            # cumulatives that we add in once per round.
            total_input_tokens += round_input_tokens
            total_output_tokens += round_output_tokens

            if fc_info is None:
                last_round_text_parts = round_text_parts
                break

            # Function call — dispatch, then loop for the model's follow-up.
            name, args, fc_id = fc_info
            yield {
                "type": "tool_call", "id": fc_id, "name": name,
                "input": args, "round": round_idx + 1,
            }
            output, latency = self._dispatch_tool(tool_index, name, args)
            tools_used.append({
                "name": name, "input": args, "output_chars": len(output),
                "latency_s": latency, "round": round_idx + 1,
            })
            yield {
                "type": "tool_result", "id": fc_id, "name": name,
                "output_chars": len(output), "latency_s": latency,
                "round": round_idx + 1,
            }

            if assistant_content is not None:
                contents.append(assistant_content)
            fr_kwargs: dict = {"name": name, "response": {"result": output}}
            if fc_id:
                fr_kwargs["id"] = fc_id
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(**fr_kwargs)],
            ))
        else:
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
        """No-op: Gemini is hosted and has no cold-start cost worth pre-paying.
        The client lazily initializes on first chat() (consistent with
        ClaudeLLM.warmup)."""
        return None
