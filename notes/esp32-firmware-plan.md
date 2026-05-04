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
| MCU | **ESP-WROOM-32** (original ESP-32, dual-core LX6, no PSRAM by default) | NOT ESP32-S3. Restricts on-device wake word — see §"Wake word" below. |
| Mic | **INMP441** I²S MEMS microphone | 24-bit signed I²S output; we'll downshift to int16 in software. |
| Display | **0.96" SSD1306** I²C OLED (128×64) | u8g2 over I²C — single bus, three pins (SCL, SDA, VCC, GND). |
| Speaker | **None on the device** | Workstation's Realtek output (already pinned via `TTS_OUTPUT_DEVICE`) plays the reply. |
| Power | USB (always-on) | No battery / sleep states for v1. |

**No I²S DAC. No MAX98357A. No audio output task. No PCM playback
queue.** The device ignores `audio_base64` on `/converse` responses.

---

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                  ESP-WROOM-32 (original ESP-32)               │
│                                                               │
│   INMP441 ──I²S0──► audio_in_task ──► ring_buf_16k_int16      │
│                       (always running while in IDLE/LISTENING) │
│                            │                                   │
│                            ▼                                   │
│                    ┌──────────────┐  (peek frames)             │
│                    │ wake_word    │ esp-skainet wakenet9       │
│                    │   (§"Wake")  │ (only if PSRAM available)  │
│                    └──────┬───────┘                            │
│                           │ wake event OR button press         │
│                           ▼                                    │
│                    ┌──────────────┐                            │
│                    │ vad_record   │ peak-threshold VAD;        │
│                    │              │ accumulates int16 PCM      │
│                    └──────┬───────┘                            │
│                           │ silence-after-speech / timeout     │
│                           ▼                                    │
│                    ┌──────────────┐                            │
│                    │ http_post    │ POST /converse, raw body,  │
│                    │              │ Content-Type: audio/l16    │
│                    └──────┬───────┘                            │
│                           │                                    │
│                           ▼                                    │
│        Reply JSON arrives ──► (audio_base64 IGNORED)           │
│                                ▲                               │
│                                │ Workstation plays the reply   │
│                                │ through Realtek speakers      │
│                                │ via play_locally=true.        │
│                                                                │
│   SSD1306 ──I²C──► ui_task ── status: idle / listening /       │
│                                       thinking / speaking      │
│                                       (and last reply text     │
│                                        in debug builds)        │
└───────────────────────────────────────────────────────────────┘
```

**FreeRTOS tasks** (each one a clear, single-purpose actor):

| Task | Stack | Purpose |
|---|---|---|
| `audio_in_task` | 4 KB | I²S0 → 16 kHz mono int16 ring buffer. Always running while not THINKING/SPEAKING. |
| `wake_word_task` | 8–16 KB (model-dependent) | esp-skainet wakenet peeks at the ring buffer. Only spawned if PSRAM detected (else skipped — see §"Wake word"). |
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

## Wake word: on-device or server-side? (open decision)

ESP-WROOM-32 (the chip the user has) is the **original** ESP-32, not S3.
esp-skainet officially targets S3 with octal PSRAM, but supports
classic ESP-32 IF the module has external PSRAM (the WROVER variants do;
WROOM-32 typically does NOT).

Three viable paths:

| Path | Pros | Cons |
|---|---|---|
| **A. esp-skainet on-device** (if PSRAM is present) | Privacy. Zero idle network traffic. Native ESP-IDF integration. | Only works if your specific ESP-WROOM-32 module has PSRAM. Bundled wake words: "alexa", "hi esp", "hi lexin" — no "hey jarvis" unless you custom-train. |
| **B. Server-side wake word** (stream raw mic 24/7 to workstation; server runs openWakeWord on the audio stream the same way `tests/jarvis.py` does today) | Works on any ESP-32 regardless of PSRAM. "hey jarvis" works out of the box. Hardware cost negligible. | Constant network traffic (~32 KB/s at 16 kHz int16). Privacy (always-on mic stream). Server load (continuous wake-word inference). |
| **C. Button-only (no wake word)** | Trivial. Works today. | Defeats the "hands-free" goal. |

**Recommended: confirm whether your ESP-WROOM-32 has PSRAM, then:**

```
$ esptool.py --port COM<N> chip_id
# if "Features:" includes "PSRAM" → A
# else → B (server-side)
```

If it's a bare WROOM-32 (no PSRAM), **B (server-side wake word)** is
strictly the right call:

- The server's `openWakeWord` integration (`tests/jarvis.py`) is
  already battle-tested.
- Streaming raw int16 mic to the workstation over WiFi is cheap on a
  LAN.
- You get "hey jarvis" for free (the bundled model openWakeWord uses).
- The server-side path becomes a new lightweight endpoint:
  `WS /listen` (or `POST /listen` with chunked-transfer raw int16
  body) that the server pumps into openWakeWord and returns a
  `wake-detected` event when it fires.

If you confirm no PSRAM, the plan above changes one milestone (M5) to
"add `WS /listen` endpoint server-side + ESP-32 client streams mic
continuously while IDLE." Easier total system because everything LLM-
adjacent stays Python.

For now this plan documents both paths and assumes (B) as default —
swap to (A) if your specific module turns out to have PSRAM.

---

## What the server already provides (no changes needed for v1)

Verified by smoke test in this same change:

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

The only **possible** future server addition is a `/listen` endpoint for
server-side wake word — only if path (B) is chosen above.

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

### Milestone 4 — Wake word

**Goal:** hands-free trigger. No button press needed.

The implementation depends on the wake-word path chosen above:

**Path A (on-device esp-skainet, only if board has PSRAM):**
- Add `espressif/esp-skainet` ESP-IDF managed component
- `wake_word_task` runs continuously while IDLE, peeking at the
  audio_in ring buffer
- On detect: brief "ding" via the OLED (animated visual chime, since
  there's no speaker), transition to LISTENING, run M3's VAD recording

**Path B (server-side openWakeWord, recommended given no-PSRAM default):**
- New server endpoint: `POST /listen` (chunked transfer-encoding;
  raw int16 LE body streamed continuously) — server pumps each
  chunk into openWakeWord, returns the response only after a wake
  event fires (or on a connection-level cancel).
- ESP-32 client: while IDLE, holds an open POST to `/listen` streaming
  mic chunks via HTTP chunked transfer encoding.
- When `/listen` returns 200 with `{"wake": true}`, transition to
  LISTENING, run M3's VAD recording, POST to `/converse` as before.
- Re-arms `/listen` after each turn.

**Verification:** Don't touch the button. Say wake word, ask, hear
reply. Returns to wake-word-listening.

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

- **On-device audio output** (MAX98357A + I²S DAC + chunked PCM
  playback). Would let the device be standalone (no workstation in
  the loop). Significant firmware addition; not needed for the v1
  goal.
- **Interrupt detection during SPEAKING** (matches web client 5B).
  Would require either AEC or speaker-bleed-aware peak handling.
  Tractable on workstation playback (mic-far-from-speaker), still
  punt for v1 since SPEAKING is purely visual on the device anyway.
- **Battery + sleep**. Wall USB is fine for desk use.
- **Custom wake-word training** ("hey jarvis" via esp-skainet's
  custom-training tools). Bundled wake words ("hi esp" / "hi lexin")
  or path-B's openWakeWord (which has "hey jarvis" pre-trained) get
  the v1 done.
- **OTA updates**. Manual flash via USB for v1.
- **BLE provisioning**. Hardcoded creds in `sdkconfig` for v1.

---

## Risks and pushback

### Real

1. **PSRAM on the WROOM-32.** Most ESP-WROOM-32 modules don't have
   PSRAM. Confirm before counting on path A. If no PSRAM → path B is
   strictly better.

2. **INMP441 24-bit → int16 shift.** INMP441 outputs 24-bit signed
   samples in 32-bit slots over I²S. We discard the bottom 8 bits to
   get int16. Quality loss is negligible for speech, but the I²S
   driver setup has to specify the right bit clock alignment.

3. **WiFi power draw + thermal during continuous mic streaming**
   (path B). 16 kHz × 16-bit × continuous = 32 KB/s sustained TX.
   ESP-WROOM-32's WiFi modem dominates the device's power budget.
   Should be fine on USB but warrants a sustained-conversation
   thermal test.

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

6. **OLED + I²C share the bus with nothing else here, but** if
   esp-skainet (path A) gets added it competes for CPU not I²C —
   wakenet runs ~50ms per 30ms frame on classic ESP-32 with PSRAM,
   leaving ~30% headroom. Tight but workable. On a no-PSRAM module,
   wakenet doesn't fit at all.

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
| 4 | `firmware/main/wake_word.c` | new — esp-skainet (path A) OR `/listen` client (path B) |
| 4 | `server/app.py` | edit — only if path B chosen, add /listen endpoint |
| 5 | `firmware/main/state_machine.c` | edit — continuous mode loop |
| 6 | `firmware/main/oled.c` | edit — VU bar + transcript overlay |
| - | `firmware/sdkconfig.defaults` | new — WiFi creds, partition table, PSRAM (if applicable) |
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
