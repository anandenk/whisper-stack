# ESP32 v2 hardware — plan

> v2-M1 (chip port to ESP32-S3-DevKitC-1 N8R8) shipped 2026-05-12 as
> `bc0e544` + face-OLED pin fix `6466d70`. This doc lays out the
> remaining v2 milestones in dependency order, with detail
> concentrated on **M2** (the next milestone) and shorter sketches
> for M3 / M4 / M5.

## Context

The v2 hardware delivery added four components beyond v1:

1. **ESP32-S3-DevKitC-1 N8R8** — chip swap (LX6 → LX7, +8 MB PSRAM,
   +4 MB flash, native USB, SIMD).
2. **MAX98357A I²S DAC + 3W speaker** — on-device audio output.
3. **LiPo battery + USB-C charging board** — untethered runtime.
4. (Already counted above) **PSRAM** unlocks on-device speech
   recognition that was hardware-impossible on the WROOM-32.

M1 absorbed the chip swap. M2–M5 each absorb one new capability.

## Decisions at a glance

| Decision | Pick | Why |
|---|---|---|
| Wake-word framework | **esp-sr (a.k.a. esp-skainet) WakeNet** | Espressif's on-device library, IDF managed component, runs on S3 with PSRAM. Replaces server-side openWakeWord for the firmware only — server-side stays for web client + `tests/jarvis.py`. |
| Wake-word model | **`wn9_jarvis_tts`** | Ships with esp-sr, Apache-2.0, closest brand match to current "Hey Jarvis". Single-word "Jarvis" instead of two-word phrase — verified 2026-05-12 via repo listing that no two-word `hi_jarvis` or `hey_jarvis` variant exists upstream. Higher false-positive risk than two-word phrases in conversational backgrounds; if M2-d's ≤ 1 FP-in-15-min target fails, fallback options are: (a) swap to a stock two-word phrase like `wn9_hiesp` (gives up Jarvis brand) or (b) commission a custom-trained "Hi Jarvis" model via Espressif's service (paid; ~day-or-two turnaround). |
| Audio front end | **AFE (single-mic, no AEC) at boot, AEC deferred to M5** | AEC needs a reference signal from the speaker output, which doesn't exist until M3 lands. Single-talk mode (same as today) until then. |
| `listen_stream.c` (raw-socket /listen client) | **Delete from firmware** | Architectural win is to remove the chronic continuous-stream exposure that drove gotcha #9 in v1. Server-side `/listen` route + `wakeword.py` stay in tree (web client + `tests/jarvis.py` still use them). Recoverable from `pre-esp32-s3-port` if needed. |
| AFE handle | **`ESP_AFE_SR_HANDLE`** | SR (speech recognition) pipeline integrates WakeNet. The `_VC_` (voice communication) variant is for full-duplex calls, not what we want. |
| Partition table | **Use esp-sr's injected model partition if available; fall back to custom CSV** | esp-sr ships a partition fragment + Python tool to generate `srmodels.bin` from the menuconfig-selected models. The component's `idf_component.yml` may inject the partition automatically. Verify on the first build of M2-a before committing to a hand-rolled `partitions_two_ota_model.csv`. |
| Mic-frame consumer ownership | **State-machine routing (option c)** | The existing `i2s_mic` Ringbuf is single-consumer. Today `listen_stream` reads it in IDLE; `vad_record` reads it in LISTENING. After M2, AFE replaces `listen_stream` as the IDLE-state consumer. Toggle stays at state transitions, no fanout, no DMA refactor. (Option a — AFE-owned DMA, retire `i2s_mic`, feed Whisper from AFE output — is cleaner but bundles a bigger refactor into M2; deferred.) |
| INMP441 gain compensation | **`WAKE_LOCAL_INPUT_GAIN=1×` (no pre-amp) with Kconfig knob** | The advisor flagged that AFE-SR's pipeline runs AGC before WakeNet — pre-amping by 6× from day one would fight AGC (silence boosted into the "amplify" envelope makes AGC lower gain on actual speech, hurting recall on real speech). The `wakeword.py` 6× exists because openWakeWord has no built-in AGC; that justification doesn't carry to esp-sr. Build the knob in (Kconfig `WAKE_INPUT_GAIN_X10`, integer ×10 form so 6× = 60), default 10 (= 1×). M2-d A/B tests 1× / 4× / 6× / 8× only if recall is poor at 1×. |
| BOOT button | **Stays as manual-wake fallback** | Independent of esp-sr; cheap insurance for when wake recall is poor or you want a deterministic trigger. |
| Gesture (HC-SR04) + IR remote | **Stay disabled** | CLAUDE.md flagged re-enabling as an open follow-up after the gotcha-#9 fix. Keep deferred — this milestone's smoke test needs a single-variable baseline against M1; bundling additional wakes muddies the recycle metric. Re-enable in a separate post-M2 milestone, one at a time. |

## Architecture diff (M1 → M2)

**Today (post-M1):**
```
INMP441 → i2s_mic ring ──┬──► listen_stream.c (raw socket)
                         │       │
                         │       └─[TCP, 32 KB/s]─► server openWakeWord
                         │                          │ wake event
                         │       ┌──────────────────┘
                         │       ▼
                         └──► vad_record → /converse upload (on wake/button)
```

**After M2:**
```
INMP441 → i2s_mic ring ──┬──► AFE feed task ──► WakeNet inference (on-chip)
                         │                          │ wake event (in-process)
                         │       ┌──────────────────┘
                         │       ▼
                         └──► vad_record → /converse upload (on wake/button)
```

The `/converse` path is unchanged. Wake detection moves from a TCP
round trip to an in-process inference loop. Mic capture cadence
stays the same (16 kHz mono int16); the ring buffer is now consumed
by AFE rather than by `listen_stream`'s HTTP body writer.

## M2 — sub-phases (one commit at the end)

Internal staging for sanity; we ship one commit per CLAUDE.md
discipline ("ship one milestone per commit, measure baseline").

### M2-a: get esp-sr building

- Add `firmware/main/idf_component.yml` with `espressif/esp-sr`
  dependency (pin a version compatible with IDF 5.3.5).
- **First build verifies partition mechanism.** If esp-sr's
  component injects a `model` partition automatically (likely —
  check the build log for a `srmodels` partition entry), skip the
  custom CSV step. Otherwise create `partitions_two_ota_model.csv`
  with a `model` partition before `factory` and wire it up via
  `CONFIG_PARTITION_TABLE_CUSTOM`.
- Configure `menuconfig` → ESP Speech Recognition → enable only
  `wn9_jarvis_tts` (uncheck the others to keep partition size down).
- Run `idf.py size-components` after the first successful build to
  pin down the actual memory cost — the numbers in "Memory + resource
  budget" below are estimates that may need revising.

**Gate:** `esp_srmodel_init(NULL)` + `esp_srmodel_filter(models, ESP_WN_PREFIX, NULL)` returns the jarvis model name without crashing.

### M2-b: AFE feed/fetch tasks + gain + state-machine routing

- New `firmware/main/wake.c` + `wake.h` — owns the AFE pipeline
  lifecycle and emits wake events. (Naming matches existing
  `gesture.c` / `ir_remote.c`.)
- `wake_init()` creates the AFE config (`ESP_AFE_SR_HANDLE`,
  single-mic, no AEC, 16 kHz), spawns feed + fetch tasks.
- **State-machine routing in `i2s_mic` (polling, NOT suspend/semaphore):**
  the AFE feed task's main loop checks `app_state_get() == APP_STATE_IDLE`
  at each iteration. If yes, drain one frame from the `i2s_mic` ring
  and feed it. If no, `vTaskDelay(pdMS_TO_TICKS(20))` and re-check.
  No `vTaskSuspend`, no semaphore — suspending mid-`feed()` would
  raise undefined behavior in AFE state, and a semaphore adds a
  primitive without solving the mid-frame problem. Polling is
  race-free (`app_state_t` writes are aligned 32-bit, atomic on the
  S3) and AFE never pauses mid-frame. During LISTENING `vad_record`
  is the sole ring consumer, same as today.
- **Gain compensation in feed task (default 1×, knob present):**
  Kconfig adds `CONFIG_WAKE_INPUT_GAIN_X10` (integer, ×10 form so
  6× → 60), default 10 (= 1×, no pre-amp). Feed task applies
  per-sample multiply with int16 saturation clipping before
  `afe_handle->feed()`. Rationale: AFE-SR's built-in AGC handles
  level normalization; pre-amping by 6× from day one would fight
  AGC (silence amplified makes AGC suppress real speech). M2-d
  tests 1× first; only A/B 4× / 6× / 8× if recall is poor.
- Fetch task: calls `afe_handle->fetch(afe_data)` in a loop; on
  `wakeup_state == WAKENET_DETECTED`, sets the shared wake flag in
  `wake_source.h` (just like the current server-side path does).
- Log wake events to serial + status OLED (existing M6 splash code
  fires, no change needed since wake_source already drives it).

**Gate:** wake events fire reliably from the deployment spot; no
new ring-buffer recycle drops; `i2s_mic` STATS still shows
`recycled=0` across a 5-minute idle period.

### M2-c: retire `listen_stream.c`

- Remove `listen_stream_init()` call from `main.c`'s startup
  sequence. Replace with `wake_init()`.
- Delete `firmware/main/listen_stream.c` and `listen_stream.h` from
  the build (remove from `CMakeLists.txt`).
- Update `firmware/main/main.c` state machine: the wake source for
  `WAKE_VOICE` now comes from `wake.c` instead of `listen_stream.c`.
  The enum and consuming code are unchanged.
- Update `notes/esp32-firmware-plan.md`'s M4 section with a
  "superseded by v2-M2" header (don't rewrite the history, just
  flag it).

**Gate:** clean build, no dead symbols, no references to `/listen`
in the firmware. `LISTEN_RESULT_*` either gets renamed or stays as
internal wake-source state; pick whichever is cleaner.

### M2-d: smoke test + commit

- 5-turn voice smoke test, same recipe as M1.
- Capture: wake-detection latency (time from user finishing "Jarvis"
  to status OLED splash), false-positive rate over a 15-min idle
  test with TV / podcast playing in the background, `recycled=N`
  across all turns, free heap at boot.
- Commit with metrics in the body.

## Memory + resource budget (estimates — re-measure after M2-a)

These numbers are pre-build estimates; confirm with
`idf.py size-components` after M2-a's first build and update the
doc with real figures before M2-b.

- WakeNet `wn9_jarvis_tts` model: ~500-700 KB in flash, ~120 KB in
  PSRAM for inference state. (`wn9_*` models can be 700 KB+, larger
  than my initial guess — verify.)
- AFE pipeline: ~50 KB internal SRAM (DMA buffers + ring), ~600 KB
  PSRAM for processing buffers.
- New tasks: 2 (feed + fetch) at ~8 KB stack each.
- **Net delta from M1 baseline (free heap 8.13 MB):** expect drop
  to ~7.3 MB after AFE init. Plenty of headroom remaining for M3/M4.
- CPU: WakeNet inference is ~20% of one core at 16 kHz, AFE ~15%.
  Combined ~35% — leaves headroom for the WiFi stack + main task.

## Risks + open questions

1. **`_tts` model recall on real speech.** Custom-trained `_real`
   models exist for `wn9_alexa` but not `wn9_jarvis_tts`. If recall
   feels weak after a few days of use, escalate to Espressif's
   custom-training service (~$0 for personal use IIRC, day or two
   turnaround) — model swap is a one-line config change, not an
   architectural one.
2. **Background-noise false positives.** Single-word wake phrases
   have higher FP rates than two-word phrases in conversational
   environments. AFE's NS + AGC mitigate but don't eliminate.
   Mitigation if it gets bad: raise the wake-net score threshold
   (default is conservative for recall; tighter = fewer FPs at the
   cost of recall).
3. **Server-side `/listen` removal isn't free.** The firmware no
   longer hits it, but `wakeword.py`'s model load (~1.2 s) still
   happens on server boot — minor inefficiency until web client +
   `tests/jarvis.py` also migrate (out of scope for M2).
4. **Audio gain calibration.** Folded into M2-b's plan — Kconfig
   `WAKE_INPUT_GAIN_X10` defaults to 10 (= 1×, no pre-amp). The
   `wakeword.py` 6× justification (compensating for INMP441's quiet
   levels because openWakeWord has no AGC) doesn't transfer:
   AFE-SR has AGC before WakeNet, so 6× pre-amp would amplify
   silence into AGC's "compress" envelope and hurt recall on real
   speech. Remaining risk: 1× might still be suboptimal at the
   AFE input stage if INMP441 levels are so quiet AGC can't lift
   them effectively. Smoke test in M2-d measures recall at 1× —
   if < 8/10 from typical reading distance, A/B 4× / 6× / 8× before
   suspecting the model.

## Smoke test recipe (M2-d gate)

1. Server running, monitor attached.
2. **Reliability test:** say "Jarvis" 10 times from typical reading
   distance, count detections. Target: ≥ 8/10.
3. **False-positive test:** play a 15-min podcast or YouTube video
   with talking near the device, do not say "Jarvis". Count
   spurious wake events. Target: ≤ 1.
4. **End-to-end test:** 5 consecutive voice turns (as M1 smoke
   test), verify `recycled=0` across all and topic threading via
   session memory.
5. Capture wake-event-to-OLED-splash latency from any one of the
   turns — should be < 200 ms (vs server-side's ~500 ms round-trip).

---

## M3 — MAX98357A on-device audio out (sketch)

- Solder MAX98357A breakout to the speaker, wire to reserved GPIO
  9 (BCK) / 10 (LRC) / 11 (DIN). 5V supply from DevKitC-1's USB rail
  with a decoupling cap.
- New `firmware/main/i2s_speaker.c` initializing I²S1 (the IDF v5.3
  `i2s_std` driver supports two simultaneous I²S peripherals on S3
  cleanly).
- Switch firmware from `POST /converse` (one-shot JSON) to
  `POST /converse/stream` (SSE) — consume audio chunks as they
  arrive, push into a PCM ring → I²S TX. Sentence-by-sentence
  playback like the web client does today.
- PCM queue size + underrun handling: target ~200 ms of latency
  budget, drop to silence on underrun rather than glitch.
- Smoke test: 5-turn conversation with audio playing through the
  device's own speaker, no workstation Realtek route.

**Deferred dependencies:** none — can ship right after M2.

## M4 — LiPo battery + USB-C charging (sketch)

- Solder charging board between USB-C input and DevKitC-1 power,
  battery on the JST connector.
- Battery monitor via ADC1 on reserved GPIO 1 or 2; voltage divider
  to scale 4.2 V LiPo into ADC range.
- New `firmware/main/battery.c` reading battery voltage periodically,
  surfacing percent on the status OLED's boot card + a low-battery
  warning state.
- Light-sleep when idle for > 5 min, wake on WAKE_GESTURE / BOOT /
  audio threshold. Power budget calculations TBD.
- Smoke test: 4+ hours of mixed idle + conversation runtime on a
  full charge. Compare drain rate to estimate.

**Deferred dependencies:** M3 only insofar as we want one final
"untethered with on-device audio" demo before declaring v2 complete.
Can land in either order.

## M5 — AEC (acoustic echo cancellation) for interrupt-during-speaking (sketch)

- Wire the I²S1 DIN as both speaker output AND a loopback into the
  AFE's reference channel.
- Reconfigure AFE init from single-mic-no-AEC to 1-mic-1-ref-with-AEC.
- Enable interrupt detection: when the device is SPEAKING, mic
  continues to AFE; if VAD says "user speaking" while AEC is active,
  cut the playback queue and transition to LISTENING.

**Deferred dependencies:** M3 must land first (need actual speaker
output to capture as the reference signal).

---

## Stage gates between milestones

Per CLAUDE.md's "ship one milestone per commit" discipline, each
milestone is one commit with smoke-test metrics in the body.
**Don't bundle.** The May 2026 audio-corruption mis-diagnosis
(gotcha #9) happened because M7 + M8 + cat-face shipped as one
commit and jointly degraded performance in ways that took ~24 h
of debug to localize.
