"""Flask app: /transcribe, /chat (with router), /converse, /speak, /health, / (web client).

Run from repo root:
    python -m server.app
"""

from __future__ import annotations

import base64
import functools
import hmac
import re
import sys
import tempfile
import time
import traceback
from datetime import timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, request

from server import audio, audio_out, config, markdown, modes, router
from server.sessions import SessionStore

# HTTP-facing stream IDs are stricter than the SessionStore's filesystem-safety
# check. Conservative charset: alphanumeric, underscore, dash, 1-64 chars. Keeps
# session files greppable and rules out anything that could surprise the parser.
_STREAM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@functools.lru_cache(maxsize=1)
def _session_store() -> SessionStore:
    return SessionStore(
        config.SESSION_DIR,
        idle_timeout=timedelta(minutes=config.SESSION_IDLE_TIMEOUT_MIN),
    )


def _validated_session_id(raw) -> tuple[str | None, str | None]:
    """Returns (session_id, error). session_id is None when not provided.

    error is set to a human-readable string when the input is malformed; the
    caller turns it into a 400 response.
    """
    if raw is None or raw == "":
        return None, None
    if not isinstance(raw, str):
        return None, "session_id must be a string"
    raw = raw.strip()
    if not raw:
        return None, None
    if not _STREAM_ID_RE.match(raw):
        return None, ("session_id must match [A-Za-z0-9_-]{1,64} "
                      "(no path separators, no spaces)")
    return raw, None

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
    # Touch each backend without forcing a load — get_*() returns the cached
    # singleton if the factory has already run, but is_loaded() reflects whether
    # the underlying model has actually been instantiated.
    stt_backend = modes.get_stt()
    tts_backend = modes.get_tts()
    return jsonify({
        "status": "ok",
        "mode": config.MODE,
        "gpu_available": gpu_available,
        "models_loaded": {
            "stt": stt_backend.is_loaded(),
            "router": router.is_loaded(),
            "tts": tts_backend.is_loaded(),
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
        result = modes.get_stt().transcribe(tmp_path)
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

    session_id, sid_err = _validated_session_id(data.get("session_id"))
    if sid_err:
        return jsonify({"error": sid_err}), 400

    try:
        # Always classify (even when forcing) so we record what the router would have decided.
        decision = router.classify(message)
        route = force_route if force_route is not None else decision["route"]
        force_routed = force_route is not None

        # If a session_id is present, load the active session and feed its
        # recent window as `history` to the LLM. Then append this new turn.
        # Absent session_id = fully stateless (existing behavior).
        session = None
        history: list[dict] | None = None
        if session_id is not None:
            session = _session_store().active_or_new(session_id)
            window = session.recent_window(config.SESSION_WINDOW_TURNS)
            history = [m for turn in window for m in turn.to_message_pair()]

        llm_result = modes.get_llm(route).chat(message, history=history)

        if session is not None:
            session.append_turn(
                user_text=message,
                reply_text=llm_result["reply"],
                route=route,
                model=llm_result["model"],
                latency_s=float(llm_result["latency_s"]),
                input_tokens=int(llm_result.get("input_tokens") or 0),
                output_tokens=int(llm_result.get("output_tokens") or 0),
            )
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"chat failed: {exc!r}"}), 500

    body = {
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
    }
    if session is not None:
        # Surface the active session file so the client can grep for it later
        # without having to know the rollover rules.
        body["session_file"] = session.path.name
    return jsonify(body)


@app.post("/speak")
def speak():
    data = request.get_json(silent=True)
    if not data or "text" not in data:
        return jsonify({"error": "missing 'text' in JSON body"}), 400
    text = data["text"]
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "'text' must be a non-empty string"}), 400

    try:
        result = audio_out.synth_and_play(text)
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
        stt_result = modes.get_stt().transcribe(tmp_path)
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
        llm_result = modes.get_llm(route).chat(
            transcript,
            max_tokens=config.CONVERSE_MAX_TOKENS,
            system_prompt=config.VOICE_SYSTEM_PROMPT,
        )
        timings["llm"] = time.perf_counter() - t
        reply_text = llm_result["reply"]
        speakable_text = markdown.to_speakable_text(reply_text)

        t = time.perf_counter()
        if play_locally:
            raw_pcm, sample_rate, _ = audio_out.synth_play_nonblock(speakable_text)
        else:
            raw_pcm, sample_rate = modes.get_tts().synth(speakable_text)
        timings["tts"] = time.perf_counter() - t

        wav_bytes = audio.pcm_to_wav(raw_pcm, sample_rate)
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
    print(f"MODE={config.MODE}", flush=True)
    if config.WARMUP:
        # STT warmup takes the longest (Whisper PTX JIT on cold start). Print
        # specific labels so a user can tell which stage is slow if boot stalls.
        print("Warming up STT...", flush=True)
        modes.get_stt().warmup()
        print("Warming up router (embedding example sets)...", flush=True)
        router.attach_embedder(modes.get_embedder())
        router.warmup()
        print("Warming up simple-route LLM...", flush=True)
        modes.get_llm("simple").warmup()
        print("Warming up complex-route LLM...", flush=True)
        modes.get_llm("complex").warmup()
        print("Warming up TTS...", flush=True)
        modes.get_tts().warmup()
        print("Warmup complete.", flush=True)
    print(f"Listening on http://{config.HOST}:{config.PORT}", flush=True)
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
