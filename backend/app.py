"""Local, buffered ElevenLabs/Haiku voice loop. No hardware integration yet."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
import os
from pathlib import Path
import re
import time
from typing import Literal
import wave

from dotenv import load_dotenv
from elevenlabs.client import AsyncElevenLabs
from elevenlabs.core.api_error import ApiError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .diagnostics import Diagnostics
from .llm import MODEL, generate_reply, key_format_valid


API_BASE = "https://api.elevenlabs.io"
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_SPEECH_BYTES = 8 * 1024 * 1024
AUDIO_TYPES = {
    "audio/wav": "recording.wav",
    "audio/x-wav": "recording.wav",
    "audio/mpeg": "recording.mp3",
    "audio/mp4": "recording.m4a",
    "audio/x-m4a": "recording.m4a",
    "audio/webm": "recording.webm",
    "audio/ogg": "recording.ogg",
    "audio/flac": "recording.flac",
}


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)
    voice_id: str = ""
    tts_model: str = "eleven_multilingual_v2"
    stt_model: str = "scribe_v2"
    anthropic_api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls):
        # Read only this backend's file. Shell values take precedence.
        load_dotenv(Path(__file__).with_name(".env"), override=False)
        return cls(
            api_key=os.getenv("ELEVENLABS_API_KEY", "").strip(),
            voice_id=os.getenv("ELEVENLABS_VOICE_ID", "").strip(),
            tts_model=os.getenv("ELEVENLABS_TTS_MODEL") or cls.tts_model,
            stt_model=os.getenv("ELEVENLABS_STT_MODEL") or cls.stt_model,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        )


class LimitedResponseStream(httpx.AsyncByteStream):
    """Keep the SDK's response buffering within the local byte budget."""

    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.limit:
                raise HTTPException(502, "ElevenLabs response exceeded the local size limit.")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class BodyLimitMiddleware:
    """Cap actual received bytes, including requests without Content-Length."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        limit = MAX_AUDIO_BYTES if scope["path"] in {"/transcribe", "/converse"} else (65536 if scope["path"] == "/chat" else 8192)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                response = JSONResponse({"detail": "Request body is too large."}, 413)
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class SpeechInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(min_length=1, max_length=1000)


class HistoryMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class ChatInput(SpeechInput):
    history: list[HistoryMessage] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def complete_exchanges(self):
        if [item.role for item in self.history] != ["user", "assistant"] * (len(self.history) // 2):
            raise ValueError("History must contain complete alternating user/assistant pairs.")
        return self


def create_app(settings: Settings | None = None, transport=None, anthropic_transport=None, diagnostics_path=None) -> FastAPI:
    settings = settings or Settings.from_env()

    async def check_upstream(upstream):
        code = upstream.status_code
        if not 200 <= code < 300:
            await upstream.aclose()
            if code in (401, 403):
                raise HTTPException(502, "ElevenLabs denied access. Check the key and permissions.")
            if code == 429:
                raise HTTPException(429, "ElevenLabs usage or rate limit reached. Check your account.")
            raise HTTPException(502, f"ElevenLabs returned HTTP {code}. Check voice/model and account access.")
        is_speech = upstream.request.url.path.startswith("/v1/text-to-speech/")
        limit = MAX_SPEECH_BYTES if is_speech else MAX_AUDIO_BYTES
        content_type = upstream.headers.get("content-type", "").split(";")[0].lower()
        if is_speech and content_type not in {"audio/pcm", "audio/raw", "application/octet-stream"}:
            await upstream.aclose()
            raise HTTPException(502, "ElevenLabs returned an unexpected audio type.")
        if upstream.is_stream_consumed:
            if len(upstream.content) > limit:
                raise HTTPException(502, "ElevenLabs response exceeded the local size limit.")
        else:
            upstream.stream = LimitedResponseStream(upstream.stream, limit)

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(60, connect=10),
            follow_redirects=False,
            transport=transport,
            event_hooks={"response": [check_upstream]},
        ) as client:
            async with httpx.AsyncClient(
                base_url="https://api.anthropic.com", timeout=httpx.Timeout(60, connect=10),
                follow_redirects=False, transport=anthropic_transport,
            ) as llm_client:
                app.state.provider = client
                app.state.speech = AsyncElevenLabs(api_key=settings.api_key, httpx_client=client)
                app.state.llm_client = llm_client
                app.state.diagnostics = Diagnostics(diagnostics_path or Path(__file__).parent / "output" / "diagnostics.sqlite3")
                yield

    app = FastAPI(title="Whisper Stack local voice backend", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.get("/tester", include_in_schema=False)
    @app.get("/tester/{asset}", include_in_schema=False)
    async def tester(asset: str = "index.html"):
        # Explicit allowlist: never expose backend configuration or output files.
        if asset not in {"index.html", "tester.css", "tester.js"}:
            raise HTTPException(404, "Tester asset not found.")
        return FileResponse(Path(__file__).parent / "tester" / asset, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Permissions-Policy": "microphone=(self), camera=()",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        })

    @asynccontextmanager
    async def provider_errors():
        if not settings.api_key:
            raise HTTPException(503, "Set ELEVENLABS_API_KEY through onboarding and restart.")
        try:
            yield
        except httpx.TimeoutException:
            raise HTTPException(504, "ElevenLabs timed out. No automatic retry was made.") from None
        except httpx.RequestError:
            raise HTTPException(502, "Could not reach ElevenLabs.") from None
        except (ValueError, ApiError):
            raise HTTPException(502, "ElevenLabs returned an unexpected response.") from None

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "api_key_configured": bool(settings.api_key),
            "voice_configured": bool(settings.voice_id),
            "provider_check": "not_performed",
            "tts_model": settings.tts_model,
            "stt_model": settings.stt_model,
            "anthropic_key_configured": bool(settings.anthropic_api_key),
            "anthropic_key_format_valid": key_format_valid(settings.anthropic_api_key),
            "llm_model": MODEL,
            "speech_format": "wav_pcm_s16le_16000_mono",
        }

    @app.get("/voices")
    async def voices():
        async with provider_errors():
            response = await app.state.provider.get(
                "/v2/voices", params={"page_size": 100},
                headers={"xi-api-key": settings.api_key},
            )
            payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(502, "ElevenLabs returned an unexpected response.")
        items = payload.get("voices")
        if not isinstance(items, list) or any(
            not isinstance(v, dict) or not isinstance(v.get("voice_id"), str)
            or not isinstance(v.get("name"), str) for v in items
        ):
            raise HTTPException(502, "ElevenLabs returned an unexpected voice list.")
        return {
            "voices": [{"voice_id": v["voice_id"], "name": v["name"]} for v in items],
            "has_more": payload.get("has_more", False),
        }

    @app.post("/speak", response_class=Response, responses={200: {"content": {"audio/wav": {}}}})
    async def speak(body: SpeechInput):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", settings.voice_id):
            raise HTTPException(503, "Set a valid ELEVENLABS_VOICE_ID in backend/.env and restart.")
        async with provider_errors():
            chunks = bytearray()
            async for chunk in app.state.speech.text_to_speech.convert(
                voice_id=settings.voice_id, text=body.text, model_id=settings.tts_model,
                output_format="pcm_16000", request_options={"max_retries": 0},
            ):
                chunks.extend(chunk)
                if len(chunks) > MAX_SPEECH_BYTES:
                    raise HTTPException(502, "ElevenLabs response exceeded the local size limit.")
            pcm = bytes(chunks)
        if not pcm or len(pcm) % 2:
            raise HTTPException(502, "ElevenLabs returned empty or incomplete PCM audio.")
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        return Response(
            output.getvalue(), media_type="audio/wav",
            headers={"Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="speech.wav"'},
        )

    @app.post("/transcribe")
    async def transcribe(request: Request):
        result = await transcribe_audio(request)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def transcribe_audio(request):
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in AUDIO_TYPES:
            raise HTTPException(415, "Send raw WAV, MP3, M4A, WebM, OGG, or FLAC bytes with an audio Content-Type.")
        audio = await request.body()
        if not audio:
            raise HTTPException(422, "The recording is empty.")
        async with provider_errors():
            result = await app.state.speech.speech_to_text.convert(
                file=(AUDIO_TYPES[content_type], audio, content_type),
                model_id=settings.stt_model, tag_audio_events=False, diarize=False,
                request_options={"max_retries": 0},
            )
        if not isinstance(getattr(result, "text", None), str):
            raise HTTPException(502, "ElevenLabs returned an unexpected transcript.")
        return {"text": result.text, "language_code": getattr(result, "language_code", None)}

    async def reply(text, history, endpoint):
        return await generate_reply(
            app.state.llm_client, settings.anthropic_api_key, text, history,
            app.state.diagnostics, endpoint,
        )

    @app.post("/chat")
    async def chat(body: ChatInput):
        text, event = await reply(body.text, [item.model_dump() for item in body.history], "chat")
        return JSONResponse({"reply": text, "diagnostics": event}, headers={"Cache-Control": "no-store"})

    @app.get("/diagnostics")
    async def diagnostics():
        return JSONResponse(app.state.diagnostics.summary(), headers={"Cache-Control": "no-store"})

    @app.get("/diagnostics/{identifier}")
    async def diagnostic_record(identifier: str):
        event = app.state.diagnostics.get(identifier)
        if event is None:
            raise HTTPException(404, "Diagnostic record not found.")
        return JSONResponse(event, headers={"Cache-Control": "no-store"})

    @app.post("/converse", response_class=Response, responses={200: {"content": {"audio/wav": {}}}})
    async def converse(request: Request):
        # Check all local configuration before spending on any provider stage.
        if not key_format_valid(settings.anthropic_api_key) or not settings.api_key or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", settings.voice_id):
            raise HTTPException(503, "Configure Anthropic, ElevenLabs, and a voice before running the voice loop.")
        started = time.perf_counter()
        transcript = await transcribe_audio(request)
        stt_ms = round((time.perf_counter() - started) * 1000, 3)
        if not transcript["text"].strip():
            raise HTTPException(422, "No speech was recognized; Haiku and TTS were not called.")
        if len(transcript["text"]) > 1000:
            raise HTTPException(422, "Transcript exceeds this increment's 1000-character limit; Haiku and TTS were not called.")
        text, event = await reply(transcript["text"], [], "converse")
        tts_started = time.perf_counter()
        try:
            response = await speak(SpeechInput(text=text))
        except HTTPException as error:
            event.update(pipeline_status="tts_failed", stt_elapsed_ms=stt_ms,
                         total_elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
            app.state.diagnostics.save(event)
            error.headers = {**(error.headers or {}), "X-Diagnostics-ID": event["id"]}
            raise
        event.update(pipeline_status="ok", stt_elapsed_ms=stt_ms,
                     tts_elapsed_ms=round((time.perf_counter() - tts_started) * 1000, 3),
                     total_elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        app.state.diagnostics.save(event)
        response.headers["X-Diagnostics-ID"] = event["id"]
        response.headers["X-Elapsed-Ms"] = str(event["total_elapsed_ms"])
        return response

    return app


app = create_app()
