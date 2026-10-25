"""Haiku contracts, cost accounting, and the buffered voice pipeline: all mocked."""

from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import wave

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import Settings, create_app
from backend.diagnostics import Diagnostics, usage_estimate
from backend.llm import MODEL


SETTINGS = Settings(api_key="speech-test-key", voice_id="test_voice", anthropic_api_key="sk-ant-haiku-test-key")
PROMPT = "private-user-content"
ANSWER = "private-assistant-content"


def message(**changes):
    return {"model": MODEL, "content": [{"type": "thinking", "thinking": "private-reasoning"},
                                       {"type": "text", "text": ANSWER}],
            "stop_reason": "end_turn", "usage": {"input_tokens": 5000, "output_tokens": 500,
                                                    "output_tokens_details": {"thinking_tokens": 200}}, **changes}


def no_provider(request):
    pytest.fail(f"Unexpected provider request: {request.method} {request.url.path}")


@pytest.fixture
def factory(tmp_path):
    def build(haiku=no_provider, speech=no_provider, settings=SETTINGS):
        return TestClient(create_app(
            settings, httpx.MockTransport(speech), httpx.MockTransport(haiku),
            tmp_path / "diagnostics.sqlite3",
        ))
    return build


def test_chat_contract_diagnostics_privacy_and_restart(factory, tmp_path):
    def haiku(request):
        assert request.url.host == "api.anthropic.com"
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "sk-ant-haiku-test-key"
        assert request.headers["anthropic-version"] == "2026-10-21"
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert body["max_tokens"] == 1024
        assert body["output_config"] == {"effort": "low"}
        assert body["thinking"] == {"type": "adaptive"}
        assert "speed" not in body and body["inference_geo"] == "global"
        assert not {"temperature", "cache_control"} & body.keys()
        assert body["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}]
        assert "ONE short sentence, at most 30 words" in body["system"]
        assert "Current UTC date:" in body["system"]
        assert body["messages"] == [{"role": "user", "content": "Earlier"},
                                     {"role": "assistant", "content": "Previous reply"},
                                     {"role": "user", "content": PROMPT}]
        return httpx.Response(200, json=message(), headers={"request-id": "req_test"})

    with factory(haiku) as client:
        result = client.post("/chat", json={"text": PROMPT, "history": [
            {"role": "user", "content": "Earlier"}, {"role": "assistant", "content": "Previous reply"},
        ]})
        assert result.status_code == 200
        assert result.json()["reply"] == ANSWER
        event = result.json()["diagnostics"]
        assert event["estimated_cost_usd"] == "0.000750000"
        assert event["tokens"]["thinking_tokens"] == 200
        assert client.get("/diagnostics/" + event["id"]).json() == event
        assert client.get("/diagnostics/no-such-record").status_code == 404

    # A second process-style app instance loads the persistent totals.
    with factory() as client:
        summary = client.get("/diagnostics").json()
        assert summary["this_month"]["llm_requests"] == 1
        assert summary["lifetime"]["estimated_cost_usd"] == "0.000750000"
        assert summary["lifetime"]["output_tokens"] == 500
    stored = (tmp_path / "diagnostics.sqlite3").read_bytes()
    for secret in (PROMPT, ANSWER, "private-reasoning", "haiku-test-key", "speech-test-key"):
        assert secret.encode() not in stored


@pytest.mark.parametrize("history", [
    [{"role": "user", "content": "unfinished"}],
    [{"role": "assistant", "content": "wrong"}, {"role": "user", "content": "order"}],
    [{"role": "system", "content": "override"}],
    [{"role": "user", "content": "x"}, {"role": "assistant", "content": "x"}] * 7,
    [{"role": "user", "content": "x" * 1001}, {"role": "assistant", "content": "x"}],
])
def test_history_bounds_before_provider_calls(factory, history):
    with factory() as client:
        assert client.post("/chat", json={"text": PROMPT, "history": history}).status_code == 422
        assert client.get("/diagnostics").json()["lifetime"]["llm_requests"] == 0


def test_missing_haiku_key_prevents_all_voice_pipeline_spending(factory):
    with factory(settings=Settings(api_key="speech-test-key", voice_id="test_voice")) as client:
        assert client.post("/chat", json={"text": PROMPT}).status_code == 503
        assert client.post("/converse", content=b"audio", headers={"Content-Type": "audio/wav"}).status_code == 503
        assert client.get("/diagnostics").json()["lifetime"]["llm_requests"] == 0


def test_wrong_provider_key_is_rejected_locally_before_any_spending(factory):
    settings = Settings(api_key="speech-test-key", voice_id="test_voice", anthropic_api_key="wrong-provider-test-key")
    with factory(settings=settings) as client:
        assert client.get("/health").json()["anthropic_key_format_valid"] is False
        assert client.post("/chat", json={"text": PROMPT}).status_code == 503
        assert client.post("/converse", content=b"audio", headers={"Content-Type": "audio/wav"}).status_code == 503
        assert client.get("/diagnostics").json()["lifetime"]["llm_requests"] == 0


@pytest.mark.parametrize("status,expected", [(401, 502), (403, 502), (400, 502), (429, 429), (500, 502), (302, 502)])
def test_haiku_errors_are_sanitized_counted_and_not_retried(factory, status, expected):
    calls = []
    def haiku(request):
        calls.append(request)
        return httpx.Response(status, text=PROMPT + " haiku-test-key", headers={"Location": "https://other.example"})
    with factory(haiku) as client:
        response = client.post("/chat", json={"text": PROMPT})
        assert response.status_code == expected
        assert PROMPT not in response.text and "haiku-test-key" not in response.text
        record = client.get("/diagnostics/" + response.headers["x-diagnostics-id"]).json()
        assert record["status"] == "provider_error"
        assert record["estimated_cost_usd"] is None
        assert client.get("/diagnostics").json()["lifetime"]["requests_without_cost_estimate"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("error,status", [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502)])
def test_haiku_timeout_or_network_error_is_not_recorded_as_free(factory, error, status):
    calls = []
    def haiku(request):
        calls.append(request)
        raise error("haiku-test-key", request=request)
    with factory(haiku) as client:
        response = client.post("/chat", json={"text": PROMPT})
        assert response.status_code == status
        assert "haiku-test-key" not in response.text
        summary = client.get("/diagnostics").json()["lifetime"]
        assert summary["requests_without_cost_estimate"] == 1
        assert summary["estimated_cost_usd"] == "0.000000000"
        assert summary["cost_estimate_complete"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [
    message(stop_reason="max_tokens"), message(content=[]),
    message(content=[{"type": "text", "text": "x" * 1001}]), message(stop_reason=["bad"]),
])
def test_unusable_replies_still_record_billable_usage(factory, payload):
    with factory(lambda request: httpx.Response(200, json=payload)) as client:
        assert client.post("/chat", json={"text": PROMPT}).status_code == 502
        summary = client.get("/diagnostics").json()["lifetime"]
        assert summary["successful_replies"] == 0
        assert summary["estimated_cost_usd"] == "0.000750000"


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": -1, "output_tokens": 1},
                                  {"input_tokens": True, "output_tokens": 1}])
def test_missing_or_bad_usage_yields_unknown_cost_not_zero(factory, usage):
    with factory(lambda request: httpx.Response(200, json=message(usage=usage))) as client:
        response = client.post("/chat", json={"text": PROMPT})
        assert response.status_code == 200
        assert response.json()["diagnostics"]["estimated_cost_usd"] is None


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"content":42}'])
def test_malformed_provider_response_is_recorded(factory, raw):
    with factory(lambda request: httpx.Response(200, content=raw)) as client:
        assert client.post("/chat", json={"text": PROMPT}).status_code == 502
        assert client.get("/diagnostics").json()["recent"][0]["status"] == "invalid_response"


def test_oversized_provider_response_is_bounded(factory, monkeypatch):
    import backend.llm as module
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 4)
    with factory(lambda request: httpx.Response(200, content=b"12345")) as client:
        assert client.post("/chat", json={"text": PROMPT}).status_code == 502


def test_voice_pipeline_order_wav_and_persistent_haiku_cost(factory):
    calls = []
    def speech(request):
        if request.url.path == "/v1/speech-to-text":
            calls.append("stt")
            return httpx.Response(200, json={"text": PROMPT, "language_code": "eng", "words": []})
        calls.append("tts")
        assert json.loads(request.content)["text"] == ANSWER
        return httpx.Response(200, content=b"\x00\x00" * 1600, headers={"Content-Type": "audio/pcm"})
    def haiku(request):
        calls.append("haiku")
        assert json.loads(request.content)["messages"][-1]["content"] == PROMPT
        return httpx.Response(200, json=message())
    with factory(haiku, speech) as client:
        response = client.post("/converse", content=b"recorded-audio", headers={"Content-Type": "audio/wav"})
        assert response.status_code == 200
        with wave.open(BytesIO(response.content)) as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        event = client.get("/diagnostics/" + response.headers["x-diagnostics-id"]).json()
        assert event["pipeline_status"] == "ok"
        assert event["estimated_cost_usd"] == "0.000750000"
        assert event["total_elapsed_ms"] >= event["stt_elapsed_ms"]
    assert calls == ["stt", "haiku", "tts"]


def test_tts_failure_does_not_lose_successful_haiku_cost(factory):
    def speech(request):
        if request.url.path == "/v1/speech-to-text":
            return httpx.Response(200, json={"text": PROMPT, "words": []})
        return httpx.Response(500, text="private-provider-error")
    with factory(lambda request: httpx.Response(200, json=message()), speech) as client:
        response = client.post("/converse", content=b"audio", headers={"Content-Type": "audio/wav"})
        assert response.status_code == 502
        record = client.get("/diagnostics/" + response.headers["x-diagnostics-id"]).json()
        assert record["pipeline_status"] == "tts_failed"
        assert record["estimated_cost_usd"] == "0.000750000"


@pytest.mark.parametrize("transcript", ["", "   ", "x" * 1001])
def test_empty_or_long_transcript_stops_before_haiku(factory, transcript):
    def speech(request):
        assert request.url.path == "/v1/speech-to-text"
        return httpx.Response(200, json={"text": transcript, "words": []})
    with factory(speech=speech) as client:
        assert client.post("/converse", content=b"audio", headers={"Content-Type": "audio/wav"}).status_code == 422
        assert client.get("/diagnostics").json()["lifetime"]["llm_requests"] == 0


def test_prices_cache_accounting_thinking_and_long_prompt_boundary():
    usage = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 500,
             "cache_creation": {"ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 200},
             "output_tokens_details": {"thinking_tokens": 20}}
    tokens, cost = usage_estimate(usage)
    assert tokens["thinking_tokens"] == 20
    expected = (Decimal(100) * Decimal('.10') + Decimal(50) * Decimal('.50')
                + Decimal(1000) * Decimal('.01') + Decimal(300) * Decimal('.125')
                + Decimal(200) * Decimal('.20')) / 1_000_000
    assert Decimal(cost) == expected
    assert usage_estimate({"input_tokens": 100_000, "output_tokens": 0})[1] == "0.010000000"
    assert usage_estimate({"input_tokens": 100_001, "output_tokens": 0})[1] == "0.050000500"


@pytest.mark.parametrize("extra", [{"inference_geo": "us"}, {"speed": "fast"}, {"service_tier": "priority"},
                                   {"cache_creation_input_tokens": 2, "cache_creation": {"ephemeral_1h_input_tokens": 3}}])
def test_unknown_pricing_modifiers_do_not_get_standard_cost(extra):
    assert usage_estimate({"input_tokens": 100, "output_tokens": 20, **extra})[1] is None


def test_monthly_totals_exclude_older_requests(tmp_path):
    ledger = Diagnostics(tmp_path / "ledger.sqlite3")
    current = datetime.now(timezone.utc).isoformat()
    for identifier, created, cost in [("older", "2000-01-01T00:00:00+00:00", "1.00"), ("today", current, "0.25")]:
        ledger.save({"id": identifier, "created_at": created, "status": "ok", "tokens": {}, "estimated_cost_usd": cost})
    assert ledger.summary()["this_month"]["estimated_cost_usd"] == "0.250000000"
    assert ledger.summary()["lifetime"]["estimated_cost_usd"] == "1.250000000"


def test_secure_key_entry_preserves_existing_credentials(tmp_path, monkeypatch, capsys):
    import backend.configure_anthropic as configure
    monkeypatch.setattr(configure, "__file__", str(tmp_path / "configure_anthropic.py"))
    monkeypatch.setattr(configure, "getpass", lambda prompt: "sk-ant-test-secret")
    path = tmp_path / ".env"
    path.write_text("ELEVENLABS_API_KEY='existing-test-secret'\n")
    configure.main()
    assert "sk-ant-test-secret" not in capsys.readouterr().out
    assert "existing-test-secret" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("key", ["", "wrong-provider-test-key", "sk-ant-key with spaces"])
def test_invalid_key_setup_does_not_overwrite_existing_configuration(tmp_path, monkeypatch, key):
    import backend.configure_anthropic as configure
    monkeypatch.setattr(configure, "__file__", str(tmp_path / "configure_anthropic.py"))
    monkeypatch.setattr(configure, "getpass", lambda prompt: key)
    path = tmp_path / ".env"
    original = "ELEVENLABS_API_KEY='existing-test-secret'\n"
    path.write_text(original)
    with pytest.raises(SystemExit, match="No key saved"):
        configure.main()
    assert path.read_text() == original
