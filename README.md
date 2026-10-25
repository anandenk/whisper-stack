# whisper stack

> **Revival workspace, October 2026:** Start with
> [the current handoff](docs/STATUS.md) and
> [the project decisions](docs/PROJECT.md). Coding agents should first read
> [AGENTS.md](AGENTS.md).
>
> The rebuild backend is in [backend/](backend/README.md): ElevenLabs STT/TTS,
> Claude Haiku 5.5 replies, and local token/cost diagnostics. Live STT, TTS, Haiku,
> and the complete buffered voice loop have passed checks using known synthesized
> speech. Microphone/hardware checks remain pending.
> A local [push-to-talk tester](backend/README.md#local-push-to-talk-tester) is
> available at `/tester`, with recording preview, reply playback, and diagnostics.
> Automated browser checks use synthetic audio and mocked providers.
> See the handoff for
> exact evidence and setup. The existing server/firmware and the description
> below document the May prototype and serve as historical reference.

A self-hosted voice assistant backend. Whisper for STT, Gemini 2.5
Flash for the LLM, Kokoro for streaming TTS — full mic-to-speaker
loop on one machine. Speak into a phone, a terminal, or a custom
ESP32-S3 voice device; the server transcribes, asks Gemini, runs
tools when needed, streams synthesized speech back.

**Status:** v1 shipped (May 2026, ESP-WROOM-32) — end-to-end voice
loop, M1–M8 + cat-face panel all on real hardware. v2 hardware
arrived 2026-05-12 and **v2-M1, M2, M6 shipped same day**: ESP32-S3
chip port, on-device wake word ("Hey Kira" via esp-sr WakeNet),
and AFE-driven mic-quality pipeline. **v2-M3/M4/M5 deferred** until
the MAX98357A DAC is soldered up (those add on-device audio out,
LiPo battery, and AEC respectively).
**Built by:** Anand Enkhjin .
**v3 prototyping** in tree (Kconfig-gated): RoboEyes face renderer,
SG90 head servo with eye-leads / head-follows gaze coupling. v3
custom-PCB hardware not yet ordered.

In plain terms: you speak into a microphone, a speech-recognition
model transcribes the audio, the text is sent to Google's Gemini
2.5 Flash (which can call tools and ground answers in live Google
Search when relevant), and the reply is synthesized back to speech.
The whole loop runs on one workstation; an optional custom hardware
device — currently an **ESP32-S3 with a microphone, two OLED panels,
and WiFi** — talks to the workstation from across the room and lets
you use the assistant hands-free.

```
audio in → STT → Gemini 2.5 Flash (with tools) → streaming TTS → audio out
                  ↑
       get_current_time  +  google_search (built-in)
```

Three voice clients reach the same server, all working today: a
self-contained web page (any browser, including phones), a
push-to-talk CLI, and the custom **ESP32-S3 + INMP441 + SSD1306×2**
voice device.

## What works today

- **Single-LLM stack.** Gemini 2.5 Flash handles every query. The
  previous routing-on-top-of-two-LLMs design was retired in May
  2026 — see "Migration history" below.
- **On-device wake word ("Hey Kira").** The v2 firmware runs
  Espressif's esp-sr WakeNet model `wn9_heykira_tts3` directly on
  the ESP32-S3 chip — no continuous network stream needed.
  Eliminated the chronic exposure that was the v1 firmware's
  biggest source of audio corruption (gotcha #9).
- **AFE-driven mic pipeline.** The mic feeds through Espressif's
  Audio Front End (noise suppression + AGC + ML VAD via
  `vadnet1_medium`) so typing, clicks, and other transient noise
  don't trigger false turns; Whisper sees denoised audio.
- **Streaming TTS** end-to-end. The web client hears the reply
  forming sentence-by-sentence ~1 second after the LLM starts
  generating, instead of waiting ~3 s for the whole reply.
- **Kokoro 82M TTS (`af_bella` voice).** Replaced Piper for a
  noticeably more natural voice. Piper backend stays in tree as
  a one-line fallback (`server/modes.py`).
- **Tools.** `get_current_time` (server-side, instant) and Gemini's
  built-in `google_search` grounding for current-info queries —
  no third-party search account.
- **Multi-turn conversation memory.** Markdown-backed sessions,
  idle-bounded (10 min default), one file per conversation under
  `sessions/`. Auto-titled by Gemini after the first turn.
- **Web client voice-activity detection** — auto-stop on
  silence-after-speech, with a live VAD meter for self-tuning.
- **Continuous mode in the web client only.** The ESP32-S3 firmware
  ships with continuous mode gated off by default
  (`CONFIG_WS_CONTINUOUS_MODE_ENABLED`) — each turn requires a fresh
  "Hey Kira" wake. This is the same default Alexa / Siri use, since
  in a shared room continuous mode picks up every nearby voice as
  input. Flip the Kconfig to opt in for solo-use scenarios.
- **Three voice clients.** Self-contained web page at `GET /`
  (records on phone, plays through Web Audio); push-to-talk CLI
  (`tests/talk.py`); and the custom **ESP32-S3 + INMP441 mic + two
  SSD1306 OLEDs** firmware ([`firmware/`](firmware/)) — on-device
  "Hey Kira", speaking-screen overlay with model + token cost.
  WPA2-Enterprise WiFi verified on an 802.1X campus network.
  Workstation Realtek output speaks the reply (on-device DAC is
  v2-M3, not yet wired).
- **Per-process token / cost accounting.** `GET /health` returns
  `usage` with input/output tokens + USD-cost-since-boot per model.
  `/converse` also returns a per-call `estimated_cost_usd` for
  per-turn cost display.
- **Auth.** Optional `API_KEY` shared-secret on `/converse`,
  `/converse/stream`, `/chat`, `/transcribe`, `/speak`.
  `GET /` and `/health` stay public.
- **Cloudflare Tunnel runbook** for off-LAN access without
  port-forwarding.

## What's pending (v2)

The three v2 milestones still blocked on hardware that isn't
soldered yet:

- **M3 — MAX98357A on-device audio out.** I²S DAC + 3 W speaker on
  GPIO 9/10/11 (already reserved in the pin map). The device
  speaks for itself instead of routing audio through the
  workstation. Includes I²S1 driver, PCM playback queue, and
  switching from `/converse` (buffered JSON) to `/converse/stream`
  (SSE chunks) for lower TTFB.
- **M4 — LiPo battery + USB-C charging.** Multi-day untethered
  runtime. Battery monitoring on a reserved ADC pin, light-sleep
  states.
- **M5 — AEC (acoustic echo cancellation).** Depends on M3. Lets
  the device listen continuously during SPEAKING (interrupt-by-
  voice). Today the SPEAKING phase keeps wake events suppressed
  until TTS finishes; AEC would let us drop that gate.

Code skeletons + pin reservations already exist; landing each
milestone is mostly wiring + a couple hundred lines of firmware.

## Migration history

The project shipped originally with a two-LLM routing architecture
(Ollama Llama 3.1 8B for "simple" queries, Claude Sonnet for
"complex"), plus a parallel two-mode (`MODE=local | cloud`) split
that would have used Claude Haiku + Deepgram + OpenAI TTS on a VPS.
In **May 2026** the project was simplified down to single-LLM
Gemini 2.5 Flash:

- Routing layer, embedding classifier, and tool-likely escalation
  removed.
- Ollama, Anthropic, and sentence-transformers dependencies removed.
- Tavily-based `web_search` replaced by Gemini's built-in
  `google_search` grounding.
- `MODE` env var, `requirements-cloud.txt`, and the cloud-only
  Deepgram/OpenAI placeholders all retired.

A `pre-ollama-removal` git tag bookmarks the last commit with the
full multi-LLM stack — see `CLAUDE.md` § "Ollama resurrection" for
the 5-minute recipe to bring any of the old backends back.

The v1 ESP-WROOM-32 firmware is preserved at git tag
`pre-esp32-s3-port` — check that tag out to rebuild for the
original DOIT V1 board.

## Quick start

> **macOS / Linux note:** commands below are PowerShell-flavored.
> Substitute `source .venv/bin/activate` for the venv activate line
> and `curl -L -o` for `Invoke-WebRequest`; everything else is
> portable Python.

You'll need:

- **Python 3.13.x** in a venv. Windows system Python 3.14 lacks the
  ML wheels we need — don't use it.
- **An NVIDIA GPU** for faster-whisper STT.
- **A Google AI Studio API key.** Free tier covers casual use;
  Gemini 2.5 Flash paid pricing is fractions of a cent per voice
  turn. Get one at
  [aistudio.google.com](https://aistudio.google.com) →
  "Get API key", drop it in `.env` as `GOOGLE_API_KEY=...`.
- **espeak-ng** installed system-wide on Windows (Kokoro's
  phonemization runtime). Grab the `.msi` from
  [espeak-ng releases](https://github.com/espeak-ng/espeak-ng/releases).

### Install

```powershell
git clone https://github.com/anandenk/whisper-stack.git
cd whisper-stack

py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Copy and fill in your keys
cp .env.example .env
notepad .env

# Download Kokoro model + voices (one-time, ~350 MB)
mkdir -Force models\kokoro | Out-Null
$base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
curl.exe -sSL -o models\kokoro\kokoro-v1.0.onnx "$base/kokoro-v1.0.onnx"
curl.exe -sSL -o models\kokoro\voices-v1.0.bin "$base/voices-v1.0.bin"
```

### Run

```powershell
.\.venv\Scripts\python.exe -m server.app
```

Wait for `Listening on http://127.0.0.1:5000`. First boot is slow
(~10 s) — faster-whisper does a PTX JIT compile on Blackwell GPUs,
and Kokoro warms its model. Subsequent boots are warm.

### Talk to it

**Browser** (laptop or phone over the same network):
- Open `http://127.0.0.1:5000`
- (If you set `API_KEY`) paste it into the API-key field
- (Optional) type a session ID into the Session field for multi-turn
  memory
- (Optional) toggle **Continuous** to keep the mic open for
  follow-ups
- Tap **Tap to talk**

**Push-to-talk CLI** (auto-stops on silence by default):
```powershell
.\.venv\Scripts\python.exe tests\talk.py
# Enter to start, then stop talking — VAD auto-sends after 1.5 s.
# Set VOICE_AI_SESSION_ID for multi-turn memory.
```

**Remote** (phone on different WiFi, ESP-32 testing):
```powershell
# install once: winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:5000
# prints a https://<random>.trycloudflare.com URL — open on your phone
```

## Configuration

All config is via env vars, loaded from `.env` at process boot.
See [.env.example](.env.example) for the full list grouped by
subsystem. Highlights:

- `GOOGLE_API_KEY=...` — required (Gemini)
- `GEMINI_MODEL=gemini-2.5-flash` — default model
- `API_KEY=...` — shared-secret auth on protected routes (default
  unset = LAN-only)
- `KOKORO_VOICE=af_bella` — TTS voice. Other stock voices include
  `af_sky`, `af_nicole`, `af_sarah`, `am_adam`, `bf_*` British,
  etc. Full list returned by `Kokoro.get_voices()` at runtime.
- `KOKORO_SPEED=1.15` — playback speed (1.0 = natural; higher =
  faster). 1.15 is the project default — feels brisker without
  becoming chipmunky.
- `PIPER_VOICE` / `PIPER_LENGTH_SCALE` — only used if you revert
  to Piper in `server/modes.py`. Kokoro is the default now.
- `TTS_OUTPUT_DEVICE=Speakers (Realtek(R) Audio), MME` — pin TTS
  playback to a specific output device. Empty = system default.
- `WHISPER_LANGUAGE=en` — pin STT language (empty = auto-detect,
  prone to misfires)
- `SESSION_IDLE_TIMEOUT_MIN=10` — gap that triggers a new session
  file
- `CONVERSE_MAX_TOKENS=96` — cap voice-mode reply length
- `TOOL_MAX_ROUNDS=5` — safety cap on tool-call iterations per
  request

## Project layout

```
.venv/                       Python 3.13 virtualenv (gitignored)
samples/                     audio fixtures
models/piper/                Piper voice .onnx + .json (gitignored, fallback only)
models/kokoro/               Kokoro ONNX model + voices bin (gitignored)
sessions/                    conversation history, one .md per session (gitignored)
tests/talk.py                push-to-talk CLI (with VAD)
tests/stage_*.py             per-feature smoke tests
server/
├── app.py                   Flask routes
├── config.py                env loading
├── modes.py                 backend factory: get_stt / get_llm / get_tts
├── protocols.py             STT / LLM / TTS Protocols
├── sessions.py              markdown session store
├── titler.py                Gemini-backed session auto-titling
├── streaming.py             SentenceChunker — feeds streaming TTS
├── usage.py                 per-process token / cost accumulator
├── tools/                   tool registry (get_current_time)
├── markdown.py              to_speakable_text (markdown/LaTeX stripper)
├── audio.py                 pcm_to_wav, synth_to_wav
├── audio_out.py             local sounddevice playback
├── web/index.html           browser/phone client
└── backends/
    ├── stt_whisper.py       WhisperLocalSTT
    ├── llm_gemini.py        GeminiLLM
    ├── tts_kokoro.py        KokoroTTS (default)
    └── tts_piper.py         PiperTTS (fallback)
plan.md                      original 7-stage build plan (historical)
notes/esp32-firmware-plan.md ESP-WROOM-32 v1 firmware plan (M1–M8 shipped)
notes/esp32-v2-plan.md       ESP32-S3 v2 milestones (M1/M2/M6 shipped, M3/4/5 pending)
notes/voice-agent-plan.md    streaming TTS, VAD, continuous, wake — all shipped
notes/sessions-plan.md       multi-turn memory — shipped
notes/face-renderer.md       v3 prototype — RoboEyes face renderer + GPL-3.0
notes/motion-character.md    v3 prototype — eye-leads / head-follows gaze coupling
notes/build-modes.md         v3 prototype — CONFIG_VOICE_ENABLED Kconfig gate
notes/servo-motion-language.md  v3 prototype — SG90 driver
notes/two-mode-plan.md       SUPERSEDED by the Gemini migration
notes/router-design.md       SUPERSEDED by the Gemini migration
CLAUDE.md                    project instructions for Claude Code agents
firmware/                    ESP-IDF v5.3.5 project for ESP32-S3-DevKitC-1 N8R8
  ├── main/                  app code: wake.c (esp-sr WakeNet), vad.c (AFE),
  │                          i2s_mic, oled, wifi, http_client, gesture, ir_remote
  ├── components/            v3 bench-prototype components:
  │   ├── robo_eyes/         FluxGarage RoboEyes V1.1.1 port (GPL-3.0)
  │   └── servo/             ESP-IDF LEDC-based SG90 driver
  ├── examples/
  │   └── servo_test/        standalone IDF project for servo bench iteration
  └── sdkconfig.defaults     committed defaults; idf.py menuconfig fills creds
```

## Routes

| Route | Purpose |
|---|---|
| `GET /` | Streaming web client (Web Audio, VAD, continuous + interrupt) |
| `GET /health` | `{status, gpu_available, models_loaded, usage}` |
| `POST /transcribe` | Multipart `audio` → `{transcript, duration_s, latency_s, model, language}` |
| `POST /chat` | JSON `{message, session_id?}` → Gemini reply |
| `POST /speak` | JSON `{text}` → synth + blocking play through default output |
| `POST /converse` | One-shot voice round-trip; server plays reply locally when `play_locally=true`. Accepts multipart, raw `audio/l16`, or `audio/wav`. Add `?omit_audio_base64=1` to drop the WAV from the JSON response |
| `POST /converse/stream` | SSE-streamed voice round-trip; client plays the audio. Same three Content-Types as `/converse`. The web client uses this |

When `API_KEY` is set, every route except `GET /` and `GET /health`
requires an `X-API-Key` header. The web page persists the key in
`localStorage`; `talk.py` reads it from `.env`.

### Upload formats

`/converse` and `/converse/stream` accept three upload shapes,
chosen by `Content-Type`:

- **`multipart/form-data`** — file in the `audio` part; params
  (`session_id`, `play_locally`) in form fields. Used by the
  browser client and `talk.py`.
- **`audio/l16; rate=N; channels=N`** — raw int16 LE PCM in the
  body; params in the query string. Used by the ESP32-S3 firmware.
- **`audio/wav`** — pre-wrapped WAV in the body; params in the
  query string. Useful for tiny clients with a WAV fixture.

## Firmware (ESP32-S3-DevKitC-1 N8R8)

ESP-IDF v5.3.5 project at [`firmware/`](firmware/). Target is
**`esp32s3`** (LX7 + octal PSRAM). v2-M1 migrated the v1 WROOM-32
firmware to the S3 board; v2-M2 added on-device wake; v2-M6 added
the AFE mic-quality pipeline.

### Shipped v1 milestones (all ported forward to S3)

- **M1** — module skeleton: SSD1306 OLED via
  `esp_lcd_panel_ssd1306` + embedded 6×8 font, WiFi STA with
  WPA2-PSK or WPA2-Enterprise (Kconfig choice; verified on WPA2-Enterprise
  College's 802.1X dorm network), INMP441 mic with RMS/peak
  response.
- **M2** — BOOT button → 3 s recording → `/converse` round-trip.
- **M3** — VAD-bounded recording over HTTP chunked-transfer.
- **M4** — hands-free wake. v1 used server-side openWakeWord;
  v2-M2 replaced this with on-device esp-sr WakeNet ("Hey Kira")
  — see below.
- **M5** — continuous-mode follow-ups. **Default-off in v2** since
  multi-person scenarios make it pick up every voice in the room
  as input. Flip `CONFIG_WS_CONTINUOUS_MODE_ENABLED` to re-enable.
- **M6** — OLED polish: boot card, wake splash, speaking screen
  with model + per-turn USD cost + token usage.
- **M7** — HC-SR04 wave-to-wake (opt-in,
  `CONFIG_WAKE_GESTURE_ENABLED`).
- **M8** — NEC IR remote for mute / snooze / cancel (opt-in,
  `CONFIG_IR_ENABLED`).
- **Cat-face OLED panel** — second SSD1306 on its own I²C bus
  running an animated mascot synced to the state machine. (Default
  face renderer is now RoboEyes per the v3 prototype work;
  cat-face is still selectable via `CONFIG_FACE_RENDERER_CAT_FACE`.)

### v2 milestones — shipped 2026-05-12

- **v2-M1 — ESP32-S3 chip port.** Target swap, full pin remap
  (every GPIO except BOOT had to move), custom partition table,
  octal PSRAM at 80 MHz. v1 build preserved at git tag
  `pre-esp32-s3-port`.
- **v2-M2 — on-device wake word.** Espressif's esp-sr 2.4.4 library
  ([`firmware/main/wake.c`](firmware/main/wake.c)) with the
  `wn9_heykira_tts3` model running on the S3 — "Hey Kira" detected
  entirely on-chip with no continuous network stream. Removed
  ~600 lines of `listen_stream.c` (the raw-socket client that
  streamed mic audio to the server). Architecturally fixes the v1
  firmware's chronic mic-frame recycle issue (gotcha #9) — the
  exposure window simply isn't there anymore.
- **v2-M6 — AFE-driven mic pipeline.** Promotes AFE from M2's
  "wake-only" role to "all audio always." `vadnet1_medium` ML VAD
  replaces the peak-threshold detector — typing, clicks, and other
  transients no longer trigger false turns. NS + AGC mean Whisper
  receives denoised, level-normalized audio. Three-task structure
  (`wake_feed` / `wake_fetch` / `rt_driver`) decouples wake
  detection from the multi-second round-trip blocking call.

### v2 milestones — pending hardware

- **v2-M3** — MAX98357A on-device audio out. Reserved on GPIO
  9/10/11.
- **v2-M4** — LiPo + USB-C charging board. Battery monitor on a
  reserved ADC pin.
- **v2-M5** — AEC (acoustic echo cancellation), depends on M3 for
  the speaker-reference signal.

### Pin map (current, ESP32-S3-DevKitC-1 N8R8)

| Signal | GPIO |
|---|---|
| BOOT button | 0 |
| INMP441 BCK / WS / DIN | 4 / 5 / 6 |
| OLED status SDA / SCL | 7 / 15 |
| OLED face SDA / SCL | 12 / 13 |
| Reserved — MAX98357A BCK / LRC / DIN (v2-M3) | 9 / 10 / 11 |
| Reserved — battery ADC (v2-M4) | 1 or 2 |

Full details, history, and gotchas in
[CLAUDE.md](CLAUDE.md#firmware-esp32-s3-devkitc-1-n8r8) and
[notes/esp32-v2-plan.md](notes/esp32-v2-plan.md).

## Why this exists

Built as the server side of an eventual ESP-32 voice device — a
small, always-on, internet-connected button that talks to you.
The codebase is structured so the hardware client doesn't need to
do anything clever: post audio, get audio back.

The original architecture sat a two-LLM router (Llama for cheap
queries, Claude for hard ones) on top of a two-mode (local vs
cloud) split. In practice neither paid off enough to justify the
complexity, so the project was simplified to single-LLM Gemini 2.5
Flash in May 2026. The repo carries the result of that work — see
"Migration history" above, the `pre-ollama-removal` git tag for
the previous state, and the `pre-esp32-s3-port` tag for the v1
WROOM-32 firmware.

## License

GPL-3.0-or-later — see [LICENSE](LICENSE). The repo entered GPL-3.0
when [firmware/components/robo_eyes/](firmware/components/robo_eyes/),
a port of
[FluxGarage RoboEyes V1.1.1](https://github.com/FluxGarage/RoboEyes)
by Dennis Hoelscher, was added as the v3-prototype face renderer.
Building the firmware with the default
`CONFIG_FACE_RENDERER_ROBO_EYES` produces a binary covered by
GPL-3.0. The original cat-face renderer remains in tree as the
alternative (`CONFIG_FACE_RENDERER_CAT_FACE`), but the source tree
ships GPL code regardless of which renderer you build with.
