"""Bounded Messages SSE -> final-answer words, with numeric usage snapshots only."""
import asyncio
import json
import re
import time

from fastapi import HTTPException
import httpx

from .diagnostics import request_estimate
from .llm import MODEL, MAX_RESPONSE_BYTES, final_answer, reply_request


class VoiceText:
    """A marker separates final speech from native-tool preambles and reasoning."""
    def __init__(self):
        self.pending = ""
        self.opened = False
        self.closed = False
        self.words = ""
        self.text = ""

    def add(self, fragment):
        if self.closed:
            if fragment.strip():
                raise ValueError("Text after final voice answer")
            return ""
        self.pending += fragment
        if not self.opened:
            position = self.pending.find("<voice>")
            if position == -1:
                if len(self.pending) > 1000:
                    raise ValueError("Missing final-answer marker")
                return ""
            self.pending = self.pending[position + 7:]
            self.opened = True
        end = self.pending.find("</voice>")
        if end >= 0:
            piece, trailing = self.pending[:end], self.pending[end + 8:]
            if trailing.strip():
                raise ValueError("Text after final voice answer")
            self.pending = ""
            self.closed = True
        else:
            # Hold a possible partial closing tag across provider deltas.
            keep = max((size for size in range(1, min(8, len(self.pending)) + 1)
                        if "</voice>".startswith(self.pending[-size:])), default=0)
            piece = self.pending[:-keep] if keep else self.pending
            self.pending = self.pending[-keep:] if keep else ""
        if "<" in piece or ">" in piece:
            raise ValueError("Unexpected voice markup")
        self.text += piece
        if len(self.text) > 1000:
            raise ValueError("Voice answer too long")
        self.words += piece
        if self.closed:
            output, self.words = self.words, ""
            return output
        splits = list(re.finditer(r"\s+", self.words))
        if not splits:
            return ""
        boundary = splits[-1].end()
        output, self.words = self.words[:boundary], self.words[boundary:]
        return output


async def sse_events(response):
    """Apply the byte cap before decoding lines, including malformed long lines."""
    total = 0
    pending = b""
    data = []
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("Model stream too large")
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if line.startswith(b"data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                event = json.loads(b"\n".join(data))
                data = []
                if not isinstance(event, dict):
                    raise ValueError("Invalid SSE event")
                yield event
    if pending.strip() or data:
        raise ValueError("Truncated SSE event")


async def stream_reply(client, api_key, text, diagnostics, event, result, pipeline_started):
    started = time.perf_counter()
    usage = {}
    blocks = []
    active = None
    complete = False
    message = None
    gate = VoiceText()
    searched = False
    saw_usage = False
    stopped_reason = None
    event["usage_complete"] = False
    diagnostics.save(event)

    def failure(code, detail):
        return HTTPException(code, detail, headers={"X-Diagnostics-ID": event["id"]})

    try:
        async with client.stream("POST", "/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2026-10-21"},
            json=reply_request(text, [], stream=True),
        ) as response:
            event["http_status"] = response.status_code
            event["provider_request_id"] = response.headers.get("request-id", "")[:256]
            if response.status_code != 200:
                event["status"] = "provider_error"
                raise failure(429 if response.status_code == 429 else 502,
                              "Anthropic rejected the streaming request. Check API access and model availability.")
            if response.headers.get("content-type", "").split(";")[0] != "text/event-stream":
                raise ValueError("Expected Messages SSE")
            async for item in sse_events(response):
                kind = item.get("type")
                if kind == "ping":
                    continue
                if kind == "error":
                    event["status"] = "provider_error"
                    raise failure(502, "Anthropic interrupted the answer. No automatic retry was made.")
                if kind == "message_start":
                    if message is not None:
                        raise ValueError("Repeated message")
                    message = item.get("message")
                    if not isinstance(message, dict) or message.get("content") != []:
                        raise ValueError("Invalid message start")
                    if isinstance(message.get("usage"), dict):
                        usage.update(message["usage"])
                elif kind == "content_block_start":
                    if message is None or active is not None or item.get("index") != len(blocks):
                        raise ValueError("Invalid content order")
                    block = item.get("content_block")
                    if not isinstance(block, dict):
                        raise ValueError("Invalid block")
                    blocks.append(block.copy())
                    active = len(blocks) - 1
                    if block.get("type") in {"server_tool_use", "web_search_tool_result"}:
                        if gate.opened:
                            raise ValueError("Tool call after spoken answer began")
                        gate = VoiceText()
                        searched |= block.get("type") == "server_tool_use" and block.get("name") == "web_search"
                    if block.get("type") == "text" and block.get("text"):
                        output = gate.add(block["text"])
                        if output:
                            event.setdefault("llm_first_text_elapsed_ms", round((time.perf_counter() - pipeline_started) * 1000, 3))
                            yield output
                elif kind == "content_block_delta":
                    if active is None or item.get("index") != active:
                        raise ValueError("Invalid delta order")
                    delta = item.get("delta")
                    if not isinstance(delta, dict):
                        raise ValueError("Invalid delta")
                    block = blocks[active]
                    if delta.get("type") == "text_delta":
                        fragment = delta.get("text")
                        if block.get("type") != "text" or not isinstance(fragment, str):
                            raise ValueError("Invalid text delta")
                        block["text"] = block.get("text", "") + fragment
                        output = gate.add(fragment)
                        if output:
                            event.setdefault("llm_first_text_elapsed_ms", round((time.perf_counter() - pipeline_started) * 1000, 3))
                            yield output
                    elif delta.get("type") == "citations_delta":
                        block.setdefault("citations", []).append(delta.get("citation"))
                    # Reasoning/signatures/tool JSON are never forwarded to speech.
                elif kind == "content_block_stop":
                    if active is None or item.get("index") != active:
                        raise ValueError("Invalid block stop")
                    active = None
                elif kind == "message_delta":
                    if message is None or active is not None:
                        raise ValueError("Invalid message delta")
                    delta = item.get("delta")
                    if not isinstance(delta, dict):
                        raise ValueError("Invalid message delta")
                    stopped_reason = delta.get("stop_reason", stopped_reason)
                    if isinstance(item.get("usage"), dict):
                        usage.update(item["usage"])
                        saw_usage = True
                elif kind == "message_stop":
                    if message is None or active is not None or not saw_usage:
                        raise ValueError("Missing message completion/usage")
                    complete = True
                    break
                # Unknown event types are forward-compatible; required completion still checked.
        event.update(request_estimate(usage, searched))
        event["usage_complete"] = complete
        event["stop_reason"] = stopped_reason if isinstance(stopped_reason, str) and stopped_reason in {"end_turn", "max_tokens", "pause_turn", "tool_use", "refusal"} else "unknown"
        if not complete:
            raise ValueError("Incomplete Messages stream")
        if message.get("model") != MODEL:
            event["estimated_cost_usd"] = event["estimated_token_cost_usd"] = None
            raise ValueError("Unexpected serving model")
        event["web_search_status"] = "failed" if any(
            block.get("type") == "web_search_tool_result" and isinstance(block.get("content"), dict)
            and block["content"].get("type") == "web_search_tool_result_error" for block in blocks
        ) else ("used" if searched else "not_used")
        wrapped, sources = final_answer(blocks)
        if (stopped_reason != "end_turn" or not gate.closed or not gate.text.strip()
                or wrapped != "<voice>" + gate.text + "</voice>"):
            event["status"] = "incomplete_reply"
            raise failure(502, "Haiku did not complete its spoken answer. Audio may be partial; no retry was made.")
        result["sources"] = sources
        event["status"] = "ok"
    except (asyncio.CancelledError, GeneratorExit):
        event["status"] = "cancelled"
        raise
    except httpx.TimeoutException:
        event["status"] = "timeout"
        raise failure(504, "Anthropic timed out. Audio may be partial; no retry was made.") from None
    except httpx.RequestError:
        event["status"] = "network_error"
        raise failure(502, "Could not complete the model stream. No retry was made.") from None
    except ValueError:
        event["status"] = "invalid_response"
        raise failure(502, "Anthropic returned an incomplete or invalid stream. Audio may be partial.") from None
    finally:
        if not complete:
            event.update(request_estimate(usage, searched))
            event["estimated_cost_usd"] = event["estimated_token_cost_usd"] = event["estimated_search_cost_usd"] = None
        event["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        event["llm_complete_elapsed_ms"] = round((time.perf_counter() - pipeline_started) * 1000, 3)
        diagnostics.save(event)
