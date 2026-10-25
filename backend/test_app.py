"""Provider-contract tests. No requests leave the process and no credits are used."""

from io import BytesIO
import json
import wave

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import MAX_AUDIO_BYTES, Settings, create_app


TEST_SETTINGS = Settings(api_key="test-key", voice_id="test_voice")


@pytest.fixture
def client_factory(tmp_path):
    def make(handler, settings=TEST_SETTINGS):
        return TestClient(create_app(settings, httpx.MockTransport(handler), diagnostics_path=tmp_path / "diagnostics.sqlite3"))
    return make


def unexpected_request(request):
    pytest.fail(f"Provider request was not expected: {request.method} {request.url.path}")


def test_health_and_missing_credentials_do_not_call_provider(client_factory):
    with client_factory(unexpected_request, Settings()) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["api_key_configured"] is False
        assert health.json()["provider_check"] == "not_performed"
        assert client.get("/voices").status_code == 503
        assert client.post("/transcribe", content=b"audio", headers={"Content-Type": "audio/wav"}).status_code == 503


def test_voice_listing_filters_provider_metadata(client_factory):
    def provider(request):
        assert request.method == "GET"
        assert request.url.path == "/v2/voices"
        assert request.headers["xi-api-key"] == "test-key"
        assert request.url.params["page_size"] == "100"
        return httpx.Response(200, json={
            "voices": [{"voice_id": "voice_1", "name": "Test Voice", "private_metadata": "unused"}],
            "has_more": True,
        })
    with client_factory(provider) as client:
        assert client.get("/voices").json() == {
            "voices": [{"voice_id": "voice_1", "name": "Test Voice"}], "has_more": True,
        }


def test_speech_request_and_lossless_wav_wrapping(client_factory):
    pcm = b"\x00\x00\xff\x7f\x00\x80" * 1600

    def provider(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/text-to-speech/test_voice"
        assert request.url.params["output_format"] == "pcm_16000"
        assert request.headers["xi-api-key"] == "test-key"
        assert json.loads(request.content) == {"text": "Hello", "model_id": "eleven_multilingual_v2"}
        return httpx.Response(200, content=pcm, headers={"Content-Type": "audio/pcm"})

    with client_factory(provider) as client:
        response = client.post("/speak", json={"text": "  Hello  "})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    with wave.open(BytesIO(response.content)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.readframes(wav.getnframes()) == pcm


@pytest.mark.parametrize("body", [
    {"text": ""}, {"text": "   "}, {"text": "x" * 1001}, {"text": 123},
    {"text": "Hello", "voice_id": "unapproved_override"}, {},
])
def test_invalid_text_never_calls_provider(client_factory, body):
    with client_factory(unexpected_request) as client:
        assert client.post("/speak", json=body).status_code == 422


@pytest.mark.parametrize("voice_id", ["", "../other", "voice?query=bad"])
def test_invalid_voice_configuration_never_calls_provider(client_factory, voice_id):
    with client_factory(unexpected_request, Settings(api_key="test-key", voice_id=voice_id)) as client:
        assert client.post("/speak", json={"text": "Hello"}).status_code == 503


def test_speak_with_voice_but_no_key_never_calls_provider(client_factory):
    with client_factory(unexpected_request, Settings(voice_id="test_voice")) as client:
        assert client.post("/speak", json={"text": "Hello"}).status_code == 503


def test_oversized_provider_audio_is_rejected(client_factory, monkeypatch):
    import backend.app as module

    monkeypatch.setattr(module, "MAX_SPEECH_BYTES", 4)
    def provider(request):
        return httpx.Response(200, content=b"\x00" * 6, headers={"Content-Type": "audio/pcm"})
    with client_factory(provider) as client:
        assert client.post("/speak", json={"text": "Hello"}).status_code == 502


@pytest.mark.parametrize("chunks,expected", [([b"\x00\x00", b"\x00\x00"], 200), ([b"\x00" * 4, b"\x00" * 2], 502)])
def test_sdk_stream_byte_limit_and_cleanup(client_factory, monkeypatch, chunks, expected):
    import backend.app as module

    monkeypatch.setattr(module, "MAX_SPEECH_BYTES", 4)
    class ProviderStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for chunk in chunks:
                yield chunk

        async def aclose(self):
            self.closed = True

    stream = ProviderStream()
    def provider(request):
        return httpx.Response(200, stream=stream, headers={"Content-Type": "audio/pcm"})
    with client_factory(provider) as client:
        assert client.post("/speak", json={"text": "Hello"}).status_code == expected
    assert stream.closed


@pytest.mark.parametrize("status,expected", [(401, 502), (403, 502), (422, 502), (429, 429), (500, 502), (302, 502)])
def test_provider_errors_are_sanitized_and_not_retried(client_factory, status, expected):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive-provider-body", headers={"Location": "https://elsewhere.example"})

    with client_factory(provider) as client:
        response = client.post("/speak", json={"text": "Hello"})
    assert response.status_code == expected
    assert "sensitive-provider-body" not in response.text
    assert "test-key" not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize("error,expected", [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502)])
def test_network_errors_are_sanitized(client_factory, error, expected):
    def provider(request):
        raise error("test-key sensitive diagnostic", request=request)
    with client_factory(provider) as client:
        response = client.post("/speak", json={"text": "Hello"})
    assert response.status_code == expected
    assert "test-key" not in response.text


@pytest.mark.parametrize("data,content_type", [(b"", "audio/pcm"), (b"x", "audio/pcm"), (b"{}", "application/json")])
def test_bad_audio_is_not_returned_as_a_wav(client_factory, data, content_type):
    def provider(request):
        return httpx.Response(200, content=data, headers={"Content-Type": content_type})
    with client_factory(provider) as client:
        assert client.post("/speak", json={"text": "Hello"}).status_code == 502


def test_recording_is_forwarded_as_multipart_not_saved(client_factory):
    audio = b"test-recording-bytes"

    def provider(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/speech-to-text"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        body = request.content
        assert audio in body
        assert b'filename="recording.m4a"' in body
        assert b"scribe_v2" in body
        assert b'name="tag_audio_events"\r\n\r\nfalse' in body
        return httpx.Response(200, json={"text": "Hello world.", "language_code": "en", "words": []})

    with client_factory(provider) as client:
        response = client.post("/transcribe", content=audio, headers={"Content-Type": "audio/mp4"})
    assert response.status_code == 200
    assert response.json() == {"text": "Hello world.", "language_code": "en"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("content,media_type,expected", [
    (b"", "audio/wav", 422), (b"audio", "text/plain", 415),
    (b"audio", "multipart/form-data", 415), (b"x" * (MAX_AUDIO_BYTES + 1), "audio/wav", 413),
])
def test_bad_upload_never_calls_provider(client_factory, content, media_type, expected):
    with client_factory(unexpected_request) as client:
        response = client.post("/transcribe", content=content, headers={"Content-Type": media_type})
    assert response.status_code == expected


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"text": 5}'])
def test_invalid_transcript_is_reported(client_factory, body):
    def provider(request):
        return httpx.Response(200, content=body)
    with client_factory(provider) as client:
        response = client.post("/transcribe", content=b"audio", headers={"Content-Type": "audio/wav"})
    assert response.status_code == 502


def test_body_limit_with_chunked_input_and_no_content_length(client_factory):
    def chunks():
        yield b"a" * 5000
        yield b"b" * 5000
    with client_factory(unexpected_request) as client:
        response = client.post("/speak", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_untrusted_host_rejected_before_provider_call(client_factory):
    with client_factory(unexpected_request) as client:
        assert client.get("/voices", headers={"Host": "untrusted.example"}).status_code == 400


def test_settings_file_is_local_and_shell_takes_precedence(tmp_path, monkeypatch):
    import backend.app as module

    monkeypatch.setattr(module, "__file__", str(tmp_path / "app.py"))
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=file-key\nELEVENLABS_VOICE_ID=local_voice\n")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "shell-key")
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    settings = Settings.from_env()
    assert settings.api_key == "shell-key"
    assert settings.voice_id == "local_voice"
    assert "shell-key" not in repr(settings)
