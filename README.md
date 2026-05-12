# whisper stack

A self-hosted voice assistant backend. Whisper for STT, Gemini 2.5
Flash for the LLM, Piper for streaming TTS — full mic-to-speaker loop
on one machine. Speak into a phone, a terminal, or a custom
ESP-WROOM-32 voice device; the server transcribes, asks Gemini, runs
tools when needed, streams synthesized speech back.

**Status:** v1 shipped (May 2026) — end-to-end voice loop, working
ESP-WROOM-32 hardware client. Migrated from a two-LLM routed
architecture to single-model Gemini 2.5 Flash in May 2026; the
two-mode (local vs cloud) split was retired in the same migration.
**Built by:** Anand Enkhjin .
**Hardware status:** v2 funded and ordered (arriving May 2026 —
ESP32-S3-DevKitC-1-N8R8, MAX98357 I²S amp, 3W speaker, 2 Ah LiPo,
TP4056 charging). v3 funded, parts list locked (GC9A01 1.28″ round
TFT, MG90S servos, capacitive touch buttons, 3D-printable enclosure
hardware), not yet ordered.

In plain terms: you speak into a microphone, a speech-recognition
model transcribes the audio, the text is sent to Google's Gemini 2.5
Flash (which can call tools and ground answers in live Google Search
results when relevant), and the reply is synthesized back to speech
and played out. The whole loop runs on one workstation; an optional
custom hardware device — currently an ESP-WROOM-32 with a microphone,
OLED display, and WiFi — talks to the workstation from across the
room and lets you use the assistant hands-free.

## What's next (v2)

V1 works but lives on a breadboard plugged into a USB cable. V2 turns
it into a self-contained device. **v2 hardware is arriving 2026-05-12**
— firmware work starts once the parts are on hand.

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

## v3 — bench prototyping (parts list locked, build pending)

V3 is the fully-3D-printed enclosed desk-assistant form factor.
Parts are funded and the hardware decisions are locked: GC9A01
1.28″ round TFT (240×240) for the face panel, MG90S metal-geared
micro-servo for the head, TTP223 capacitive touch buttons,
threaded-insert assembly hardware for the 3D-printed enclosure.
On-device wake word stays deferred to when the ESP32-S3's PSRAM is
in hand (v2 covers that). What's in tree today is bench prototyping
of the two pieces that need iteration before assembly:

- **Animated face renderer** — a port of
  [FluxGarage RoboEyes](https://github.com/FluxGarage/RoboEyes)
  (GPL-3.0) in [firmware/components/robo_eyes/](firmware/components/robo_eyes/),
  with state→mood mapping and an autonomous idle-wander gaze. Runs
  on the v1 firmware's second SSD1306 panel today; design portable
  to the future round display via a layout config.
- **Plastic SG90 servo head + gaze coupling** — the head follows
  where the eyes are already looking (50 ms poll, 800 ms eased move).
  Bench prototype only; the motion vocabulary is being tuned before
  committing to a specific servo + enclosure geometry.

Both are Kconfig-gated on the v1 firmware (`CONFIG_FACE_RENDERER`,
`CONFIG_GAZE_COUPLING_DEMO`) with defaults that preserve v1 behavior
where it matters. See [notes/face-renderer.md](notes/face-renderer.md),
[notes/motion-character.md](notes/motion-character.md), and
[notes/servo-motion-language.md](notes/servo-motion-language.md) for
design docs.

**v2 and v3 are both funded and on the way.**

```
audio in → STT → Gemini 2.5 Flash (with tools) → streaming TTS → audio out
                  ↑
       get_current_time  +  google_search (built-in)
```

Four voice clients reach the same server, all working today: a self-
contained web page (any browser, including phones), a push-to-talk
CLI, a workstation-side "hey jarvis" wake-word loop, and a custom
ESP-WROOM-32 + INMP441 + SSD1306 voice device — M1 through M8 +
cat-face panel all shipped on real hardware.

## What works today

- **Single-LLM stack.** Gemini 2.5 Flash via Google AI Studio handles
  every query. The previous router-on-top-of-two-LLMs design was
  retired in May 2026 — see "Migration history" below.
- **Streaming TTS** end-to-end. The web client hears the assistant's
  reply forming sentence-by-sentence ~1 second after the LLM starts
  generating, instead of waiting ~3 s for the whole reply. Sentence
  boundaries fed to Piper as they arrive; PCM chunks ship over
  Server-Sent Events.
- **Tools.** `get_current_time` (server-side, free, instant) and
  Gemini's built-in `google_search` grounding for "current info"
  queries — no third-party search account needed. The server picks
  one tool config per turn (Gemini 2.5 forbids combining custom
  function declarations with google_search in one request).
- **Multi-turn conversation memory.** Markdown-backed sessions, idle-
  bounded (10-min default), one file per conversation under
  `sessions/`. Files auto-titled by Gemini after the first turn.
- **Voice-activity detection** in the web client and `tests/talk.py`
  — auto-stop on silence-after-speech. Tunable peak threshold +
  silence dwell; web client shows a live VAD meter for self-tuning.
- **Continuous + interrupt mode** in the web client. Tap once at the
  start, talk freely, the assistant takes its turn, mic auto-reopens
  for the next user turn. Talking over the assistant cuts it off and
  swings the mic back to you mid-reply (~50 ms).
- **Wake-word client** (`tests/jarvis.py`). Workstation-side "hey
  jarvis" via [openWakeWord](https://github.com/dscripka/openWakeWord).
  No tap, no Enter — say the wake word, ask, get a reply, follow-ups
  for 30 s of silence, then back to listening.
- **Four voice clients.** Self-contained web page at `GET /` (records
  on phone, plays through Web Audio); push-to-talk CLI
  (`tests/talk.py`); hands-free wake-word loop (`tests/jarvis.py`);
  and a custom **ESP-WROOM-32 + INMP441 mic + SSD1306 OLED** firmware
  ([`firmware/`](firmware/)) — hands-free "hey jarvis", continuous
  follow-ups, speaking-screen overlay with model name + token usage.
  WPA2-Enterprise WiFi verified on a real dorm network. The
  workstation's Realtek output speaks the reply — no on-device DAC
  in v1. Two opt-in extras: **HC-SR04 wave-to-wake** and an **IR
  remote** for mute / snooze / cancel.
- **Per-process token / cost accounting.** `GET /health` returns a
  `usage` block with input/output tokens + USD-cost-since-boot per
  model. `/converse` also returns a per-call `estimated_cost_usd`
  so clients (e.g. the ESP-32 OLED) can show what each individual
  turn cost. Resets on restart; meant for "is this turning into a
  money fire" visibility, not billing accuracy. Gemini 2.5 Flash
  pricing baked in ($0.30 in / $2.50 out per 1M tokens) — verify
  current rates at [ai.google.dev/pricing](https://ai.google.dev/pricing).
- **Auth.** Optional `API_KEY` shared-secret on `/converse`,
  `/converse/stream`, `/chat`, `/transcribe`, `/speak`, `/listen`.
  `GET /` and `/health` stay public.
- **Cloudflare Tunnel runbook** for off-LAN access without
  port-forwarding.

## Migration history

The project shipped originally with a two-LLM routing architecture
(Ollama Llama 3.1 8B for "simple" queries, Claude Sonnet for
"complex"), plus a parallel two-mode (`MODE=local | cloud`) split
that would have used Claude Haiku + Deepgram + OpenAI TTS on a VPS.
In **May 2026** the project was simplified down to single-LLM Gemini
2.5 Flash:

- Routing layer, embedding classifier, and tool-likely escalation
  removed.
- Ollama, Anthropic, and sentence-transformers dependencies removed.
- Tavily-based `web_search` replaced by Gemini's built-in
  `google_search` grounding (no third-party signup needed).
- `MODE` env var, `requirements-cloud.txt`, and the cloud-only
  Deepgram/OpenAI placeholders all retired.

A `pre-ollama-removal` git tag bookmarks the last commit with the
full multi-LLM stack — see `CLAUDE.md` § "Ollama resurrection" for
the 5-minute recipe to bring any of the old backends back.

## Quick start

> **macOS / Linux note:** the commands below are PowerShell-flavored.
> Substitute `source .venv/bin/activate` for the venv activate line
> and standard `mkdir -p` / `curl -L -o` for the model-download
> lines; everything else is portable Python.

You'll need:

- **Python 3.13.x** in a venv. The system Python on Windows
  defaults to 3.14 which lacks the ML wheels we need. Don't use it.
- **An NVIDIA GPU** for faster-whisper STT.
- **A Google AI Studio API key.** Free tier covers casual use;
  Gemini 2.5 Flash paid pricing is fractions of a cent per voice
  turn. Get one at [aistudio.google.com](https://aistudio.google.com)
  → "Get API key", drop it in `.env` as `GOOGLE_API_KEY=...`.

### Install

```powershell
git clone https://github.com/anandenk/whisper-stack.git
cd whisper-stack

# Create the 3.13 venv
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Copy and fill in your keys
cp .env.example .env
notepad .env

# Download a Piper voice model (en_US-amy-medium is the project default)
mkdir -Force models\piper | Out-Null
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium"
curl.exe -sSL -o models\piper\en_US-amy-medium.onnx      "$base/en_US-amy-medium.onnx"
curl.exe -sSL -o models\piper\en_US-amy-medium.onnx.json "$base/en_US-amy-medium.onnx.json"

# Optional: download the bundled openwakeword models for tests/jarvis.py
.\.venv\Scripts\python.exe -c "import openwakeword; openwakeword.utils.download_models()"
```

### Run

```powershell
.\.venv\Scripts\python.exe -m server.app
```

Wait for `Listening on http://127.0.0.1:5000`. First boot is slow
(~10 s) — faster-whisper does a PTX JIT compile on Blackwell GPUs.
Subsequent boots are warm.

### Talk to it

**Browser** (laptop or phone over the same network):
- Open `http://127.0.0.1:5000`
- (If you set `API_KEY`) paste it into the API-key field
- (Optional) type a session ID into the Session field for multi-turn
  memory
- (Optional) toggle **Continuous** to turn the button into a
  Start/End conversation toggle — the assistant takes its turn, mic
  auto-reopens, loop until you tap End. Talking over the assistant
  interrupts it.
- Tap **Tap to talk** (or **Start conversation** if Continuous is
  on)

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
[.env.example](.env.example) for the full list grouped by subsystem.
Highlights:

- `GOOGLE_API_KEY=...` — required (Gemini)
- `GEMINI_MODEL=gemini-2.5-flash` — default model (override if Google
  ships a faster Flash alias)
- `API_KEY=...` — shared-secret auth on protected routes (default
  unset = LAN-only)
- `PIPER_VOICE=en_US-amy-medium` — TTS voice (any from the
  [Piper voice catalog](https://github.com/rhasspy/piper/blob/master/VOICES.md))
- `PIPER_LENGTH_SCALE=0.85` — speech rate; lower = faster (sane
  0.7–1.1)
- `TTS_OUTPUT_DEVICE=Speakers (Realtek(R) Audio), MME` — pin TTS
  playback to a specific output device by name substring or integer
  index, so a Bluetooth headset plugging in doesn't reroute the
  assistant's voice. Empty = system default.
- `WHISPER_LANGUAGE=en` — pin STT language (empty = auto-detect,
  prone to misfires)
- `SESSION_IDLE_TIMEOUT_MIN=10` — gap that triggers a new session
  file
- `CONVERSE_MAX_TOKENS=96` — cap voice-mode reply length
- `TOOL_MAX_ROUNDS=5` — safety cap on tool-call iterations per
  request
- `WAKEWORD_THRESHOLD=0.5` — `tests/jarvis.py` wake detection
  sensitivity
- `WAKEWORD_CONVERSATION_TIMEOUT_S=30` — silence dwell after a
  reply before `jarvis.py` drops back to wake-word listening

## Project layout

```
.venv/                       Python 3.13 virtualenv (gitignored)
samples/                     audio fixtures (jfk.wav + test-generated _*.wav)
models/piper/                Piper voice .onnx + .json (gitignored)
sessions/                    conversation history, one .md per session (gitignored)
tests/stage{1,4,6,7,8,9,10,11}_*.py
                             per-feature smoke tests (run after server start)
tests/stage_gemini.py        Gemini SDK smoke (chat, tools, search, streaming)
tests/talk.py                push-to-talk CLI (with VAD)
tests/jarvis.py              wake-word ("hey jarvis") CLI
server/
├── app.py                   Flask routes
├── config.py                env loading
├── modes.py                 backend factory: get_stt / get_llm / get_tts
├── protocols.py             STT / LLM / TTS Protocols
├── sessions.py              markdown session store
├── titler.py                Gemini-backed session auto-titling
├── streaming.py             SentenceChunker — feeds streaming TTS pipeline
├── usage.py                 per-process token / cost accumulator
├── tools/                   tool registry (get_current_time)
├── markdown.py              to_speakable_text (markdown/LaTeX stripper)
├── audio.py                 pcm_to_wav, synth_to_wav
├── audio_out.py             local sounddevice playback (TTS_OUTPUT_DEVICE pin)
├── wakeword.py              server-side openWakeWord wrapper
├── web/index.html           browser/phone client (streaming + VAD + continuous + interrupt)
└── backends/
    ├── stt_whisper.py       WhisperLocalSTT
    ├── llm_gemini.py        GeminiLLM (chat + chat_stream + tool dispatch)
    └── tts_piper.py         PiperTTS
plan.md                      original 7-stage build plan (historical)
notes/sessions-plan.md       multi-turn conversation memory (shipped)
notes/voice-agent-plan.md    streaming TTS, VAD, continuous, interrupt, wake (all shipped)
notes/esp32-firmware-plan.md ESP-WROOM-32 firmware plan (M1–M8 all shipped)
notes/two-mode-plan.md       SUPERSEDED — retired in the Gemini migration
notes/router-design.md       SUPERSEDED — retired in the Gemini migration
notes/face-renderer.md       v3 prototype — RoboEyes face renderer + GPL-3.0 rationale
notes/motion-character.md    v3 prototype — eye-leads / head-follows gaze coupling
notes/build-modes.md         v3 prototype — CONFIG_VOICE_ENABLED Kconfig gate
notes/servo-motion-language.md  v3 prototype — SG90 driver + motion vocabulary
CLAUDE.md                    project instructions for Claude Code agents
firmware/                    ESP-IDF v5.3.5 project for ESP-WROOM-32
  ├── main/                  application code (state machine, OLED, WiFi, mic, VAD,
  │                          HTTP, listen-stream, gesture, IR, control-state, Kconfig)
  ├── components/            v3 bench-prototype components:
  │   ├── robo_eyes/          FluxGarage RoboEyes V1.1.1 port (GPL-3.0)
  │   └── servo/              ESP-IDF LEDC-based SG90 driver
  ├── examples/
  │   └── servo_test/         standalone IDF project for isolated servo bench
  └── sdkconfig.defaults     committed defaults; idf.py menuconfig to fill creds
```

## Routes

| Route | Purpose |
|---|---|
| `GET /` | Streaming web client (Web Audio, VAD, continuous + interrupt) |
| `GET /health` | `{status, gpu_available, models_loaded, usage}` — `usage` is the per-process token counter + cost estimate |
| `POST /transcribe` | Multipart `audio` → `{transcript, duration_s, latency_s, model, language}` |
| `POST /chat` | JSON `{message, session_id?}` → Gemini reply |
| `POST /speak` | JSON `{text}` → synth + blocking play through default output |
| `POST /converse` | One-shot voice round-trip; server plays reply locally when `play_locally=true`. Accepts multipart, raw `audio/l16`, or raw `audio/wav` body. Add `?omit_audio_base64=1` to drop the WAV from the JSON response (saves ~100–200 KB per turn for clients that don't need it — used by the ESP-32 firmware) |
| `POST /converse/stream` | SSE-streamed voice round-trip; client plays the audio. Accepts the same three Content-Types as `/converse`. The web client uses this |
| `POST /listen` | Chunked-transfer raw int16 LE @ 16 kHz body; server runs openWakeWord and returns `{"wake": true, ...}` on detection (or `{"wake": false, "reason": "idle_timeout" \| "client_closed"}` otherwise). Used by the ESP-32 firmware in IDLE state. Requires `pip install openwakeword` + `download_models()` on the server |

When `API_KEY` is set, every route except `GET /` and `GET /health`
requires an `X-API-Key` header. The web page persists the key in
`localStorage`; `talk.py` and `jarvis.py` read it from the loaded
`.env`.

### Upload formats

`/converse` and `/converse/stream` accept three upload shapes,
chosen by `Content-Type`:

- **`multipart/form-data`** — file in the `audio` part; params
  (`session_id`, `play_locally`) in form fields. Used by the browser
  client and `talk.py`.
- **`audio/l16; rate=N; channels=N`** — raw int16 LE PCM in the
  body; params in the query string. Skips WAV-wrapping on the
  client. Used by the ESP-WROOM-32 firmware (INMP441 captures int16
  LE natively).
- **`audio/wav`** — pre-wrapped WAV in the body; params in the
  query string. Useful for tiny clients with a WAV fixture in flash.

## Firmware (ESP-WROOM-32)

ESP-IDF v5.3.5 project at [`firmware/`](firmware/). **M1 through M8
+ cat-face panel all shipped on real hardware.**

  - **M1** — full module skeleton: SSD1306 OLED via
    `esp_lcd_panel_ssd1306` + embedded 6×8 font, WiFi STA with
    **WPA2-PSK or WPA2-Enterprise** (Kconfig choice; verified on
    an 802.1X campus network with PEAP-MSCHAPv2), INMP441
    mic capture with RMS/peak response.
  - **M2** — BOOT button → 3 s recording → `/converse` round-trip,
    workstation Realtek output speaks the reply. Preserved as a
    fallback under `#define M2_BUFFERED_FALLBACK`.
  - **M3** — VAD-bounded recording (peak ≥ 200, silence dwell
    2.5 s) streamed over HTTP chunked-transfer — no heap cap on
    length. Long-press BOOT (≥ 1 s) cancels mid-recording.
  - **M4** — hands-free **"hey jarvis"** via server-side
    openWakeWord over a long-lived chunked POST to `/listen`, with
    a 1 s pre-roll so the start of the question after the wake
    word isn't truncated. BOOT button retained as a manual-wake
    fallback. Server hardened with pre-warmed model, threaded
    mode, fast-fail request handler, and lock-acquire timeouts so
    a wedged client connection can't lock out other handlers.
  - **M5** — **continuous mode after wake**. Once a turn finishes
    speaking, the device stays open for a follow-up — no re-wake
    needed — for 30 s of silence
    (`WS_CONVERSATION_TIMEOUT_MS` in `main.c`), then drops back
    to /listen.
  - **M6** — OLED polish: 2-second boot card, brief "WAKE / score
    N.NN" splash on each wake event, and a SPEAKING screen that
    shows the model name + per-call USD cost + token usage. Cost
    comes from the server (`estimated_cost_usd` on `/converse`)
    so pricing lives in one place. *Note:* the M6 live VU bar was
    removed on 2026-05-10 as part of the audio-corruption fix;
    the cat-face panel's `?` stroke animation gives the same
    "I'm listening" feedback without per-frame I²C contention.
  - **M7** — **HC-SR04 wave-to-wake** (opt-in alternative wake
    source). Wave a hand 10 cm in front of the sensor — no
    speaking required. Runtime-gated on
    `CONFIG_WAKE_GESTURE_ENABLED`.
  - **M8** — **NEC IR remote** for mute / snooze / cancel (opt-in).
    Runtime-gated on `CONFIG_IR_ENABLED`; first-time setup learns
    the NEC codes from the serial log.
  - **Cat-face OLED panel** (2026-05-10) — a second SSD1306 128×64
    panel on a separate I²C bus renders an animated cat mascot
    synchronized to the app state machine.
  - **Audio-corruption diagnosis + fix** (2026-05-10) — a chronic
    "frames recycled mid-LISTENING" issue (originally misdiagnosed
    as weak WiFi) was root-caused to per-frame `oled_flush_row()`
    I²C contention from the LISTENING VU bar. Removing the bar
    dropped frame-recycle from 16.5 % → 0.0 % across matched
    runs. Device now sustains arbitrarily long continuous-chat
    sessions cleanly.
  - **Multi-turn memory** — firmware passes `?session_id=esp32`
    on `/converse`, so wakes (and follow-ups within continuous
    mode) remember context (10 min idle TTL by default).

## Why this exists

Built as the server side of an eventual ESP-32 voice device — a
small, always-on, internet-connected button that talks to you. The
codebase is structured so the hardware client doesn't need to do
anything clever: post audio, get audio back.

The original architecture sat a two-LLM router (Llama for cheap
queries, Claude for hard ones) on top of a two-mode (local vs
cloud) split. In practice both the routing decision and the cloud
mode never paid off enough to justify the complexity, so the
project was simplified to single-LLM Gemini 2.5 Flash in May 2026.
The repo carries the result of that work — see "Migration history"
above, the `pre-ollama-removal` git tag for the previous state.

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
itself ships GPL code regardless of which renderer you build with.
