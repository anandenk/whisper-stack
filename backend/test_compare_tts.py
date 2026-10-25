"""The comparison uses the same contract, bounds, and zero retries for either model."""
import asyncio
import json
import wave

import httpx
import pytest

from backend.app import Settings
from backend.compare_tts import MODELS, TEXT, sample


@pytest.mark.parametrize("model", MODELS)
def test_comparison_contract_and_valid_wav(tmp_path, model):
    calls = []
    def provider(request):
        calls.append(request)
        assert request.url.path == "/v1/text-to-speech/test_voice/stream"
        assert request.url.params["output_format"] == "pcm_16000"
        assert json.loads(request.content) == {"model_id": model, "text": TEXT}
        return httpx.Response(200, content=b"\x01\x00" * 1600, headers={"Content-Type": "audio/pcm"})
    path = tmp_path / "sample.wav"
    result = asyncio.run(sample(Settings(api_key="test-key", voice_id="test_voice"), model, path,
                                httpx.MockTransport(provider)))
    assert result["status"] == "ok"
    assert result["first_pcm_chunk_ms"] <= result["total_ms"]
    with wave.open(str(path)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert wav.getnframes() == 1600
    assert len(calls) == 1


def test_comparison_does_not_retry_or_save_provider_errors(tmp_path):
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(401, text="PRIVATE provider detail")
    path = tmp_path / "sample.wav"
    result = asyncio.run(sample(Settings(api_key="test-key", voice_id="test_voice"), MODELS[0], path,
                                httpx.MockTransport(provider)))
    assert result["status"] == "failed"
    assert result["http_status"] == 401
    assert "PRIVATE" not in json.dumps(result)
    assert not path.exists()
    assert len(calls) == 1
