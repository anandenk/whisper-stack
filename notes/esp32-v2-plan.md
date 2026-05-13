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
**M6 was added 2026-05-12** to fix a software-only bug surfaced
during M2 bench testing: keyboard/mouse clicks during continuous-
mode LISTENING were triggering false turns and wasting Gemini
credit. M6 promotes AFE from "wake-only" (M2) to "all audio always"
and replaces the peak-threshold VAD with AFE's duration-aware VAD
that filters transients by design.

## Milestone order

Implementation order differs from numbering because **M6 is
software-only and addresses an active bug, while M3/M4/M5 need
hardware that isn't soldered yet:**

| # | Milestone | Status | Reason for ordering |
|---|---|---|---|
| M1 | Chip port | Shipped (`bc0e544`) | Foundation |
| M2 | On-device wake | Code complete, test pending | Foundation for everything else |
| **M6** | **Mic-quality pipeline** | **Next — implement after M2 ships** | Software-only, fixes active typing-hallucination bug |
| M3 | MAX98357A audio out | Hardware-blocked (soldering) | Needs DAC wired |
| M4 | LiPo + charging | Hardware-blocked | Needs charging board wired |
| M5 | AEC | Hardware-blocked on M3 | Needs speaker output as ref signal |

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

## M6 — Mic-quality pipeline via always-on AFE

> **Motivation:** keyboard / mouse clicks during continuous-mode
> LISTENING fire today's peak-threshold VAD (`VAD_PEAK_THRESHOLD = 200`
> in `firmware/main/vad.h`), get captured as "speech," uploaded to
> /converse, hallucinated into a fake transcript by Whisper, and
> answered by Gemini — wasting real cents per false trigger.
> This is the next priority because it's an active bug that costs
> money, and it's pure-software (no v2 hardware required).
>
> **Priority slot:** **promote ahead of M3 / M4 / M5.** M3 needs the
> MAX98357A soldered, M4 needs the LiPo wired, M5 depends on M3.
> M6 can ship today and the win is immediate.

### Why this fixes the typing problem

The current VAD in `vad.c` is a peak detector: any single 16-bit
sample whose absolute value crosses 200 gets counted as "speech."
A typing click is a sharp transient with a 5-30 ms duration and
peak values in the thousands — exactly the failure mode.

AFE's vadnet1_medium VAD helps via **two complementary mechanisms**:

1. **Learned speech features.** vadnet1 is a small neural net
   trained to classify 30 ms audio chunks as voiced speech vs.
   anything else. The training data includes typing, clicking,
   keyboard taps, dishes, and other transient noise as negative
   examples. Each click is classified on its acoustic shape — not
   energy — so even repeated rapid typing doesn't fool it.
2. **Duration aggregation.** `vad_min_speech_ms = 128 ms` requires
   sustained "speech" classification before flipping to
   `AFE_VAD_SPEECH`. A single 30 ms click can't cross that floor
   even if vadnet1 momentarily misclassifies it.

Both together address the failure mode. The duration filter is
the safety net; vadnet1's spectral discrimination is the primary
defense against sustained typing (~5 clicks/sec at 60 WPM, which
would fool a duration-only filter).

Two secondary wins come along for free:

1. **NS (noise suppression)** — WebRTC's spectral subtraction
   removes stationary background noise (fans, HVAC). Whisper hears
   cleaner audio → fewer hallucinations on real captures too.
2. **AGC (automatic gain control)** — INMP441 quiet conversational
   levels get normalized on-device, making server-side
   `_peak_normalize_wav` redundant. Keep the server-side fallback
   for now — easy to remove in a follow-up once we trust AFE's AGC.

### Decisions at a glance

| Decision | Pick | Why |
|---|---|---|
| VAD backend | **vadnet1_medium** | ML-based neural-net VAD trained specifically to discriminate speech from non-speech transients. Strictly stronger than WebRTC at rejecting clicks/keystrokes/dishes — the exact failure mode that motivated M6. Locked in upfront (2026-05-12) to avoid a possible swap cycle after WebRTC underperforms. Cost: ~100-150 KB PSRAM + the vadnet1_medium model gets packed into the existing `model` partition alongside `wn9_heykira_tts3` (well under the 2 MB partition budget). Selected via `CONFIG_SR_VADN_VADNET1_MEDIUM=y` in `sdkconfig.defaults`. |
| Mic-frame ownership | **AFE owns the mic stream end-to-end** | The option (a) refactor deferred from M2. AFE feed runs in all states (not just IDLE). The `i2s_mic` ring stays as the producer; `wake.c` is the only consumer; AFE's fetch output becomes the new single source of truth for both wake detection AND LISTENING audio. |
| LISTENING audio source | **AFE output queue (denoised)** | `/converse` upload pulls from a new queue that `wake.c`'s fetch task fills with AFE's processed audio chunks. Whisper sees post-NS, post-AGC audio. |
| VAD state machine | **Thin wrapper over `afe_fetch_result_t.vad_state`** | `vad.c` keeps the same external API (`vad_record()`) but its internals replace peak-threshold + dwell counter with AFE's per-chunk `AFE_VAD_SPEECH` / `AFE_VAD_SILENCE` events. Timing logic (has-speech-been-heard, dwell-since-speech-end, total-window-timeout) stays the same shape — just driven by AFE events instead of raw peaks. |
| Preroll mechanism | **Keep wake.c's preroll ring** | AFE's `vad_cache` is "audio captured before VAD flipped to SPEECH" — semantics are tied to **VAD onset**, not **wake-word onset**. With M6's always-on VAD, if the user says "Hey Kira what's the weather" with no pause, VAD has been in SPEECH state continuously since "Hey" and `vad_cache` is whatever the cache was *128 ms before VAD onset* — possibly silence, definitely not 1 second of "hey kira what's the weath...". The wake.c hand-rolled ring (1 s before wake-fire instant) preserves the right semantics. Use `vad_cache` as a SECONDARY signal only for catching speech that started just before VAD's duration filter flipped. |
| Task structure | **Three tasks, not two: `wake_feed`, `wake_detect`, `listen_forward`** | `wake_detect`'s call to `do_round_trip_streaming_with_preroll` blocks for the entire LISTENING → THINKING → SPEAKING cycle (many seconds). If the same task also had to forward AFE-fetch output to the LISTENING queue, that wouldn't happen. So: `wake_feed` runs always (mic → AFE feed); `wake_detect` reads fetches and dispatches based on state — on WAKEUP_DETECTED in IDLE it triggers do_round_trip on a SEPARATE driver; `listen_forward` reads AFE fetches in parallel and pushes to `afe_out_queue` when state is LISTENING. **Or** simpler-but-equivalent: one fetch task always, but the wake event posts to a `wake_event_queue` consumed by a `round_trip_driver` task — fetch task continues running, listening forward path stays in fetch task. Pick this latter shape; it's one task less. |
| LISTENING queue policy | **Drop-newest with counter** | When the queue is full (consumer behind), drop the newest incoming frame and increment a counter. Matches the `recycled=N` diagnostic pattern from gotcha #9. Logged at the end of each `vad_record` so we can spot consumer lag. |
| `_peak_normalize_wav` on server | **Keep for now, evaluate removal post-M6** | Server-side gain compensation was the gotcha-#7 fix. AFE's AGC should obviate it, but until we measure that Whisper recall is fine without the server-side normalize, keep both. Remove in a v2-M6.x follow-up once trust is built. |

### Architecture diff (M2 → M6)

**After M2 (current):**
```
mic → i2s_mic ring (single consumer) → state-machine routing:
    IDLE      → wake.c → AFE → wakeup_state
    LISTENING → vad.c (peak threshold) → /converse upload
```

**After M6:**
```
mic → i2s_mic ring → wake_feed_task (always running)
                         │
                         ▼
                       AFE (WakeNet + VAD + NS + AGC, all on)
                       SE task pinned to core 1 (WiFi owns core 0)
                         │
                         ▼
                    wake_fetch_task → afe_fetch_result_t:
                                       ├─ wakeup_state
                                       ├─ vad_state
                                       ├─ data (denoised audio)
                                       └─ vad_cache (post-VAD onset)
                         │
                         ├─ wakeup_state == WAKENET_DETECTED
                         │    → push to wake_event_queue
                         │       (with preroll ring snapshot)
                         │
                         └─ APP_STATE == LISTENING
                              → push (data, vad_state) into afe_out_queue
                                drop-newest if full, bump counter

  wake_event_queue ─► round_trip_driver_task ─► do_round_trip_streaming...
                                                  (consumes preroll +
                                                   afe_out_queue via vad.c)

  afe_out_queue ─► vad_record (refactored) reads frames:
                     - count consecutive VAD_SPEECH frames
                     - count consecutive VAD_SILENCE frames
                     - hand audio chunks to http_converse stream
                     - stop on silence-dwell or timeout
```

Key task decoupling: `wake_fetch_task` NEVER blocks on `do_round_trip` —
it just posts wake events to a queue and continues processing fetches.
The `round_trip_driver_task` is what blocks for the multi-second
LISTENING → THINKING → SPEAKING cycle. Without this split, the LISTENING
audio queue would starve the moment a wake fired.

### Sub-phases

**M6 fallback knob (added throughout, not a sub-phase):**
`CONFIG_VAD_BACKEND` choice in Kconfig — `PEAK` (today's vad.c) or
`AFE` (M6). Default `AFE` post-merge. Lets a single build A/B between
the two backends without code changes. If a regression surfaces post-
ship, one menuconfig flip is enough to revert without re-flashing
from a tag. The PEAK code path stays compiled in; `--gc-sections`
drops whichever isn't selected.

**M6-a — Enable AFE VAD with vadnet1_medium + pin SE task to core 1**
- Enable `CONFIG_SR_VADN_VADNET1_MEDIUM=y` in `sdkconfig.defaults`
  (replaces / coexists with `CONFIG_SR_VADN_WEBRTC=y` — esp-sr picks
  whichever is selected). The vadnet1 model gets packed into the
  existing `model` partition by `movemodel.py` at build time.
- Flip `cfg->vad_init = true` (currently false in M2).
- Resolve the vadnet1 model name at init via
  `esp_srmodel_filter(s_models, ESP_VADNET_PREFIX, NULL)` (or the
  equivalent constant — check esp_vadn_models.h) and assign to
  `cfg->vad_model_name`. Log the resolved name at boot like we do
  for the wakenet.
- Set `cfg->afe_perferred_core = 1` (WiFi pins to core 0; AFE's
  SE task fighting WiFi on the same core is a known latency cliff
  in other esp-sr projects).
- Set `cfg->afe_perferred_priority = 5` to match our other tasks.
- Kconfig knob `WAKE_VAD_MIN_SPEECH_MS` (default 128) — exposes the
  duration filter for tuning if 128 ms cuts off short utterances.
- **Smoke gate before M6-b:** boot log shows
  `wake: resolved vad model: vadnet1_medium` (or similar). Fetch
  task log shows VAD transitions IDLE → SPEECH → SILENCE on real
  voice. Boot-time `recycled=N` STATS still 0. Free heap ≥ 7.8 MB
  (allow ~300 KB drop from M2 baseline to absorb vadnet1 PSRAM
  allocations).

**M6-b — Split fetch into wake_fetch + round_trip_driver tasks**
- `wake_fetch_task` (the existing one, renamed for clarity) NEVER
  calls `do_round_trip_streaming_with_preroll` directly. On
  WAKENET_DETECTED it: (1) snapshots the preroll ring, (2) posts a
  `wake_event_t {seg1, seg1_n, seg2, seg2_n, score, source}` to a
  new `wake_event_queue` (length 1, drop if full), (3) calls
  `s_afe_handle->reset_buffer()`, (4) continues fetching.
- New `round_trip_driver_task`: blocks on `wake_event_queue`,
  pops events, calls `do_round_trip_streaming_with_preroll`,
  loops. This is the task that owns the multi-second blocking
  cycle.
- Remove the `s_state != APP_STATE_IDLE` gate in feed_task. AFE
  feeds always.
- Keep `s_preroll` (1 s ring) — its semantics (last 1 s before
  wake fires) aren't substitutable by AFE's `vad_cache`.
- **Smoke gate before M6-c:** wake still fires reliably on "hey
  kira", round_trip_driver picks up the event, /converse round-trip
  works end-to-end. recycled=0.

**M6-c — AFE output queue + LISTENING audio path**
- New `afe_out_queue` (FreeRTOS queue) holds
  `afe_frame_t { int16_t *data; size_t n_samples; vad_state_t vad_state; }`
  tuples. Note: AFE owns the `data` pointer's lifetime (the buffer
  is reused on the next fetch), so the consumer MUST copy or
  process before yielding.
- Sized for ~2 s of audio (~67 chunks @ 30 ms each). Drop-newest
  policy when full; counter `afe_out_drops` exposed via a getter
  for diagnostics (logged at end of vad_record same as
  `recycled=N`).
- `wake_fetch_task`: on every fetch result, if
  `s_state == APP_STATE_LISTENING`, push to queue. Otherwise drop.
- New `wake.h` API:
  ```c
  esp_err_t wake_listen_next(int16_t **data, size_t *n_samples,
                             vad_state_t *vad_state, uint32_t timeout_ms);
  uint32_t  wake_listen_drops_take_and_reset(void);
  ```
- **Smoke gate before M6-d:** wake + manual VAD test (just use
  existing `vad.c` for now, but log the AFE vad_state in parallel
  to compare). Confirm `afe_out_drops` stays at 0 during a normal
  conversation.

**M6-d — Refactor `vad.c` to consume from AFE queue (with PEAK fallback)**
- Behind `CONFIG_VAD_BACKEND_AFE` (the new fallback knob): replace
  the inner `i2s_mic_capture_frame` + peak-threshold loop with
  `wake_listen_next` + AFE's `vad_state`. Behind
  `CONFIG_VAD_BACKEND_PEAK`: existing code unchanged.
- Same external signature: `vad_record(..., stop_reason_t *out)`
  so `do_round_trip` doesn't touch.
- State machine: same shape (has-spoken yet, silence-dwell counter,
  hard timeout) but counters increment per-chunk based on
  `vad_state == AFE_VAD_SPEECH` / `AFE_VAD_SILENCE`.
- Audio writes to http stream come from AFE's denoised data, not raw
  mic.
- **Button-wake path explicit AFE flush:** when wake source is
  BUTTON, the user pressed the button after AFE has potentially
  been in VAD_SPEECH from background noise. On entry to
  `vad_record`, call `s_afe_handle->reset()` (or equivalent state
  flush) so the button press doesn't get a stale "already speaking"
  signal from AFE.
- Delete `i2s_mic.h`'s `i2s_mic_capture` / `i2s_mic_capture_frame` /
  `i2s_mic_drain` ONLY if nothing else references them after both
  backends compile. The PEAK backend still uses them.

**M6-e — Smoke test typing rejection + STT quality + regression**

**Test prep:** server-side `_peak_normalize_wav` MUST be temporarily
disabled (in `server/app.py`) before this smoke. AFE's AGC runs
on-device now; double-normalization at the server distorts levels
and would mask whether AFE's on-device AGC is sufficient. Re-enable
after the test, or remove permanently if the AGC proves good enough.

1. **Typing test:** wake the device, ask one short follow-up to enter
   continuous mode, then type at normal cadence near the mic for
   30 seconds. Expected: **zero `/converse` triggers, zero `[xxx]
   transcript:` log lines.** Before M6 this would have triggered
   several false turns.
2. **Clicking test:** same, but mouse-click rapidly. Same target.
3. **Quiet-speech recall:** speak a short follow-up at conversational
   volume from typical reading distance. Should still trigger a turn.
4. **STT quality A/B:** ask "what's the weather in Mongolia like right
   now?" twice — once with `CONFIG_VAD_BACKEND_PEAK` (raw mic,
   pre-M6 behavior), once with `CONFIG_VAD_BACKEND_AFE` (M6).
   Compare transcripts qualitatively. Note any difference in proper-noun
   accuracy in the commit body. **Don't re-enable the server-side
   normalize between runs** — that's the variable we're testing.
5. **5-turn regression:** standard `recycled=N` + `afe_out_drops`
   smoke test. Both target 0 across all turns.

### Memory + resource budget (estimates — confirm after build)

- AFE always-on adds ~50–80 KB PSRAM for the VAD pipeline core.
- vadnet1_medium model: +100–150 KB PSRAM at runtime + ~50–100 KB
  in the `model` partition (the existing 2 MB partition has ~1.7 MB
  free after wn9_heykira_tts3's 284 KB; vadnet1 fits comfortably).
- The `s_preroll` ring stays — 32 KB heap. (Earlier draft proposed
  retiring it; the advisor's preroll-semantics catch reverted that.)
- Net delta from M2 baseline: ~+150–230 KB PSRAM used. Free heap
  expected to drop from ~8.13 MB → ~7.9 MB after AFE init.

### Risks + open questions

1. **vadnet1 false negatives on quiet speech.** ML models can
   under-predict when input is far from training distribution.
   If real speech is missed, we have escape hatches:
   `WAKE_VAD_MIN_SPEECH_MS` can be dropped from 128 → 80 to
   accept shorter sustained intervals, and `vad_mode` can be
   tuned via the existing `vad_mode_t` field. Worst case: fall
   back to WebRTC VAD by flipping `CONFIG_SR_VADN_WEBRTC=y`
   (the existing model partition still has room for both).
2. **`vad_min_speech_ms = 128 ms` could clip "yes" / "no" / "stop".**
   Single-syllable English words are ~150-250 ms. 128 ms is below
   that, so the floor should be safe — but worth measuring.
3. **AFE-output audio is single-channel post-NS.** Whisper expects
   16 kHz mono int16 — same as today. No format change for the
   server. But the audio is no longer the literal raw mic samples,
   so any server-side debugging that assumes "this is what the mic
   heard" needs an update. The `samples/_last_received.wav` debug
   dump still captures whatever the server received — still useful,
   just denoised now.
4. **AEC interaction in M5.** When M5 lands and adds AEC to the same
   AFE config, the same audio pipeline carries through — no rework.
   M6 is exactly the foundation M5 needs.
5. **What if AFE is busy and `fetch` times out?** Today the fetch
   default timeout is 2000 ms. During LISTENING, queue contention
   shouldn't be an issue (no other consumer). Worst case: an
   `AFE_FETCH_TIMEOUT` event triggers a state recovery — log + move on.
6. **AFE-frame data ownership.** AFE's fetch returns a `data` pointer
   into its internal buffer that's reused on the next fetch. The
   `afe_out_queue` consumer (`vad_record`) must either copy each
   frame before the next fetch overwrites it, OR maintain the
   invariant that fetches don't outpace consumption. The latter is
   fragile — copy on consume. ~960 B per copy (480 samples × 2),
   ~32 copies/s @ 30 ms cadence ≈ 30 KB/s memcpy, trivial.

### Decisions resolved 2026-05-12

**vadnet1 upfront** (user decision). Skips the possible WebRTC →
vadnet1 swap cycle; locks in the stronger ML-based VAD from the
start. Memory cost is comfortable (under +250 KB PSRAM total).

---

## Stage gates between milestones

Per CLAUDE.md's "ship one milestone per commit" discipline, each
milestone is one commit with smoke-test metrics in the body.
**Don't bundle.** The May 2026 audio-corruption mis-diagnosis
(gotcha #9) happened because M7 + M8 + cat-face shipped as one
commit and jointly degraded performance in ways that took ~24 h
of debug to localize.
