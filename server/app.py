"""Flask app: /transcribe, /chat (with router), /converse, /speak, /health, / (web client).

Run from repo root:
    python -m server.app
"""

from __future__ import annotations

import base64
import functools
import hmac
import json
import re
import sys
import shutil
import tempfile
import time
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from flask import Flask, Response, jsonify, request, stream_with_context

from server import audio, audio_out, config, markdown, modes, router, titler, usage, wakeword
from server.sessions import Session, SessionStore
from server.streaming import SentenceChunker
from server.tools import all_tools

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


def _maybe_autotitle(session: Session, user_text: str, reply_text: str) -> None:
    """Fire the titler if this session has exactly one turn and no title yet.

    Synchronous on purpose — titling is a single fast Haiku call (~400-700ms)
    and waiting for it inside the route handler keeps the rename atomic
    relative to the user's next request. Failures inside titler.title_for_turn
    are already swallowed; this wrapper additionally guards against any
    bug in the storage layer (set_title is also fault-tolerant on rename
    failure).
    """
    if session.title is not None:
        return
    if len(session.all_turns()) != 1:
        return
    title = titler.title_for_turn(user_text, reply_text)
    if not title:
        return
    try:
        session.set_title(title)
    except Exception:
        # Don't let a cosmetic rename take down the route response.
        pass


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
PROTECTED_PATHS = frozenset({"/converse", "/converse/stream", "/chat", "/transcribe", "/speak", "/listen"})


def _sse_event(event: str, data: dict) -> str:
    """Frame a single SSE event. JSON-encodes data on one line so the SSE
    parser doesn't need to worry about multi-line `data:` continuations.
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_content_type(ct: str | None) -> tuple[str, dict[str, str]]:
    """Returns (media_type_lowercase, params_dict). Empty media_type if
    Content-Type is missing or malformed."""
    if not ct:
        return "", {}
    parts = [p.strip() for p in ct.split(";")]
    media = parts[0].lower()
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().lower()] = v.strip().strip('"')
    return media, params


# Server-side peak normalization for incoming /converse audio.
# INMP441 with the firmware's natural >>16 conversion produces conversational
# speech at peaks of ~1500-2000 out of int16's 32767 range — clean but quiet.
# Whisper handles quiet audio worse than scaled audio (more hallucinations,
# more substitutions). We auto-scale every received recording to a target
# peak so whisper sees consistent levels regardless of how close the user
# was to the mic, voice volume, or distance.
#
# Adaptive (peak-based) rather than fixed-gain so loud speech doesn't clip:
#   gain = min(MAX_GAIN, TARGET_PEAK / current_peak)
# Skip when peak is below MIN_PEAK (assume silence/noise — don't blow up
# the noise floor).
NORMALIZE_TARGET_PEAK = 24000   # ~73% of int16 full scale, 6 dB headroom
NORMALIZE_MAX_GAIN    = 12.0    # cap so pure noise doesn't get amplified to ear-splitting
NORMALIZE_MIN_PEAK    = 200     # below this we treat the recording as silence

def _peak_normalize_wav(path: str | Path) -> None:
    """Peak-normalize an int16 WAV file in-place. No-op for non-int16 WAVs
    or recordings below the silence floor."""
    import wave
    import numpy as np
    try:
        with wave.open(str(path), "rb") as wf:
            params = wf.getparams()
            frames = wf.readframes(wf.getnframes())
        if params.sampwidth != 2:
            return
        samples = np.frombuffer(frames, dtype=np.int16)
        if samples.size == 0:
            return
        peak = int(np.abs(samples).max())
        if peak < NORMALIZE_MIN_PEAK:
            return
        gain = min(NORMALIZE_MAX_GAIN, NORMALIZE_TARGET_PEAK / peak)
        if gain <= 1.0:
            return
        boosted = np.clip(
            samples.astype(np.int32) * gain,
            -32768, 32767,
        ).astype(np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setparams(params)
            wf.writeframes(boosted.tobytes())
        app.logger.info("normalized audio: peak %d -> ~%d (gain %.2fx)",
                        peak, int(peak * gain), gain)
    except (OSError, wave.Error) as e:
        app.logger.warning("peak normalize failed: %s — leaving audio as-is", e)


def _capture_audio_upload(suffix_hint: str = ".wav") -> tuple[str | None, dict | None, int | None, callable | None]:
    """Save the audio body to a tempfile, returning
    (tmp_path, error_response_dict_or_None, http_status_or_None, get_field).

    Three accepted upload shapes:

    1. multipart/form-data: existing path. File in 'audio' part, params
       in form fields. `get_field` is `request.form.get`.

    2. audio/l16; rate=N[; channels=N]: raw int16 LE PCM in body, params
       from query string. ESP-32 / INMP441 captures int16 LE natively;
       this skips WAV-wrapping on the device. We wrap to WAV here so the
       existing faster-whisper / Deepgram pipeline doesn't have to know
       about raw bodies.

    3. audio/wav, audio/wave, audio/x-wav: already-wrapped WAV in body,
       params from query string. Useful for tiny clients that have a WAV
       file ready (e.g. a hardware unit-test fixture stored in flash).

    On error returns (None, error_dict, http_status, None) so the caller
    can `return jsonify(error_dict), status` immediately.
    """
    media, params = _parse_content_type(request.content_type)

    # multipart upload
    if media.startswith("multipart/"):
        if "audio" not in request.files:
            return None, {"error": "missing 'audio' file in multipart form"}, 400, None
        f = request.files["audio"]
        if not f.filename:
            return None, {"error": "empty audio filename"}, 400, None
        suffix = Path(f.filename).suffix or suffix_hint
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_path = tmp.name
        tmp.close()
        f.save(tmp_path)
        return tmp_path, None, None, request.form.get

    # raw int16 PCM body
    if media in ("audio/l16", "audio/x-l16"):
        try:
            rate = int(params.get("rate", "16000"))
            channels = int(params.get("channels", "1"))
        except ValueError:
            return None, {"error": "Content-Type must include integer rate; "
                                   "e.g. 'audio/l16; rate=16000; channels=1'"}, 400, None
        if rate < 8000 or rate > 48000 or channels < 1 or channels > 2:
            return None, {"error": f"unsupported PCM rate/channels: rate={rate} "
                                   f"channels={channels}"}, 400, None
        raw = request.get_data(cache=False)
        if not raw:
            return None, {"error": "empty audio body"}, 400, None
        if len(raw) % 2 != 0:
            return None, {"error": "audio/l16 body length must be even (int16 samples)"}, 400, None

        # Diagnostic: dump the raw bytes the client sent, before any wrapping
        # or normalization. Lets us localize an audio-corruption bug to either
        # firmware/wire (raw is corrupt) or server-side post-receive (raw is
        # clean but the WAV / normalized output isn't).
        try:
            raw_dump = config.REPO_ROOT / "samples" / "_last_raw.bin"
            raw_dump.parent.mkdir(parents=True, exist_ok=True)
            raw_dump.write_bytes(raw)
            # Loopback-test diagnostic: also keep a per-turn copy keyed by
            # unix timestamp so an offline analyzer can pair each upload with
            # the matching serial-log STATS line. Cheap; ignored if disabled.
            if config.LOOPBACK_KEEP_PER_TURN:
                ts = int(time.time())
                per_turn = config.REPO_ROOT / "samples" / f"_loopback_turn_{ts}.bin"
                per_turn.write_bytes(raw)
            app.logger.info(
                "raw body received: %d bytes (%.2f s @ %d Hz mono int16)",
                len(raw), len(raw) / 2.0 / float(rate), rate,
            )
        except OSError as exc:
            app.logger.warning("could not save raw body dump: %s", exc)

        wav_bytes = audio.pcm_to_wav(raw, rate, channels=channels)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        Path(tmp_path).write_bytes(wav_bytes)
        return tmp_path, None, None, request.args.get

    # raw WAV body
    if media in ("audio/wav", "audio/wave", "audio/x-wav"):
        raw = request.get_data(cache=False)
        if not raw:
            return None, {"error": "empty audio body"}, 400, None
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        Path(tmp_path).write_bytes(raw)
        return tmp_path, None, None, request.args.get

    return None, {
        "error": f"unsupported Content-Type {request.content_type!r}; "
                 f"use multipart/form-data, audio/l16 (with rate=N; channels=N), "
                 f"or audio/wav"
    }, 415, None


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
        "usage": usage.snapshot(),
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

        # Auto-escalate "tool-likely" simple queries to complex. Keeps users
        # from getting a "my training cutoff" answer when a tool could have
        # served the real-time question. force_route="simple" overrides this
        # — explicit user intent wins.
        auto_escalated = False
        if (not force_routed) and route == "simple" and router.is_tool_likely(message):
            route = "complex"
            auto_escalated = True
        decision["auto_escalated"] = auto_escalated

        # If a session_id is present, load the active session and feed its
        # recent window as `history` to the LLM. Then append this new turn.
        # Absent session_id = fully stateless (existing behavior).
        session = None
        history: list[dict] | None = None
        if session_id is not None:
            session = _session_store().active_or_new(session_id)
            window = session.recent_window(config.SESSION_WINDOW_TURNS)
            history = [m for turn in window for m in turn.to_message_pair()]

        # Tools flow ONLY to the complex route. Auto-escalation already moved
        # tool-likely queries there; the simple route (Llama 3.1 8B) becomes
        # confused when given tools for non-tool conversation — it'll call
        # tools spontaneously on greetings or output raw JSON tool-call
        # syntax mid-conversation. Don't tempt it.
        tools = (all_tools() or None) if route == "complex" else None

        llm_result = modes.get_llm(route).chat(message, history=history, tools=tools)
        usage.record(
            llm_result.get("model"),
            input_tokens=llm_result.get("input_tokens"),
            output_tokens=llm_result.get("output_tokens"),
            route=route,
        )

        if session is not None:
            session.append_turn(
                user_text=message,
                reply_text=llm_result["reply"],
                route=route,
                model=llm_result["model"],
                latency_s=float(llm_result["latency_s"]),
                input_tokens=int(llm_result.get("input_tokens") or 0),
                output_tokens=int(llm_result.get("output_tokens") or 0),
                auto_escalated=auto_escalated,
                tools_used=llm_result.get("tools_used"),
            )
            _maybe_autotitle(session, message, llm_result["reply"])
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"chat failed: {exc!r}"}), 500

    body = {
        **llm_result,
        "route": route,
        "force_routed": force_routed,
        "auto_escalated": decision.get("auto_escalated", False),
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
    """Full pipeline: audio -> STT -> router -> LLM -> TTS -> audio out + JSON.

    Accepts the same three upload shapes as /converse/stream:
      * multipart/form-data           (browser, talk.py)
      * audio/l16; rate=N; channels=N (ESP-WROOM-32 firmware: raw int16 LE
                                       mic capture, no WAV-wrap on the device)
      * audio/wav                     (clients with a WAV fixture in flash)

    Difference from /converse/stream: this endpoint plays the synthesized
    reply through the WORKSTATION's default output device when
    play_locally=true (default) — the ESP-32 doesn't need an on-board
    speaker because the workstation is the speaker. The audio_base64 in
    the JSON response is informational; mic-only clients can ignore it.
    """
    tmp_path, err, status, get_field = _capture_audio_upload(suffix_hint=".wav")
    if err is not None:
        return jsonify(err), status

    # Auto-gain: scale the recording so whisper sees a healthy signal level
    # regardless of mic distance / voice volume. Adaptive — never clips.
    _peak_normalize_wav(tmp_path)

    # Debug: keep a copy of the most recent received audio (post-normalize,
    # so this is exactly what whisper will see) for offline listening.
    try:
        debug_audio = config.REPO_ROOT / "samples" / "_last_received.wav"
        debug_audio.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp_path, debug_audio)
        # Loopback-test diagnostic: also keep a per-turn copy keyed by unix
        # timestamp so each turn can be analyzed offline without being
        # overwritten by the next one. Same content as _last_received.wav
        # (post-normalize). Ignored if disabled.
        if config.LOOPBACK_KEEP_PER_TURN:
            ts = int(time.time())
            per_turn = config.REPO_ROOT / "samples" / f"_loopback_turn_{ts}.wav"
            shutil.copyfile(tmp_path, per_turn)
    except OSError as e:
        app.logger.warning("could not save debug audio: %s", e)

    force_route = get_field("force_route")
    if force_route is not None and force_route not in ("simple", "complex"):
        return jsonify({"error": "force_route must be 'simple', 'complex', or omitted"}), 400

    session_id, sid_err = _validated_session_id(get_field("session_id"))
    if sid_err:
        return jsonify({"error": sid_err}), 400

    # Phone/web clients want to play the reply audio themselves and don't want
    # the workstation playing it through its speakers. Default true preserves
    # the ESP-WROOM-32 firmware's behavior where the workstation IS the speaker.
    play_locally = (get_field("play_locally") or "true").strip().lower() != "false"

    # ESP-32 firmware ignores audio_base64 (workstation plays the reply via
    # play_locally=true). Letting it omit the base64 from the JSON saves
    # ~100-200 KB per /converse response on a chip with ~300 KB heap, which
    # is the difference between "cJSON parses cleanly" and "heap exhaustion".
    # Always parsed from the query string — multipart form fields work too
    # but the firmware uses raw audio/l16 with query params.
    omit_audio_base64 = (request.args.get("omit_audio_base64", "0").strip().lower()
                         in ("1", "true", "yes"))

    timings: dict[str, float] = {}
    t_total = time.perf_counter()

    try:
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

        # Auto-escalate "tool-likely" simple-route queries to complex (Sonnet)
        # — same logic as /chat. Voice users asking "what's the weather"
        # benefit just like text users do.
        auto_escalated = False
        if (not force_routed) and route == "simple" and router.is_tool_likely(transcript):
            route = "complex"
            auto_escalated = True
        decision["auto_escalated"] = auto_escalated

        # Load conversation history if a session_id is in play. We append the
        # turn AFTER the LLM call but BEFORE TTS — that way if TTS or playback
        # crashes, the conversation state still persists.
        session = None
        history: list[dict] | None = None
        if session_id is not None:
            session = _session_store().active_or_new(session_id)
            window = session.recent_window(config.SESSION_WINDOW_TURNS)
            history = [m for turn in window for m in turn.to_message_pair()]

        # Tools complex-route-only. See /chat for the rationale — Llama 3.1 8B
        # (simple route) misbehaves when handed a tool list mid-conversation.
        tools = (all_tools() or None) if route == "complex" else None

        t = time.perf_counter()
        llm_result = modes.get_llm(route).chat(
            transcript,
            max_tokens=config.CONVERSE_MAX_TOKENS,
            system_prompt=config.VOICE_SYSTEM_PROMPT,
            history=history,
            tools=tools,
        )
        timings["llm"] = time.perf_counter() - t
        reply_text = llm_result["reply"]
        usage.record(
            llm_result.get("model"),
            input_tokens=llm_result.get("input_tokens"),
            output_tokens=llm_result.get("output_tokens"),
            route=route,
        )

        # Persist the turn while it's fresh — store the raw reply, NOT the
        # markdown-stripped version. Strip is a TTS concern; future turns
        # benefit from seeing the reply as the model actually produced it.
        if session is not None:
            session.append_turn(
                user_text=transcript,
                reply_text=reply_text,
                route=route,
                model=llm_result["model"],
                latency_s=float(llm_result["latency_s"]),
                input_tokens=int(llm_result.get("input_tokens") or 0),
                output_tokens=int(llm_result.get("output_tokens") or 0),
                auto_escalated=auto_escalated,
                tools_used=llm_result.get("tools_used"),
            )
            _maybe_autotitle(session, transcript, reply_text)

        speakable_text = markdown.to_speakable_text(reply_text)

        t = time.perf_counter()
        if play_locally:
            raw_pcm, sample_rate, _ = audio_out.synth_play_nonblock(speakable_text)
        else:
            raw_pcm, sample_rate = modes.get_tts().synth(speakable_text)
        timings["tts"] = time.perf_counter() - t

        if omit_audio_base64:
            # Skip the WAV wrap + base64 entirely on the firmware path —
            # both are pure CPU waste when the client will discard the
            # field. audio_duration_s is computed from raw_pcm directly
            # so the device can still time its SPEAKING state.
            audio_b64 = None
        else:
            wav_bytes = audio.pcm_to_wav(raw_pcm, sample_rate)
            audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"converse failed: {exc!r}"}), 500
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    timings["total"] = time.perf_counter() - t_total

    body = {
        "transcript": transcript,
        "route": route,
        "force_routed": force_routed,
        "auto_escalated": decision.get("auto_escalated", False),
        "model": llm_result["model"],
        "reply_text": reply_text,
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
        "estimated_cost_usd": usage.estimated_cost_usd(
            llm_result["model"],
            llm_result.get("input_tokens"),
            llm_result.get("output_tokens"),
        ),
    }
    if audio_b64 is not None:
        body["audio_base64"] = audio_b64
    if llm_result.get("tools_used"):
        body["tools_used"] = llm_result["tools_used"]
    if session is not None:
        body["session_file"] = session.path.name
    return jsonify(body)


@app.post("/converse/stream")
def converse_stream():
    """SSE-streamed converse pipeline.

    Three upload shapes accepted, chosen by Content-Type:

      * multipart/form-data           file in `audio` part, params (force_route,
                                      session_id, play_locally) in form fields.
                                      The default for browser/MediaRecorder
                                      clients.
      * audio/l16; rate=N; channels=N raw int16 LE PCM in the body, params from
                                      query string. Skips WAV-wrapping on the
                                      device — the natural shape for ESP-32 +
                                      INMP441 (which captures int16 LE natively).
      * audio/wav                     pre-wrapped WAV in body, params from query
                                      string. Useful for tiny clients with a
                                      WAV fixture in flash.

    Phase 3D: real STT + router + LLM streaming + sentence-boundary
    streaming TTS. Text deltas land in a SentenceChunker; complete
    sentences are handed to TTS.synth_stream as they arrive, and
    resulting PCM chunks ship as `audio` SSE events. Audio interleaves
    with text — the user starts hearing the reply while the model is
    still generating.

    Tool calls remain inside the LLM call. tool_call / tool_result SSE
    events fire around handler runs. The chunker is reset on tool_call
    so any partial sentence buffered from "thinking text" before the
    call is dropped (audio chunks already emitted for completed
    sentences in that round can't be unspoken — Sonnet under
    VOICE_SYSTEM_PROMPT typically goes straight to tool_use without
    preamble, so this rarely actually fires).

    Backends without streaming raise NotImplementedError on first
    iteration; this route catches that and falls back to non-streaming
    chat() with the whole reply emitted as a single text delta and
    one synth() call. (After 3C, both ClaudeLLM and OllamaLLM stream,
    so the fallback is dead code in practice — kept as a safety net.)
    """
    # Three accepted upload shapes — multipart, raw audio/l16 PCM body,
    # or raw audio/wav body. _capture_audio_upload writes to a tempfile and
    # hands back a getter for the rest of the params (form fields for
    # multipart, query string for the raw paths). The tempfile MUST be
    # written here before the generator runs — Flask tears down the
    # request context once the view returns, so reading request.files /
    # request.get_data() inside the generator would race the teardown.
    tmp_path, err, status, get_field = _capture_audio_upload(suffix_hint=".wav")
    if err is not None:
        return jsonify(err), status

    # Auto-gain: scale the recording so whisper sees a healthy signal level
    # regardless of mic distance / voice volume. Adaptive — never clips.
    _peak_normalize_wav(tmp_path)

    # Debug: keep a copy of the most recent received audio (post-normalize,
    # so this is exactly what whisper will see) for offline listening.
    try:
        debug_audio = config.REPO_ROOT / "samples" / "_last_received.wav"
        debug_audio.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp_path, debug_audio)
    except OSError as e:
        app.logger.warning("could not save debug audio: %s", e)

    force_route = get_field("force_route")
    if force_route is not None and force_route not in ("simple", "complex"):
        return jsonify({"error": "force_route must be 'simple', 'complex', or omitted"}), 400

    session_id, sid_err = _validated_session_id(get_field("session_id"))
    if sid_err:
        return jsonify({"error": sid_err}), 400

    # play_locally is parsed and echoed back in the `ready` event but
    # unused server-side: /converse/stream emits PCM only via SSE and
    # leaves playback to the client (the web client uses Web Audio).
    # Workstation callers that want the workstation to speak the reply
    # should keep using the non-streaming /converse endpoint.
    play_locally = (get_field("play_locally") or "true").strip().lower() != "false"

    def generate() -> Iterator[str]:
        timings: dict[str, float] = {}
        t_total = time.perf_counter()
        try:
            yield _sse_event("ready", {
                "mode": config.MODE,
                "session_id": session_id,
                "play_locally": play_locally,
            })

            t = time.perf_counter()
            stt_result = modes.get_stt().transcribe(tmp_path)
            timings["stt"] = time.perf_counter() - t
            transcript = stt_result["transcript"]

            yield _sse_event("transcript", {
                "transcript": transcript,
                "duration_s": stt_result.get("duration_s"),
                "latency_s": timings["stt"],
            })

            if not transcript.strip():
                yield _sse_event("error", {
                    "error": "transcript was empty - no speech detected",
                })
                return

            t = time.perf_counter()
            decision = router.classify(transcript)
            timings["route"] = time.perf_counter() - t
            route = force_route if force_route is not None else decision["route"]
            force_routed = force_route is not None

            auto_escalated = False
            if (not force_routed) and route == "simple" and router.is_tool_likely(transcript):
                route = "complex"
                auto_escalated = True

            llm = modes.get_llm(route)

            yield _sse_event("route", {
                "route": route,
                "force_routed": force_routed,
                "auto_escalated": auto_escalated,
                "model": getattr(llm, "model", "unknown"),
                "router_scores": {
                    "simple": decision["score_simple"],
                    "complex": decision["score_complex"],
                    "margin": decision["margin"],
                    "margin_threshold": decision["margin_threshold"],
                },
            })

            # Load conversation history if a session_id was supplied. Mirrors
            # /converse: window of recent turns becomes the LLM's `history`,
            # and the new turn is appended on `final` (using last-round text,
            # not joined deltas — see ClaudeLLM.chat_stream docstring).
            session: Session | None = None
            history: list[dict] | None = None
            if session_id is not None:
                session = _session_store().active_or_new(session_id)
                window = session.recent_window(config.SESSION_WINDOW_TURNS)
                history = [m for turn in window for m in turn.to_message_pair()]

            # Tools complex-route-only — same rationale as /chat and /converse:
            # Llama 3.1 8B (simple) misbehaves with tools mid-conversation, and
            # auto-escalation has already moved tool-likely queries to Sonnet.
            tool_list = (all_tools() or None) if route == "complex" else None

            reply_text = ""
            llm_input_tokens = 0
            llm_output_tokens = 0
            tools_used: list[dict] = []

            chunker = SentenceChunker()
            tts_backend = modes.get_tts()
            # `tts_total_pcm_bytes` and `audio_sample_rate` are accumulated
            # across all per-sentence synth_stream() calls so the final
            # event can report a single audio_duration_s. `tts_total_s`
            # sums the wall-clock time of each synth_stream() invocation
            # — preserves the meaning of timings.tts as "total time spent
            # synthesizing", even though synth is no longer one
            # contiguous block.
            tts_total_pcm_bytes = 0
            audio_sample_rate: int | None = None
            tts_total_s = 0.0

            def synth_and_emit(sentences: list[str]):
                """Synth a list of sentences and yield SSE audio events.

                Markdown is stripped per sentence; sentences that strip to
                empty are skipped (a stray flush of e.g. a bullet marker
                shouldn't fire a zero-length voice call). PCM bytes and
                sample rate are accumulated into the surrounding closure
                for the final audio_duration_s report.
                """
                nonlocal tts_total_pcm_bytes, audio_sample_rate, tts_total_s
                if not sentences:
                    return
                speakable: list[str] = []
                for s in sentences:
                    stripped = markdown.to_speakable_text(s)
                    if stripped.strip():
                        speakable.append(stripped)
                if not speakable:
                    return
                synth_t0 = time.perf_counter()
                for pcm_chunk, sr in tts_backend.synth_stream(iter(speakable)):
                    audio_sample_rate = sr
                    tts_total_pcm_bytes += len(pcm_chunk)
                    yield _sse_event("audio", {
                        "sample_rate": sr,
                        "pcm_b64": base64.b64encode(pcm_chunk).decode("ascii"),
                    })
                tts_total_s += time.perf_counter() - synth_t0

            t = time.perf_counter()
            try:
                stream_iter = llm.chat_stream(
                    transcript,
                    max_tokens=config.CONVERSE_MAX_TOKENS,
                    system_prompt=config.VOICE_SYSTEM_PROMPT,
                    history=history,
                    tools=tool_list,
                )
                for ev in stream_iter:
                    etype = ev.get("type")
                    if etype == "text":
                        delta = ev["delta"]
                        yield _sse_event("text", {"delta": delta})
                        sentences = chunker.feed(delta)
                        if sentences:
                            yield from synth_and_emit(sentences)
                    elif etype == "tool_call":
                        # The round that emitted this tool_call ends here —
                        # any partial sentence in the chunker was "thinking
                        # text" the model didn't intend as the final reply.
                        # Drop it. (Already-emitted audio for completed
                        # sentences in this round can't be unspoken; see
                        # docstring.)
                        chunker.reset()
                        yield _sse_event("tool_call", {
                            "id": ev.get("id"),
                            "name": ev["name"],
                            "input": ev.get("input", {}),
                            "round": ev.get("round"),
                        })
                    elif etype == "tool_result":
                        yield _sse_event("tool_result", {
                            "id": ev.get("id"),
                            "name": ev["name"],
                            "output_chars": ev.get("output_chars"),
                            "latency_s": ev.get("latency_s"),
                            "round": ev.get("round"),
                        })
                    elif etype == "final":
                        # Flush the trailing fragment (last sentence often
                        # has no terminating punctuation+space).
                        yield from synth_and_emit(chunker.flush())
                        reply_text = ev.get("reply", "")
                        llm_input_tokens = int(ev.get("input_tokens") or 0)
                        llm_output_tokens = int(ev.get("output_tokens") or 0)
                        tools_used = ev.get("tools_used") or []
                        usage.record(
                            ev.get("model") or getattr(llm, "model", None),
                            input_tokens=llm_input_tokens,
                            output_tokens=llm_output_tokens,
                            route=route,
                        )
            except NotImplementedError:
                # Backend hasn't implemented streaming. Both shipped
                # backends (ClaudeLLM, OllamaLLM) do as of 3C, so this
                # is a safety-net path for any backend added later
                # without streaming. Fall back to non-streaming chat()
                # and a single synth() over the whole reply.
                result = llm.chat(
                    transcript,
                    max_tokens=config.CONVERSE_MAX_TOKENS,
                    system_prompt=config.VOICE_SYSTEM_PROMPT,
                    history=history,
                    tools=tool_list,
                )
                reply_text = result["reply"]
                llm_input_tokens = int(result.get("input_tokens") or 0)
                llm_output_tokens = int(result.get("output_tokens") or 0)
                tools_used = result.get("tools_used") or []
                if reply_text:
                    yield _sse_event("text", {"delta": reply_text})
                    yield from synth_and_emit([reply_text])
            timings["llm"] = time.perf_counter() - t
            timings["tts"] = tts_total_s

            # Persist the turn BEFORE the final SSE event, same ordering
            # as /converse — if anything below this crashes, conversation
            # memory still moves forward. Store final.reply (last-round
            # text), not joined text deltas: chat_stream drops "thinking
            # text" from earlier rounds for voice purposes.
            if session is not None:
                session.append_turn(
                    user_text=transcript,
                    reply_text=reply_text,
                    route=route,
                    model=getattr(llm, "model", "unknown"),
                    latency_s=timings["llm"],
                    input_tokens=llm_input_tokens,
                    output_tokens=llm_output_tokens,
                    auto_escalated=auto_escalated,
                    tools_used=tools_used or None,
                )
                _maybe_autotitle(session, transcript, reply_text)

            timings["total"] = time.perf_counter() - t_total
            audio_duration_s = (
                (tts_total_pcm_bytes // 2) / audio_sample_rate
                if audio_sample_rate else 0.0
            )
            final_payload = {
                "reply_text": reply_text,
                "audio_duration_s": audio_duration_s,
                "timings": timings,
                "tools_used": tools_used,
                "llm_tokens": {"input": llm_input_tokens, "output": llm_output_tokens},
            }
            if session is not None:
                final_payload["session_file"] = session.path.name
            yield _sse_event("final", final_payload)
        except Exception as exc:
            traceback.print_exc()
            yield _sse_event("error", {"error": f"converse stream failed: {exc!r}"})
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # X-Accel-Buffering disables proxy buffering (nginx + cloudflared honor it),
    # so SSE events flush to the client as they're yielded instead of being
    # held until the response completes.
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/listen")
def listen():
    """Server-side openWakeWord over a chunked-transfer raw PCM body.

    Spec (see notes/esp32-firmware-plan.md "Wake word"): the client (the
    ESP-WROOM-32 firmware in IDLE state, or tests/stage11_listen.py)
    holds an open chunked-transfer POST while streaming int16 LE mono
    @ 16 kHz from its mic. The server frames to 1280-sample windows
    and runs openWakeWord.Model.predict() per frame. On detection:
    respond 200 with {"wake": true, "score": ..., "model": ...} and
    close. After IDLE_TIMEOUT_S (10 min) of bytes-but-no-detection:
    respond {"wake": false, "reason": "idle_timeout"}. Client EOF
    (TCP close from device side) is graceful: {"wake": false,
    "reason": "client_closed"}.

    Content-Type must be audio/l16; rate=16000; channels=1. Anything
    else returns 415 — there's no point trying to handle other formats
    here since the device emits exactly this shape.
    """
    if not wakeword.is_available():
        return jsonify({
            "error": "openwakeword not installed on the server; install via "
                     "`pip install openwakeword` and openwakeword.utils."
                     "download_models()",
        }), 503

    media, params = _parse_content_type(request.content_type)
    if media not in ("audio/l16", "audio/x-l16"):
        return jsonify({
            "error": f"Content-Type must be audio/l16; got {request.content_type!r}",
        }), 415
    try:
        rate = int(params.get("rate", "16000"))
        channels = int(params.get("channels", "1"))
    except ValueError:
        return jsonify({
            "error": "Content-Type must include integer rate; "
                     "e.g. 'audio/l16; rate=16000; channels=1'",
        }), 400
    if rate != 16000 or channels != 1:
        # openWakeWord's bundled models are trained at 16 kHz mono; any
        # other rate/channel combo would need resampling which is out of
        # scope for v1. The firmware contract is explicit (16 kHz mono),
        # so reject anything else with a clear error.
        return jsonify({
            "error": f"unsupported PCM format: rate={rate} channels={channels}; "
                     f"need rate=16000 channels=1",
        }), 415

    try:
        result = wakeword.stream_predict(request.stream.read)
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": f"listen failed: {exc!r}"}), 500

    if result.get("wake"):
        usage.record_wake_detection()
    return jsonify(result)


def main():
    print(f"MODE={config.MODE}", flush=True)
    # One-shot session prune at boot when SESSION_TTL_DAYS is configured.
    # No background daemon — files just stop accumulating between restarts.
    if config.SESSION_TTL_DAYS is not None:
        try:
            deleted = _session_store().prune_older_than(config.SESSION_TTL_DAYS)
            if deleted:
                print(f"Pruned {len(deleted)} session file(s) older than "
                      f"{config.SESSION_TTL_DAYS} days.", flush=True)
        except Exception as exc:
            # Pruning is best-effort; never block boot on it.
            print(f"WARNING: session prune failed: {exc!r}", flush=True)
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
        # Pre-load the openwakeword model so the first /listen request
        # doesn't pay a ~1.2 s lazy-load cost while the firmware is already
        # streaming chunks at it. Without this pre-warm, the firmware's
        # send buffer fills before the server starts draining, send()
        # times out under SO_SNDTIMEO, and the device enters a runaway
        # reconnect loop. Best-effort: if openwakeword isn't installed,
        # /listen returns 503 anyway, so silently skip.
        try:
            import server.wakeword as _ww
            if _ww.is_available():
                print("Warming up wake-word model...", flush=True)
                _ww._ensure_model()
        except Exception as exc:
            print(f"WARNING: wakeword warmup skipped: {exc!r}", flush=True)
        print("Warmup complete.", flush=True)
    print(f"Listening on http://{config.HOST}:{config.PORT}", flush=True)
    # threaded=True so a slow /listen (which holds wakeword._LOCK for the
    # life of the request) doesn't queue all other handlers behind it.
    # The Werkzeug dev server is single-threaded by default — without
    # this flag, one stuck firmware /listen request can wedge /converse,
    # /health, and every other endpoint indefinitely. (Discovered during
    # M4 firmware bench-up: a TCP-backpressure deadlock left a /listen
    # request hung in request.stream.read forever, locking out new
    # connections from the firmware's reconnect loop.)
    #
    # Custom request handler with a 30 s socket timeout. Werkzeug's default
    # is 60 s, which is too long for /listen (when the firmware RSTs an
    # idle connection, the server-side recv blocks for the full 60 s,
    # holding wakeword._LOCK and starving subsequent requests). 5 s was
    # the original setting but turned out too aggressive for /converse —
    # long VAD recordings (10-30 s of chunked audio) plus normal WiFi
    # WiFi jitter caused TimeoutError during request.get_data()
    # on the chunked stream, surfacing as 500s mid-turn. 30 s is well
    # above the firmware's per-frame cadence (~80 ms between chunks),
    # accommodates several seconds of WiFi backoff, and is still fast
    # enough that dead /listen clients recycle the wakeword lock without
    # piling up — the wakeword module's own 2 s lock-acquire timeout
    # handles short-term contention.
    from werkzeug.serving import WSGIRequestHandler
    class FastFailHandler(WSGIRequestHandler):
        timeout = 30
    app.run(host=config.HOST, port=config.PORT, debug=False,
            use_reloader=False, threaded=True,
            request_handler=FastFailHandler)


if __name__ == "__main__":
    main()
