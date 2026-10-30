# Loopback test — audio corruption verdict + control experiment

**Date:** 2026-05-10
**Network:** Enterprise Wi-Fi 2.4 GHz, RSSI -77 to -81 at bench spot
**Firmware:** current `main` (commit `5d8ad66`)
**Device state at end of session:** baseline firmware reflashed; back to commit-`5d8ad66`-equivalent

## TL;DR

1. **Verdict:** audio corruption is **firmware-side**, not network-side.
   16.5% of mic frames are recycled by the producer over 20 turns on
   the test bench. Same network sustained **0 errors / 750 frames** in the
   synthetic throughput test (commit `b0da8ef`), so transit is fine.
2. **Root cause (localized this session):** **the per-frame OLED VU-bar
   redraw in `cb_frame` is the dominant source of producer-side
   recycle.** Disabling it via a `CONTROL_EXP_OLED_VU_OFF` build
   (compile-time gate around the `oled_flush_row(WS_BAR_LINE)` block
   at [firmware/main/main.c:211-225](firmware/main/main.c:211))
   drops recycled frames from **16.5% → 0.0%** and `ring overflow`
   warnings from **89+ → 2** across a matched 5-iteration run.
3. **What to do:** drop the live VU bar from LISTENING, or move it to
   only redraw on state transitions (one-shot per turn instead of
   every 3rd frame). The bar is cosmetic; the cost is real.

## Experiments

### Method

Workstation speakers (Realtek, MME) play `hey jarvis` (Piper, 22050 Hz)
then `samples/jfk.wav` (11 s, 16 kHz) as separate `sd.play()` calls.
Device wakes, captures, POSTs to `/converse`. Server saves each upload
to `samples/_loopback_turn_<unix-ts>.wav` (post-normalize) +
`.bin` (raw pre-normalize), gated by `LOOPBACK_KEEP_PER_TURN=1`. Serial
capture via `tools/serial_capture.py` collects every
`STATS[vad] ... recycled=N` line for offline pairing.

**Discriminator (gotcha #9 + advisor):**

1. Primary: `recycled=N` count from firmware STATS (producer-side, ground truth).
2. Secondary: WAV duration shortfall vs expected (~13.5 s = passage + VAD tail).
3. Tertiary: WER vs the whisper reference transcript of `jfk.wav` (acoustic loopback adds intrinsic noise, so some WER on clean turns is normal — only useful when corroborating 1+2).

### Baseline (current main firmware)

Pass 1 — 10 stimulus iterations + 1 sanity prefix = 12 turns total
(no per-turn WAVs; LOOPBACK env var was missing for pass 1):

| # | pushed | recycled | loss % |
|---|---|---|---|
| 1 (sanity, silent) | 190 | 89 | **31.9%** |
| 2 | 131 | 48 | **26.8%** |
| 3 | 194 | 119 | **38.0%** |
| 4 | 235 | 13 | 5.2% |
| 5 | 118 | 0 | 0.0% |
| 6 | 390 | 128 | **24.7%** |
| 7 | 390 | 0 | 0.0% |
| 8 | 375 | 134 | **26.3%** |
| 9 | 75 | 19 | **20.2%** |
| 10 | 133 | 0 | 0.0% |
| 11 | 323 | 0 | 0.0% |
| 12 | 375 | 0 | 0.0% |
| **total** | **2929** | **550** | **18.8%** |

Pass 2 — 5 iterations, 8 turns total, with per-turn WAVs:

| # | pushed | recycled | loss % | recv-dur s | peak | WER | transcript (first 80 c) |
|---|---|---|---|---|---|---|---|
| 1 | 75 | 56 | **74.7%** | 2.59 | 3684 | 0.91 | Thank you, thank you. |
| 2 | 326 | 5 | 1.5% | 23.76 | 7188 | 1.00 | Hey, Jarvis. |
| 3 | 65 | 1 | 1.5% | 3.20 | 4488 | 0.68 | what you wish, what your country can do for you. |
| 4 | 300 | 73 | **24.3%** | 16.29 | 6588 | 0.86 | Hey, Jarvis And so, my fellow Americans |
| 5 | 33 | 0 | 0.0% | 2.64 | 3228 | 0.91 | Thank you. Thank you. Thank you. |
| 6 | 299 | 24 | 8.0% | 20.08 | 7368 | 0.41 | Hey, Jarvis, and so my fellow Americans, ask not what your country can do for... |
| 7 | 419 | 61 | **14.6%** | 26.72 | 6948 | 0.68 | Hey, Jarvis, and so my fellow Americans, ask not what you |
| 8 | 225 | 0 | 0.0% | — | — | — | (heartbeat-only, no turn) |
| **total** | **1742** | **220** | **12.6%** |

**Combined baseline (20 turns):** pushed=4671, recycled=770, loss=**16.5%**
8/20 turns had ≥20% loss; 8/20 were clean.

### Control: VU-bar redraw disabled (`CONTROL_EXP_OLED_VU_OFF=1`)

Same hardware, same network, same stimulus, same 5 iterations. Build
gate compiled the live VU-bar update out of `cb_frame`; everything
else identical (state-machine OLED screens, cat-face panel, gesture/IR
stays disabled, PS_NONE + TCP_SND_BUF=32768).

| # | pushed | recycled | loss % | recv-dur s | peak | WER | transcript (first 80 c) |
|---|---|---|---|---|---|---|---|
| 1 | 169 | 0 | 0.0% | 14.86 | 7068 | **0.05** | Jarvis. And so my fellow Americans, ask not what your country can do for you,... |
| 2 | 63 | 0 | 0.0% | 5.04 | 12912 | 0.95 | Thank you. |
| 3 | 284 | 0 | 0.0% | 22.72 | 7248 | **0.09** | Hey, Jarvis. And so, my fellow Americans, ask not what your country can do fo... |
| 4 | 376 | 0 | 0.0% | 30.08 | 6060 | 1.45 | Hey, Jarvis. And so, my fellow Americans, ask not what your country can do fo... |
| 5 | 47 | 0 | 0.0% | 3.76 | 5076 | 0.95 | Thank you. |
| 6 | 214 | 0 | 0.0% | 17.12 | 6804 | **0.00** | And so, my fellow Americans, ask not what your country can do for you, ask wh... |
| 7 | 0 | 0 | — | 2.72 | 4908 | 1.00 | done. |
| **total** | **1153** | **0** | **0.0%** |

`ring overflow during LISTENING` warnings: **2 over 350 s** (vs. ~89
during the baseline 5-iter pass-2 window — direct compare).

When real JFK content was captured, **WER dropped from 0.41-1.0
(baseline pass 2) to 0.00-0.09 (control)** — three turns at near-zero
WER. The short-turn "Thank you" / "done" outputs are reply self-wake
artifacts from continuous-mode follow-up (the workstation's TTS reply
playing through the same Realtek speakers re-wakes the device);
they're measurement noise, not corruption.

### Side-by-side

| metric | baseline | VU-off | delta |
|---|---|---|---|
| Pushed frames | 4671 (20 turns) | 1153 (6 turns) | — |
| Recycled frames | 770 | **0** | -100% |
| Loss rate | 16.5% | **0.0%** | -16.5 pp |
| Ring overflow lines | ~89+ | **2** | -98% |
| Best-turn WER (real JFK) | 0.41 | **0.00** | -0.41 |
| Worst-turn loss | 74.7% | 0.0% | -74.7 pp |

## Why this matters

The verdict shifts from **"firmware-side, root cause TBD"** to
**"firmware-side, root cause LOCALIZED: per-frame `oled_flush_row()`
contention on the I²C consumer task starves the mic-ring HTTP-write
consumer enough that the producer recycles."**

Mechanism (informed by the data + gotcha #9):

- The VU bar fires every 3 frames during LISTENING (cadence ~240 ms).
- Each redraw is `~3.5 ms` of I²C bus time per
  [oled.h:51-52](firmware/main/oled.h:51).
- Bus-0 I²C is synchronous and serialized on the same task that
  runs `cb_frame` → `http_converse_stream_write`.
- Under sustained streaming at 32 KB/s, the 3.5 ms hit lands often
  enough to slip the consumer past the producer's add cadence,
  forcing the ring to recycle.
- Aggregating across all the redraws in one turn: at ~80 frames/s
  capture rate, every 3rd-frame redraw = ~27 redraws/s × 3.5 ms =
  ~95 ms of consumer CPU stolen per second, on a single-core MCU
  also running WiFi, TCP, and the cat-face task. That's enough to
  starve `xRingbufferReceive` at the wrong moments.

## Recommended fix (not implemented this session)

Cheapest: **make the LISTENING VU bar event-driven, not per-frame.**
Options in order of effort:

1. **Drop it entirely.** Boot-card already shows the threshold marker;
   user gets visual feedback from the cat face's `?` animation. Zero
   downside, total fix.
2. **Redraw on state change only.** Move the `oled_flush_row` out of
   `cb_frame`; just flush once on enter-LISTENING and on exit. Visual
   confirmation of "I'm hearing you" is preserved.
3. **Throttle to ~1 Hz.** Redraw at most every 1 s (instead of every
   240 ms). Visual smoothness drops; producer pressure drops by 4×.
   Lower-confidence fix; will need re-running this control with
   the slowed-down redraw to confirm 16.5% → ~4%.

The data favors option 1 or 2.

## What this does NOT rule out

The throughput test's two transient 700-1000 ms write stalls
([notes/network-diagnosis-summary.md](notes/network-diagnosis-summary.md))
are still in the production firmware's send path — they didn't cause
recycle when the producer was unloaded (throughput test) and they
don't cause recycle now that the VU bar's gone, but they could re-emerge
under harsher RF (lower RSSI, more 2.4 GHz contention, more clients on
the AP). The next-most-suspect causes from gotcha #9 — `/listen`↔`/converse`
handoff window contention, and the ~27 KB heap low-water mark — also
remain unaddressed. They just aren't dominant.

## Build hook used (now removed)

A `CONTROL_EXP_OLED_VU_OFF` env var was wired into
[firmware/main/CMakeLists.txt](firmware/main/CMakeLists.txt) to
compile-out the VU-bar block. Both the source `#if` guard and the
CMake env-var hook were **reverted at the end of this session**;
the device was reflashed with baseline firmware. To repeat the
experiment, re-add a temporary guard and:

```powershell
. $env:IDF_PATH\export.ps1
$env:CONTROL_EXP_OLED_VU_OFF = "1"
cd firmware
idf.py reconfigure   # mandatory — env var only re-read at configure
idf.py build
# verify: grep CONTROL_EXP_OLED_VU_OFF firmware\build\compile_commands.json
idf.py -p COM3 flash
```

After test, unset and rebuild to restore baseline:

```powershell
Remove-Item Env:CONTROL_EXP_OLED_VU_OFF
idf.py reconfigure
idf.py build
idf.py -p COM3 flash
# verify: grep returns 0 lines
```

## Follow-up control: face_task — innocent

After the VU-bar fix landed, the residual 2.9% loss across 4 turns
included one spike (recycle=31, 12% of one turn) coinciding with a
3-second sustained ring-overflow burst. Suspected `face_task`
(`oled_face_flush()` ~3.7×/s during LISTENING on I²C bus 1). Test:
gate `xTaskCreate(face_task, ...)` behind `CONTROL_EXP_FACE_OFF=1`,
rebuild, reflash, re-run 5-iter loopback.

| run | turns | pushed | recycled | loss | ring overflows |
|---|---|---|---|---|---|
| baseline (VU on, face on) | 20 | 4671 | 770 | 16.5% | ~89 |
| VU off, face on | 4 | 1063 | 31 | **2.9%** | 5 |
| VU off, face off | 6 | 1571 | 153 | 9.7% | 16 |

**Face-off was WORSE.** Two spike turns (recycle=71, recycle=82),
each coinciding with 6-8-in-a-row ring overflows. Pattern matches
the throughput test's sustained-WiFi-stall signature, not OLED
contention. With only 5 iterations the 2.9% vs 9.7% gap is sample
variance — the takeaway is that face_task isn't pulling weight
either way.

**Conclusion: the remaining loss is WiFi/networking-side, not
firmware-side OLED.** The cat-face animation stays in. Next thing
worth trying is a TX absorption window or producer throttle when
the ring is >50% full, so that the WiFi stalls don't immediately
translate to recycle.

face_task gate reverted; device firmware is back at commit
`5695da9` (VU bar removed, face_task spawning).

## Artifacts

| File | Purpose |
|---|---|
| `samples/baseline/_loopback_turn_*.{wav,bin}` | Pass-2 baseline (7 turns) |
| `samples/_loopback_turn_*.{wav,bin}` | VU-off control run (7 turns) |
| `samples/vu-off/*` | Same VU-off WAVs, archive copy |
| `notes/baseline/loopback-monitor-pass1-full.log` | Pass-1 serial capture |
| `notes/baseline/loopback-monitor.log` | Pass-2 serial capture |
| `notes/loopback-monitor-vu-off.log` (also `vu-off/`) | Control serial capture |
| `notes/baseline/loopback-iterations.jsonl` | Pass-2 stimulus timestamps |
| `notes/loopback-iterations.jsonl` (also `vu-off/`) | Control stimulus timestamps |
| `notes/loopback-reference-transcript.txt` | Whisper transcript of `samples/jfk.wav` |
| `notes/loopback-test-results.md` | this file |

## Re-running the loopback test

```powershell
$env:HOST = "0.0.0.0"
$env:LOOPBACK_KEEP_PER_TURN = "1"
.\.venv\Scripts\python.exe -m server.app

# in another shell:
.\.venv\Scripts\python.exe tools\serial_capture.py COM3 350 > notes\loopback-monitor.log 2>&1 &
.\.venv\Scripts\python.exe tools\loopback_test.py 5
.\.venv\Scripts\python.exe tools\loopback_analyze.py
# verdict in notes/loopback-test-results.md (analyzer overwrites)
```
