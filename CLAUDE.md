# Voice AI Server

Workstation backend for a future ESP-32 + INMP441 voice device. Pipeline:
**audio → faster-whisper STT → router → Llama 3.1 8B (local) or Claude API → Piper TTS → Bose BT speaker + ESP-32 OLED**.

Full plan with all 7 stages: see [plan.md](plan.md).

## Environment

- **Working dir:** `C:\voice-ai-server`
- **Venv:** `.venv` — **Python 3.13.13** (the system `python` is 3.14, which lacks ML wheels — do NOT use it)
- **Run scripts directly via** `.venv/Scripts/python.exe tests/stageN_*.py` (or activate the venv first)
- **GPU:** RTX 5070 Ti **Laptop**, Blackwell `sm_120`, 12 GB VRAM
- **Ollama** running on `http://localhost:11434`, model `llama3.1:8b-instruct-q5_K_M` pre-pulled

## Stage status

| # | Stage | State |
|---|---|---|
| 1 | faster-whisper GPU | ✅ verified — RTF ~7.8x, `tests/stage1_whisper.py` |
| 2 | Ollama Llama 3.1 8B | ✅ verified — ~90 TPS, 100% GPU, `tests/stage2_ollama.py` |
| 3 | Anthropic API smoke | ✅ verified — `claude-sonnet-4-6`, ~1.8s, `tests/stage3_anthropic.py` |
| 4 | Flask `/transcribe` + `/chat` + `/health` | ✅ verified — warm transcribe ~0.4s, chat ~1.5s, `tests/stage4_flask.py` |
| 5 | Router + dual-LLM `/chat` | ✅ verified — embedding similarity, ~30ms classify, both models VRAM-resident, `tests/stage5_router.py` |
| 6 | Piper TTS + `/speak` | ✅ verified — 22050 Hz mono, RTF 37x warm, `tests/stage6_tts.py` |
| 7 | `/converse` end-to-end | ✅ verified — full pipeline 1.1–3.6s, `tests/stage7_e2e.py` |

Each stage leaves a re-runnable smoke test in `tests/`.

## Stage gates

Hard stop at the end of every stage to check in with the user. Do not progress autonomously between stages — push back on bad assumptions before moving on.

## Critical gotchas

1. **CTranslate2 Windows wheels do NOT bundle cuBLAS/cuDNN.** Before `import faster_whisper`, add the pip-installed NVIDIA DLL dirs to the search path:
   ```python
   _nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
   if _nvidia_root.exists():
       for _bin in _nvidia_root.glob("*/bin"):
           os.add_dll_directory(str(_bin))
   ```
   Pattern is already in `tests/stage1_whisper.py` — reuse it in `server/stt.py` when stage 4 lands.

2. **Cold-start penalty ~6s on whisper** due to `sm_120` PTX JIT compile. Stage 4 server needs a warmup call at startup.

3. **VRAM budget is tight.** Whisper turbo (~2 GB) + Llama 8B Q5_K_M (~6 GB) ≈ 8.4 GB of 12 GB hot. ~3.5 GB headroom — acceptable, but don't add a third large model casually.

4. **Bluetooth audio adds 150–300 ms latency.** Calibrate stage 7 expectations accordingly.

5. **`load_dotenv()` must use `override=True`.** An empty `ANTHROPIC_API_KEY=` is set somewhere in the user's Windows environment; the default `load_dotenv()` call refuses to overwrite it, so the real key in `.env` is silently ignored. Always: `load_dotenv(REPO_ROOT / ".env", override=True)`. Pattern is in `tests/stage3_anthropic.py`.

6. **Use `127.0.0.1`, never `localhost`, for Ollama / any local HTTP.** On this Windows box, `localhost` resolution adds ~2 seconds per request (IPv6/DNS fallback). Ollama itself is fast — `total_duration` reports 15ms — but the client wall-clock is 2050ms. Discovered at stage 5: classify latency was 2050ms with `localhost`, dropped to 30ms with `127.0.0.1`. Default in `server/config.py:OLLAMA_HOST` is `http://127.0.0.1:11434`.

## Layout

```
.venv/                  Python 3.13 virtualenv
samples/jfk.wav         11s public-domain STT fixture
tests/stageN_*.py       per-stage standalone smoke tests
server/                 config.py, stt.py, llm.py, app.py (router.py + tts.py land at stages 5/6)
notes/                  (empty until stage 5) — router-design.md goes here first, before code
plan.md                 full 7-stage plan
```

## TODO when audio setup is finalized

Stage 6 ships using the **system default output device** (currently Razer USB Sound Card). When the speaker setup is finalized — Bose paired and set as default, or a specific device chosen — pin the device by name in `.env` so playback doesn't accidentally route to whatever is default at the moment (e.g. headphones plugging in). Mechanism: `sd.default.device = (None, "<substring of name>")` set from a `TTS_OUTPUT_DEVICE` env var in `server/config.py` and applied in `server/tts.py`. Avoid Hands-Free Bluetooth profiles (`Headset (...)` entries) — they run at 8 kHz and sound terrible for TTS. Use A2DP/stereo profile.

## Running the server

```
.venv/Scripts/python.exe -m server.app
```

Warmup runs at boot (~10s on cold start to amortize PTX JIT). Disable with `WARMUP=0` env var. Default bind: `127.0.0.1:5000`.

Routes:
- `GET /health` — model load status
- `POST /transcribe` — multipart `audio` → `{transcript, duration_s, latency_s, model, language}`
- `POST /chat` — JSON `{message, force_route?}` → router-dispatched LLM reply with `route`, `router_scores`
- `POST /speak` — JSON `{text}` → synth + blocking play through default output
- `POST /converse` — multipart `audio` (+ optional `force_route` form field) → STT → router → LLM → TTS → JSON with `transcript`, `route`, `reply_text`, `audio_base64` (WAV), per-stage `timings`, plus non-blocking local playback

`CONVERSE_MAX_TOKENS=192` caps voice replies so they don't turn into 30s monologues. Tune via env.

## Talking to it (push-to-talk client)

```
.venv/Scripts/python.exe -m server.app    # terminal 1, wait for "Listening on..."
.venv/Scripts/python.exe tests/talk.py    # terminal 2
```

Inside `talk.py`: press **Enter** to start recording, speak, press **Enter** again to send. Reply text prints, audio plays through the server's default output. Ctrl+C to quit. Records at 16 kHz mono; falls back to mic default sample rate if unsupported. Records until you press Enter — no fixed duration limit.

If `API_KEY` is set in `.env`, `talk.py` reads it from the loaded environment and sends `X-API-Key` automatically.

## Web client (any browser, including phone)

`GET /` serves a self-contained HTML page that records from the device mic via MediaRecorder, posts to `/converse` with `play_locally=false` (so the workstation doesn't speak the reply too), and plays the returned audio in-browser. The page persists the API key in localStorage. Mic access requires HTTPS — works directly via Cloudflare Tunnel or via `http://localhost:5000` from the same machine.

## Exposing the server publicly via Cloudflare Tunnel

For testing the ESP-32-style flow from anywhere (e.g., phone on different WiFi):

1. Install `cloudflared`:
   ```
   winget install --id Cloudflare.cloudflared
   ```
2. Generate an API key and add to `.env`:
   ```
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Put the result in `.env` as `API_KEY=...`. Restart the server.
3. Run the tunnel alongside the server:
   ```
   cloudflared tunnel --url http://127.0.0.1:5000
   ```
   It prints a URL like `https://forty-prawn-2351.trycloudflare.com`. That URL is reachable from anywhere on the internet, terminates HTTPS at Cloudflare, and forwards to your localhost via an outbound tunnel. No port-forwarding needed.
4. On your phone, open that URL. Paste the API key into the page's input. Tap to talk.

The `cloudflared` quick-mode URL changes every time you restart it. For a stable URL, register a free Cloudflare account, attach a domain, and create a named tunnel — see Cloudflare docs.

**Auth model:** when `API_KEY` is set, all of `/converse`, `/chat`, `/transcribe`, `/speak` require the matching `X-API-Key` header. `GET /` and `GET /health` stay public. When `API_KEY` is empty, no auth is enforced (LAN-only mode).
