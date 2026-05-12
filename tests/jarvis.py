"""Wake-word workstation client — say "hey jarvis" to talk.

Listens passively for "hey jarvis" via openWakeWord; on detection, plays a
chime and enters a continuous conversation loop (Phase 6B). Each turn is a
VAD-bounded recording posted to /converse; the server speaks the reply
through the workstation default output. After
WAKEWORD_CONVERSATION_TIMEOUT_S seconds of initial silence on a turn
(no speech crossed the threshold), drops back to passive wake-word
listening. No tap, no Enter.

UX:
    1. python tests/jarvis.py     # in another terminal alongside the server
    2. say "hey jarvis"           # mic listens passively
    3. (chime plays)              # confirmation that wake-word fired
    4. ask a question             # VAD-bounded; auto-stops on silence
    5. server speaks the reply through the workstation default output
    6. ask another (no wake-word needed for ~30s of silence)
    7. ... or just stay quiet for 30s -> back to wake-word listening

Setup (one-time):
    .venv/Scripts/python.exe -m pip install openwakeword
    .venv/Scripts/python.exe -c "import openwakeword; openwakeword.utils.download_models()"

Tunable env vars (all optional):
    WAKEWORD_MODEL                  default "hey_jarvis"
    WAKEWORD_THRESHOLD              default 0.5; lower = more sensitive
    WAKEWORD_CHIME                  path to confirmation WAV;
                                    falls back to a synthetic beep if missing
    VAD_PEAK_THRESHOLD              int16 abs floor for "speech" (default 600)
    VAD_SILENCE_MS                  silence dwell before auto-stop (default 1500)
    VOICE_AI_BASE                   server URL (default http://127.0.0.1:5000)
    VOICE_AI_SESSION_ID             multi-turn session id (default stateless)
    API_KEY                         X-API-Key header value (default unset)

Requires the server to be running:
    python -m server.app
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Importing config triggers .env load AND surfaces all the WAKEWORD_* knobs.
from server import config

BASE = os.environ.get("VOICE_AI_BASE", "http://127.0.0.1:5000")
API_KEY = os.environ.get("API_KEY", "").strip()
SESSION_ID = os.environ.get("VOICE_AI_SESSION_ID", "").strip()

# VAD knobs — same defaults as talk.py / index.html so all three clients
# agree on what counts as silence.
VAD_PEAK_THRESHOLD = int(os.environ.get("VAD_PEAK_THRESHOLD", "600"))
VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "1500"))

# openWakeWord operates on int16 mono 16 kHz frames in multiples of 1280
# samples (80 ms). That's also a friendly InputStream blocksize — keeps
# wake-word inference latency bounded.
WAKEWORD_SAMPLE_RATE = 16000
WAKEWORD_FRAME_SAMPLES = 1280


# ---------------------------------------------------------------------------
# small helpers reused across the listen / record / send pipeline
# ---------------------------------------------------------------------------

def _auth_headers() -> dict:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def _health() -> dict | None:
    try:
        r = requests.get(f"{BASE}/health", timeout=3)
        if r.status_code == 200:
            return r.json()
    except requests.exceptions.RequestException:
        pass
    return None


def _pcm_to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


def _play_chime() -> None:
    """Confirmation sound on wake-word detection. Uses WAKEWORD_CHIME if it
    exists, else generates a brief synthetic beep so the user ALWAYS gets
    auditory feedback. Blocks until the chime is done so the recording phase
    doesn't capture the chime itself."""
    chime_path = config.WAKEWORD_CHIME
    try:
        if chime_path.exists():
            with wave.open(str(chime_path), "rb") as wf:
                sr = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
                dtype = np.int16 if wf.getsampwidth() == 2 else np.uint8
                audio = np.frombuffer(raw, dtype=dtype)
                if wf.getnchannels() > 1:
                    audio = audio.reshape(-1, wf.getnchannels()).mean(axis=1).astype(dtype)
                sd.play(audio, sr)
                sd.wait()
                return
    except Exception as exc:
        print(f"  (chime file failed: {exc!r}; falling back to synthetic beep)")

    # Synthetic two-tone "ding" — short and unobtrusive.
    sr = 22050
    duration_s = 0.18
    n = int(sr * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    beep = 0.25 * (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t))
    # Quick fade-in/out so it doesn't click.
    n_fade = max(8, int(sr * 0.01))
    fade = np.linspace(0.0, 1.0, n_fade)
    beep[:n_fade] *= fade
    beep[-n_fade:] *= fade[::-1]
    audio = (beep * 32000).astype(np.int16)
    sd.play(audio, sr)
    sd.wait()


# ---------------------------------------------------------------------------
# wake-word listener
# ---------------------------------------------------------------------------

def listen_for_wakeword(model, stop_event: threading.Event) -> bool:
    """Block until the wake-word fires, returns True. Returns False if
    `stop_event` is set first (used by Ctrl+C path / future continuous-mode
    handoff in 6B).

    Uses sd.InputStream at 16 kHz mono int16 with blocksize=1280; each
    callback gives openWakeWord exactly the chunk shape it likes. Frames
    are queued and drained on the main thread so the audio driver doesn't
    block on inference.
    """
    frame_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)

    def cb(indata, frames, time_info, status):
        # indata is shape (frames, channels). We requested mono; flatten.
        try:
            frame_q.put_nowait(indata.copy().flatten())
        except queue.Full:
            # Drop on overflow — wake-word is not safety-critical, and
            # building up a 5-second backlog of frames hurts detection
            # responsiveness more than skipping a few.
            pass

    threshold = config.WAKEWORD_THRESHOLD
    model_name = config.WAKEWORD_MODEL

    print(f"  Listening for \"{model_name.replace('_', ' ')}\" "
          f"(threshold={threshold:.2f}, Ctrl+C to quit)...")

    with sd.InputStream(samplerate=WAKEWORD_SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=WAKEWORD_FRAME_SAMPLES,
                        callback=cb):
        last_print = 0.0
        peak_score = 0.0
        while not stop_event.is_set():
            try:
                frame = frame_q.get(timeout=0.2)
            except queue.Empty:
                continue
            scores = model.predict(frame)
            score = float(scores.get(model_name, 0.0))
            if score > peak_score:
                peak_score = score
            if score >= threshold:
                # Drain the queue so post-detection silence isn't fed
                # back into the next predict() and re-trigger.
                while not frame_q.empty():
                    try: frame_q.get_nowait()
                    except queue.Empty: break
                model.reset()
                print(f"  >>> wake word detected (score={score:.3f}) <<<")
                return True
            now = time.perf_counter()
            if now - last_print > 5.0:
                # Periodic heartbeat so the user can tell the listener is alive
                # and how loud their environment looks to the model. Helps tune
                # WAKEWORD_THRESHOLD.
                print(f"  ...still listening (peak score so far: {peak_score:.3f})")
                last_print = now
                peak_score = 0.0
    return False


# ---------------------------------------------------------------------------
# VAD-bounded recording (lifted shape from talk.py; standalone copy so
# importing this module doesn't drag talk.py's stdin reader in)
# ---------------------------------------------------------------------------

def find_input_config() -> tuple[int, int]:
    """Probe the default input device for an opener-friendly (sr, channels)."""
    in_dev = sd.query_devices(sd.default.device[0])
    native_sr = int(in_dev["default_samplerate"])
    max_ch = max(1, int(in_dev["max_input_channels"]))
    candidates: list[tuple[int, int]] = []
    for sr in (16000, native_sr):
        for ch in (1, 2):
            if ch <= max_ch and (sr, ch) not in candidates:
                candidates.append((sr, ch))
    last_err: Exception | None = None
    for sr, ch in candidates:
        try:
            with sd.InputStream(samplerate=sr, channels=ch, dtype="int16"):
                pass
            return sr, ch
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Could not open default input device. Last error: {last_err!r}")


def record_with_vad(sr: int, channels: int,
                    initial_timeout_s: float = 15.0) -> tuple[np.ndarray, str]:
    """Record from the mic, auto-stop on silence-after-speech via VAD.

    Returns (int16 mono PCM, stop_reason).
      - "vad"     normal silence-after-speech auto-stop
      - "timeout" no speech ever crossed VAD_PEAK_THRESHOLD within
                  initial_timeout_s (e.g., user walked away after wake;
                  Phase 6B uses this longer to drop out of continuous
                  mode after WAKEWORD_CONVERSATION_TIMEOUT_S of silence)

    initial_timeout_s only protects against the no-speech case; once
    speech has started the recording continues until VAD_SILENCE_MS of
    silence has dwelled.
    """
    chunks: list[np.ndarray] = []
    stop = threading.Event()
    stop_reason = ["timeout"]
    speech_started = [False]
    last_voice_t = [0.0]
    start_t = time.perf_counter()

    def cb(indata, frames, time_info, status):
        chunks.append(indata.copy())
        if stop.is_set():
            return
        peak = int(np.abs(indata).max()) if indata.size else 0
        now = time.perf_counter()
        if peak >= VAD_PEAK_THRESHOLD:
            speech_started[0] = True
            last_voice_t[0] = now
        elif speech_started[0]:
            silent_ms = (now - last_voice_t[0]) * 1000.0
            if silent_ms > VAD_SILENCE_MS:
                stop_reason[0] = "vad"
                stop.set()
        elif now - start_t > initial_timeout_s:
            stop_reason[0] = "timeout"
            stop.set()

    with sd.InputStream(samplerate=sr, channels=channels, dtype="int16", callback=cb):
        stop.wait()

    if not chunks:
        return np.array([], dtype=np.int16), stop_reason[0]
    audio = np.concatenate(chunks)
    if channels > 1:
        audio = audio.mean(axis=1).astype(np.int16)
    else:
        audio = audio.flatten()
    return audio, stop_reason[0]


# ---------------------------------------------------------------------------
# /converse/stream round-trip with chunked PCM playback
# ---------------------------------------------------------------------------
#
# Why the queue + consumer thread:
# `sd.OutputStream.write()` blocks until the chunk has been queued for the
# OS audio buffer. If we wrote chunks straight from the SSE-parsing loop the
# main thread would block under back-pressure, stop draining the HTTP
# response, and eventually the server would time out trying to push more
# events. Decoupling SSE consumption from audio playback via a queue keeps
# both pipelines flowing — the SSE loop pushes PCM bytes into the queue
# free-running, the consumer thread blocks on stream.write() at the
# playback rate.
# This is also the shape the ESP-32 firmware will use: I²S DMA writer thread
# pulled from a ring buffer, while the WiFi/HTTP task pushes chunks in.

class _StreamingPlayer:
    """Thread-safe sink for PCM chunks arriving from /converse/stream."""

    def __init__(self) -> None:
        self._q: queue.Queue[bytes | None] = queue.Queue()
        self._stream: sd.OutputStream | None = None
        self._thread: threading.Thread | None = None
        self._sr: int | None = None
        self._lock = threading.Lock()

    def start(self, sample_rate: int) -> None:
        """Open the OutputStream on the first audio chunk's announced rate."""
        with self._lock:
            if self._stream is not None:
                return
            self._sr = sample_rate
            self._stream = sd.OutputStream(
                samplerate=sample_rate, channels=1, dtype="int16",
            )
            self._stream.start()
            self._thread = threading.Thread(target=self._consume, daemon=True)
            self._thread.start()

    def _consume(self) -> None:
        # Drains the queue and writes to the stream. Each write blocks until
        # the chunk is accepted into the OS audio buffer; that's the
        # back-pressure mechanism that keeps memory bounded.
        while True:
            item = self._q.get()
            if item is None:
                return
            arr = np.frombuffer(item, dtype=np.int16)
            try:
                self._stream.write(arr)
            except Exception as exc:
                print(f"  audio write failed: {exc!r}")
                return

    def push(self, pcm_bytes: bytes) -> None:
        if self._stream is None or not pcm_bytes:
            return
        self._q.put(pcm_bytes)

    def finish(self) -> None:
        # Sentinel + join + close. After this returns, all pushed audio has
        # been written to the OS buffer (but may still be playing).
        with self._lock:
            if self._stream is None:
                return
            self._q.put(None)
            if self._thread:
                self._thread.join(timeout=30.0)
            try: self._stream.stop()
            except Exception: pass
            try: self._stream.close()
            except Exception: pass
            self._stream = None
            self._thread = None


def post_converse_stream(wav_bytes: bytes) -> dict | None:
    """POST audio to /converse/stream; play PCM chunks through the workstation
    speaker as they arrive; return the parsed `final` event dict (None on
    error). Mirrors the Phase 6 flow but with /converse/stream's lower
    perceived first-byte latency — the same shape the ESP-32 firmware will
    use, just with I²S in place of sounddevice.

    play_locally is sent as 'false' because /converse/stream NEVER plays
    locally server-side anyway (PCM only ships via SSE) — the field is just
    surfaced in the `ready` event for client visibility.
    """
    form: dict[str, str] = {"play_locally": "false"}
    if SESSION_ID:
        form["session_id"] = SESSION_ID

    player = _StreamingPlayer()
    final_event: dict | None = None
    transcript_seen = False

    try:
        r = requests.post(
            f"{BASE}/converse/stream",
            files={"audio": ("user.wav", wav_bytes, "audio/wav")},
            data=form,
            headers=_auth_headers(),
            stream=True,
            timeout=180,
        )
    except requests.exceptions.RequestException as exc:
        print(f"  request failed: {exc}")
        return None

    if r.status_code != 200:
        try: err = r.json().get("error", r.text)
        except Exception: err = r.text
        print(f"  server {r.status_code}: {err}")
        return None

    cur_event: str | None = None
    cur_data: str | None = None
    try:
        for raw in r.iter_lines(decode_unicode=False):
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if line == "":
                if cur_event and cur_data is not None:
                    try:
                        data = json.loads(cur_data)
                    except json.JSONDecodeError:
                        cur_event = cur_data = None
                        continue
                    if cur_event == "transcript" and not transcript_seen:
                        transcript_seen = True
                        print(f"  you said: {data.get('transcript', '')!r}")
                    elif cur_event == "model":
                        print(f"  model={data.get('model')}")
                    elif cur_event == "tool_call":
                        print(f"  tool_call: {data.get('name')}({data.get('input', {})})")
                    elif cur_event == "tool_result":
                        print(f"  tool_result: {data.get('name')} "
                              f"({data.get('output_chars')}ch / {data.get('latency_s', 0):.2f}s)")
                    elif cur_event == "audio":
                        sr = int(data["sample_rate"])
                        pcm = base64.b64decode(data["pcm_b64"])
                        player.start(sr)
                        player.push(pcm)
                    elif cur_event == "final":
                        final_event = data
                    elif cur_event == "error":
                        print(f"  server error: {data.get('error')}")
                cur_event = cur_data = None
                continue
            if line.startswith("event: "):
                cur_event = line[len("event: "):]
            elif line.startswith("data: "):
                cur_data = line[len("data: "):]
    finally:
        # Wait for the audio queue to drain through OS buffer before we move
        # on. Without this the next "Listening for hey jarvis..." print
        # would race ahead of the assistant's last syllable.
        player.finish()
        # Belt-and-suspenders: wait for the OS audio buffer to actually
        # finish playing what we wrote. sd.wait() blocks on the default
        # stream — works because we used the default device.
        try: sd.wait()
        except Exception: pass

    return final_event


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------

def main() -> int:
    h = _health()
    if h is None:
        print(f"ERROR: cannot reach server at {BASE}")
        print("Run `python -m server.app` in another terminal first.")
        return 1

    # Lazy-import openwakeword so an install-missing case produces a clear
    # error instead of a top-of-module ImportError on `python tests/jarvis.py`.
    try:
        from openwakeword.model import Model
    except ImportError as exc:
        print(f"ERROR: openwakeword not installed ({exc}).")
        print("Run: .venv/Scripts/python.exe -m pip install openwakeword")
        print("Then download the bundled models:")
        print("  .venv/Scripts/python.exe -c \"import openwakeword; "
              "openwakeword.utils.download_models()\"")
        return 2

    print("Loading wake-word model...", flush=True)
    try:
        model = Model(wakeword_models=[config.WAKEWORD_MODEL])
    except Exception as exc:
        print(f"ERROR: failed to load wake-word model {config.WAKEWORD_MODEL!r}: {exc!r}")
        print("Did you run download_models() after install? See top of file.")
        return 3

    try:
        rec_sr, rec_ch = find_input_config()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 4

    print(f"Server: {BASE}  auth={'on' if API_KEY else 'off'}  "
          f"session={SESSION_ID or '(stateless)'}  "
          f"models={h.get('models_loaded', {})}")
    print(f"Wake:   {config.WAKEWORD_MODEL}  threshold={config.WAKEWORD_THRESHOLD}")
    print(f"Mic:    record at {rec_sr} Hz / {rec_ch} ch  "
          f"(wake-word listens at {WAKEWORD_SAMPLE_RATE} Hz mono)")
    print(f"VAD:    peak>{VAD_PEAK_THRESHOLD} silence>{VAD_SILENCE_MS}ms")
    conv_timeout = config.WAKEWORD_CONVERSATION_TIMEOUT_S
    print()
    print("=" * 60)
    print(f"  Say \"hey jarvis\" to begin. After each reply, the mic stays")
    print(f"  open for {conv_timeout:.0f}s of silence before falling back to")
    print(f"  wake-word listening. Ctrl+C to quit.")
    print("=" * 60)
    print()

    stop_event = threading.Event()
    while True:
        try:
            woke = listen_for_wakeword(model, stop_event)
        except KeyboardInterrupt:
            print("\nbye.")
            return 0
        if not woke:
            return 0

        try:
            _play_chime()
        except Exception as exc:
            print(f"  chime failed (non-fatal): {exc!r}")

        # Continuous-mode inner loop: each turn uses a longer initial
        # timeout (the conversation-timeout) so a thinking pause doesn't
        # drop the user back to wake-word. The very first post-wake turn
        # still uses a short initial timeout (15s) — if the user said
        # the wake word by accident, we want to bail quickly. After a
        # successful turn the loop switches to conv_timeout.
        first_turn = True
        while True:
            initial_timeout_s = 15.0 if first_turn else conv_timeout
            print(f"  recording... (silent-bail at {initial_timeout_s:.0f}s)")
            try:
                audio, stop_reason = record_with_vad(rec_sr, rec_ch,
                                                    initial_timeout_s=initial_timeout_s)
            except KeyboardInterrupt:
                print("\nbye.")
                return 0
            except Exception as exc:
                print(f"  recording failed: {exc!r}")
                break  # back to wake-word listening

            duration = len(audio) / rec_sr if rec_sr > 0 else 0.0
            peak = int(np.abs(audio).max()) if audio.size else 0
            print(f"  captured {duration:.1f}s  peak={peak}  stop={stop_reason}")

            if stop_reason == "timeout":
                print("  (silent; back to wake-word listening)")
                print()
                break
            if duration < 0.3 or peak < 400:
                # Too quiet to be useful, but don't bail out of continuous
                # mode — the user may have just hesitated.
                print("  (recording too short/quiet; staying in continuous mode)")
                first_turn = False
                continue

            wav = _pcm_to_wav_bytes(audio, rec_sr)
            print(f"  sending {len(wav)/1024:.0f} KB to /converse/stream...")
            t0 = time.perf_counter()
            d = post_converse_stream(wav)
            wall = time.perf_counter() - t0
            if d is None:
                # Server / network error. Bail out so we don't spam the
                # server with broken audio. User can wake again.
                print("  (server error; back to wake-word listening)")
                print()
                break

            t = d.get("timings", {})
            # transcript was printed live during the stream; reply text is
            # only complete once `final` arrives, so it goes here.
            print(f"  reply:    {d.get('reply_text', '')}")
            print(f"  stt={t.get('stt', 0):.2f} llm={t.get('llm', 0):.2f} "
                  f"tts={t.get('tts', 0):.2f} total={t.get('total', 0):.2f} "
                  f"http={wall:.2f}  audio_dur={d.get('audio_duration_s', 0):.2f}s "
                  f"in/out={d.get('llm_tokens', {}).get('input')}/{d.get('llm_tokens', {}).get('output')}")
            if "session_file" in d:
                print(f"  session:  {d['session_file']}")
            print()
            first_turn = False
            # Stay in continuous mode — loop back to record next turn.


if __name__ == "__main__":
    sys.exit(main())
