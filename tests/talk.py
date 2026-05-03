"""Push-to-talk client for the voice AI server.

UX: press Enter to start recording, speak, press Enter again to send.
The reply text prints to this terminal; the reply audio plays automatically
through the server's default output device. Ctrl+C to quit.

Requires the server to be running:
    python -m server.app

Run from repo root after activating .venv:
    python tests/talk.py
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd

# Importing tts triggers config.load_dotenv(...), so API_KEY shows up in os.environ.
from server import tts as _tts_unused  # noqa: F401

BASE = os.environ.get("VOICE_AI_BASE", "http://127.0.0.1:5000")
API_KEY = os.environ.get("API_KEY", "").strip()
LAST_RECORDING_PATH = Path(__file__).resolve().parent.parent / "samples" / "_last_recording.wav"
QUIET_PEAK_THRESHOLD = 500  # int16 abs max; below this, mic is effectively silent


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


def record_until_enter(sr: int, channels: int) -> np.ndarray:
    """Stream from default mic until user presses Enter. Returns int16 mono PCM."""
    chunks: list[np.ndarray] = []
    stop = threading.Event()

    def audio_cb(indata, frames, time_info, status):
        chunks.append(indata.copy())

    def wait_for_enter():
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            pass
        stop.set()

    threading.Thread(target=wait_for_enter, daemon=True).start()

    with sd.InputStream(samplerate=sr, channels=channels, dtype="int16", callback=audio_cb):
        stop.wait()

    if not chunks:
        return np.array([], dtype=np.int16)
    audio = np.concatenate(chunks)
    if channels > 1:
        # Downmix to mono so the server gets a single-channel WAV
        audio = audio.mean(axis=1).astype(np.int16)
    else:
        audio = audio.flatten()
    return audio


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

    print(f"Mic:    [{in_idx}] {in_name}")
    print(f"Recording: {sr} Hz, {ch} channel(s)" + ("  -> downmixed to mono" if ch > 1 else ""))
    print(f"Server: {BASE}  auth={'on' if API_KEY else 'off'}  models={h.get('models_loaded', {})}")
    print()
    print("=" * 60)
    print("  PUSH-TO-TALK LOOP:")
    print("    1. Press Enter to START recording")
    print("    2. SPEAK your question")
    print("    3. Press Enter again to STOP and send")
    print("  Ctrl+C to quit.")
    print("=" * 60)
    print()

    while True:
        try:
            input("> Press Enter to start recording...")
        except (KeyboardInterrupt, EOFError):
            print()
            return 0

        print()
        print("  >>>>>>>>>>  RECORDING — SPEAK NOW  <<<<<<<<<<")
        print("             Press Enter to send when you're done.")
        print()
        try:
            audio = record_until_enter(sr, ch)
        except Exception as exc:
            print(f"  recording failed: {exc!r}\n")
            continue

        duration = len(audio) / sr if sr > 0 else 0.0
        peak = int(np.abs(audio).max()) if audio.size else 0
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0.0
        print(f"  Captured {duration:.1f}s @ {sr} Hz.  peak={peak}/32767 ({peak/327.67:.1f}%)  rms={rms:.0f}")

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

        t0 = time.perf_counter()
        try:
            r = requests.post(
                f"{BASE}/converse",
                files={"audio": ("user.wav", wav, "audio/wav")},
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
        print(f"  (Reply audio playing through the server's default output)")
        print()


if __name__ == "__main__":
    sys.exit(main())
