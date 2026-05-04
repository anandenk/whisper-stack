# ESP-32 firmware — plan

## Context

The voice-ai-server is fully shipped server-side: STT + router + dual-LLM
+ streaming TTS + sessions + tool use + auto-titling + VAD + continuous +
interrupt + wake word (in `tests/jarvis.py`). The original product goal —
a hands-free ESP-32 + INMP441 voice device that speaks to that server —
is the last big piece.

This plan ports `tests/jarvis.py`'s shape onto an ESP-32 microcontroller.
Same client-side state machine: wake-word → chime → record → upload →
play → loop. Same network protocol: HTTPS POST to `/converse/stream`
(raw `audio/l16` body, SSE response with PCM chunks). The big new
constraints are real-time audio I²S + WiFi + tight memory + on-device
wake word.

`tests/jarvis.py` is the reference implementation; the firmware is its
C/C++ twin.

---

## Decisions to lock in before any C code is written

These are real forks in the road. Locking them now saves a lot of
"oh wait" once the board is on the bench. **Each is marked
[DECISION-NEEDED]**; a default is suggested but should be confirmed.

### 1. Wake word: on-device or server-side? [DECISION-NEEDED]

| Option | Pro | Con |
|---|---|---|
| **On-device via [esp-skainet](https://github.com/espressif/esp-skainet) (DEFAULT)** | Privacy (no audio leaves the device until you wake it). Minimal bandwidth (idle = no traffic). Native to ESP-IDF. | esp-skainet's pre-trained wake words don't include "hey jarvis". Options: pick from bundled ("alexa", "hi esp", "hi lexin"), do a custom training pass (1-2 days of TTS data + tools), or use Picovoice Porcupine on-device (commercial, has hey-jarvis ready). |
| Server-side (stream raw mic 24/7, server runs openWakeWord) | "hey jarvis" works out of the box (same model `jarvis.py` uses). | Constant network traffic. Privacy (always-on mic stream). Server load. ESP-32 WiFi power draw. |

**Recommended default:** on-device with esp-skainet, picking from a
bundled wake word ("hi esp" or "hi lexin"). Trades wake-phrase nostalgia
for everything else. Custom-training "hey jarvis" can be added in
Milestone 5 once the rest works.

### 2. Hardware target — which ESP-32 board specifically? [DECISION-NEEDED]

What you bought today determines:
- Audio support (I²S pins for INMP441 mic + I²S DAC out, or PDM, or
  built-in DAC)
- Wake-word feasibility (esp-skainet needs ESP32-S3 with PSRAM)
- OLED interface (I²C SSD1306 or SPI ST7789?)

**Recommended default:** **ESP32-S3-DevKitC** + 8 MB PSRAM + INMP441 mic
+ MAX98357A I²S DAC + 0.96" SSD1306 I²C OLED. esp-skainet officially
targets S3, and the I²S DAC keeps audio out clean. If you have a
different board, this plan still applies but specifics in Milestone 1
(audio I/O) change.

### 3. OLED scope: status only, or full transcript/reply? [DECISION-NEEDED]

| Option | What | Cost |
|---|---|---|
| **Status only (DEFAULT)** | "listening", "thinking", "speaking", small VU bar, error states | Minimal. ~5 lines of u8g2 code per state. |
| Status + transcript scroll | Status + last user turn + last reply, scrolling | Tight on a 128x64 OLED. Word-wrapping in C is annoying. Useful for debugging on the bench. |
| Full UI (waveform / token usage / route badge) | Pretty | A real GUI project. |

**Recommended default:** start with status-only; add a debug build flag
that overlays last transcript + reply for bench testing. Final UX is
status-only; you don't need to read a screen when you're talking to it.

### 4. Power profile: always-on vs. battery + sleep? [DECISION-NEEDED]

| Option | When to pick |
|---|---|
| **Wall USB always-on (DEFAULT)** | Desk assistant, fixed location. Simplest firmware (no sleep states). |
| Battery + light-sleep on silence | Portable assistant, want to clip it to a backpack. Adds significant complexity (waking from light sleep on mic activity needs ULP coprocessor or external mic-VAD chip). |

**Recommended default:** wall USB. Defer battery/sleep to a v2 unless
you have a specific portable use case. Wake word listening ~80mA on S3,
streaming reply ~150mA peaks — fine for USB but real budget for battery.

### 5. WiFi provisioning: hardcoded creds vs. BLE provisioning vs. SmartConfig? [DECISION-NEEDED]

| Option | UX |
|---|---|
| **Hardcoded in `sdkconfig` (DEFAULT for v1)** | You compile the SSID + password into the firmware. Re-flash to change. Fine for solo bench use. |
| BLE provisioning (Espressif Wi-Fi provisioning) | Phone app pairs over BLE, sends creds. ~500 LOC of glue, but real-product polish. |
| SmartConfig | EspTouch app pushes creds via WiFi probe trick. Janky; deprecated by Espressif. |

**Recommended default:** hardcoded for the prototype, BLE provisioning
later if you ever ship beyond your own desk.

### 6. Server URL: LAN IP, mDNS, or Cloudflare Tunnel? [DECISION-NEEDED]

| Option | Setup |
|---|---|
| **LAN IP hardcoded (DEFAULT for v1)** | `http://192.168.x.y:5000` in firmware. Fastest. Breaks if router renumbers. |
| mDNS (`voice-ai.local`) | Server registers `voice-ai.local`; ESP-32 resolves via mDNS. Survives DHCP renumbering. |
| Cloudflare Tunnel HTTPS | Works from anywhere, proper TLS, but adds ~80–200ms WAN round-trip. Only matters if the device leaves your LAN. |

**Recommended default:** LAN IP for prototype, switch to mDNS for v1
final. Cloudflare-tunnel mode reuses the existing API_KEY auth.

---

## Architecture (all decisions taking the recommended defaults)

```
┌────────────────────────────────────────────────────────────────────────┐
│                            ESP32-S3-DevKitC                            │
│                                                                        │
│  INMP441 ─┬─► I²S0 ─► ring_buf_16k_input                               │
│  (mic)    │            │                                               │
│           │            ▼                                               │
│           │       ┌─────────────────┐                                  │
│           │       │ wake_word task  │  (esp-skainet on PSRAM)          │
│           │       │ peeks at buf    │                                  │
│           │       └────┬────────────┘                                  │
│           │            │ wake event                                    │
│           │            ▼                                               │
│           │       ┌─────────────────┐                                  │
│           │       │ vad_record task │  ───► HTTP POST /converse/stream │
│           │       │ (peak threshold)│      Content-Type: audio/l16     │
│           │       └────┬────────────┘      body = raw int16 LE PCM     │
│           │            │                                               │
│           │            ▼                                               │
│           │       (recording chunks ring-buffered while sending)       │
│           │                                                            │
│  MAX98357A ◄── I²S1 ◄── audio_play task ◄── SSE PCM chunk queue        │
│  (speaker)                  │                       ▲                  │
│                             │                       │                  │
│  SSD1306    ◄── I²C ◄── ui task ◄── state ◄── sse_client task ─────────┘
│  (OLED)                                              ┌────►/converse/stream
│                                                      └──── parses SSE,
│                                                            pushes audio
│                                                            chunks into
│                                                            queue,
│                                                            updates state
└────────────────────────────────────────────────────────────────────────┘
```

**FreeRTOS tasks** (one per box above):
- `audio_in_task`     I²S0 -> 16 kHz int16 ring buffer (always running)
- `wake_word_task`    esp-skainet peeks at ring buffer (always running while IDLE)
- `vad_record_task`   peak-threshold VAD; uploads when triggered
- `sse_client_task`   parses SSE response; pushes PCM chunks
- `audio_out_task`    drains PCM queue -> I²S1 (back-pressured by I²S DMA)
- `ui_task`           SSD1306 redraws on state changes

**State machine** (single global enum, mirrors `jarvis.py`'s):
```
IDLE -> LISTENING -> THINKING -> SPEAKING -> IDLE (or LISTENING in continuous)
```

---

## Milestones

Same shape as the prior plans — each milestone is a usable pause point.
Hard manual stage gates between them; you'll be flashing the board, not
me, so each milestone is "verified by you on bench."

### Milestone 1 — Bring-up: I²S in, I²S out, OLED, WiFi

- ESP-IDF project scaffold
- I²S0 capture from INMP441 at 16 kHz mono int16
- I²S1 playback to MAX98357A at 22050 Hz mono int16 (matches Piper)
- SSD1306 over I²C; "Hello" on screen
- WiFi STA connect, mDNS resolve `voice-ai.local`
- Smoke: record 3s of mic via I²S0, play it back via I²S1 (loopback)

Pause: the board can hear, talk, and connect. **This is most of the
hardware risk.**

### Milestone 2 — Manual record + POST /converse

- "Press BOOT button" -> 3s VAD-bounded recording -> POST raw `audio/l16`
  to `/converse` (NOT streaming yet; uses the simple JSON endpoint)
- Decode reply WAV (extract PCM from RIFF header in C; trivial)
- Play reply through I²S1
- OLED: "listening" / "uploading" / "speaking" / "idle" status

Pause: end-to-end voice round-trip works. Nothing fancy yet — same UX
as a worse `talk.py`.

### Milestone 3 — Switch to /converse/stream

- HTTP client switches to `/converse/stream` raw `audio/l16` upload
- Inline SSE parser (line-buffered; ~100 LOC in C)
- On `audio` event: base64-decode (use mbedtls's b64), push PCM bytes
  into `audio_out_queue`
- `audio_out_task` writes from queue to I²S DMA — same back-pressure
  shape `_StreamingPlayer` uses in `jarvis.py`
- Update OLED on `text`, `tool_call`, `tool_result` for visibility

Pause: latency drops noticeably (~1s vs ~3s). UX matches the web
client streaming flow.

### Milestone 4 — VAD-bounded recording (no manual button)

- Peak-threshold VAD on the I²S0 audio_in ring buffer
- Same defaults as `jarvis.py`: peak ≥ 600, silence dwell 1500 ms
- INITIAL_TIMEOUT_S = 15s; auto-cancel and return to IDLE on no-speech

Pause: button only needed to "wake" — once awake, conversation is
hands-free.

### Milestone 5 — Wake word (on-device)

- esp-skainet integration (`espressif/esp-skainet` ESP-IDF component)
- Wake_word_task runs continuously while IDLE, peeking at the audio_in
  ring buffer
- On detect: chime (tiny WAV in flash, played through I²S1), transition
  to LISTENING
- WAKEWORD_THRESHOLD tunable via NVS (the ESP-32's "preferences" KV)

Pause: full hands-free flow, no button at all. **This matches the
behavior `tests/jarvis.py` proves out today.**

### Milestone 6 — Continuous mode after wake

- After SPEAKING ends, mic stays open; VAD-bounded record next turn
- 30s of initial silence -> back to wake-word listening
- Same shape as `jarvis.py` Phase 6B

Pause: full voice-agent behavior on the device. Closes the original
ESP-32 product goal.

### Milestone 7 (optional) — Interrupt detection during SPEAKING

- VAD runs during SPEAKING (mic still open), with higher threshold +
  N-frame debounce (matches web 5B)
- On voice detected: stop I²S1 output, abort SSE fetch, transition to
  LISTENING
- Echo cancellation is real on a desk speaker; might need WebRTC AEC
  port or a software echo canceller. **This is the hardest feature.**

Pause: parity with the web client's full feature set.

---

## Server-side changes needed

**None — the server is already done.**

What the firmware will use that's already shipped:

- `POST /converse/stream` accepts `Content-Type: audio/l16; rate=16000;
  channels=1` raw PCM bodies (added in this same session).
- SSE response includes `audio` events with raw int16 LE PCM (no WAV
  wrapping needed on receive).
- `X-API-Key` header for auth (same as the web client uses).
- Query-string params for `session_id`, `force_route`, `play_locally`
  when uploading raw bodies (no multipart form fields needed).
- `/health` returns mode/usage so the firmware can log a one-line cost
  estimate at boot or on demand.

The firmware NEVER needs to do TTS or STT or LLM work. The server is the
brain; the device is microphone + speaker + state machine.

---

## Out of scope for v1

- Custom wake-word training ("hey jarvis" on esp-skainet). v1 ships with
  whatever bundled word fits.
- Multi-device coordination. One device, one server.
- OTA firmware updates. Manual flash for v1.
- Multilingual. English only.
- Display animations / scrolling waveforms. Status text only.

---

## Risks and pushback

### Real

1. **PSRAM and esp-skainet.** esp-skainet wants octal PSRAM on S3 for
   reasonable inference latency. Confirm what the board you bought has.
   If it's quad PSRAM, latency degrades; if no PSRAM, esp-skainet won't
   fit at all.

2. **I²S2 vs I²S0 mic.** INMP441 datasheet specs SCK / WS / SD pins
   that are flexible on S3 but constrained on classic ESP-32. Map pins
   carefully against the dev board's silkscreen.

3. **Audio glitches at the chunk boundary.** SSE chunks arrive at LLM-
   stream rate (faster than realtime); I²S1 plays at realtime. The PCM
   queue must be deep enough to absorb arrival jitter. Sized to ~500 ms
   of audio = ~22 KB at 22050 Hz mono int16. Smaller -> underruns
   (clicks); much larger -> latency.

4. **WiFi power draw spikes during upload.** Continuous mode + always
   uploading audio = TX duty cycle stays high. Heat + power from the
   WiFi modem dominates the device's thermal envelope. Confirm with a
   sustained-conversation test.

5. **Echo cancellation (Milestone 7).** Speaker output → mic capture →
   VAD interrupt. Browser AEC works because Chromium has WebRTC AEC3.
   ESP-32 has no equivalent. Options: (a) keep the speaker physically
   distant from the mic, (b) duck the mic during SPEAKING (interrupt
   detection becomes "did the user TALK LOUDER than expected"), (c)
   port a software AEC — non-trivial. Defer Milestone 7 until 1–6 are
   working.

6. **Mongolian-style bandwidth.** A 12-second reply at 22050 Hz mono
   int16 = ~520 KB. Over 802.11n at typical 30 Mbps useful throughput
   that's ~140 ms — fine. Over 2.4 GHz at the edge of range with retries,
   could be 1+ seconds. SSE handles this gracefully (audio just arrives
   slower) but the I²S queue depth has to absorb the variance.

### Pushback on alternative framings

1. **"Why not run the server on the ESP-32 too?"** Not enough CPU/RAM
   for Whisper or Llama. Even ggml-quantized models on S3 are 30+ tok/s
   (slow) for the smallest models. The split — tiny client + powerful
   server — is the right shape for this hardware class.

2. **"Why not Bluetooth instead of WiFi?"** Latency. BLE audio (LE
   Audio / LC3) needs phone-as-bridge. WiFi direct to server is one
   hop, lower latency, no phone in the loop.

3. **"Why streaming TTS instead of file-at-a-time?"** Phase 3 of the
   voice-agent plan made this decision: streaming drops perceived
   first-byte latency from ~3s to ~1s on long replies. The firmware
   inherits the same win.

---

## Files this plan will touch (when implementation starts)

| Phase | File | Action |
|---|---|---|
| 1 | `firmware/main/i2s_audio.c` | new — I²S0 capture, I²S1 playback drivers |
| 1 | `firmware/main/oled.c` | new — SSD1306 over I²C via u8g2 |
| 1 | `firmware/main/wifi.c` | new — STA + mDNS resolve |
| 2 | `firmware/main/http_client.c` | new — POST raw audio/l16 to /converse |
| 2 | `firmware/main/wav_play.c` | new — extract PCM from /converse JSON reply |
| 3 | `firmware/main/sse_parser.c` | new — line-buffered SSE consumer |
| 3 | `firmware/main/audio_out_queue.c` | new — PCM ring + I²S1 DMA writer |
| 4 | `firmware/main/vad.c` | new — peak threshold + silence dwell |
| 5 | `firmware/main/wake_word.c` | new — esp-skainet integration |
| 6 | `firmware/main/state_machine.c` | new — IDLE/LISTENING/THINKING/SPEAKING |
| 7 | `firmware/main/aec.c` | new (maybe) — software echo cancellation |
| - | `firmware/main/main.c` | new — task setup, state machine kickoff |
| - | `firmware/sdkconfig.defaults` | new — PSRAM, WiFi, mDNS, etc. |
| - | `firmware/CMakeLists.txt` | new — ESP-IDF project file |
| - | `CLAUDE.md` | edit — add "Firmware" section once Milestone 1 lands |
| - | `notes/esp32-firmware-plan.md` | this file |

---

## Verification at the end of each milestone

| M | Verification |
|---|---|
| 1 | Loopback test: speak into INMP441 for 3s, hear it back from MAX98357A. WiFi connected; OLED shows "ready". |
| 2 | Press BOOT, ask a question, hear a reply. /health.usage on the workstation shows the LLM call. |
| 3 | First audio chunk plays within ~1s of pressing BOOT (vs ~3s in M2). OLED updates as text/tool events arrive. |
| 4 | Press BOOT, talk, *stop talking*; reply fires automatically. No second button press. |
| 5 | Don't press BOOT. Say wake word, ask, hear reply. Returns to wake-word listening. |
| 6 | After a reply, ask a follow-up — no wake word needed. Stay quiet 30s -> wake-word listening resumes. |
| 7 | While the device is speaking, talk over it. Speaker stops; new turn begins. (May not pass cleanly without AEC; v1 may ship with M1-M6 only.) |
