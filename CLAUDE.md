# Voice AI Server

Workstation backend for a future ESP-32 + INMP441 voice device. Pipeline:
**audio → STT → router → simple-LLM or complex-LLM → TTS → speaker + ESP-32 OLED**.

The seven local-mode stages all shipped (see "Stage status" below). The
codebase is now split into a backend abstraction so a single `MODE` env
var picks the implementation set at process boot (see "MODE: local vs
cloud"). Original 7-stage plan: [plan.md](plan.md). Two-mode refactor
plan: [notes/two-mode-plan.md](notes/two-mode-plan.md).

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

## Plans (read these before starting work)

Each plan is a self-contained doc with its own decisions, phases, and
risks. Don't merge them — they reflect distinct chunks of work at
different statuses.

- [plan.md](plan.md) — original 7-stage local-mode build. **Shipped.**
- [notes/two-mode-plan.md](notes/two-mode-plan.md) — `MODE=local` vs `MODE=cloud` refactor (one codebase, two deploy shapes). **Phases 1, 2, 3, 4, 6, 8 shipped; 5 (Deepgram STT), 7 (OpenAI TTS), 9 (Fly.io deploy) queued behind cloud signups.**
- [notes/sessions-plan.md](notes/sessions-plan.md) — multi-turn conversation memory, server-side, idle-bounded sessions in markdown files. **All 5 phases (A-E) shipped.** Set `VOICE_AI_SESSION_ID` for talk.py, type a session ID into the web client's Session field, or pass `session_id` form/JSON field on `/converse` / `/chat` to opt in. Absent = stateless, default behavior preserved.
- [notes/voice-agent-plan.md](notes/voice-agent-plan.md) — auto-titling, tool use (web search + time), streaming TTS, VAD, continuous + interrupt mode, "hey jarvis" wake word. **Not yet started.** Six milestones in dependency order; each is a usable pause point.

## MODE: local vs cloud

The server picks one backend set at process boot via the `MODE` env var
(default `local`). One process = one mode; no runtime switching.

| | `MODE=local` (workstation) | `MODE=cloud` (VPS) |
|---|---|---|
| STT | faster-whisper (GPU) | Deepgram nova-3 *(Phase 5 — pending)* |
| Simple LLM | Ollama Llama 3.1 8B | Claude Haiku 4.5 |
| Complex LLM | Claude Sonnet 4.6 | Claude Sonnet 4.6 |
| TTS | Piper (local synth + sounddevice play) | OpenAI tts-1 (PCM, no local play) *(Phase 7 — pending)* |
| Router embedder | Ollama nomic-embed-text | sentence-transformers MiniLM-L6-v2 |
| Audio playback | yes (system default output) | no-op (server has no audio device) |

The factory in [server/modes.py](server/modes.py) returns the right
backend per call — `modes.get_stt()`, `modes.get_llm("simple"|"complex")`,
`modes.get_tts()`, `modes.get_embedder()`. Backend imports live INSIDE the
factory branches so cloud-mode boot doesn't pay the faster-whisper DLL
bootstrap and local-mode boot doesn't pay the ~250 MB torch import.

Cloud-only branches that aren't wired yet raise `NotImplementedError`
naming the phase that adds them. See [notes/two-mode-plan.md](notes/two-mode-plan.md)
for the full plan.

`/health` reports `mode` so clients can confirm what they're talking to.

Local-mode install uses the existing setup. Cloud-mode install uses
[requirements-cloud.txt](requirements-cloud.txt) — slim, no GPU, no Piper,
no faster-whisper.

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
   Pattern lives at the top of `server/backends/stt_whisper.py` — it MUST stay at module top, not inside the class init, so it runs before `from faster_whisper import WhisperModel`.

2. **Cold-start penalty ~6s on whisper** due to `sm_120` PTX JIT compile. Stage 4 server needs a warmup call at startup.

3. **VRAM budget is tight.** Whisper turbo (~2 GB) + Llama 8B Q5_K_M (~6 GB) ≈ 8.4 GB of 12 GB hot. ~3.5 GB headroom — acceptable, but don't add a third large model casually.

4. **Bluetooth audio adds 150–300 ms latency.** Calibrate stage 7 expectations accordingly.

5. **`load_dotenv()` must use `override=True`.** An empty `ANTHROPIC_API_KEY=` is set somewhere in the user's Windows environment; the default `load_dotenv()` call refuses to overwrite it, so the real key in `.env` is silently ignored. Always: `load_dotenv(REPO_ROOT / ".env", override=True)`. Pattern is in `tests/stage3_anthropic.py`.

6. **Use `127.0.0.1`, never `localhost`, for Ollama / any local HTTP.** On this Windows box, `localhost` resolution adds ~2 seconds per request (IPv6/DNS fallback). Ollama itself is fast — `total_duration` reports 15ms — but the client wall-clock is 2050ms. Discovered at stage 5: classify latency was 2050ms with `localhost`, dropped to 30ms with `127.0.0.1`. Default in `server/config.py:OLLAMA_HOST` is `http://127.0.0.1:11434`.

## Layout

```
.venv/                       Python 3.13 virtualenv
samples/jfk.wav              11s public-domain STT fixture
models/piper/                Piper voice .onnx + .json (local-mode TTS)
tests/stageN_*.py            per-stage local-mode smoke tests
tests/cloud_*.py             cloud-backend smoke tests (Phase 5/6/7/8)
tests/talk.py                push-to-talk client
server/
├── app.py                   Flask routes; main() warms each backend via modes
├── config.py                env loading + MODE validation
├── modes.py                 factory: get_stt/get_llm/get_tts/get_embedder
├── protocols.py             STT / LLM / TTS / Embedder Protocols
├── router.py                embedding-similarity classifier; embedder injected
├── markdown.py              to_speakable_text (markdown/LaTeX stripper)
├── audio.py                 pcm_to_wav, synth_to_wav (mode-independent)
├── audio_out.py             local sounddevice playback; cloud no-op
├── web/index.html           browser/phone client
└── backends/
    ├── stt_whisper.py       local — WhisperLocalSTT (+ DLL bootstrap)
    ├── llm_ollama.py        local-simple — OllamaLLM
    ├── llm_claude.py        both — ClaudeLLM(model=Sonnet | Haiku)
    ├── tts_piper.py         local — PiperTTS
    ├── embed_ollama.py      local — OllamaEmbedder (nomic-embed-text)
    └── embed_st.py          cloud — SentenceTransformerEmbedder (MiniLM-L6)
plan.md                      original 7-stage build plan
notes/two-mode-plan.md       two-mode refactor plan (1, 2, 3, 4, 6, 8 landed)
requirements-cloud.txt       slim cloud install (no GPU/Piper/Whisper)
```

## TODO when audio setup is finalized

Stage 6 ships using the **system default output device** (currently Razer USB Sound Card). When the speaker setup is finalized — Bose paired and set as default, or a specific device chosen — pin the device by name in `.env` so playback doesn't accidentally route to whatever is default at the moment (e.g. headphones plugging in). Mechanism: `sd.default.device = (None, "<substring of name>")` set from a `TTS_OUTPUT_DEVICE` env var in `server/config.py` and applied at the top of `server/audio_out.py` (the local-mode playback module). Avoid Hands-Free Bluetooth profiles (`Headset (...)` entries) — they run at 8 kHz and sound terrible for TTS. Use A2DP/stereo profile.

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

`CONVERSE_MAX_TOKENS=96` caps voice replies so they don't turn into 30s monologues. Tune via env.

`/health` includes `mode` (`local`|`cloud`) and `models_loaded` (per-backend `is_loaded()` snapshot).

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
