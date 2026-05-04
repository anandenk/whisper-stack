# ESP-32 firmware — plan

## Context

The voice-ai-server is fully shipped server-side: STT + router + dual-LLM
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
| MCU | **ESP-WROOM-32** (original ESP-32, dual-core LX6, **no PSRAM** — confirmed via `esptool.py chip_id`) | NOT ESP32-S3. Verified absence of PSRAM rules out on-device wake word; v1 uses server-side wake (path B locked) — see §"Wake word" below. |
| Mic | **INMP441** I²S MEMS microphone | 24-bit signed I²S output; we'll downshift to int16 in software. |
| Display | **0.96" SSD1306** I²C OLED (128×64) | u8g2 over I²C — single bus, three pins (SCL, SDA, VCC, GND). |
| Speaker | **None on the device** | Workstation's Realtek output (already pinned via `TTS_OUTPUT_DEVICE`) plays the reply. |
| Power | USB (always-on) | No battery / sleep states for v1. |

**No I²S DAC. No MAX98357A. No audio output task. No PCM playback
queue.** The device ignores `audio_base64` on `/converse` responses.

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
- `/converse/stream` is also available for clients that want SSE +
  per-sentence audio chunks (the web client uses this), but the
  ESP-WROOM-32 firmware uses `/converse` because the workstation
  speaker is already pinned.

### Planned addition (Milestone 4 server-side scope)

- `POST /listen` — chunked-transfer raw int16 LE PCM body, server runs
  `openWakeWord.Model.predict()` per chunk, returns
  `{"wake": true, "score": ..., "model": "hey_jarvis"}` on detection
  (200 OK) or `{"wake": false, "reason": "idle_timeout"}` after a
  10-minute idle cap. Detailed spec in §"Wake word" above.
- Reuses the existing `openWakeWord` setup `tests/jarvis.py` already
  loads. No new model files, no new dependencies; only a route handler
  + a small frame-buffering loop wrapping `model.predict()`.
- Added to the existing `PROTECTED_PATHS` so the X-API-Key gate covers
  it. Logged in `usage.snapshot()` with a per-detection counter (cost
  zero — local inference).

---

## Milestones

Same shape as the prior plans — each milestone is a usable pause point,
manual stage gate at the end. Hardware bring-up is sequential because
each milestone builds on the previous board state.

### Milestone 1 — Bring-up

**Goal:** the board can capture mic, drive the display, and connect to WiFi.

- ESP-IDF project scaffold (`firmware/main/`)
- I²S0 capture from INMP441, 16 kHz, 32-bit raw → shift down to int16
- SSD1306 over I²C with u8g2; "Hello, voice-ai" on screen
- WiFi STA connect using hardcoded creds in `sdkconfig.defaults`
- mDNS resolve (or hardcoded LAN IP if mDNS proves flaky)
- Serial-log a 3-second mic sample's RMS so you can verify the mic is wired

**Verification:** OLED shows "ready" + IP address; serial log prints
non-zero mic RMS when you snap your fingers near the board.

### Milestone 2 — Button + POST /converse

**Goal:** a button-driven round-trip works end-to-end.

- BOOT button GPIO triggers a 3-second fixed-length recording
- Build a heap buffer of int16 samples from the I²S ring buffer
- POST to `/converse` with:
  - `Content-Type: audio/l16; rate=16000; channels=1`
  - `X-API-Key: <key>` header
  - Raw int16 LE bytes in body
  - Optional `?session_id=esp32` query string for multi-turn memory
- Parse JSON reply for `transcript` + `reply_text` + `timings`
- OLED shows "listening" → "uploading" → "speaking (Ns)" → "idle"
  using `audio_duration_s` to time the SPEAKING state
- Discard `audio_base64`

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

This milestone has both server-side and firmware-side scope; ship in
that order so the device has a working endpoint to talk to.

**Server-side scope (do first):**
- Add `POST /listen` route in `server/app.py` matching the spec in
  §"Wake word" above: chunked-transfer raw int16 LE @ 16 kHz body,
  responds with `{"wake": true, ...}` (200 OK) on detection or
  `{"wake": false, "reason": "idle_timeout"}` after a 10-minute cap.
- Reuses the openWakeWord setup `tests/jarvis.py` already loads
  (`Model(wakeword_models=[config.WAKEWORD_MODEL])`,
  `config.WAKEWORD_THRESHOLD` from `.env`).
- Frame-buffers the streamed body to 1280-sample chunks before each
  `model.predict()` call (openWakeWord's preferred frame size; the
  device may send chunks of any size).
- Add to `PROTECTED_PATHS` (X-API-Key gate).
- Per-detection counter logged via `usage.snapshot()` so `/health`
  surfaces wake activity.
- Regression test (`tests/stage11_listen.py`): synthesize an utterance
  containing "hey jarvis", stream chunks via chunked-transfer to
  `/listen`, assert 200 + `{"wake": true}`. Idle-timeout test:
  stream silence for 11 minutes (or fake the clock), assert
  `idle_timeout` reply.

**Firmware-side scope:**
- New `listen_stream_task`: while IDLE, holds an open
  chunked-transfer POST to `/listen`, draining the audio_in ring buffer
  in 1280-sample chunks. Backed by `esp_http_client` with
  `transfer_encoding=ESP_HTTP_CLIENT_TRANSFER_ENCODING_CHUNKED`.
- On JSON response parse: if `wake==true`, cancel the stream, transition
  to LISTENING, run M3's VAD recording, POST to `/converse` as before.
  After SPEAKING ends, re-POST `/listen` to re-arm.
- On `idle_timeout` or connection-level error: backoff 1s, re-POST.
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

### Real

1. **Sustained TX power + thermal during continuous mic streaming.**
   Path B keeps the WROOM-32's WiFi modem in TX continuously while
   IDLE (~32 KB/s at 16 kHz × 16-bit). The modem dominates the
   device's power budget — the WROOM-32 spec sheet rates
   continuous-TX current draw at ~150-240 mA peaks. On USB this is
   well within margin, but the chip die heats up: bench
   measurement at M4 should record die temp after 30 min of
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
| 1 | `firmware/main/i2s_mic.c` | new — I²S0 capture, 24→16 shift |
| 1 | `firmware/main/oled.c` | new — SSD1306 over I²C via u8g2 |
| 1 | `firmware/main/wifi.c` | new — STA + mDNS |
| 1 | `firmware/main/main.c` | new — task setup, state machine |
| 2 | `firmware/main/http_client.c` | new — POST raw audio/l16 to /converse |
| 2 | `firmware/main/json_parse.c` | new — extract transcript/reply_text |
| 3 | `firmware/main/vad.c` | new — peak threshold + silence dwell |
| **4** | **`server/app.py`** | **edit — add `POST /listen` endpoint** (chunked-transfer raw int16 PCM body, openWakeWord predict loop, idle-timeout reply). **Locked, not conditional.** |
| **4** | **`tests/stage11_listen.py`** | **new — server-side regression: stream a synth'd "hey jarvis" utterance through `/listen` via chunked-transfer, assert wake fires; idle-timeout test.** |
| 4 | `firmware/main/listen_stream.c` | new — `listen_stream_task`: chunked POST to /listen, parses JSON wake response |
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
