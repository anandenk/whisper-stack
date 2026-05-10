# whisper stack

A self-hosted voice assistant backend. Whisper for STT, a router on top
of two LLMs, Piper for streaming TTS — full mic-to-speaker loop on one
machine. Speak into a phone, a terminal, or a custom ESP-WROOM-32
voice device; the server transcribes, routes the query to the right
LLM, runs tools when needed, streams synthesized speech back.

**Status:** v1 shipped (May 2026) — end-to-end voice loop, seven shipped feature plans, working ESP-WROOM-32 hardware client.
**Built by:** Anand Enkhjin .
**Looking to fund:** v2 — battery-powered, enclosed, on-device wake word, USB-C charging.

In plain terms: you speak into a microphone, a speech-recognition model
transcribes the audio, the text is routed to either a small local AI
model (for simple or factual questions) or Anthropic's Claude (for
anything more complex — Claude can also call tools like web search when
relevant), and the reply is synthesized back to speech and played out.
The whole loop runs on one workstation; an optional custom hardware
device — currently an ESP-WROOM-32 with a microphone, OLED display, and
WiFi — talks to the workstation from across the room and lets you use
the assistant hands-free.

## What's next (v2)

V1 works but lives on a breadboard plugged into a USB cable. V2 turns
it into a self-contained device:

- **ESP32-S3 microcontroller** (replaces the ESP-WROOM-32). PSRAM
  unlocks on-device wake-word inference, eliminating the always-on
  network connection v1 needs.
- **LiPo battery + USB-C charging.** Multi-day runtime, recharges
  like a phone.
- **I²S amplifier (MAX98357) + 3W speaker.** Device speaks for itself
  instead of routing audio back to the workstation's speakers.
- **3D-printed enclosure** with acoustic separation between the
  microphone and the speaker — needed once the device makes its own
  sound.
- **WiFi provisioning over a captive portal** (no more re-flashing to
  change networks).
- **OTA firmware updates** so the device can be updated without
  opening the case.

**Estimated cost:** ~$50 in parts (ESP32-S3 dev board with built-in
LiPo charging, MAX98357, 3W speaker, LiPo cell, switches, enclosure
filament).
**Estimated time:** ~5 weekends, phased so each phase is independently
testable.
**Currently seeking funding** for parts + tooling.

```
audio in → STT → router → simple-LLM or complex-LLM → streaming TTS → audio out
                            ↓                  ↓
                    (Llama 3.1 / Haiku)  (Claude Sonnet
                                          + tools, when needed)
```

Four voice clients reach the same server, all working today: a self-
contained web page (any browser, including phones), a push-to-talk CLI,
a workstation-side "hey jarvis" wake-word loop, and a custom
ESP-WROOM-32 + INMP441 + SSD1306 voice device — bring-up through M6
shipped, hands-free wake + continuous follow-ups + live VU bar all
verified on real hardware.

## What works today

- **Two-stage routing.** Cheap local LLM (Llama 3.1 8B via Ollama) for
  conversational + factual queries; Claude Sonnet for anything complex
  (writing, code, multi-step reasoning). Embedding-similarity classifier
  decides per-query in ~30 ms. Tool-likely queries auto-escalate to Sonnet.
- **Streaming TTS** end-to-end. The web client hears the assistant's
  reply forming sentence-by-sentence ~1 second after the LLM starts
  generating, instead of waiting ~3 s for the whole reply. Sentence
  boundaries fed to Piper as they arrive; PCM chunks ship over Server-
  Sent Events.
- **Tools.** `get_current_time` (server-side, free) and Tavily-backed
  `web_search` (free tier 1k queries/mo). Set `TAVILY_API_KEY` in
  `.env` and "what's the weather in Chicago" works. Tool calls are
  recorded per-turn in the session file for observability.
- **Multi-turn conversation memory.** Markdown-backed sessions, idle-
  bounded (10-min default), one file per conversation under `sessions/`.
  Files auto-titled by Claude Haiku after the first turn.
- **Voice-activity detection** in the web client and `tests/talk.py` —
  auto-stop on silence-after-speech. No more "did I tap stop yet?"
  Tunable peak threshold + silence dwell; web client shows a live VAD
  meter for self-tuning.
- **Continuous + interrupt mode** in the web client. Tap once at the
  start, talk freely, the assistant takes its turn, mic auto-reopens
  for the next user turn. Talking over the assistant cuts it off and
  swings the mic back to you mid-reply (~50 ms).
- **Wake-word client** (`tests/jarvis.py`). Workstation-side "hey jarvis"
  via [openWakeWord](https://github.com/dscripka/openWakeWord). No tap,
  no Enter — say the wake word, ask, get a reply, follow-ups for 30 s of
  silence, then back to listening.
- **Two-mode architecture.** A single `MODE` env var picks the backend
  set: `local` (workstation, GPU, Ollama) or `cloud` (CPU-only, ready for
  a $5–10/mo VPS). Local mode is fully shipped; cloud mode is partial —
  Deepgram STT and OpenAI TTS backends raise `NotImplementedError` until
  signed up.
- **Four voice clients.** Self-contained web page at `GET /` (records on
  phone, plays through Web Audio); push-to-talk CLI (`tests/talk.py`);
  hands-free wake-word loop (`tests/jarvis.py`); and a custom
  **ESP-WROOM-32 + INMP441 mic + SSD1306 OLED** firmware
  ([`firmware/`](firmware/)) — hands-free "hey jarvis", continuous
  follow-ups, live VU bar showing your voice clearing the VAD floor,
  speaking-screen overlay with model name + token usage. WPA2-Enterprise
  WiFi verified on a real dorm network. The workstation's Realtek
  output speaks the reply — no on-device DAC in v1.
- **Per-process token / cost accounting.** `GET /health` returns a
  `usage` block with input/output tokens + USD-cost-since-boot per model.
  `/converse` also returns a per-call `estimated_cost_usd` so clients
  (e.g. the ESP-32 device) can show what each individual turn cost.
  Resets on restart; meant for "is this turning into a money fire"
  visibility, not billing accuracy.
- **Auth.** Optional `API_KEY` shared-secret on `/converse`,
  `/converse/stream`, `/chat`, `/transcribe`, `/speak`. `GET /` and
  `/health` stay public.
- **Cloudflare Tunnel runbook** for off-LAN access without port-forwarding.

## Quick start

> **macOS / Linux note:** the commands below are PowerShell-flavored. Substitute `source .venv/bin/activate` for the venv activate line and standard `mkdir -p` / `curl -L -o` for the model-download lines; everything else is portable Python.

You'll need:

- **Python 3.13.x** in a venv. The system Python on Windows defaults to
  3.14 which lacks the ML wheels we need. Don't use it.
- **Ollama** running on `http://127.0.0.1:11434` with
  `llama3.1:8b-instruct-q5_K_M` pulled (or whatever you set `OLLAMA_MODEL`
  to) and `nomic-embed-text` (the router embedder).
- **An NVIDIA GPU** for local mode (faster-whisper inference). Cloud mode
  is CPU-only.
- **An Anthropic API key** in `.env` as `ANTHROPIC_API_KEY=sk-ant-...`.

### Install (local mode)

```powershell
git clone https://github.com/anandenk/whisper-stack.git
cd whisper-stack

# Create the 3.13 venv
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flask python-dotenv requests numpy anthropic sounddevice piper-tts faster-whisper

# Copy and fill in your keys
cp .env.example .env
notepad .env

# Download a Piper voice model (en_GB-alan-medium is the project default)
mkdir -Force models\piper | Out-Null
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium"
curl.exe -sSL -o models\piper\en_GB-alan-medium.onnx      "$base/en_GB-alan-medium.onnx"
curl.exe -sSL -o models\piper\en_GB-alan-medium.onnx.json "$base/en_GB-alan-medium.onnx.json"

# Optional: install openwakeword + download bundled models for tests/jarvis.py
.\.venv\Scripts\python.exe -m pip install openwakeword
.\.venv\Scripts\python.exe -c "import openwakeword; openwakeword.utils.download_models()"
```

### Run

```powershell
.\.venv\Scripts\python.exe -m server.app
```

Wait for `Listening on http://127.0.0.1:5000`. First boot is slow (~15 s) —
faster-whisper does a PTX JIT compile on Blackwell GPUs and Ollama loads
the LLM into VRAM. Subsequent boots are warm.

### Talk to it

**Browser** (laptop or phone over the same network):
- Open `http://127.0.0.1:5000`
- (If you set `API_KEY`) paste it into the API-key field
- (Optional) type a session ID into the Session field for multi-turn memory
- (Optional) toggle **Continuous** to turn the button into a Start/End
  conversation toggle — the assistant takes its turn, mic auto-reopens,
  loop until you tap End. Talking over the assistant interrupts it.
- Tap **Tap to talk** (or **Start conversation** if Continuous is on)

**Push-to-talk CLI** (auto-stops on silence by default):
```powershell
.\.venv\Scripts\python.exe tests\talk.py
# Enter to start, then just stop talking — VAD auto-sends after 1.5 s.
# Set VOICE_AI_SESSION_ID for multi-turn memory.
# Set VOICE_AI_VAD=0 to fall back to the old "Enter twice" flow.
```

**Wake-word CLI** (hands-free, workstation-only):
```powershell
.\.venv\Scripts\python.exe tests\jarvis.py
# Say "hey jarvis", hear chime, ask. After each reply, the mic stays
# open for ~30 s so follow-ups don't need re-waking. Stay silent and
# the device drops back to wake-word listening.
```

**Remote** (phone on different WiFi, ESP-32 testing):
```powershell
# install once: winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:5000
# prints a https://<random>.trycloudflare.com URL — open on your phone
```

## Configuration

All config is via env vars, loaded from `.env` at process boot. See
[.env.example](.env.example) for the full list with `[both]` / `[local]` /
`[cloud]` tags. Highlights:

- `MODE=local | cloud` — backend set selection (default `local`)
- `API_KEY=...` — shared-secret auth (default unset = LAN-only)
- `TAVILY_API_KEY=...` — enables `web_search` tool (free tier 1k/mo)
- `PIPER_VOICE=en_GB-alan-medium` — TTS voice (any from the
  [Piper voice catalog](https://github.com/rhasspy/piper/blob/master/VOICES.md))
- `PIPER_LENGTH_SCALE=0.85` — speech rate; lower = faster (sane 0.7–1.1)
- `TTS_OUTPUT_DEVICE=Speakers (Realtek(R) Audio), MME` — pin TTS
  playback to a specific output device by name substring or integer
  index, so a Bluetooth headset plugging in doesn't reroute the
  assistant's voice. Empty = system default.
- `WHISPER_LANGUAGE=en` — pin STT language (empty = auto-detect, prone
  to misfires)
- `SESSION_IDLE_TIMEOUT_MIN=10` — gap that triggers a new session file
- `ROUTER_MARGIN_S=0.03` — embedding-router margin; higher → more
  queries to Sonnet (default tuned via `tests/router_eval.py`)
- `CONVERSE_MAX_TOKENS=96` — cap voice-mode reply length
- `TOOL_MAX_ROUNDS=5` — safety cap on tool-call iterations per request
- `WAKEWORD_THRESHOLD=0.5` — `tests/jarvis.py` wake detection sensitivity
- `WAKEWORD_CONVERSATION_TIMEOUT_S=30` — silence dwell after a reply
  before `jarvis.py` drops back to wake-word listening
- `VOICE_AI_VAD=1`, `VAD_PEAK_THRESHOLD=600`, `VAD_SILENCE_MS=1500` —
  `tests/talk.py` VAD knobs

## Project layout

```
.venv/                       Python 3.13 virtualenv (gitignored)
samples/                     audio fixtures (jfk.wav + test-generated _*.wav)
models/piper/                Piper voice .onnx + .json (gitignored)
sessions/                    conversation history, one .md per session (gitignored)
tests/stageN_*.py            per-feature smoke tests (run after server start)
tests/stage10_stream_*.py    streaming-TTS regression suite
tests/cloud_*.py             cloud-backend smoke tests
tests/talk.py                push-to-talk CLI (with VAD)
tests/jarvis.py              wake-word ("hey jarvis") CLI
server/
├── app.py                   Flask routes
├── config.py                env loading + MODE validation
├── modes.py                 backend factory: get_stt / get_llm / get_tts / get_embedder
├── protocols.py             STT / LLM / TTS / Embedder Protocols
├── router.py                embedding-similarity classifier + tool-likely heuristic
├── sessions.py              markdown session store
├── titler.py                Haiku-backed session auto-titling
├── streaming.py             SentenceChunker — feeds streaming TTS pipeline
├── usage.py                 per-process token / cost accumulator
├── tools/                   tool registry (get_current_time, web_search)
├── markdown.py              to_speakable_text (markdown/LaTeX stripper)
├── audio.py                 pcm_to_wav, synth_to_wav (mode-independent)
├── audio_out.py             local sounddevice playback (TTS_OUTPUT_DEVICE pin)
├── web/index.html           browser/phone client (streaming + VAD + continuous + interrupt)
└── backends/                STT/LLM/TTS/Embedder implementations
plan.md                      original 7-stage build plan
notes/two-mode-plan.md       local vs cloud refactor (mostly shipped)
notes/sessions-plan.md       multi-turn conversation memory (shipped)
notes/voice-agent-plan.md    streaming TTS, VAD, continuous, interrupt, wake (all shipped)
notes/esp32-firmware-plan.md ESP-WROOM-32 firmware plan (M1-M6 all shipped)
CLAUDE.md                    project instructions for Claude Code agents
firmware/                    ESP-IDF v5.3.5 project for ESP-WROOM-32
├── main/                    application code:
│   ├── main.c                state machine, button polling, OLED orchestration
│   ├── oled.c/.h             SSD1306 + 6×8 ASCII font
│   ├── wifi.c/.h             WPA2-PSK or WPA2-Enterprise STA, Kconfig-selected
│   ├── i2s_mic.c/.h          INMP441 → 16 kHz int16 ring buffer
│   ├── http_client.c/.h      esp_http_client wrapper for /converse (M3 streaming)
│   ├── vad.c/.h              peak-threshold VAD with cancel hook (M3)
│   ├── listen_stream.c/.h    raw-socket /listen client + 500 ms pre-roll (M4)
│   └── Kconfig.projbuild     WiFi creds, server addr, API key, session id
├── sdkconfig.defaults       committed defaults; idf.py menuconfig to fill creds
├── partitions: built-in two-OTA layout (CONFIG_PARTITION_TABLE_TWO_OTA)
└── .gitignore               build/, sdkconfig (creds), managed_components/
```

## Routes

| Route | Purpose |
|---|---|
| `GET /` | Streaming web client (Web Audio, VAD, continuous + interrupt) |
| `GET /health` | `{status, mode, gpu_available, models_loaded, usage}` — `usage` is the per-process token counter + cost estimate |
| `POST /transcribe` | Multipart `audio` → `{transcript, duration_s, latency_s, model, language}` |
| `POST /chat` | JSON `{message, session_id?, force_route?}` → routed LLM reply |
| `POST /speak` | JSON `{text}` → synth + blocking play through default output |
| `POST /converse` | One-shot voice round-trip; server plays reply locally when `play_locally=true`. Accepts multipart, raw `audio/l16`, or raw `audio/wav` body. Add `?omit_audio_base64=1` to drop the WAV from the JSON response (saves ~100–200 KB per turn for clients that don't need it — used by the ESP-32 firmware) |
| `POST /converse/stream` | SSE-streamed voice round-trip; client plays the audio. Accepts the same three Content-Types as `/converse`. The web client uses this |
| `POST /listen` | Chunked-transfer raw int16 LE @ 16 kHz body; server runs openWakeWord and returns `{"wake": true, ...}` on detection (or `{"wake": false, "reason": "idle_timeout" \| "client_closed"}` otherwise). Used by the ESP-32 firmware in IDLE state. Requires `pip install openwakeword` + `download_models()` on the server |

When `API_KEY` is set, every route except `GET /` and `GET /health`
requires an `X-API-Key` header. The web page persists the key in
`localStorage`; `talk.py` and `jarvis.py` read it from the loaded `.env`.

### Upload formats

`/converse` and `/converse/stream` accept three upload shapes, chosen by
`Content-Type`:

- **`multipart/form-data`** — file in the `audio` part; params
  (`force_route`, `session_id`, `play_locally`) in form fields. Used by
  the browser client and `talk.py`.
- **`audio/l16; rate=N; channels=N`** — raw int16 LE PCM in the body;
  params in the query string. Skips WAV-wrapping on the client. Used by
  the future ESP-WROOM-32 firmware (INMP441 captures int16 LE
  natively).
- **`audio/wav`** — pre-wrapped WAV in the body; params in the query
  string. Useful for tiny clients with a WAV fixture in flash.

## What's shipped vs. planned

### Shipped
- Original 7-stage local build (faster-whisper + Ollama + Piper +
  Sonnet) — see [plan.md](plan.md)
- Two-mode refactor — phases 1, 2, 3, 4, 6, 8 — see
  [notes/two-mode-plan.md](notes/two-mode-plan.md)
- Multi-turn sessions — all 5 phases (A–E) — see
  [notes/sessions-plan.md](notes/sessions-plan.md)
- **Voice-agent plan — all 6 milestones**: auto-titling, tool use,
  streaming TTS, VAD, continuous + interrupt, "hey jarvis" wake word —
  see [notes/voice-agent-plan.md](notes/voice-agent-plan.md)

- **ESP-WROOM-32 firmware — M1 through M6 all shipped on real
  hardware** ([`firmware/`](firmware/), ESP-IDF 5.3.5).
  - **M1** — full module skeleton: SSD1306 OLED via
    `esp_lcd_panel_ssd1306` + embedded 6×8 font, WiFi STA with
    **WPA2-PSK or WPA2-Enterprise** (Kconfig choice; verified on WPA2-Enterprise
    College's 802.1X dorm network with PEAP-MSCHAPv2), INMP441 mic
    capture with RMS/peak response.
  - **M2** — BOOT button → 3 s recording → `/converse` round-trip,
    workstation Realtek output speaks the reply. Preserved as a
    fallback under `#define M2_BUFFERED_FALLBACK`.
  - **M3** — VAD-bounded recording (peak ≥ 300, silence dwell 2.5 s)
    streamed over HTTP chunked-transfer — no heap cap on length.
    Long-press BOOT (≥ 1 s) cancels mid-recording.
  - **M4** — hands-free **"hey jarvis"** via server-side openWakeWord
    over a long-lived chunked POST to `/listen`, with a 500 ms pre-roll
    so the start of the question after the wake word isn't truncated.
    BOOT button retained as a manual-wake fallback. Server hardened
    with pre-warmed model, threaded mode, fast-fail request handler,
    and lock-acquire timeouts so a wedged client connection can't lock
    out other handlers.
  - **M5** — **continuous mode after wake**. Once a turn finishes
    speaking, the device stays open for a follow-up — no re-wake
    needed — for 30 s of silence (`WS_CONVERSATION_TIMEOUT_MS` in
    `main.c`), then drops back to /listen. Mirrors `tests/jarvis.py`
    Phase 6B.
  - **M6** — OLED polish: 2-second boot card (IP / server /
    wake model / session id), brief "WAKE / score N.NN" splash on
    each wake event, live VU bar during LISTENING with a threshold
    marker so you can see your voice clearing the VAD floor, and a
    SPEAKING screen that shows the model name + per-call USD cost
    + token usage instead of truncated transcript fragments. Cost
    comes from the server (`estimated_cost_usd` on `/converse`) so
    pricing lives in one place; local Llama replies print
    `cost  $0.0000`. Partial-row OLED flushes keep the bar
    flicker-free.
  - **Multi-turn memory** — firmware passes `?session_id=esp32` on
    `/converse`, so wakes (and follow-ups within continuous mode)
    remember context (10 min idle TTL by default).

### Planned but not shipped
- **Router improvements** — short queries (e.g. `"1+1"`) misroute to
  complex. Tiered investigation + plan documented at the top of
  [server/router.py](server/router.py); not started yet.
- **Two-mode plan phases 5, 7, 9** — Deepgram STT, OpenAI TTS, Fly.io
  deploy. Queued behind cloud-provider signups. Only relevant if you
  ever want to host the brain on a VPS instead of your workstation.
- **ElevenLabs Flash v2 TTS** — optional premium voice swap (footnote
  in voice-agent-plan).

See [`CLAUDE.md`](CLAUDE.md) and the per-plan docs in [`notes/`](notes/)
for the full roadmap and decisions log.

## Why this exists

Built as the server side of an eventual ESP-32 voice device — a small,
always-on, internet-connected button that talks to you. The codebase is
structured so the hardware client doesn't need to do anything clever:
post audio, get audio back. Two-mode architecture lets the same server
run on a workstation with a GPU OR (eventually) on a cheap VPS,
depending on whether you're willing to pay for cloud STT/TTS in
exchange for not running a 24/7 GPU machine.

It's also a pretty nice way to talk to Claude.
