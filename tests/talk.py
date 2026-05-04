"""Push-to-talk client for the voice AI server.

UX: press Enter to start recording, speak. With VAD enabled (the default),
recording auto-stops after 1.5s of silence — no second Enter needed.
Pressing Enter still works as an immediate manual send. The reply text
prints to this terminal; the reply audio plays automatically through the
server's default output device. Ctrl+C to quit.

Tune VAD via env: VOICE_AI_VAD=0 disables it (back to manual-only),
VAD_PEAK_THRESHOLD=N sets the int16-abs threshold below which the mic
is considered silent (default 600), VAD_SILENCE_MS=N sets the dwell
time in milliseconds (default 1500).

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/talk.py
"""

from __future__ import annotations

import io
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

# Importing config triggers load_dotenv(...), so API_KEY shows up in os.environ.
from server import config as _config_unused  # noqa: F401

BASE = os.environ.get("VOICE_AI_BASE", "http://127.0.0.1:5000")
API_KEY = os.environ.get("API_KEY", "").strip()
# Set VOICE_AI_SESSION_ID=<id> to opt into multi-turn memory. Empty = stateless.
SESSION_ID = os.environ.get("VOICE_AI_SESSION_ID", "").strip()
LAST_RECORDING_PATH = Path(__file__).resolve().parent.parent / "samples" / "_last_recording.wav"
QUIET_PEAK_THRESHOLD = 500  # int16 abs max; below this, mic is effectively silent

# VAD knobs. Threshold is in int16 abs space (0..32767). Quiet rooms floor
# around 100-200, breathing around 200-400, normal speech 1500+. 600 ignores
# breathing without clipping quiet talkers. Silence dwell time of 1500ms
# matches the web client default (4A).
VAD_ENABLED = os.environ.get("VOICE_AI_VAD", "1").strip() not in ("0", "false", "False", "")
VAD_PEAK_THRESHOLD = int(os.environ.get("VAD_PEAK_THRESHOLD", "600"))
VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "1500"))


def find_input_config() -> tuple[int, int]:
    """Probe the default input device for a (samplerate, channels) combo it'll actually open.

    Windows MME drivers on USB audio cards often refuse mono+16kHz. We try a few
    common configs in order of upload-friendliness and return the first that opens.
    """
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
            continue

    raise RuntimeError(
        f"Could not open the default input device in any tried config "
        f"({candidates}). Last error: {last_err!r}\n"
        f"Common causes: another app is holding the mic exclusively "
        f"(Voicemeeter, Discord, OBS), or the mic is muted in Windows."
    )


# Single owner of stdin. Multiple parts of the loop need to "wait for Enter"
# (the start prompt, plus the in-recording manual-override). If each one spawned
# its own input() thread, leftover daemons from VAD-aborted recordings would
# race the next prompt for the same stdin newline and randomly eat user input.
# A persistent reader thread routes every line through this queue; callers
# block on the queue (at the start prompt) or poll it with a timeout (during
# recording).
_stdin_q: "queue.Queue[str | None]" = queue.Queue()
_stdin_thread_started = False


def _ensure_stdin_thread() -> None:
    global _stdin_thread_started
    if _stdin_thread_started:
        return
    _stdin_thread_started = True

    def reader() -> None:
        while True:
            try:
                line = input()
            except (KeyboardInterrupt, EOFError):
                _stdin_q.put(None)
                return
            _stdin_q.put(line)

    threading.Thread(target=reader, daemon=True).start()


def _wait_enter() -> None:
    """Block forever until the user presses Enter (or the reader sees EOF/^C)."""
    line = _stdin_q.get()
    if line is None:
        raise KeyboardInterrupt()


def _poll_enter(timeout_s: float) -> bool:
    """Returns True if Enter arrived within timeout_s; False otherwise."""
    try:
        line = _stdin_q.get(timeout=timeout_s)
    except queue.Empty:
        return False
    if line is None:
        raise KeyboardInterrupt()
    return True


def record_until_stop(sr: int, channels: int,
                      vad: bool = True) -> tuple[np.ndarray, str]:
    """Stream from the default mic until either the user presses Enter or
    VAD detects silence-after-speech. Returns (int16 mono PCM, stop_reason).

    stop_reason is one of:
      - "manual"  user pressed Enter (always available, even with VAD on)
      - "vad"     VAD silence-after-speech timeout fired

    VAD state machine inside audio_cb:
      Before first peak >= VAD_PEAK_THRESHOLD: ignore silence (don't auto-stop;
      otherwise the timer fires before the user has spoken).
      After speech detected: track most-recent voice frame; if elapsed since
      that frame exceeds VAD_SILENCE_MS, set the stop event with reason="vad".

    audio_cb runs in a sounddevice driver thread. The main thread polls the
    stdin queue with a short timeout so it can exit promptly when audio_cb
    sets the stop event.
    """
    _ensure_stdin_thread()
    chunks: list[np.ndarray] = []
    stop = threading.Event()
    stop_reason = ["manual"]  # mutable container so audio_cb can write through

    speech_started = [False]
    last_voice_t = [0.0]

    def audio_cb(indata, frames, time_info, status):
        chunks.append(indata.copy())
        if not vad or stop.is_set():
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

    with sd.InputStream(samplerate=sr, channels=channels, dtype="int16", callback=audio_cb):
        while not stop.is_set():
            # Poll for Enter; small timeout so we exit quickly when VAD fires.
            if _poll_enter(0.05):
                stop_reason[0] = "manual"
                stop.set()
                break

    if not chunks:
        return np.array([], dtype=np.int16), stop_reason[0]
    audio = np.concatenate(chunks)
    if channels > 1:
        audio = audio.mean(axis=1).astype(np.int16)
    else:
        audio = audio.flatten()
    return audio, stop_reason[0]


def pcm_to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


def _auth_headers() -> dict:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def health() -> dict | None:
    try:
        r = requests.get(f"{BASE}/health", timeout=3)
        if r.status_code == 200:
            return r.json()
    except requests.exceptions.RequestException:
        pass
    return None


def main() -> int:
    h = health()
    if h is None:
        print(f"ERROR: cannot reach server at {BASE}")
        print("Run `python -m server.app` in another terminal, wait for 'Listening on...', then re-run.")
        return 1

    in_idx = sd.default.device[0]
    try:
        in_name = sd.query_devices(in_idx)["name"]
    except Exception:
        in_name = "(default)"

    try:
        sr, ch = find_input_config()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2

    _ensure_stdin_thread()

    vad_status = (f"on (peak>{VAD_PEAK_THRESHOLD}, silence>{VAD_SILENCE_MS}ms)"
                  if VAD_ENABLED else "off")
    print(f"Mic:    [{in_idx}] {in_name}")
    print(f"Recording: {sr} Hz, {ch} channel(s)" + ("  -> downmixed to mono" if ch > 1 else ""))
    print(f"Server: {BASE}  auth={'on' if API_KEY else 'off'}  "
          f"session={SESSION_ID or '(stateless)'}  "
          f"models={h.get('models_loaded', {})}")
    print(f"VAD:    {vad_status}  (set VOICE_AI_VAD=0 to disable)")
    print()
    print("=" * 60)
    print("  PUSH-TO-TALK LOOP:")
    print("    1. Press Enter to START recording")
    print("    2. SPEAK your question")
    if VAD_ENABLED:
        print(f"    3. Stop talking — auto-sends after {VAD_SILENCE_MS}ms silence")
        print("       (or press Enter to send immediately)")
    else:
        print("    3. Press Enter again to STOP and send")
    print("  Ctrl+C to quit.")
    print("=" * 60)
    print()

    while True:
        print("> Press Enter to start recording...", end="", flush=True)
        try:
            _wait_enter()
        except KeyboardInterrupt:
            print()
            return 0
        print()

        print()
        print("  >>>>>>>>>>  RECORDING — SPEAK NOW  <<<<<<<<<<")
        if VAD_ENABLED:
            print(f"             Auto-sends after {VAD_SILENCE_MS}ms silence "
                  "(or press Enter to send now).")
        else:
            print("             Press Enter to send when you're done.")
        print()
        try:
            audio, stop_reason = record_until_stop(sr, ch, vad=VAD_ENABLED)
        except KeyboardInterrupt:
            print("\n  cancelled.\n")
            return 0
        except Exception as exc:
            print(f"  recording failed: {exc!r}\n")
            continue

        duration = len(audio) / sr if sr > 0 else 0.0
        peak = int(np.abs(audio).max()) if audio.size else 0
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
        print(f"  Captured {duration:.1f}s @ {sr} Hz.  peak={peak}/32767 ({peak/327.67:.1f}%)  "
              f"rms={rms:.0f}  (stop={stop_reason})")

        if duration < 0.3:
            print("  (too short, try again)\n")
            continue

        wav = pcm_to_wav_bytes(audio, sr)
        try:
            LAST_RECORDING_PATH.write_bytes(wav)
        except Exception:
            pass

        if peak < QUIET_PEAK_THRESHOLD:
            print(f"  WARNING: peak={peak} is below {QUIET_PEAK_THRESHOLD} — mic seems silent.")
            print(f"  Check Windows: Sound settings -> Input -> device level. Then re-run.")
            print(f"  (saved last clip to {LAST_RECORDING_PATH.name} so you can play it back to verify)\n")
            continue

        print(f"  Sending {len(wav)/1024:.0f} KB to /converse...")

        form_data: dict[str, str] = {}
        if SESSION_ID:
            form_data["session_id"] = SESSION_ID

        t0 = time.perf_counter()
        try:
            r = requests.post(
                f"{BASE}/converse",
                files={"audio": ("user.wav", wav, "audio/wav")},
                data=form_data,
                headers=_auth_headers(),
                timeout=180,
            )
        except requests.exceptions.RequestException as exc:
            print(f"  request failed: {exc}\n")
            continue
        http_wall = time.perf_counter() - t0

        if r.status_code != 200:
            try:
                err = r.json().get("error", r.text)
            except Exception:
                err = r.text
            print(f"  server {r.status_code}: {err}\n")
            continue

        d = r.json()
        t = d["timings"]
        print()
        print(f"  You said:  {d['transcript']!r}")
        print(f"  Route:     {d['route']}  (model: {d['model']})")
        print(f"  Reply:     {d['reply_text']}")
        print(f"  Timings:   stt={t['stt']:.2f}  route={t['route']:.3f}  "
              f"llm={t['llm']:.2f}  tts={t['tts']:.2f}  total={t['total']:.2f}  http={http_wall:.2f}")
        if "session_file" in d:
            print(f"  Session:   {d['session_file']}")
        print(f"  (Reply audio playing through the server's default output)")
        print()


if __name__ == "__main__":
    sys.exit(main())
