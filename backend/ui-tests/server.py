"""Browser-test backend: synthetic audio, fake credentials, no provider network."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app import Settings, create_app
from backend.llm import MODEL


def speech(request):
    if request.url.path == "/v1/speech-to-text":
        assert b"Content-Type: audio/" in request.content
        return httpx.Response(200, json={"text": "A simulated question", "words": [], "language_code": "eng"})
    assert request.url.path == "/v1/text-to-speech/test_voice"
    assert json.loads(request.content)["text"] == "A simulated reply."
    return httpx.Response(200, content=b"\x00\x00" * 1600, headers={"Content-Type": "audio/pcm"})


def haiku(request):
    assert request.url.path == "/v1/messages"
    assert json.loads(request.content)["messages"][-1]["content"] == "A simulated question"
    return httpx.Response(200, json={
        "model": MODEL, "content": [{"type": "text", "text": "A simulated reply."}],
        "stop_reason": "end_turn", "usage": {"input_tokens": 100, "output_tokens": 20},
    })


if __name__ == "__main__":
    with TemporaryDirectory(prefix="whisper-stack-ui-") as folder:
        app = create_app(
            Settings(api_key="fake-speech-key", voice_id="test_voice", anthropic_api_key="sk-ant-fake-key"),
            transport=httpx.MockTransport(speech), anthropic_transport=httpx.MockTransport(haiku),
            diagnostics_path=Path(folder) / "diagnostics.sqlite3",
        )
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
