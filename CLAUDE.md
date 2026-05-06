# whisper stack

> **Note for any machine pulling this commit:** This project was renamed
> from `voice-ai-server` to `whisper-stack`. The Python package
> structure (`server/`, `tests/`) is unchanged — all imports still
> work. To update your local checkout: rename the parent directory
> (`mv voice-ai-server whisper-stack`), and on Windows the `.venv`
> may need to be recreated since it embeds absolute paths. The GitHub
> remote URL is being updated separately; if `git pull` fails with
> "repository not found," run `git remote set-url origin <new-url>`.

Workstation backend for a future ESP-32 + INMP441 voice device. Pipeline:
**audio → STT → router → simple-LLM or complex-LLM → TTS → speaker + ESP-32 OLED**.

The seven local-mode stages all shipped (see "Stage status" below). The
codebase is now split into a backend abstraction so a single `MODE` env
var picks the implementation set at process boot (see "MODE: local vs
cloud"). Original 7-stage plan: [plan.md](plan.md). Two-mode refactor
plan: [notes/two-mode-plan.md](notes/two-mode-plan.md).

## Environment

- **Working dir:** `C:\whisper-stack`
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
- [notes/voice-agent-plan.md](notes/voice-agent-plan.md) — auto-titling, tool use (web search + time), streaming TTS, VAD, continuous + interrupt mode, "hey jarvis" wake word. **All 6 milestones shipped.** Web client at `/` is the streaming + continuous + interrupt UI; `tests/jarvis.py` is the workstation wake-word client (`pip install openwakeword` + download the bundled models, then say "hey jarvis"). `web_search` stays disabled until `TAVILY_API_KEY` is set.
- [notes/esp32-firmware-plan.md](notes/esp32-firmware-plan.md) — port `tests/jarvis.py`'s shape onto **ESP-WROOM-32 + INMP441 + SSD1306 OLED**. **Hardware + wake-word path locked, no firmware code yet.** Device is mic + display + WiFi only — TTS plays through the workstation's pinned Realtek output via `play_locally=true` on `/converse`. No on-device audio out (no I²S DAC, no audio playback task, no PCM queue). Wake word: **server-side openWakeWord via a new `POST /listen` chunked-transfer endpoint** (Path B; PSRAM-less WROOM-32 verified, on-device esp-skainet deferred to v2). Path C (button-only) documented as fallback if continuous mic streaming proves unreliable. Six milestones; M4 has both server-side scope (the `/listen` endpoint, in-scope and not conditional) and firmware-side scope (the device's chunked-transfer client).

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
firmware/                    ESP-IDF v5.3.5 project (M1 + M2 shipped)
└── main/                    main.c + oled.c/h + wifi.c/h + i2s_mic.c/h
                             + http_client.c/h + app_state.h
```

## Firmware (ESP-WROOM-32)

ESP-IDF v5.3.5 project at `firmware/`. Build target is `esp32` (LX6
classic, NOT s3). **M1 + M2 + M3 shipped** (workstation session
2026-05-05). State of the device:

- OLED renders status text, WiFi STA gets a DHCP lease on enterprise Wi-Fi
  via WPA2-Enterprise (802.1X), INMP441 captures audio with RMS/peak
  responsive to sound.
- Pressing BOOT starts a **VAD-bounded** recording (was fixed 3 s in
  M2) that streams over **HTTP chunked-transfer** as it's captured —
  no heap cap on length. VAD stops on 2.5 s of silence (`vad.h`)
  after voice has been heard, or returns NO_SPEECH after 15 s of
  initial silence, or BUTTON_CANCEL on a ≥1 s long-press during
  recording.
- Server replies (`/converse?omit_audio_base64=1`) come back as
  small JSON; workstation Realtek output speaks the answer; OLED
  shows transcript + speaking countdown.
- All three subsystems (`ENABLE_OLED` / `ENABLE_MIC` / `ENABLE_WIFI`
  in `firmware/main/app_state.h`) are on.

Next milestone is M4 (server-side openWakeWord via the existing
`POST /listen`; firmware adds a `listen_stream_task` that holds a
chunked-transfer connection open while IDLE).

### VAD tuning notes (worth preserving for future-Claude)

`firmware/main/vad.h` defaults differ from `tests/jarvis.py` based on
real-mic real-room behavior at the workstation:

- **`VAD_PEAK_THRESHOLD = 300`** (jarvis.py uses 600). INMP441 has no
  AGC and at typical reading distance (~12+ in.) most conversational-
  speech peaks land around 300-700. Threshold of 600 caused the dwell
  counter to reset only on the loudest syllables, so quieter inter-
  word audio looked like silence and the dwell tripped mid-utterance.
  300 catches every syllable; idle ambient peak (~100 from M1's snap
  test) is well below.
- **`VAD_SILENCE_DWELL_MS = 2500`** (jarvis.py uses 1500). When a
  user pauses to think mid-sentence — common when reading from a
  screen — 1.5 s isn't enough. 2.5 s is the trade-off floor: shorter
  cuts off mid-thought; longer feels sluggish at end-of-utterance.
- **`M2_BUFFERED_FALLBACK`** in `main.c` defaults to 0 (M3 streaming).
  Flip to 1 and rebuild to revert to M2's fixed-3 s buffered POST if
  the streaming path turns out flaky on a different network.

### Build environment quirk (Windows)

The Espressif installer creates a Python 3.11 venv at
`C:\Espressif\python_env\idf5.3_py3.11_env\` regardless of the system
Python (which on this box is 3.14). `export.ps1` does
`python --version` to detect what Python env to expect, so a vanilla
PowerShell with system Python 3.14 first on PATH will look for an
`idf5.3_py3.14_env` that doesn't exist and fail with
`No module named 'click'` / "tool ... has no installed versions".

**Fix:** put the IDF Python first on PATH, or use the Start menu's
"ESP-IDF 5.3.5 PowerShell" shortcut (which does this for you). For
scripted invocations:

```powershell
$env:IDF_PATH = "C:\Espressif\frameworks\esp-idf-v5.3.5"
$env:IDF_TOOLS_PATH = "C:\Espressif"
$env:IDF_PYTHON_ENV_PATH = "C:\Espressif\python_env\idf5.3_py3.11_env"
$env:PATH = "C:\Espressif\python_env\idf5.3_py3.11_env\Scripts;$env:PATH"
. $env:IDF_PATH\export.ps1
```

### Build / flash / monitor

```powershell
cd C:\voice-ai-server\firmware
idf.py build
idf.py -p COM3 flash       # COM port from Device Manager — was COM3 on this box
idf.py -p COM3 monitor     # interactive serial; Ctrl-T then Ctrl-X to exit
```

Subsystem toggles in `firmware/main/app_state.h`:
`ENABLE_OLED` / `ENABLE_MIC` / `ENABLE_WIFI`. Each subsystem's `_init`
call in `main.c` is `#if`-gated; the .c files always compile but
unreferenced functions get `--gc-sections`-removed at link time.

### sdkconfig fragility

`firmware/sdkconfig` (gitignored) holds the WiFi creds + server IP +
API key. `firmware/sdkconfig.defaults` (committed) has placeholders
only. **`idf.py fullclean` deletes `sdkconfig`** and regenerates from
defaults — wipes creds. After a fullclean, re-run `idf.py menuconfig`
to set `WS_WIFI_SSID` / WiFi auth mode + creds (`WS_WIFI_PASS` for
PSK, or `WS_WIFI_EAP_USERNAME` + `WS_WIFI_EAP_PASSWORD` for
WPA2-Enterprise) / `WS_SERVER_HOST` / `WS_API_KEY` before rebuilding.

### WiFi auth modes (PSK + WPA2-Enterprise)

`firmware/main/Kconfig.projbuild` has a `choice WS_WIFI_AUTH_MODE`
between `WPA2_PSK` (home / phone hotspot) and
`WPA2_ENTERPRISE` (eduroam, dorm 802.1X). The default in
`sdkconfig.defaults` is PSK. `firmware/main/wifi.c` branches on the
choice — PSK sets `wifi_config.sta.password`; enterprise calls
`esp_eap_client_set_username/password/identity` and
`esp_wifi_sta_enterprise_enable()`. PEAP-MSCHAPv2 (the IDF default
inner method) is what eduroam-style networks use; we don't support
EAP-TLS / client certs. Server cert validation is **off** in v1
(documented in `wifi.c`'s comment block) — works for a single known
network; revisit if MITM threat model changes.

To switch auth modes: `idf.py menuconfig` → "Whisper Stack Device" →
"WiFi auth mode".

### enterprise Wi-Fi deployment recipe (verified 2026-05-05)

The dorm-WiFi-doesn't-work blocker is resolved. Recipe for connecting
the device to an 802.1X enterprise network:

1. **Auth mode:** WPA2-Enterprise. **Outer identity:** empty.
   **Username:** bare username (e.g. `username`), **NOT** `username@example.edu`.
   **Password:** network login password. **EAP method:** PEAP-MSCHAPv2
   (IDF default — no extra config). **CA cert validation:** off (if local network policy omits certificate validation).
2. **First boot will associate but DHCP stalls.** A device new to
   enterprise Wi-Fi lands on a quarantine VLAN (`10.x.x.x`) with no
   route to the workstation subnet (`10.x.x.x`). The first
   `wifi_init_sta_blocking` may even time out before DHCP completes
   the second time around — bumped the timeout to 90 s (`wifi.c`)
   to cover FortiNAC's reclassification window.
3. **Register the device's MAC at**
   [https://network-access.example.edu/registration/](https://network-access.example.edu/registration/)
   (MAC printed at boot in serial log + by `esptool.py read_mac`).
   Network policy may limit devices per user. **The device must have associated
   to enterprise Wi-Fi once before** this page accepts the MAC — if you
   try to pre-register, you'll get "Unable to locate client for
   Physical Address". Flash → boot → see WiFi assoc → THEN register.
4. **After registration, reboot the ESP-32.** FortiNAC will issue a
   `10.x.x.x` lease (same /16 as the workstation). The first wifi
   attempt may still fail while FortiNAC reclassifies; the firmware's
   retry logic + 90 s timeout handle it.
5. **There is also a `IoT-Network` SSID** for IoT devices (WPA2-PSK,
   no netid, separate VLAN). Not used here — cross-VLAN reachability
   to a workstation on enterprise Wi-Fi was unverified, and the same
   netid-based 802.1X path was simpler. Documented for future devices.

### 🚧 Notes / gotchas

1. **ELEGOO 830-tie-point breadboard has an internal mid-rail split.**
   The colored line on each rail runs continuously edge-to-edge, BUT
   the metal contact strip is split in half around the middle of the
   board (~row 30). Connections on opposite halves of the same rail
   are NOT electrically connected. Workaround: keep all rail
   connections on the **same half** OR drop a M-M jumper across the
   middle to bridge them. This burned us during M1 hardening — the
   OLED dropped dark because its VCC bridge landed on the opposite
   half from the 3V3 feed.

2. **Server must bind to `0.0.0.0`, not the default `127.0.0.1`,**
   for the ESP-32 to reach it from another LAN device. Run with
   `HOST=0.0.0.0 .venv/Scripts/python.exe -m server.app`. The
   `localhost`-only default is convenient for desktop dev but tripped
   up the first dorm bring-up.

## TTS output device

Pinned in `.env` via `TTS_OUTPUT_DEVICE` (default unset → system default).
Accepts a substring of the device name OR an integer index. On Windows the
same device often appears under MME / DirectSound / WASAPI host APIs; the
substring must be unique across input AND output devices, otherwise pin by
integer index. Wired in [server/audio_out.py](server/audio_out.py) at module
import; the boot log prints the resolved device. Avoid Hands-Free Bluetooth
profiles (`Headset (...)` entries) — they run at 8 kHz and TTS sounds awful
through them; use A2DP/stereo or a wired/USB device.

Enumerate candidates:

```
.venv/Scripts/python.exe -c "import sounddevice as sd; print(sd.query_devices())"
```

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
