"""Streaming, native search, citations, and billing: synthetic, no provider network."""
import json

import httpx
import pytest

from backend.app import Settings
from backend.diagnostics import request_estimate
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


def test_stream_preflights_and_body_bounds_before_spend(factory, monkeypatch):
    with factory(settings=Settings()) as client:
        assert client.post("/converse/stream", content=b"audio", headers={"Content-Type": "audio/wav"}).status_code == 503
    monkeypatch.setattr("backend.app.MAX_AUDIO_BYTES", 4)
    with factory() as client:
        assert client.post("/converse/stream", content=b"12345", headers={"Content-Type": "audio/wav"}).status_code == 413
