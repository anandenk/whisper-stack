# ESP-32 firmware — plan

## Context

The whisper-stack server is fully shipped: STT + router + dual-LLM
+ streaming TTS + sessions + tool use + auto-titling + VAD + continuous +
interrupt + wake word (in `tests/jarvis.py`). The original product goal —
a hands-free ESP-32 + INMP441 voice device that speaks to that server —
is the last big piece.

This plan ports `tests/jarvis.py`'s shape onto an ESP-WROOM-32. **The
device is mic + display + WiFi only**: TTS audio plays through the
workstation's onboard Realtek speakers via the existing server's local
playback path. The device never decodes or plays audio. That's a big
firmware simplification — no I²S DAC, no audio codec, no audio out
task, no PCM playback queue, no echo cancellation problem.

`tests/jarvis.py` is the closest reference today; the firmware is its C
twin minus the audio playback path (since `jarvis.py` plays through the
workstation already, mostly via `/converse`'s `play_locally=true`).

---

## Hardware (locked)

| Component | Choice | Notes |
|---|---|---|
| Dev board | **DOIT ESP32 DEVKIT V1**, 30-pin variant | MicroUSB, CP2102 USB-UART, EN + BOOT buttons. Reuses the BOOT button (GPIO0) as the runtime trigger for M2 — see "Pin map" §below for the strapping-pin caveat. GPIOs 6-11 are tied to flash and unavailable. |
| MCU | **ESP-WROOM-32** (original ESP-32, dual-core LX6, **no PSRAM** — confirmed via `esptool.py chip_id`) | NOT ESP32-S3. Verified absence of PSRAM rules out on-device wake word; v1 uses server-side wake (path B locked) — see §"Wake word" below. |
| Mic | **INMP441** I²S MEMS microphone | 24-bit signed I²S output in 32-bit slots; we'll downshift to int16 in software. Silkscreen: L/R, WS, SCK, SD, VDD, GND. **L/R tied to GND** for mono left-channel capture. |
| Display | **0.96" SSD1306** I²C OLED (128×64), 4-pin variant | u8g2 over I²C. Address 0x3C (default). Built-in pull-ups present, no external resistors needed. Silkscreen pin order: GND/VCC/SCL/SDA. |
| Speaker | **None on the device** | Workstation's Realtek output (already pinned via `TTS_OUTPUT_DEVICE`) plays the reply. |
| Power | USB (always-on, MicroUSB on the DOIT V1) | No battery / sleep states for v1. |

**No I²S DAC. No MAX98357A. No audio output task. No PCM playback
queue.** The device ignores `audio_base64` on `/converse` responses
(and also passes `?omit_audio_base64=1` to keep the JSON small — see
§"Server surface" below).

### Tooling (locked)

- **ESP-IDF v5.3.2** on the dev MacBook (~/esp/esp-idf). The new I²S
  driver (`driver/i2s_std.h`) replaces the legacy `driver/i2s.h` —
  any tutorial copy-paste from before ~2023 needs translating.
- **Build target:** `idf.py set-target esp32` (LX6 classic, NOT s3).
- **Partition table:** `partitions_two_ota.csv` from M1 onward, even
  though OTA is deferred to v2. Picking the wrong table now means a
  full re-flash later when OTA lands; pre-allocating the OTA slots
  costs nothing at v1 and avoids that.
- **Secrets in `sdkconfig`:** `sdkconfig` is `.gitignore`'d.
  `sdkconfig.defaults` is checked in with **placeholder** values for
  WiFi SSID / password / `API_KEY` / `SERVER_HOST`. First-run
  workflow: `cp sdkconfig.defaults sdkconfig` then edit the local
  copy. NVS-based provisioning is a v2 concern.

### Pin map (locked, DOIT ESP32 DEVKIT V1)

| Function | GPIO | Notes |
|---|---|---|
| **I²S0 BCK** (mic SCK) | **GPIO 26** | Output. Standard I²S BCK choice. |
| **I²S0 WS** (mic WS / LRCLK) | **GPIO 25** | Output. Adjacent to BCK on the DOIT V1 silkscreen — short jumpers. |
| **I²S0 SD** (mic SD / DOUT) | **GPIO 33** | Input. Input-only-capable pins (34-39) would also work but 33 is fine and leaves 34-39 free for future analog inputs. |
| **I²C SDA** (OLED) | **GPIO 21** | Default Arduino-ESP32 SDA pin. Internal pull-up enabled in driver as belt-and-suspenders; the OLED breakout's pull-ups dominate. |
| **I²C SCL** (OLED) | **GPIO 22** | Default Arduino-ESP32 SCL pin. |
| **Trigger button** (M2+) | **GPIO 0 / BOOT** | Reuses the on-board BOOT button. **Strapping-pin caveat:** holding it during reset puts the chip in download mode, so don't long-press through a power cycle. Runtime presses are fine because the strap is sampled only at reset. |
| **Reserved — status LED** | GPIO 2 | On-board blue LED on the DOIT V1; available for v2 status indication without extra wiring. NOT used in v1. |
| **Reserved — second button** | GPIO 4 | Free for a v2 add-on (e.g. mute or session-clear). Not wired in v1. |
| **Reserved — I²S1 BCK / LRCK / DIN** | GPIO 27 / 14 / 13 | For v2 MAX98357A on-device speaker output. Block-reserved so a future PCB layout doesn't have to re-route. NOT used in v1. |

GPIOs 6-11 are tied to the on-board flash and **must not be touched**.
GPIOs 34-39 are input-only and reserved for future ADC use. GPIO 1/3
are UART0 TX/RX and stay tied to the CP2102. GPIO 12 is a strapping
pin (flash voltage); avoid using it as an output during boot.

### INMP441 wiring

| INMP441 pin | Connect to | Notes |
|---|---|---|
| VDD | 3V3 (DOIT V1) | INMP441 is 1.8–3.3 V tolerant; 3V3 is fine. |
| GND | GND | |
| L/R | **GND** | Mono left-channel capture. The I²S driver's `slot_mode = I2S_SLOT_MODE_MONO` with `slot_mask = I2S_STD_SLOT_LEFT` matches. |
| WS | GPIO 25 | LRCLK / word select |
| SCK | GPIO 26 | Bit clock |
| SD | GPIO 33 | Serial data out (mic → ESP) |

### SSD1306 wiring

| SSD1306 pin | Connect to | Notes |
|---|---|---|
| GND | GND | |
| VCC | 3V3 | The 4-pin module is 3V3-only on most variants. |
| SCL | GPIO 22 | I²C clock |
| SDA | GPIO 21 | I²C data |

I²C address is 0x3C (default jumper position). Pull-ups on the
breakout are confirmed present, so no external 4.7 k resistors are
needed. If you later swap to an SSD1306 board without pull-ups, add
4.7 k SDA→3V3 and SCL→3V3.

### Power budget (revised)

Continuous-TX WiFi on the WROOM-32 averages ~160–180 mA with bursts
to ~250–280 mA during chunked uploads on `/listen` and `/converse`.
INMP441 draws ~1.4 mA, SSD1306 ~15–25 mA depending on pixel-on
density. Total typical: **~180–230 mA average, ~270–310 mA peak**.
USB 2.0 ports supply 500 mA; the DOIT V1's AMS1117-3.3 LDO is rated
~800 mA. Margin is comfortable. The thermal risk under continuous
streaming (Risk #1 below) still applies — current draw is fine, but
sustained TX heats the module.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                  ESP-WROOM-32 (original ESP-32)                  │
│                                                                  │
│   INMP441 ──I²S0──► audio_in_task ──► ring_buf_16k_int16         │
│                       (always running while in IDLE/LISTENING)    │
│                            │                                      │
│              ┌─────────────┴──────────────┐                       │
│              ▼ (drain in IDLE)            ▼ (drain in LISTENING)  │
│      ┌────────────────┐           ┌──────────────┐                │
│      │ listen_stream  │           │ vad_record   │                │
│      │ POST /listen   │           │ peak-thresh  │                │
│      │ chunked TX     │           │ silence dwell │               │
│      │ raw int16 PCM  │           └──────┬───────┘                │
│      └────────┬───────┘                  │ silence/timeout         │
│               │ {"wake":true} ── server  ▼                        │
│               │ openWakeWord fired       │                        │
│               ▼                          ▼                        │
│           transition ──────► LISTENING ──► THINKING               │
│                                              │                    │
│                                              ▼                    │
│                                   ┌───────────────────┐           │
│                                   │ http_post         │           │
│                                   │ POST /converse    │           │
│                                   │ Content-Type:     │           │
│                                   │   audio/l16       │           │
│                                   └────────┬──────────┘           │
│                                            │                      │
│                                            ▼                      │
│                       Reply JSON ──► (audio_base64 IGNORED)       │
│                                            ▲                      │
│                                            │ Workstation plays    │
│                                            │ reply via Realtek    │
│                                            │ (play_locally=true)  │
│                                                                  │
│   SSD1306 ──I²C──► ui_task ── status: idle / listening /         │
│                                       thinking / speaking         │
│                                       (debug build also           │
│                                        shows last reply)          │
└──────────────────────────────────────────────────────────────────┘
```

**FreeRTOS tasks** (each one a clear, single-purpose actor):

| Task | Stack | Purpose |
|---|---|---|
| `audio_in_task` | 4 KB | I²S0 → 16 kHz mono int16 ring buffer. Always running while not THINKING/SPEAKING. |
| `listen_stream_task` | 8 KB | While IDLE: holds an open chunked-transfer `POST /listen` to the server, streaming raw int16 LE PCM frames from the ring buffer continuously. Exits on the server's response (wake fired) or on cancel; main loop transitions to LISTENING and starts `vad_record_task`. |
| `vad_record_task` | 4 KB | Peak-threshold VAD on the ring buffer; accumulates int16 PCM into a heap buffer; stops on silence or timeout. |
| `http_post_task` | 8 KB | POST raw `audio/l16` body to `/converse`; consume JSON response (transcript + reply_text), discard audio_base64. |
| `ui_task` | 4 KB | SSD1306 redraws on state changes via u8g2. |

**State machine** (single global enum):

```
IDLE → LISTENING → THINKING → SPEAKING → IDLE   (continuous: → LISTENING)
```

- `THINKING` covers HTTP upload + server processing.
- `SPEAKING` is purely informational on the device — the server is the one
  actually speaking. The device just shows "speaking" on the OLED for the
  duration the server reports as `audio_duration_s` in the reply, then
  drops back to IDLE (or LISTENING in continuous mode).

---

## Wake word — locked: server-side openWakeWord via `POST /listen`

**Path B is locked for v1.** The user verified via `esptool.py chip_id`
that the ESP-WROOM-32 module has no PSRAM, ruling out on-device
esp-skainet. The server already runs `openWakeWord` for `tests/jarvis.py`
with the bundled `hey_jarvis` model — extending that code path to
accept a streamed audio body from the device is the smallest reuse of
existing, battle-tested machinery.

### Spec

**Endpoint:** `POST /listen`

- **Request body:** raw int16 LE mono PCM at 16 kHz, sent as
  HTTP/1.1 chunked transfer-encoding while the device is IDLE.
  Chunks should be 1280 samples (80 ms) — the frame size openWakeWord
  prefers — but the server-side parser rebuffers regardless.
- **Request headers:**
    - `Content-Type: audio/l16; rate=16000; channels=1`
    - `Transfer-Encoding: chunked`
    - `X-API-Key: <key>` (same auth as everything else)
- **Response:** the connection stays open while the server feeds chunks
  into `openWakeWord.Model.predict()`. On detection, the server sends
  `200 OK` with `Content-Type: application/json` body
  `{"wake": true, "score": 0.78, "model": "hey_jarvis"}` and closes
  the connection. The device transitions to LISTENING and re-arms by
  re-POSTing `/listen` after each turn.
- **Cancellation:** the device closes the TCP connection to abort
  (e.g. on shutdown). Server treats abrupt close as graceful.
- **Idle timeout:** the server caps an open `/listen` connection at
  10 minutes, replying `{"wake": false, "reason": "idle_timeout"}` so
  the device re-arms cleanly without leaking long-lived sockets.
- **Keep-alive frames:** none for v1 — the device's TCP keepalive
  + WiFi-layer retransmits handle line health. Add only if real-world
  testing surfaces socket teardowns from intermediate routers.
- **Pre-roll buffer (firmware-side):** when `{"wake": true}` arrives,
  the user's question is *already in progress* — they're saying
  "...jarvis, what's the weather". The firmware MUST retain ~500 ms
  of audio that was just streamed to `/listen` and prepend it to the
  recording it sends to `/converse`, otherwise VAD chops the start
  of every utterance. Implementation: the audio_in ring buffer (the
  one `listen_stream_task` drains in IDLE) keeps a 500 ms window of
  history; on transition to LISTENING, that window seeds the new
  recording buffer. Cheap — ~16 KB at 16 kHz int16. Without this,
  recordings would routinely cut off the first phoneme of the
  question.

The server side reuses `openWakeWord` exactly the way `jarvis.py` does:
one `Model(wakeword_models=["hey_jarvis"])` per process, threshold
from `config.WAKEWORD_THRESHOLD` (default 0.5), `model.reset()` after
each detection so the next `/listen` arm starts fresh.

### Why Path B over A or C

| Path | Status | Reason |
|---|---|---|
| **A.** On-device esp-skainet | **v2 hardware-upgrade option** — re-evaluate only if/when an ESP32-S3 board (with PSRAM) enters the project. No work tracked here for v1. | Confirmed unavailable on the WROOM-32 the user has (no PSRAM). |
| **B. Server-side via `/listen`** | **Locked for v1.** | Reuses `tests/jarvis.py`'s already-working openWakeWord. "hey jarvis" works out of the box. No firmware DSP / no on-device model load. The continuous-mic-stream cost is acceptable on a LAN. |
| **C.** Button-only (no wake word) | **Documented fallback** — used only if Path B's continuous mic streaming proves unreliable in real deployment (criteria below). | Defeats the hands-free goal but is a known-good degraded mode. |

### Path C (button-only) — fallback criteria

Path C is the contingency *if* Path B turns out not to hold up in
practice on this specific WROOM-32 + WiFi network. Concrete fallback
triggers — any one of these justifies the swap:

1. **TCP / TLS breakage.** `/listen` connection drops more than once
   per ~30 minutes from network flakes (router resets, WiFi roaming,
   MTU issues, CGNAT timeouts) such that the device spends measurable
   time in "reconnecting" instead of listening.

2. **Sustained-TX power / thermal trouble.** The WROOM-32's WiFi modem
   running TX continuously (~32 KB/s) heats the module enough to
   trigger thermal throttling, brownouts on USB, or visibly degraded
   ADC readings on the I²S clock. Bench measurement: chip die temp
   above ~75 °C after 30 min of continuous streaming with the case
   closed.

3. **False-positive rate too high.** openWakeWord triggers more than
   ~once per hour on ambient sound for several days running, even
   after raising `WAKEWORD_THRESHOLD`. (Less likely — `tests/jarvis.py`
   has been clean — but listed here for completeness.)

4. **Server CPU saturation.** Continuous wake inference + Whisper +
   Llama + Piper all on one workstation pushes the GPU/CPU to a
   sustained level that visibly interferes with /converse latency.

If we hit any of these, **Path C ships:** a hardware button on a free
GPIO becomes the wake trigger, the `listen_stream_task` is removed, and
the device idles silently between presses. Plan-wise, that's a one-
milestone swap (M4 redirects from "POST /listen client" to "wired
button + GPIO ISR + debounce"). The rest of the firmware is unchanged.

The fallback is here so we don't paint ourselves into a corner — but
the v1 plan commits to Path B, and we ship M4 against it.

---

## Server surface

### Already shipped (verified)

- `POST /converse` accepts **`Content-Type: audio/l16; rate=N; channels=N`**
  raw int16 LE PCM body. INMP441 captures int16 LE (after 24-bit-to-16-bit
  shift) — wraps cleanly into a single HTTP POST without WAV-wrapping
  on the device.
- Params (`force_route`, `session_id`, `play_locally`) come from the
  query string when the body is raw. Multipart form fields still
  work for the existing browser path.
- `play_locally=true` (default) → workstation plays the reply through
  the pinned Realtek output. Device never sees audio.
- `audio_base64` is in the JSON reply but the device ignores it.
- **`?omit_audio_base64=1`** — query param the firmware always sets.
  When true, the JSON response omits the `audio_base64` field
  entirely (no WAV wrap, no base64 encode server-side either). This
  is critical: a 4-second TTS reply is ~176 KB of base64, on a chip
  with ~300 KB heap and a non-streaming cJSON parser. With the field
  omitted, `/converse` JSON responses to the firmware are typically
  <2 KB and parse cleanly. **Landed alongside the `/listen` work.**
- `/converse/stream` is also available for clients that want SSE +
  per-sentence audio chunks (the web client uses this), but the
  ESP-WROOM-32 firmware uses `/converse` because the workstation
  speaker is already pinned.

### Milestone 4 server-side scope (✅ shipped — MacBook session 2026-05-04)

The server-side half of M4 is now in the repo. The firmware-side
half is the next thing to write at the workstation; the device has
a working endpoint to talk to.

- ✅ `POST /listen` — `server/app.py`. Chunked-transfer raw int16 LE
  @ 16 kHz body, returns `{"wake": true, "score": ..., "model": ...}`
  on detection or `{"wake": false, "reason": "idle_timeout" |
  "client_closed"}` otherwise.
- ✅ `server/wakeword.py` — process-level `openWakeWord.Model`
  singleton guarded by a lock. Lazy import so the server still
  boots if the optional dep isn't installed (`/listen` returns 503
  with a clear install hint in that case). `model.reset()` between
  arms. 1280-sample (80 ms) frame buffer wrapping `predict()`.
- ✅ `PROTECTED_PATHS` updated; `X-API-Key` covers `/listen`.
- ✅ `usage.record_wake_detection()` counter; `/health.usage`
  surfaces `wake_detections`. Cost zero (local inference).
- ✅ `tests/stage11_listen.py` — wire-format negatives (415 on bad
  Content-Type / rate / channels), graceful client-close path
  (`reason: client_closed`), best-effort detection via TTS-synthed
  "hey jarvis" (logged, not asserted — TTS fixtures don't reliably
  cross threshold; real-mic detection is verified at firmware
  bring-up), and an in-process idle-timeout test that drives
  `wakeword.stream_predict` directly.

Server-side install on the workstation, before the firmware-side M4
work starts:

```
.venv/Scripts/python.exe -m pip install openwakeword
.venv/Scripts/python.exe -c "import openwakeword; openwakeword.utils.download_models()"
.venv/Scripts/python.exe tests/stage11_listen.py
```

---

## Milestones

Same shape as the prior plans — each milestone is a usable pause point,
manual stage gate at the end. Hardware bring-up is sequential because
each milestone builds on the previous board state.

### Milestone 1 — Bring-up

**Status:** ⚠️ **partial-shipped (workstation session 2026-05-05)** —
OLED + WiFi green on real hardware; mic deferred behind a soldering
blocker. The firmware skeleton at [`firmware/`](../firmware/) is
complete; flipping `ENABLE_MIC=1` in `app_state.h` after wiring the
mic finishes M1.

**Goal:** the board can capture mic, drive the display, and connect to WiFi.

- ✅ ESP-IDF project scaffold (`firmware/main/` — main.c, oled.c/h,
  wifi.c/h, i2s_mic.c/h, app_state.h, Kconfig.projbuild). Build target
  ESP32 (LX6 classic), partition table `CONFIG_PARTITION_TABLE_TWO_OTA=y`.
- ✅ SSD1306 via IDF-builtin `esp_lcd_panel_ssd1306` (instead of u8g2 —
  no managed-component dep, ships with IDF 5.3+, ~3 KB extra .a) plus a
  small embedded 6×8 ASCII font (~600 B). "Hello, whisper-stack"
  → "ready" rendered on real hardware.
- ✅ WiFi STA, creds via `Kconfig.projbuild` (CONFIG_WS_WIFI_SSID /
  CONFIG_WS_WIFI_PASS in `sdkconfig`, NOT `sdkconfig.defaults`).
  Hardcoded server IP via CONFIG_WS_SERVER_HOST. mDNS deferred.
- ⏸️ I²S0 capture from INMP441 — code complete (`i2s_mic.c` with
  `driver/i2s_std.h`, 32→16 bit shift via `>> 16`), gated on
  `ENABLE_MIC` in `app_state.h`. Awaits the user's circular INMP441
  breakout being soldered (came with loose headers).

**Verification:**
- ✅ OLED shows "ready" + IP address.
- ⏸️ Serial log prints non-zero mic RMS when you snap your fingers near
  the board (deferred to mic-wiring stage).
- ✅ Workstation `ping <esp32-ip>` succeeds at LAN RTT.

### Milestone 2 — Button + POST /converse

**Goal:** a button-driven round-trip works end-to-end.

- BOOT button (GPIO 0) triggers a 3-second fixed-length recording.
  Strapping-pin caveat: don't hold through reset (puts chip in flash
  download mode). Runtime presses are fine.
- Build a heap buffer of int16 samples from the I²S ring buffer
- POST to `/converse?omit_audio_base64=1` with:
  - `Content-Type: audio/l16; rate=16000; channels=1`
  - `X-API-Key: <key>` header
  - Raw int16 LE bytes in body
  - Optional `&session_id=esp32` query string for multi-turn memory
- Parse JSON reply for `transcript` + `reply_text` + `timings`. With
  `omit_audio_base64=1` the response is typically <2 KB so cJSON
  parses cleanly on the device.
- OLED shows "listening" → "uploading" → "speaking (Ns)" → "idle"
  using `audio_duration_s` to time the SPEAKING state.
- The `audio_base64` field is absent from the response — workstation
  speaks the reply via `play_locally=true` (the `/converse` default).

**Verification:** Press BOOT, ask a question, hear the workstation
speakers reply, see the transcript on OLED. Workstation `/health.usage`
counter increments.

### Milestone 3 — VAD-bounded recording (no fixed duration)

**Goal:** stop the recording on silence, not a fixed timer.

- Peak-threshold VAD on the I²S0 audio_in ring buffer
- Defaults match `jarvis.py` (peak ≥ 600, silence dwell 1500 ms)
- INITIAL_TIMEOUT_S = 15s (no-speech-after-press → cancel)
- Long-press of button = cancel current recording

**Verification:** Press BOOT, speak, *don't* time anything; recording
ends ~1.5s after you stop talking. Long-press cancels.

### Milestone 4 — Wake word via server-side `/listen`

**Goal:** hands-free trigger. No button press needed. **Path B locked
(see §"Wake word" above) — Path A and Path C are not in scope for this
milestone.**

This milestone has both server-side and firmware-side scope. Server-
side **shipped during the MacBook planning session 2026-05-04** (see
§"Server surface" above for the landed file list); the firmware-side
half is the work below.

**Server-side scope** — see §"Server surface" → "Milestone 4 server-
side scope (✅ shipped)" above. Summary: `POST /listen`,
`server/wakeword.py`, `usage.wake_detections` counter,
`tests/stage11_listen.py`.

**Firmware-side scope:**
- New `listen_stream_task`: while IDLE, holds an open
  chunked-transfer POST to `/listen`, draining the audio_in ring buffer
  in 1280-sample chunks. Backed by `esp_http_client` with
  `transfer_encoding=ESP_HTTP_CLIENT_TRANSFER_ENCODING_CHUNKED`.
- **Pre-roll buffer** — see §"Wake word" above. Retain the last 500 ms
  of audio that was just streamed to `/listen` so the
  user's question (which often overlaps the tail of "...jarvis") isn't
  truncated when LISTENING starts. Implementation note: keep a 16 KB
  ring window separate from the chunked-upload feed; on transition
  to LISTENING, copy that window into the new recording buffer
  before VAD starts.
- On JSON response parse: if `wake==true`, cancel the stream, transition
  to LISTENING, run M3's VAD recording (seeded with pre-roll), POST
  to `/converse?omit_audio_base64=1` as in M2. After SPEAKING ends,
  re-POST `/listen` to re-arm.
- On `idle_timeout`, `client_closed`, or connection-level error:
  backoff 1s, re-POST.
- OLED status flips between "listening for wake" / "wake fired" /
  M3's existing labels.

**Verification:** Don't touch any button. Say "hey jarvis", ask a
question, hear reply through workstation speakers. Returns to
wake-word-listening within ~1s. `/health.usage` shows incremented
wake-detection counter.

### Milestone 5 — Continuous mode after wake

**Goal:** mid-conversation follow-ups don't need re-waking.

- After SPEAKING ends, transition straight to LISTENING (M3 again)
  with a longer initial timeout (`WAKEWORD_CONVERSATION_TIMEOUT_S`,
  default 30s on server)
- 30s of initial silence → drop back to wake-word listening (M4)
- Same shape as `jarvis.py` Phase 6B, just over a different wake source

**Verification:** Wake, ask, hear reply, ask follow-up *without* re-
waking, hear reply. Stay quiet 30s → back to wake mode.

### Milestone 6 — OLED polish + diagnostics

**Goal:** the device is pleasant to use.

- States rendered with consistent layout (status line + small icon)
- A live VU bar on the OLED during LISTENING (mirrors the web client's
  VAD meter — useful for tuning peak threshold per environment)
- Last transcript + last reply (truncated, scrolling on overflow) in
  debug build
- Boot screen shows IP, server URL, wake mode

**Verification:** OLED is informative without being noisy. You can
diagnose "is my mic working" with the VU bar.

---

## What's deliberately deferred to v2

- **On-device wake word** (Path A — esp-skainet wakenet running on the
  ESP-32 itself). Requires a board with PSRAM, which the user's
  ESP-WROOM-32 doesn't have. **Re-evaluate only if/when an ESP32-S3
  board (or any PSRAM-equipped variant) enters the project.** Until
  then, the locked v1 path is server-side `/listen` (Path B). Custom
  wake-word training ("hey jarvis" on esp-skainet) is also part of
  this deferral — Path B uses openWakeWord's pre-trained model so the
  custom-training pipeline isn't needed for v1.
- **On-device audio output** (MAX98357A + I²S DAC + chunked PCM
  playback). Would let the device be standalone (no workstation in
  the loop). Significant firmware addition; not needed for the v1
  goal.
- **Interrupt detection during SPEAKING** (matches web client 5B).
  Would require either AEC or speaker-bleed-aware peak handling.
  Tractable on workstation playback (mic-far-from-speaker), still
  punt for v1 since SPEAKING is purely visual on the device anyway.
- **Battery + sleep**. Wall USB is fine for desk use.
- **OTA updates**. Manual flash via USB for v1.
- **BLE provisioning**. Hardcoded creds in `sdkconfig` for v1.

---

## Risks and pushback

### Open blockers (discovered during M1 bring-up)

- **Dorm WiFi is WPA2-Enterprise (802.1X / EAP).** Our `wifi.c`
  uses `WIFI_AUTH_WPA2_PSK` only. M1 was verified on iPhone
  hotspot. Before M2, swap in `esp_wifi_sta_wpa2_ent_set_*` so the
  device works in the actual deployment environment. Hotspot is a
  bench-only fallback (172.20.10.0/28 changes per session, sleeps
  after ~90s idle).

- **ELEGOO 830-tie-point breadboard rails are split mid-board** —
  colored line runs continuous but the contact strip splits around
  row 30. Keep all rail connections on the same half, or bridge
  with an extra M-M jumper. Bit us during M1 OLED re-routing.

### Real

1. **Sustained TX power + thermal during continuous mic streaming.**
   Path B keeps the WROOM-32's WiFi modem in TX continuously while
   IDLE (~32 KB/s at 16 kHz × 16-bit). The modem dominates the
   device's power budget. Revised numbers (see "Power budget"
   §above): ~180–230 mA average, ~270–310 mA peak under
   continuous TX + I²S + OLED, well within USB 2.0 (500 mA) and
   the DOIT V1's AMS1117 LDO (~800 mA). Current draw is fine; the
   real risk is thermal — chip die heats up under sustained TX,
   so the M4 bench check is to record die temp after 30 min of
   continuous streaming with the case closed. If it crosses ~75 °C
   or starts thermal-throttling, that's a Path-C trigger (see
   §"Wake word" Path C criteria).

2. **`/listen` connection stability over real WiFi.** Holding a TCP
   connection open for ~10 minutes through a consumer router is
   normally fine but failure modes exist (router idle-purge, DHCP
   lease renewal, mesh roaming). The plan specifies a 10-minute
   server-side cap that re-arms cleanly + on-device backoff retry
   on errors. Real-world reliability is the stage gate for M4 → M5;
   if the device spends measurable time reconnecting, Path C
   activates.

3. **INMP441 24-bit → int16 shift.** INMP441 outputs 24-bit signed
   samples in 32-bit slots over I²S. We discard the bottom 8 bits to
   get int16. Quality loss is negligible for speech, but the I²S
   driver setup has to specify the right bit clock alignment.
   openWakeWord and Whisper were both trained on int16 audio, so
   the precision drop doesn't degrade detection accuracy.

4. **No audio out makes interrupt-mode-on-device meaningless.** The
   v1 device cannot detect "user is talking over the assistant"
   because it doesn't know when the assistant is speaking (the
   workstation is). Could be added later via the `audio_duration_s`
   in the JSON reply (device knows "speaking" lasts N seconds, opens
   mic again early on detected speech), but it's a v2 feature.

5. **Network latency.** Each `/converse` round-trip = STT 0.3s + LLM
   1.5s + TTS 0.3s + HTTP overhead ≈ 2-3s total before workstation
   speakers start. Acceptable for v1; `/converse/stream` would shave
   ~1s off but requires the device to play audio (defeats the
   no-audio-out simplification). The right trade-off for hardware
   v1; revisit if total latency feels bad in practice.

6. **Server CPU competing with /converse.** With Path B, the
   workstation runs continuous openWakeWord inference *plus* its
   existing Whisper / Llama / Piper load on every turn. openWakeWord
   is light (~5% CPU on a single core) but if the GPU/CPU is
   already saturated by an LLM call, wake-word inference latency
   could spike to where the device's `/listen` post buffers up many
   seconds of audio waiting for response. Mitigation: dedicate a
   thread pool for `/listen` separate from the main route handlers
   if this surfaces in practice.

### Pushback on alternative framings

1. **"Why not put a small speaker on the ESP-32 too?"** Closes the
   loop without depending on the workstation, but adds significant
   firmware complexity (M2 grows the audio_out task, PCM queue,
   speaker-bleed risk for any future interrupt detection). The user's
   workstation is already paired with a Realtek output that's pinned
   via `TTS_OUTPUT_DEVICE` — no reason not to use it.

2. **"Why /converse and not /converse/stream?"** Streaming TTS only
   matters when the *client* plays the audio, because the win is
   "first audio chunk arrives ~1s sooner than the full reply". Here
   the workstation plays audio, and the workstation is already
   running streaming TTS internally on every /converse call (see
   `audio_out.synth_play_nonblock` — playback starts as soon as
   synth finishes, doesn't wait for round-trip). The device gains
   nothing by using `/converse/stream`.

3. **"Why not skip the OLED for v1?"** The OLED is the only visual
   feedback on a device with no light or sound output. Without it,
   the device is invisible — you wouldn't know whether wake word
   fired, recording is in progress, or the server is down. Worth
   the I²C complexity.

---

## Files this plan will touch (when implementation starts)

| Phase | File | Action |
|---|---|---|
| 1 | `firmware/main/i2s_mic.c` | ✅ shipped (gated on `ENABLE_MIC`) — I²S0 capture, 32→16 shift via `>> 16` |
| 1 | `firmware/main/oled.c` | ✅ shipped — SSD1306 via IDF `esp_lcd_panel_ssd1306` + embedded 6×8 font (chose over u8g2 — no managed-component dep) |
| 1 | `firmware/main/wifi.c` | ✅ shipped — STA + Kconfig.projbuild creds; mDNS deferred |
| 1 | `firmware/main/main.c` | ✅ shipped — task setup, ENABLE_X-gated subsystem init, heartbeat |
| 1 | `firmware/main/app_state.h` | ✅ shipped — state enum + ENABLE_OLED/MIC/WIFI toggles |
| 1 | `firmware/main/Kconfig.projbuild` | ✅ shipped — exposes WS_WIFI_SSID/PASS, WS_SERVER_HOST/PORT, WS_API_KEY in menuconfig |
| 2 | `firmware/main/http_client.c` | new — POST raw audio/l16 to /converse |
| 2 | `firmware/main/json_parse.c` | new — extract transcript/reply_text |
| 3 | `firmware/main/vad.c` | new — peak threshold + silence dwell |
| **2** | **`server/app.py`** | ✅ **shipped** — `?omit_audio_base64=1` query param on `/converse` so firmware JSON responses fit in heap. |
| **4** | **`server/wakeword.py`** | ✅ **shipped** — singleton openWakeWord Model + frame-buffered predict loop. |
| **4** | **`server/app.py`** | ✅ **shipped** — `POST /listen` route, added to `PROTECTED_PATHS`. |
| **4** | **`server/usage.py`** | ✅ **shipped** — `record_wake_detection()` + `wake_detections` field in `/health.usage`. |
| **4** | **`tests/stage11_listen.py`** | ✅ **shipped** — wire-format negatives, EOF path, best-effort detection, in-process idle-timeout test. |
| 4 | `firmware/main/listen_stream.c` | new — `listen_stream_task`: chunked POST to /listen, parses JSON wake response, retains pre-roll window for the LISTENING transition. |
| 5 | `firmware/main/state_machine.c` | edit — continuous mode loop |
| 6 | `firmware/main/oled.c` | edit — VU bar + transcript overlay |
| - | `firmware/sdkconfig.defaults` | new — WiFi creds, partition table |
| - | `firmware/CMakeLists.txt` | new — ESP-IDF project file |
| - | `CLAUDE.md` | edit — "Firmware" section once M1 lands |
| - | `notes/esp32-firmware-plan.md` | this file |

---

## Verification at the end of each milestone

| M | Verification |
|---|---|
| 1 | Boot board → OLED shows "ready" + IP; serial log prints mic RMS that responds to sound. |
| 2 | Press BOOT, ask "what's the capital of France?", hear reply through workstation speakers. `/health.usage` increments by 1. |
| 3 | Press BOOT, speak, stop talking → recording auto-ends. Long-press during recording cancels. |
| 4 | Don't touch BOOT. Say wake word, hear reply through workstation. Returns to wake-word-listening. |
| 5 | After a reply, ask a follow-up *without* waking. Stay quiet 30s → wake mode resumes. |
| 6 | OLED is pleasant to use; VU bar tracks your voice peak in LISTENING; debug build shows last transcript + reply. |
