"""Single-request Haiku replies using the documented Messages API."""

from datetime import datetime, timezone
import json
import time
from uuid import uuid4

from fastapi import HTTPException
import httpx

from .diagnostics import PRICING_DATE, usage_estimate


MODEL = "claude-haiku-5-5"
MAX_RESPONSE_BYTES = 256 * 1024
SYSTEM_PROMPT = (
    "You are Whisper Stack, a helpful desk voice assistant. Answer the current user "
    "directly in one to three short sentences, at most 80 words and 900 characters. "
    "Use plain spoken text, without Markdown. Be clear about uncertainty. You have "
    "no connected tools or live web access; do not claim to perform external actions."
)


def key_format_valid(key):
    return key.startswith("sk-ant-") and not any(character.isspace() for character in key)


async def generate_reply(client, api_key, text, history, diagnostics, endpoint):
    if not api_key:
        raise HTTPException(503, "Configure ANTHROPIC_API_KEY locally with python -m backend.configure_anthropic, then restart.")
    if not key_format_valid(api_key):
        raise HTTPException(503, "The configured key is not in Anthropic's sk-ant- format. Run the local key setup again.")
    event = {"id": str(uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
             "endpoint": endpoint, "model": MODEL, "status": "pending", "tokens": None,
             "estimated_cost_usd": None, "pricing_checked": PRICING_DATE}
    # Fail before any billable call if local diagnostic storage is unavailable.
    diagnostics.save(event)
    started = time.perf_counter()

    def failure(status, message):
        return HTTPException(status, message, headers={"X-Diagnostics-ID": event["id"]})

    try:
        async with client.stream(
            "POST", "/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2026-10-21"},
            json={"model": MODEL, "max_tokens": 1024, "system": SYSTEM_PROMPT,
                  "messages": [*history, {"role": "user", "content": text}],
                  "thinking": {"type": "adaptive"}, "output_config": {"effort": "low"},
                  "inference_geo": "global", "service_tier": "standard_only"},
        ) as response:
            event["http_status"] = response.status_code
            event["provider_request_id"] = response.headers.get("request-id", "")[:256]
            if response.status_code != 200:
                event["status"] = "provider_error"
                message = "Anthropic rejected the request. Check API access, credits, and model availability."
                if response.status_code in (401, 403):
                    message = "Anthropic denied access. Check the API key and permissions."
                raise failure(429 if response.status_code == 429 else 502, message)
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise ValueError("oversized response")
            payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("invalid response")
        event["tokens"], event["estimated_cost_usd"] = usage_estimate(payload.get("usage"))
        if payload.get("model") != MODEL:
            event["estimated_cost_usd"] = None
        blocks = payload.get("content")
        if not isinstance(blocks, list) or not all(isinstance(block, dict) for block in blocks):
            raise ValueError("invalid content")
        parts = [block.get("text") for block in blocks if block.get("type") == "text"]
        if not all(isinstance(part, str) for part in parts):
            raise ValueError("invalid text")
        reply = " ".join(parts).strip()
        stop = payload.get("stop_reason")
        event["stop_reason"] = stop if isinstance(stop, str) and stop in {"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"} else "unknown"
        if stop != "end_turn" or not reply or len(reply) > 1000:
            event["status"] = "incomplete_reply"
            raise failure(502, "Haiku did not return a complete short reply. No automatic retry was made.")
        event["status"] = "ok"
    except httpx.TimeoutException:
        event["status"] = "timeout"
        raise failure(504, "Anthropic timed out. Usage may have been billed; no automatic retry was made.") from None
    except httpx.RequestError:
        event["status"] = "network_error"
        raise failure(502, "Could not reach Anthropic. No automatic retry was made.") from None
    except ValueError:
        event["status"] = "invalid_response"
        raise failure(502, "Anthropic returned an unexpected response.") from None
    finally:
        event["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        diagnostics.save(event)
    return reply, event
