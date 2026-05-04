"""Flask app: /transcribe, /chat (with router), /converse, /speak, /health, / (web client).

Run from repo root:
    python -m server.app
"""

from __future__ import annotations

import base64
import hmac
import sys
import tempfile
import time
import traceback
from pathlib import Path

from flask import Flask, Response, jsonify, request

from server import config, llm, router, stt, tts
from server.backends.embed_ollama import OllamaEmbedder

app = Flask(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"

# Endpoints that mutate or cost money/credits. /, /health, /static stay public.
PROTECTED_PATHS = frozenset({"/converse", "/chat", "/transcribe", "/speak"})


@app.before_request
def _gate_protected_routes():
    if not config.API_KEY:
        return None  # auth disabled; LAN-only mode
    if request.path not in PROTECTED_PATHS:
        return None
    sent = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(sent, config.API_KEY):
        return jsonify({"error": "missing or invalid X-API-Key header"}), 401
    return None


@app.get("/")
def index():
    return Response(
        (WEB_DIR / "index.html").read_text(encoding="utf-8"),
        mimetype="text/html; charset=utf-8",
    )


@app.get("/health")
def health():
    gpu_available = (Path(sys.prefix) / "Lib" / "site-packages" / "nvidia").exists()
    return jsonify({
        "status": "ok",
        "gpu_available": gpu_available,
        "models_loaded": {
            "whisper": stt.is_loaded(),
            "router": router.is_loaded(),
            "tts": tts.is_loaded(),
        },
    })


@app.post("/transcribe")
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "missing 'audio' file in multipart form"}), 400
    f = request.files["audio"]
    if not f.filename:
        return jsonify({"error": "empty audio filename"}), 400

    suffix = Path(f.filename).suffix or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        f.save(tmp_path)
        result = stt.transcribe(tmp_path)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"transcription failed: {exc!r}"}), 500
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return jsonify(result)


@app.post("/chat")
def chat():
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"error": "missing 'message' in JSON body"}), 400
    message = data["message"]
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "'message' must be a non-empty string"}), 400

    force_route = data.get("force_route")
    if force_route is not None and force_route not in ("simple", "complex"):
        return jsonify({"error": "force_route must be 'simple', 'complex', or omitted"}), 400

    try:
        # Always classify (even when forcing) so we record what the router would have decided.
        decision = router.classify(message)
        route = force_route if force_route is not None else decision["route"]
        force_routed = force_route is not None

        if route == "simple":
            llm_result = llm.chat_ollama(message)
        else:
            llm_result = llm.chat_claude(message)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"chat failed: {exc!r}"}), 500

    return jsonify({
        **llm_result,
        "route": route,
        "force_routed": force_routed,
        "router_latency_s": decision["latency_s"],
        "router_scores": {
            "simple": decision["score_simple"],
            "complex": decision["score_complex"],
            "margin": decision["margin"],
            "margin_threshold": decision["margin_threshold"],
        },
    })


@app.post("/speak")
def speak():
    data = request.get_json(silent=True)
    if not data or "text" not in data:
        return jsonify({"error": "missing 'text' in JSON body"}), 400
    text = data["text"]
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "'text' must be a non-empty string"}), 400

    try:
        result = tts.synth_and_play(text)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"speak failed: {exc!r}"}), 500

    return jsonify(result)


@app.post("/converse")
def converse():
    """Full pipeline: audio -> STT -> router -> LLM -> TTS -> audio out + JSON."""
    if "audio" not in request.files:
        return jsonify({"error": "missing 'audio' file in multipart form"}), 400
    f = request.files["audio"]
    if not f.filename:
        return jsonify({"error": "empty audio filename"}), 400

    force_route = request.form.get("force_route")
    if force_route is not None and force_route not in ("simple", "complex"):
        return jsonify({"error": "force_route must be 'simple', 'complex', or omitted"}), 400

    # Phone/web clients want to play the reply audio themselves and don't want
    # the workstation playing it through its speakers. Default true preserves
    # the ESP-32-style behavior where the workstation IS the speaker.
    play_locally = request.form.get("play_locally", "true").strip().lower() != "false"

    suffix = Path(f.filename).suffix or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = tmp.name
    tmp.close()

    timings: dict[str, float] = {}
    t_total = time.perf_counter()

    try:
        f.save(tmp_path)

        t = time.perf_counter()
        stt_result = stt.transcribe(tmp_path)
        timings["stt"] = time.perf_counter() - t
        transcript = stt_result["transcript"]

        if not transcript.strip():
            return jsonify({"error": "transcript was empty — no speech detected"}), 422

        t = time.perf_counter()
        decision = router.classify(transcript)
        timings["route"] = time.perf_counter() - t
        route = force_route if force_route is not None else decision["route"]
        force_routed = force_route is not None

        t = time.perf_counter()
        if route == "simple":
            llm_result = llm.chat_ollama(
                transcript,
                max_tokens=config.CONVERSE_MAX_TOKENS,
                system_prompt=config.VOICE_SYSTEM_PROMPT,
            )
        else:
            llm_result = llm.chat_claude(
                transcript,
                max_tokens=config.CONVERSE_MAX_TOKENS,
                system_prompt=config.VOICE_SYSTEM_PROMPT,
            )
        timings["llm"] = time.perf_counter() - t
        reply_text = llm_result["reply"]
        speakable_text = tts.to_speakable_text(reply_text)

        t = time.perf_counter()
        if play_locally:
            raw_pcm, sample_rate, _ = tts.synth_play_nonblock(speakable_text)
        else:
            raw_pcm, sample_rate = tts.synth(speakable_text)
        timings["tts"] = time.perf_counter() - t

        wav_bytes = tts.pcm_to_wav(raw_pcm, sample_rate)
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"converse failed: {exc!r}"}), 500
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    timings["total"] = time.perf_counter() - t_total

    return jsonify({
        "transcript": transcript,
        "route": route,
        "force_routed": force_routed,
        "model": llm_result["model"],
        "reply_text": reply_text,
        "audio_base64": audio_b64,
        "audio_sample_rate": sample_rate,
        "audio_duration_s": (len(raw_pcm) // 2) / sample_rate if sample_rate else 0.0,
        "timings": timings,
        "router_scores": {
            "simple": decision["score_simple"],
            "complex": decision["score_complex"],
            "margin": decision["margin"],
            "margin_threshold": decision["margin_threshold"],
        },
        "llm_tokens": {
            "input": llm_result.get("input_tokens"),
            "output": llm_result.get("output_tokens"),
        },
    })


def main():
    if config.WARMUP:
        print("Warming up Whisper (cold start ~10s on Blackwell sm_120)...", flush=True)
        stt.warmup()
        print("Warming up router (embedding example sets)...", flush=True)
        router.attach_embedder(OllamaEmbedder())
        router.warmup()
        print("Warming up Ollama (loads Llama into VRAM)...", flush=True)
        llm.warmup_ollama()
        print("Warming up Piper TTS...", flush=True)
        tts.warmup()
        print("Warmup complete.", flush=True)
    print(f"Listening on http://{config.HOST}:{config.PORT}", flush=True)
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
