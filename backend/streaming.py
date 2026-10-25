"""Bounded PCM events: send audio immediately, keep text and audio out of storage."""
import asyncio
import base64
from contextlib import aclosing
import json
import time

from elevenlabs.core.api_error import ApiError
from fastapi import HTTPException
import httpx


def encode_event(kind, **values):
    return (json.dumps({"type": kind, **values}, separators=(",", ":")) + "\n").encode()


async def audio_events(speech, settings, text, event, sources, diagnostics, started, stt_ms, limit):
    tts_started = time.perf_counter()
    event.update(pipeline_status="streaming", stt_elapsed_ms=stt_ms, audio_bytes=0)
    diagnostics.save(event)
    failure = None
    carry = b""
    try:
        yield encode_event("start", id=event["id"], sample_rate=16000, channels=1,
                           encoding="pcm_s16le", sources=sources)
        async with aclosing(speech.text_to_speech.stream(
            voice_id=settings.voice_id, text=text, model_id=settings.tts_model,
            output_format="pcm_16000", request_options={"max_retries": 0, "chunk_size": 1024},
        )) as chunks:
            async for chunk in chunks:
                event["audio_bytes"] += len(chunk)
                if event["audio_bytes"] > limit:
                    raise HTTPException(502, "ElevenLabs response exceeded the local size limit.")
                pcm = carry + chunk
                carry = pcm[len(pcm) // 2 * 2:]
                pcm = pcm[:len(pcm) // 2 * 2]
                if not pcm:
                    continue
                if "first_audio_elapsed_ms" not in event:
                    event["first_audio_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    diagnostics.save(event)
                yield encode_event("audio", pcm=base64.b64encode(pcm).decode("ascii"))
        if carry or not event["audio_bytes"]:
            raise HTTPException(502, "ElevenLabs returned empty or incomplete PCM audio.")
        event["pipeline_status"] = "ok"
    except (asyncio.CancelledError, GeneratorExit):
        event["pipeline_status"] = "disconnected"
        raise
    except HTTPException as error:
        event["pipeline_status"] = "tts_failed"
        failure = error.detail
    except httpx.TimeoutException:
        event["pipeline_status"] = "tts_failed"
        failure = "ElevenLabs timed out. Audio may be incomplete; no automatic retry was made."
    except (httpx.RequestError, ApiError, ValueError):
        event["pipeline_status"] = "tts_failed"
        failure = "Speech streaming failed. Audio may be incomplete; no automatic retry was made."
    finally:
        event.update(tts_elapsed_ms=round((time.perf_counter() - tts_started) * 1000, 3),
                     total_elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        diagnostics.save(event)
    if failure:
        yield encode_event("error", id=event["id"], detail=failure)
    else:
        yield encode_event("done", id=event["id"])
