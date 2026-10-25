"""Streaming, native search, citations, and billing: synthetic, no provider network."""
import asyncio
import base64
from datetime import datetime, timezone
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from backend.app import Settings
from backend.diagnostics import Diagnostics, request_estimate
from backend.streaming import audio_events
from backend.test_chat import ANSWER, PROMPT, factory, message


def search_message(**changes):
    return message(content=[
        {"type": "text", "text": "PRIVATE search preamble"},
        {"type": "server_tool_use", "id": "tool1", "name": "web_search", "input": {"query": "PRIVATE query"}},
        {"type": "web_search_tool_result", "tool_use_id": "tool1", "content": [
            {"type": "web_search_result", "url": "https://example.com/weather", "encrypted_content": "PRIVATE page"}]},
        {"type": "text", "text": ANSWER, "citations": [
            {"type": "web_search_result_location", "url": "https://example.com/weather", "title": "Forecast"},
            {"url": "javascript:alert(1)"}, {"url": "https://[invalid"},
            {"url": "https://example.com/weather", "title": "Duplicate"},
        ]},
    ], usage={"input_tokens": 5000, "output_tokens": 500, "server_tool_use": {"web_search_requests": 1}}, **changes)


def test_native_search_final_answer_sources_and_separate_costs(factory, tmp_path):
    with factory(lambda request: httpx.Response(200, json=search_message())) as client:
        response = client.post("/chat", json={"text": "Search for today's weather in Paris."})
        assert response.status_code == 200
        result = response.json()
        assert result["reply"] == ANSWER
        assert result["sources"] == [{"url": "https://example.com/weather", "title": "Forecast"}]
        event = result["diagnostics"]
        assert event["estimated_token_cost_usd"] == "0.000750000"
        assert event["estimated_search_cost_usd"] == "0.010000000"
        assert event["estimated_cost_usd"] == "0.010750000"
        assert event["web_search_requests"] == 1
        totals = client.get("/diagnostics").json()["lifetime"]
        assert totals["web_search_requests"] == 1
        assert totals["estimated_search_cost_usd"] == "0.010000000"
        assert totals["estimated_cost_usd"] == "0.010750000"
    stored = (tmp_path / "diagnostics.sqlite3").read_bytes()
    for private in ["PRIVATE", "example.com/weather", ANSWER, "Search for today's"]:
        assert private.encode() not in stored


@pytest.mark.parametrize("counter", [None, -1, True, "1"])
def test_missing_or_invalid_search_usage_is_not_assumed_free(counter):
    usage = {"input_tokens": 10, "output_tokens": 5}
    if counter is not None:
        usage["server_tool_use"] = {"web_search_requests": counter}
    result = request_estimate(usage, searched=True)
    assert result["estimated_token_cost_usd"] is not None
    assert result["estimated_search_cost_usd"] is None
    assert result["estimated_cost_usd"] is None


def test_server_tool_error_records_unbilled_search_and_only_speaks_final_text(factory):
    payload = search_message()
    payload["content"][2]["content"] = {"type": "web_search_tool_result_error", "error_code": "unavailable"}
    payload["usage"]["server_tool_use"]["web_search_requests"] = 0
    with factory(lambda request: httpx.Response(200, json=payload)) as client:
        result = client.post("/chat", json={"text": PROMPT}).json()
        assert result["reply"] == ANSWER
        assert result["diagnostics"]["web_search_status"] == "failed"
        assert result["diagnostics"]["estimated_search_cost_usd"] == "0.000000000"


def test_pause_turn_does_not_speak_partial_answer_or_retry(factory):
    calls = []
    def haiku(request):
        calls.append(request)
        return httpx.Response(200, json=search_message(stop_reason="pause_turn"))
    with factory(haiku) as client:
        assert client.post("/chat", json={"text": PROMPT}).status_code == 502
        assert client.get("/diagnostics").json()["lifetime"]["estimated_cost_usd"] == "0.010750000"
    assert len(calls) == 1


def test_stream_route_orders_pcm_events_and_cost_diagnostics(factory):
    calls = []
    pcm = b"\x01\x00\xff\x7f" * 1000
    def speech(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/speech-to-text":
            return httpx.Response(200, json={"text": PROMPT, "words": []})
        assert request.url.path == "/v1/text-to-speech/test_voice/stream"
        assert request.url.params["output_format"] == "pcm_16000"
        assert json.loads(request.content)["text"] == ANSWER
        return httpx.Response(200, content=pcm, headers={"Content-Type": "audio/pcm"})
    with factory(lambda request: httpx.Response(200, json=search_message()), speech) as client:
        response = client.post("/converse/stream", content=b"recording", headers={"Content-Type": "audio/webm"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.iter_lines()]
        assert events[0]["type"] == "start" and events[-1]["type"] == "done"
        assert events[0]["sources"] == [{"url": "https://example.com/weather", "title": "Forecast"}]
        assert b"".join(base64.b64decode(event["pcm"]) for event in events[1:-1]) == pcm
        record = client.get("/diagnostics/" + response.headers["x-diagnostics-id"]).json()
        assert record["pipeline_status"] == "ok"
        assert record["audio_bytes"] == len(pcm)
        assert record["first_audio_elapsed_ms"] <= record["total_elapsed_ms"]
        assert record["estimated_cost_usd"] == "0.010750000"
    assert calls == ["/v1/speech-to-text", "/v1/text-to-speech/test_voice/stream"]


@pytest.mark.parametrize("mode", ["timeout", "empty", "odd", "oversize"])
def test_stream_failure_is_explicit_and_retains_known_model_usage(factory, monkeypatch, mode):
    if mode == "oversize":
        monkeypatch.setattr("backend.app.MAX_SPEECH_BYTES", 4)
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            if mode == "timeout":
                yield b"\0\0" * 1024
                raise httpx.ReadTimeout("PRIVATE provider detail")
            yield {"empty": b"", "odd": b"123", "oversize": b"123456"}.get(mode, b"")
    def speech(request):
        if request.url.path == "/v1/speech-to-text":
            return httpx.Response(200, json={"text": PROMPT, "words": []})
        return httpx.Response(200, stream=Broken(), headers={"Content-Type": "audio/pcm"})
    with factory(lambda request: httpx.Response(200, json=message()), speech) as client:
        response = client.post("/converse/stream", content=b"audio", headers={"Content-Type": "audio/wav"})
        events = [json.loads(line) for line in response.iter_lines()]
        assert events[-1]["type"] == "error"
        assert "PRIVATE" not in response.text
        record = client.get("/diagnostics/" + response.headers["x-diagnostics-id"]).json()
        assert record["pipeline_status"] == "tts_failed"
        assert record["estimated_cost_usd"] == "0.000750000"


def test_stream_preflights_and_body_bounds_before_spend(factory, monkeypatch):
    with factory(settings=Settings()) as client:
        assert client.post("/converse/stream", content=b"audio", headers={"Content-Type": "audio/wav"}).status_code == 503
    monkeypatch.setattr("backend.app.MAX_AUDIO_BYTES", 4)
    with factory() as client:
        assert client.post("/converse/stream", content=b"12345", headers={"Content-Type": "audio/wav"}).status_code == 413


def test_pcm_is_yielded_before_provider_finishes_and_disconnect_closes_upstream(tmp_path):
    async def scenario():
        closed = []
        async def chunks(**kwargs):
            assert kwargs["request_options"] == {"max_retries": 0, "chunk_size": 1024}
            try:
                yield b"1234"
                pytest.fail("Provider tail must not be requested before the first audio event is consumed")
            finally:
                closed.append(True)
        speech = SimpleNamespace(text_to_speech=SimpleNamespace(stream=chunks))
        event = {"id": "disconnect-test", "created_at": datetime.now(timezone.utc).isoformat(),
                 "status": "ok", "tokens": {}, "estimated_cost_usd": "0.01"}
        ledger = Diagnostics(tmp_path / "ledger.sqlite3")
        stream = audio_events(speech, Settings(), "private reply", event, [], ledger, time.perf_counter(), 2, 100)
        assert json.loads(await anext(stream))["type"] == "start"
        assert json.loads(await anext(stream))["type"] == "audio"
        await stream.aclose()
        assert closed == [True]
        assert ledger.get(event["id"])["pipeline_status"] == "disconnected"
        assert ledger.get(event["id"])["estimated_cost_usd"] == "0.01"
    asyncio.run(scenario())
