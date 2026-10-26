"""Messages SSE + speech WebSocket checks. No external network or audio playback."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import create_app
from backend.diagnostics import Diagnostics
from backend.llm import new_event
from backend.model_stream import VoiceText
from backend.overlap import ConnectOnce, overlapped_audio_events
from backend.test_chat import ANSWER, PROMPT, SETTINGS, message
from backend.test_streaming import search_message


def sse_payload(payload, *, wrap=True):
    """Model-shaped events, deliberately split across arbitrary text boundaries."""
    def emit(kind, **values):
        return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **values}) + "\n\n").encode()
    usage = payload["usage"].copy()
    usage["output_tokens"] = 0
    yield emit("message_start", message={"model": payload["model"], "content": [], "usage": usage})
    final_start = max((i + 1 for i, block in enumerate(payload["content"])
                       if block["type"] in {"server_tool_use", "web_search_tool_result"}), default=0)
    for i, block in enumerate(payload["content"]):
        initial = block.copy()
        if block["type"] == "text":
            initial["text"] = ""
            initial["citations"] = []
        yield emit("content_block_start", index=i, content_block=initial)
        if block["type"] == "text":
            text = block["text"]
            if wrap and i >= final_start:
                text = "<voice>" + text + "</voice>"
            for offset in range(0, len(text), 7):
                yield emit("content_block_delta", index=i, delta={"type": "text_delta", "text": text[offset:offset + 7]})
            for citation in block.get("citations", []):
                yield emit("content_block_delta", index=i, delta={"type": "citations_delta", "citation": citation})
        elif block["type"] == "thinking":
            yield emit("content_block_delta", index=i, delta={"type": "thinking_delta", "thinking": "PRIVATE reasoning delta"})
        yield emit("content_block_stop", index=i)
    yield emit("message_delta", delta={"stop_reason": payload["stop_reason"]}, usage=payload["usage"])
    yield emit("message_stop")


class FakeSocket:
    def __init__(self, mode="ok", final_delay=0, audio_bytes=3200):
        self.mode = mode
        self.final_delay = final_delay
        self.audio_bytes = audio_bytes
        self.received = []
        self.queue = asyncio.Queue()
        self.closed = False

    async def send(self, raw):
        value = json.loads(raw)
        self.received.append(value)
        if value["text"] == " " and len(self.received) == 1:
            assert value["generation_config"] == {"chunk_length_schedule": [50]}
        elif value["text"]:
            assert value["try_trigger_generation"] is True
            assert "<voice>" not in value["text"] and "</voice>" not in value["text"]
            if self.mode not in {"empty", "no_final"}:
                pcm = b"x" if self.mode == "odd" else b"\x01\x00" * (self.audio_bytes // 2)
                await self.queue.put(json.dumps({"audio": base64.b64encode(pcm).decode()}))
        elif self.mode != "no_final":
            await self.queue.put(json.dumps({"isFinal": True}))
        else:
            await self.queue.put("closed")

    async def recv(self):
        if self.mode == "timeout":
            raise TimeoutError("PRIVATE socket timeout")
        if self.mode == "error":
            return json.dumps({"error": "PRIVATE socket error"})
        if self.mode == "malformed":
            return "not JSON"
        raw = await self.queue.get()
        if raw == "closed":
            raise OSError("PRIVATE closed before final")
        if '"isFinal"' in raw and self.final_delay:
            await asyncio.sleep(self.final_delay)
        return raw


class FakeConnect:
    def __init__(self, mode="ok", final_delay=0, audio_bytes=3200):
        self.mode = mode
        self.final_delay = final_delay
        self.audio_bytes = audio_bytes
        self.socket = FakeSocket(mode, final_delay, audio_bytes)
        self.calls = 0

    @asynccontextmanager
    async def __call__(self, uri, **options):
        self.calls += 1
        self.socket = FakeSocket(self.mode, self.final_delay, self.audio_bytes)
        assert uri.startswith("wss://api.elevenlabs.io/v1/text-to-speech/test_voice/stream-input?")
        assert "output_format=pcm_16000" in uri and "api_key" not in uri
        assert options["additional_headers"] == {"xi-api-key": "speech-test-key"}
        assert options["max_size"] == 512 * 1024
        try:
            yield self.socket
        finally:
            self.socket.closed = True


def build_client(tmp_path, payload=None, connector=None, model_stream=None):
    payload = payload or message()
    def speech(request):
        assert request.url.path == "/v1/speech-to-text"
        return httpx.Response(200, json={"text": PROMPT, "words": []})
    def haiku(request):
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["tools"][0]["max_uses"] == 1
        assert "<voice>" in body["system"]
        return httpx.Response(200, content=b"".join(sse_payload(payload)) if model_stream is None else None,
                              stream=model_stream, headers={"Content-Type": "text/event-stream"})
    connector = connector or FakeConnect()
    return TestClient(create_app(SETTINGS, httpx.MockTransport(speech), httpx.MockTransport(haiku),
                                  tmp_path / "diagnostics.sqlite3", tts_connect=connector)), connector


def exchange(client):
    response = client.post("/converse/stream", content=b"synthetic", headers={"Content-Type": "audio/wav"})
    assert response.status_code == 200
    frames = [json.loads(line) for line in response.iter_lines()]
    return response, frames, client.get("/diagnostics/" + response.headers["x-diagnostics-id"]).json()


def test_overlapped_route_search_sources_billing_and_privacy(tmp_path):
    client, connector = build_client(tmp_path, search_message())
    with client:
        response, frames, record = exchange(client)
    assert frames[0]["type"] == "start" and frames[-1]["type"] == "done"
    assert frames[0]["sources"] == []
    assert frames[-1]["sources"] == [{"url": "https://example.com/weather", "title": "Forecast"}]
    assert record["pipeline_status"] == "ok"
    assert record["usage_complete"] is True
    assert record["estimated_cost_usd"] == "0.010750000"
    assert record["tokens"]["output_tokens"] == 500  # Cumulative usage, not added to message_start.
    assert record["tts_first_text_elapsed_ms"] <= record["total_elapsed_ms"]
    spoken = "".join(item["text"] for item in connector.socket.received[1:] if item["text"])
    assert spoken == ANSWER
    assert connector.calls == 1 and connector.socket.closed
    stored = (tmp_path / "diagnostics.sqlite3").read_bytes()
    for private in [PROMPT, ANSWER, "PRIVATE", "example.com/weather", "speech-test-key", "haiku-test-key"]:
        assert private.encode() not in stored


@pytest.mark.parametrize("mode", ["timeout", "empty", "odd", "oversize", "error", "malformed", "no_final"])
def test_ws_failures_are_explicit_sanitized_and_closed(tmp_path, monkeypatch, mode):
    if mode == "oversize":
        monkeypatch.setattr("backend.app.MAX_SPEECH_BYTES", 4)
        mode = "ok"
    client, connector = build_client(tmp_path, connector=FakeConnect(mode))
    with client:
        response, frames, record = exchange(client)
    assert frames[-1]["type"] == "error"
    assert "PRIVATE" not in response.text
    assert record["pipeline_status"] == "stream_failed"
    assert connector.calls == 1 and connector.socket.closed
    if record["usage_complete"]:
        assert record["estimated_cost_usd"] == "0.000750000"
    else:
        assert record["estimated_cost_usd"] is None


@pytest.mark.parametrize("audio_bytes", [67774, 300000])
def test_large_provider_audio_is_split_for_browser_without_losing_samples(tmp_path, audio_bytes):
    client, connector = build_client(tmp_path, connector=FakeConnect(audio_bytes=audio_bytes))
    with client:
        response, frames, record = exchange(client)
    assert frames[-1]["type"] == "done" and record["pipeline_status"] == "ok"
    assert all(len(line) <= 65536 for line in response.iter_lines())
    pcm = [base64.b64decode(frame["pcm"]) for frame in frames if frame["type"] == "audio"]
    assert all(len(chunk) % 2 == 0 for chunk in pcm)
    sends = sum(bool(item["text"]) for item in connector.socket.received[1:])
    assert b"".join(pcm) == b"\x01\x00" * (audio_bytes // 2) * sends
    assert sum(map(len, pcm)) == record["audio_bytes"]


def test_missing_message_stop_is_not_complete_or_free(tmp_path):
    class Truncated(httpx.AsyncByteStream):
        async def __aiter__(self):
            for frame in list(sse_payload(message()))[:-1]:
                yield frame
    client, connector = build_client(tmp_path, model_stream=Truncated())
    with client:
        _, frames, record = exchange(client)
    assert frames[-1]["type"] == "error" and record["usage_complete"] is False
    assert record["estimated_cost_usd"] is None
    assert record["pipeline_status"] == "llm_failed"
    assert connector.socket.closed


def test_pause_turn_retains_complete_usage_without_continuation(tmp_path):
    client, connector = build_client(tmp_path, search_message(stop_reason="pause_turn"))
    with client:
        _, frames, record = exchange(client)
    assert frames[-1]["type"] == "error"
    assert record["estimated_cost_usd"] == "0.010750000"
    assert record["stop_reason"] == "pause_turn" and connector.calls == 1


def test_text_and_audio_are_sent_before_model_tail_and_disconnect_cancels(tmp_path):
    async def scenario():
        release = asyncio.Event()
        closed = []
        payload = message(content=[{"type": "text", "text": "A sufficiently long first phrase before the delayed final words."}])
        # Gate after a small number of text deltas, independent of the fixture split text.
        class Gated(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    for index, frame in enumerate(sse_payload(payload)):
                        if index == 7:
                            await release.wait()
                        yield frame
                finally:
                    closed.append(True)
        async with httpx.AsyncClient(base_url="https://api.anthropic.com", transport=httpx.MockTransport(lambda request: httpx.Response(
                200, stream=Gated(), headers={"Content-Type": "text/event-stream"}))) as client:
            ledger = Diagnostics(tmp_path / "ledger.sqlite3")
            event = new_event("converse_stream")
            connector = FakeConnect()
            stream = overlapped_audio_events(client, SETTINGS, PROMPT, event, ledger,
                                              time.perf_counter(), 1, 8 * 1024 * 1024, connector)
            assert json.loads(await anext(stream))["type"] == "start"
            assert json.loads(await asyncio.wait_for(anext(stream), 2))["type"] == "audio"
            assert event["text_sent_before_llm_complete"] is True
            assert event["audio_before_llm_complete"] is True
            await stream.aclose()
            assert connector.socket.closed and closed
            assert ledger.get(event["id"])["pipeline_status"] == "disconnected"
            assert ledger.get(event["id"])["estimated_cost_usd"] is None
    asyncio.run(scenario())


def test_voice_gate_handles_split_tags_and_words():
    gate = VoiceText()
    output = "".join(gate.add(piece) for piece in ["<vo", "ice>Hello wo", "rld.</vo", "ice>"])
    assert output == "Hello world." and gate.closed
    with pytest.raises(ValueError):
        gate.add("unexpected tail")


def test_websocket_redirect_is_rejected_without_retry():
    connector = ConnectOnce("wss://api.elevenlabs.io/test")
    error = RuntimeError("redirect")
    assert connector.process_redirect(error) is error


@pytest.mark.parametrize("fault", ["oversized", "malformed", "http_denied", "late_tool", "missing_marker"])
def test_model_protocol_failures_close_speech_without_leaking_content(tmp_path, monkeypatch, fault):
    class BadStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if fault == "oversized":
                yield b"x" * 256001
            elif fault == "malformed":
                yield b"data: not JSON\n\n"
            elif fault == "missing_marker":
                for frame in sse_payload(message(), wrap=False):
                    yield frame
            else:
                payload = message(content=[
                    {"type": "text", "text": "An answer before a tool."},
                    {"type": "server_tool_use", "name": "web_search", "id": "tool1", "input": {}},
                ])
                frames = list(sse_payload(payload))
                # Force an invalid early voice answer before the later tool.
                for frame in frames:
                    if b'"type": "text_delta"' in frame:
                        frame = frame.replace(b'"text": "An answ"', b'"text": "<voice>An answer "')
                    yield frame
    if fault == "oversized":
        monkeypatch.setattr("backend.model_stream.MAX_RESPONSE_BYTES", 100)
    if fault == "http_denied":
        connector = FakeConnect()
        def speech(request):
            return httpx.Response(200, json={"text": PROMPT, "words": []})
        client = TestClient(create_app(SETTINGS, httpx.MockTransport(speech),
            httpx.MockTransport(lambda request: httpx.Response(401, text="PRIVATE error")),
            tmp_path / "diagnostics.sqlite3", tts_connect=connector))
    else:
        client, connector = build_client(tmp_path, model_stream=BadStream())
    with client:
        response, frames, record = exchange(client)
    assert frames[-1]["type"] == "error"
    assert "PRIVATE" not in response.text
    assert connector.socket.closed and connector.calls == 1
    assert record["pipeline_status"] == "llm_failed"
    if not record["usage_complete"]:
        assert record["estimated_cost_usd"] is None
