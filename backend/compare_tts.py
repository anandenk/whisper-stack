"""Two paid TTS samples, only with --live. No STT, Anthropic, or default changes."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import wave

from elevenlabs.client import AsyncElevenLabs
from elevenlabs.core.api_error import ApiError
import httpx

from .app import API_BASE, MAX_SPEECH_BYTES, LimitedResponseStream, Settings


MODELS = ("eleven_multilingual_v2", "eleven_flash_v2_5")
TEXT = ("This is a voice test for Ulaanbaatar. The example forecast is -5 degrees "
        "Celsius, with light winds and a high of 2 degrees.")


async def sample(settings, model, path, transport=None):
    async def bounded(response):
        if response.is_success:
            if response.headers.get("content-type", "").split(";")[0] not in {"audio/pcm", "audio/raw", "application/octet-stream"}:
                await response.aclose()
                raise ValueError("Unexpected audio format")
            if not response.is_stream_consumed:
                response.stream = LimitedResponseStream(response.stream, MAX_SPEECH_BYTES)

    result = {"model": model, "status": "failed", "first_pcm_chunk_ms": None, "chunks": 0}
    pcm = bytearray()
    # Fresh connection for each model, same endpoint/voice/text/format/chunk size.
    async with httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(60, connect=10),
                                 follow_redirects=False, transport=transport,
                                 event_hooks={"response": [bounded]}) as client:
        speech = AsyncElevenLabs(api_key=settings.api_key, httpx_client=client)
        started = time.perf_counter()
        try:
            async with speech.text_to_speech.with_raw_response.stream(
                voice_id=settings.voice_id, model_id=model, text=TEXT,
                output_format="pcm_16000", request_options={"max_retries": 0, "chunk_size": 1024},
            ) as upstream:
                result["headers_ms"] = round((time.perf_counter() - started) * 1000, 3)
                result["http_status"] = upstream.status_code
                result["provider_metadata"] = {name: upstream.headers[name] for name in
                    ("request-id", "history-item-id", "character-cost", "x-character-cost", "x-region")
                    if name in upstream.headers}
                async for chunk in upstream.data:
                    if not chunk:
                        continue
                    if result["first_pcm_chunk_ms"] is None:
                        result["first_pcm_chunk_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    result["chunks"] += 1
                    pcm.extend(chunk)
                    if len(pcm) > MAX_SPEECH_BYTES:
                        raise ValueError("Audio exceeds local cap")
            result["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
            if not pcm or len(pcm) % 2:
                raise ValueError("Empty or incomplete PCM")
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(pcm)
            result.update(status="ok", pcm_bytes=len(pcm), audio_seconds=len(pcm) / 32000,
                          wav_path=str(path))
        except Exception as error:
            # Never print provider bodies, exception messages, or credentials.
            result["error_type"] = type(error).__name__
            if isinstance(error, ApiError):
                result["http_status"] = error.status_code
            result["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


async def compare():
    settings = Settings.from_env()
    if not settings.api_key or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", settings.voice_id):
        raise SystemExit("Configure ElevenLabs and a valid voice locally before testing.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(__file__).parent / "output" / ("tts-comparison-" + stamp)
    folder.mkdir(parents=True, exist_ok=False)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "voice_id": settings.voice_id,
              "text": TEXT, "characters": len(TEXT), "format": "pcm_16000", "chunk_size": 1024,
              "calls_per_model": 1, "automatic_retries": 0, "fresh_connection_per_model": True,
              "scope": "TTS only; excludes STT, LLM, search, browser playback, and physical audio latency",
              "limitation": "One sample per model, fixed order; not a statistical latency/quality benchmark",
              "default_changed": False, "results": []}
    for model in MODELS:
        report["results"].append(await sample(settings, model, folder / (model + ".wav")))
        (folder / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if any(result["status"] != "ok" for result in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize exactly two short, paid ElevenLabs generations")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to run two paid ElevenLabs calls. No call was made.")
    asyncio.run(compare())
