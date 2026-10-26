"""One Messages SSE request feeding one bounded ElevenLabs text-input WebSocket."""
import asyncio
import base64
import json
import time
from urllib.parse import urlencode

import anyio
from fastapi import HTTPException
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .model_stream import stream_reply
from .streaming import encode_event


class ConnectOnce(connect):
    """Reject redirects, including cross-origin redirects carrying xi-api-key."""
    def process_redirect(self, error):
        return error


async def overlapped_audio_events(client, settings, transcript, event, diagnostics,
                                   started, stt_ms, limit, connector=ConnectOnce):
    text_queue = asyncio.Queue(maxsize=8)
    audio_queue = asyncio.Queue(maxsize=8)
    tasks = []
    result = {}
    carry = b""
    sent_all_text = False
    tts_started = None
    failure = None
    event.update(pipeline_status="streaming", stt_elapsed_ms=stt_ms, audio_bytes=0,
                 tts_text_characters=0, text_audio_overlap=True)
    diagnostics.save(event)

    async def produce_text():
        try:
            async for text in stream_reply(client, settings.anthropic_api_key, transcript,
                                            diagnostics, event, result, started):
                await text_queue.put(text)
            await text_queue.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await text_queue.put(error)

    try:
        yield encode_event("start", id=event["id"], sample_rate=16000,
                           channels=1, encoding="pcm_s16le", sources=[])
        tasks.append(asyncio.create_task(produce_text()))
        tts_started = time.perf_counter()
        uri = ("wss://api.elevenlabs.io/v1/text-to-speech/" + settings.voice_id +
               "/stream-input?" + urlencode({"model_id": settings.tts_model,
                                             "output_format": "pcm_16000"}))
        async with connector(uri, additional_headers={"xi-api-key": settings.api_key},
                             open_timeout=10, close_timeout=2, max_size=512 * 1024,
                             max_queue=4, compression=None) as socket:
            await socket.send(json.dumps({"text": " ", "generation_config": {"chunk_length_schedule": [50]}}))

            async def send_text():
                nonlocal sent_all_text
                try:
                    while True:
                        text = await text_queue.get()
                        if isinstance(text, Exception):
                            raise text
                        if text is None:
                            sent_all_text = True
                            await socket.send(json.dumps({"text": ""}))
                            return
                        await socket.send(json.dumps({"text": text, "try_trigger_generation": True}))
                        event.setdefault("tts_first_text_elapsed_ms", round((time.perf_counter() - started) * 1000, 3))
                        event.setdefault("text_sent_before_llm_complete", not event.get("usage_complete", False))
                        event["tts_text_characters"] += len(text)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await audio_queue.put(error)

            async def receive_audio():
                try:
                    while True:
                        raw = await asyncio.wait_for(socket.recv(), timeout=60)
                        if not isinstance(raw, (str, bytes)) or len(raw) > 512 * 1024:
                            raise ValueError("Oversized speech WebSocket message")
                        message = json.loads(raw)
                        if not isinstance(message, dict) or message.get("error") or message.get("message"):
                            raise ValueError("Speech WebSocket error")
                        if message.get("audio"):
                            pcm = base64.b64decode(message["audio"], validate=True)
                            await audio_queue.put(pcm)
                        if message.get("isFinal") is True or message.get("is_final") is True:
                            if not sent_all_text:
                                raise ValueError("Premature speech completion")
                            await audio_queue.put(None)
                            return
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await audio_queue.put(error)

            tasks.extend([asyncio.create_task(send_text()), asyncio.create_task(receive_audio())])
            while True:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=70)
                if isinstance(chunk, Exception):
                    raise chunk
                if chunk is None:
                    break
                event["audio_bytes"] += len(chunk)
                if event["audio_bytes"] > limit:
                    raise ValueError("Speech response exceeded local size limit")
                pcm = carry + chunk
                size = len(pcm) // 2 * 2
                carry, pcm = pcm[size:], pcm[:size]
                if pcm:
                    if "first_audio_elapsed_ms" not in event:
                        event["first_audio_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
                        event["audio_before_llm_complete"] = not event.get("usage_complete", False)
                        diagnostics.save(event)
                    yield encode_event("audio", pcm=base64.b64encode(pcm).decode("ascii"))
            await asyncio.gather(*tasks)
        if carry or not event["audio_bytes"] or event.get("status") != "ok":
            raise ValueError("Incomplete voice exchange")
        event["pipeline_status"] = "ok"
    except (asyncio.CancelledError, GeneratorExit):
        event["pipeline_status"] = "disconnected"
        raise
    except HTTPException as error:
        event["pipeline_status"] = "llm_failed"
        failure = error.detail
    except (WebSocketException, OSError, TimeoutError, ValueError, TypeError):
        event["pipeline_status"] = "stream_failed"
        failure = "The overlapped voice stream failed. Audio may be partial; no automatic retry was made."
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        with anyio.CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
        event["total_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if tts_started is not None:
            event["tts_elapsed_ms"] = round((time.perf_counter() - tts_started) * 1000, 3)
        diagnostics.save(event)
    if failure:
        yield encode_event("error", id=event["id"], detail=failure)
    else:
        yield encode_event("done", id=event["id"], sources=result.get("sources", []))
