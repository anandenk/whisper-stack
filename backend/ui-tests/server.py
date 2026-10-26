"""Browser-test backend: synthetic audio, fake credentials, no provider network."""
import json
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app import Settings, create_app
from backend.llm import MODEL
from backend.test_overlap import FakeConnect, sse_payload


def speech(request):
    if request.url.path == "/v1/speech-to-text":
        assert b"Content-Type: audio/" in request.content
        return httpx.Response(200, json={"text": "A simulated question", "words": [], "language_code": "eng"})
    assert request.url.path in {"/v1/text-to-speech/test_voice", "/v1/text-to-speech/test_voice/stream"}
    assert json.loads(request.content)["text"] == "A simulated reply."
    return httpx.Response(200, stream=DelayedAudio(), headers={"Content-Type": "audio/pcm"})


class DelayedAudio(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"\x00\x00" * 8000
        await asyncio.sleep(0.7)
        yield b"\x00\x00" * 8000


def haiku(request):
    assert request.url.path == "/v1/messages"
    assert json.loads(request.content)["messages"][-1]["content"] == "A simulated question"
    payload = {
        "model": MODEL, "content": [{"type": "text", "text": "A simulated reply."}],
        "stop_reason": "end_turn", "usage": {"input_tokens": 100, "output_tokens": 20,
                                              "server_tool_use": {"web_search_requests": 0}},
    }
    if json.loads(request.content).get("stream"):
        return httpx.Response(200, content=b"".join(sse_payload(payload)), headers={"Content-Type": "text/event-stream"})
    return httpx.Response(200, json=payload)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="whisper-stack-ui-") as folder:
        app = create_app(
            Settings(api_key="speech-test-key", voice_id="test_voice", anthropic_api_key="sk-ant-haiku-test-key"),
            transport=httpx.MockTransport(speech), anthropic_transport=httpx.MockTransport(haiku),
            diagnostics_path=Path(folder) / "diagnostics.sqlite3",
            tts_connect=FakeConnect(final_delay=0.7, audio_bytes=67774),
        )
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
