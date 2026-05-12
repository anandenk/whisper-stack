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

- **Working dir:** `C:\voice-ai-server` (local checkout still uses the pre-rename name; everything else — repo, docs, build commands — uses `whisper-stack`. The rename header at the top of this file describes the gap. Mentally substitute when you read build recipes that say `cd C:\whisper-stack\...`.)
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
- [notes/esp32-firmware-plan.md](notes/esp32-firmware-plan.md) — port `tests/jarvis.py`'s shape onto **ESP-WROOM-32 + INMP441 + SSD1306 OLED**. **All 8 milestones shipped on real hardware**: M1 bring-up, M2 button + buffered `/converse`, M3 VAD streaming, M4 server-side openWakeWord via `POST /listen`, M5 continuous mode after wake, M6 OLED polish (boot card + wake splash + VU bar + speaking screen). Plus M7 (HC-SR04 wave-to-wake), M8 (NEC IR remote for mute/snooze/cancel), and the cat-face OLED on I²C bus 1. Device is mic + display + WiFi only — TTS plays through the workstation's pinned Realtek output via `play_locally=true` on `/converse`. No on-device audio out (no I²S DAC, no audio playback task, no PCM queue). On-device esp-skainet wake word remains deferred to v2 hardware (needs PSRAM). The audio-corruption diagnosis (gotcha #9 below) localized a chronic mic-frame recycle issue to the LISTENING VU bar's per-frame I²C contention and removed it; firmware is now stable for arbitrarily long continuous chat.
- **v3 prototype notes (early — hardware not yet finalized or ordered):**
  - [notes/build-modes.md](notes/build-modes.md) — the `CONFIG_VOICE_ENABLED` Kconfig switch that gates the entire voice path so display/servo iteration doesn't compete with the wake-word stream.
  - [notes/face-renderer.md](notes/face-renderer.md) — FluxGarage RoboEyes V1.1.1 ported into `firmware/components/robo_eyes/` as a second selectable face renderer (`CONFIG_FACE_RENDERER` choice, default `ROBO_EYES`, alternative `CAT_FACE`). **This is the reason the repo is now GPL-3.0** — see the LICENSE section below.
  - [notes/motion-character.md](notes/motion-character.md) — the eye-leads / head-follows gaze coupling architecture: RoboEyes drives the eyes autonomously, a small follower task reads `robo_eyes_get_eye_x_angle()` every 50 ms and commands the SG90 servo over an 800 ms eased move. Gated behind `CONFIG_GAZE_COUPLING_DEMO` (default `n`).
  - [notes/servo-motion-language.md](notes/servo-motion-language.md) — the SG90 servo's motion vocabulary, LEDC config, easing curves, and the standalone `firmware/examples/servo_test/` IDF project for isolated bench iteration.

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

**Firmware milestones in particular: ship one per commit, never bundle.**
After each milestone lands, run the smoke test — N consecutive voice
turns from the deployment spot, check `recycled=N` STATS at end of every
turn, free heap at boot, key task timings — and record the result in the
commit message body or a notes file. If milestone N degrades the smoke
test vs the prior commit's baseline, investigate before stacking
milestone N+1 on top. The May 2026 audio-corruption mis-diagnosis
(gotcha #9) happened because M7 + M8 + cat face shipped bundled into one
commit, jointly exceeding the sustained-load budget in ways that took
~24 hours of debug to untangle. With separate commits, the introducing
milestone would have failed its smoke test and the regression would have
been caught at the source.

## Known issues / TODO

- **Router Tier 1 shipped 2026-05-10.** The 95-query held-out fixture
  ([tests/router_eval.py](tests/router_eval.py)) now scores **85.3%
  at production margin 0.03** (up from 68.4% at the original 0.05),
  with **only 4 under-routings** (false-simple — the asymmetric error)
  vs 8 before. Eight of twelve categories at 100%; remaining failures
  are mostly judgment-call borderlines and a few `quick_lookup` items
  hugging the threshold. Tier 1 changes: lowered `ROUTER_MARGIN_S`
  default 0.05 → 0.03; added regex shortcut for pure arithmetic +
  single-word queries (zero-latency bypass); curated SIMPLE/COMPLEX
  example sets for the weak neighborhoods. Re-run
  `python tests/router_eval.py` after any router change to confirm
  no regression. Tier 3 (LLM classifier) is **explicitly deferred**
  — Tier 1 plateaued at a comfortable number, and the LLM tax
  (~150 ms per turn, doubles cloud token spend) is hard to justify
  for the remaining ~15 pp of headroom against mostly-borderline
  failures.

- **Audio corruption root-cause FIXED 2026-05-10.** Localized via the
  acoustic loopback control experiment (see gotcha #9 + commit `5695da9`
  + [notes/loopback-test-results.md](notes/loopback-test-results.md))
  to per-frame `oled_flush_row()` I²C contention inside `cb_frame`.
  Removing the LISTENING VU bar dropped recycle from 16.5 % → 0.0 %
  and `ring overflow` warnings from ~89 → 2 across matched 5-iter
  runs. Residual ~3 % loss matches the throughput test's transient
  WiFi-stall pattern, not firmware. **Open follow-up:** verify
  whether gesture (`CONFIG_WAKE_GESTURE_ENABLED`) and IR
  (`CONFIG_IR_ENABLED`) can be safely re-enabled now that the VU
  bar is gone — they were disabled during bisection and not
  individually re-tested with the VU fix in place. Re-enable one
  at a time, run `tools/loopback_test.py` + `loopback_analyze.py`
  after each, only commit if recycle stays at baseline.

- **Automated regression smoke test (queued, not built).** A pyserial
  script that flashes a build, runs N voice turns (or replays via the
  throughput-test mode shipped 2026-05-10 — see
  [notes/network-diagnosis-summary.md](notes/network-diagnosis-summary.md)
  for the recipe), parses `recycled=N` from the serial monitor, and
  fails if the count exceeds a baseline recorded in a checked-in JSON.
  Would prevent regressions like the M7+M8+cat-face bundling from
  shipping unnoticed. Real work — not for the next session, but worth
  doing once v2 lands and we're back to active feature work.

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

7. **Audio gain pipeline lives on the server, not the device.** INMP441 +
   the firmware's natural `>>16` 24→16-bit conversion produces
   conversational-speech peaks of only ~1500-2000 in int16 — clean but
   so quiet that whisper hallucinates and openWakeWord misses wake
   events. We compensate at *two* server-side stages:
   - **`/converse`**: `_peak_normalize_wav()` in `server/app.py` reads
     each upload, finds the peak, scales to a target peak (~24000)
     with a max-gain cap (~12×) and silence floor (~200). Adaptive,
     never clips. Tunable via `NORMALIZE_TARGET_PEAK`/`NORMALIZE_MAX_GAIN`
     constants — no firmware reflash to retune.
   - **`/listen`**: `wakeword.py` applies a fixed `LISTEN_GAIN` (default
     6×) to each 80 ms frame before `model.predict()`. Boosts wake-word
     recall dramatically. Fixed (not adaptive) because frame-level
     normalization would amplify silence noise into false positives.
   - **Debug listening**: every `/converse` upload is also dumped to
     `samples/_last_received.wav` (post-normalize) so you can hear
     exactly what whisper saw.

   We deliberately do *not* compensate in firmware — server-side gain
   is way easier to tune (no reflash) and the lost-precision argument
   (firmware shifts away the bottom 8 bits of 24-bit data) is
   academic, since those bits are dominated by INMP441's noise floor
   for voice levels.

8. **`/converse` socket timeout is 30 s, not the original 5 s.** The
   `FastFailHandler` in `server/app.py` was added at M4 with
   `timeout=5` to recover stuck `/listen` connections. But it applies
   globally, and 5 s is too aggressive for `/converse` chunked uploads
   under enterprise Wi-Fi jitter — long recordings hit
   `TimeoutError: timed out` mid-`request.get_data()` and surface as
   500s. 30 s is well above the firmware's per-frame cadence (~80 ms)
   and the wakeword module's own 2 s lock-acquire timeout still
   handles short-term `/listen` contention.

9. **Audio corruption (`recycled=N`, `ring overflow during LISTENING`)
   was originally misdiagnosed as a weak-WiFi problem on 2026-05-09;
   PROVEN INCORRECT on 2026-05-10 by a synthetic throughput test;
   ROOT CAUSE LOCALIZED later that day by an acoustic-loopback control
   experiment. The dominant cause was the per-frame `oled_flush_row()`
   call in `cb_frame` — synchronous I²C bus 0 contention with the
   HTTP-write task. Fixed in commit `5695da9` (drop the LISTENING VU
   bar). Reduced producer-side mic-frame recycle from 16.5 % → 0.0 %
   and `ring overflow` warnings from ~89 → 2 across matched 5-iteration
   loopback runs.** **Future sessions: do not assume "ring overflow
   → weak WiFi"**. Treat residual losses as WiFi-transient territory
   and instrument the consumer-side first.

   Two diagnostic experiments back this verdict, both reproducible:

   1. **Synthetic throughput test** (commit `b0da8ef`,
      [notes/network-diagnosis-summary.md](notes/network-diagnosis-summary.md))
      — bypassed mic / VAD / wake / OLED entirely, opened one chunked
      POST to `/converse`, wrote silent 1280-sample int16 frames at
      80 ms intervals (= 32 KB/s) for 60 s. **Result: 750 frames sent,
      0 errors, 34 KB/s avg at RSSI -77 on Enterprise Wi-Fi 2.4 GHz**;
      `finish` returned `ESP_OK status=200`. Two transient `last_write`
      stalls (730 ms, 1030 ms) fully recovered. So the WiFi link, TCP
      path, and our HTTP code are all fine in isolation.
   2. **Acoustic loopback control** (commits `309de01`, `f4527ef`,
      `5695da9`, `057a26d`,
      [notes/loopback-test-results.md](notes/loopback-test-results.md))
      — workstation speakers play `hey jarvis` + `samples/jfk.wav` in
      a loop; server saves each `/converse` upload to
      `samples/_loopback_turn_<ts>.wav`; serial monitor captures
      `STATS[vad] ... recycled=N`. Baseline: **20 turns, 16.5 % loss,
      8/20 turns ≥20 % loss**. Control with VU bar gated out
      (`CONTROL_EXP_OLED_VU_OFF=1`): **0/1153 frames recycled, best-turn
      WER 0.41 → 0.00**. Same hardware, same network, same stimulus,
      delta = the OLED VU redraw.

   Recognize the symptom:
   - **`STATS[vad] ... recycled=N` with N > 0** in the firmware monitor.
     Ground-truth count of frames the producer threw away. Logged at end
     of every `vad_record` in `vad.c` via `i2s_mic_dump_stats("vad")`.
   - **`ring overflow during LISTENING` warnings** firing repeatedly
     (rate-limited to 1 Hz; absence is not proof — use STATS as the
     authoritative signal).
   - **Transcripts with words missing**, or audio in
     `samples/_last_received.wav` audibly chunky vs what was said.

   Root cause (FOUND, fixed in `5695da9`):
   3. **OLED I²C bus contention from LISTENING-state VU-bar redraws.**
      `oled_flush_row(WS_BAR_LINE)` ran every 3 frames (~240 ms cadence)
      inside `cb_frame`, on the same task as `http_converse_stream_write`.
      I²C bus 0 is synchronous: ~3.5 ms × ~27 redraws/s = ~95 ms/s of
      consumer CPU stolen, on a single-core MCU also running WiFi, TCP,
      and the bus-1 cat-face task. Enough to slip the consumer past the
      producer's 80 ms add cadence, forcing the mic ring to recycle.

   Other candidates from the original suspect list — examined and
   downgraded (data in [notes/loopback-test-results.md](notes/loopback-test-results.md)):
   1. `/listen` ↔ `/converse` handoff socket contention — plausible
      mechanism but not the dominant signal; residual ~3 % loss
      post-VU-fix matches the synthetic throughput test's transient
      stall pattern, not handoff-shaped.
   2. mic capture loop (`xRingbufferReceive`) blocking HTTP-write
      consumer — same task ordering as #3, but the per-iteration
      cost is much smaller (no I²C wait) so it's a non-issue once
      I²C work leaves `cb_frame`.
   4. **Cat-face `face_task`** (separate I²C bus 1, ~3.7 flushes/s) —
      tested with `CONTROL_EXP_FACE_OFF=1`, results inconclusive at
      N=4 vs N=6 (declared "innocent" by the loopback session, but the
      sample is small — re-test with more iterations if it ever comes
      under suspicion again).
   5. Heap pressure: production heap was ~107 KB at boot pre-fix vs
      ~209 KB in throughput-test mode. Did not cause OOM; was a
      contributing comfort factor, not the dominant cause.

   Active fixes / defensive measures (all should remain in place):

   - **VU-bar removed** (commit `5695da9`) — the actual root-cause fix.
     Don't add anything I²C-touching back into `cb_frame` or any other
     hot path called per mic frame; if you need a "I'm listening"
     visual, drive it from a separate task or only on state transitions.
     The cat-face `?` animation already provides this feedback.
   - **`esp_wifi_set_ps(WIFI_PS_NONE);`** in `firmware/main/wifi.c`
     after `esp_wifi_start()` (commit `133a95d`). Removes the 5–15 ms
     beacon-wake latency of the default `WIFI_PS_MIN_MODEM`. Marginal
     effect measured in isolation but cheap insurance.
   - **`CONFIG_LWIP_TCP_SND_BUF_DEFAULT=32768`** (and matching `WND`,
     commit `133a95d`). Default 5760 is too small at our packet rates.
     Two constraints to respect:
     - Kconfig range is `2440..65535` with `LWIP_WND_SCALE` off. **Do
       not use 65536** — Kconfig silently reverts to the default.
     - Larger send buffers consume per-socket heap. With production's
       ~96 KB free heap at boot, 65 KB × 2 sockets would have
       OOM-crashed mid-conversation. **Stick with 32768.**
   - **`# CONFIG_WAKE_GESTURE_ENABLED is not set` and
     `# CONFIG_IR_ENABLED is not set`** in `sdkconfig`. Disabled during
     the 2026-05-10 bisection (gesture poll 50 ms cadence, IR RMT-RX
     interrupt overhead). With the VU bar fix landed, **may no longer
     be necessary** — but not yet re-tested. If you want them back,
     re-enable ONE at a time per the milestone discipline, run a
     loopback regression after each (`tools/loopback_test.py 5` +
     `tools/loopback_analyze.py`), and only commit if recycle stays
     ≤ baseline.

   Things that DON'T fix the chronic problem (don't waste time on these):
   - Better RSSI alone — tested at -60 vs -79; both fail with full
     feature set enabled.
   - Bigger mic ring buffer — current 64 KB is amply sized.
   - Reverting to an earlier commit (e.g., before gesture/IR were
     added) — bisection at 9ff64b2 (May 6) also degraded eventually,
     just later. Same code path, same failure mode.

   Long-term fix: on-device wake-word (`esp-skainet` on the ESP32-S3
   v2 hardware that's already on order — see
   [notes/esp32-firmware-plan.md](notes/esp32-firmware-plan.md))
   eliminates the continuous `/listen` stream entirely, removing the
   chronic exposure window. ESP32-C5 (5 GHz dual-band) would also
   help by reducing 2.4 GHz contention, but it doesn't fix the
   architectural issue the way on-device wake does.

   To re-run the throughput test in a future session, see the
   step-by-step reconstruction recipe in
   [notes/network-diagnosis-summary.md](notes/network-diagnosis-summary.md).
   The reusable Python helpers (`tools/throughput_sink.py`,
   `tools/serial_capture.py`) are still in tree — only the firmware
   test mode in `main.c` needs to be re-added (~25 lines).

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
notes/sessions-plan.md       multi-turn session memory (A–E shipped)
notes/voice-agent-plan.md    streaming TTS / VAD / continuous / wake (1–6 shipped)
notes/esp32-firmware-plan.md ESP-WROOM-32 firmware plan (M1–M8 shipped)
notes/build-modes.md         v3 prototype — CONFIG_VOICE_ENABLED Kconfig gate
notes/face-renderer.md       v3 prototype — RoboEyes face renderer + GPL-3.0 rationale
notes/motion-character.md    v3 prototype — eye-leads / head-follows gaze coupling
notes/servo-motion-language.md  v3 prototype — SG90 driver + motion vocabulary
notes/router-design.md       router classifier design + Tier 1 history
notes/network-diagnosis-summary.md  throughput-test reconstruction recipe
notes/loopback-test-results.md  acoustic loopback control experiment (gotcha #9)
requirements-cloud.txt       slim cloud install (no GPU/Piper/Whisper)
LICENSE                      GPL-3.0 (entered the project with the RoboEyes port)
firmware/                    ESP-IDF v5.3.5 project (M1–M8 shipped + cat-face panel)
├── main/                    application code:
│   ├── main.c               state machine, button polling, OLED orchestration,
│   │                        face_task (cat-face or RoboEyes), optional servo follower
│   ├── app_state.h          app_state_t + ENABLE_OLED/MIC/WIFI subsystem toggles
│   ├── oled.{c,h}           SSD1306 driver (status panel + face panel multi-panel API)
│   ├── cat_face.h           auto-gen from tests/oled_dump_c.py — legacy face renderer
│   ├── wifi.{c,h}           WPA2-PSK or WPA2-Enterprise STA (Kconfig choice)
│   ├── i2s_mic.{c,h}        INMP441 → 16 kHz int16 ring buffer + STATS dump
│   ├── vad.{c,h}            peak-threshold VAD with cancel hook (M3+)
│   ├── http_client.{c,h}    esp_http_client wrapper for /converse (buffered + streaming)
│   ├── listen_stream.{c,h}  raw-socket /listen client + 500 ms pre-roll (M4)
│   ├── wake_source.h        wake_source_t enum (VOICE / GESTURE / BUTTON / IR)
│   ├── gesture.{c,h}        M7 — HC-SR04 wave-to-wake (runtime-gated)
│   ├── ir_remote.{c,h}      M8 — NEC IR receiver via RMT-RX (runtime-gated)
│   ├── control_state.{c,h}  M8 — shared mute/snooze/cancel state with mutex
│   └── Kconfig.projbuild    WiFi creds, server addr, API key, session id,
│                            FACE_RENDERER choice, VOICE_ENABLED, GAZE_COUPLING_DEMO,
│                            M7/M8 submenus
├── components/              v3 prototype components:
│   ├── robo_eyes/           FluxGarage RoboEyes V1.1.1 port — GPL-3.0
│   └── servo/               ESP-IDF LEDC-based SG90 driver
├── examples/
│   └── servo_test/          standalone IDF project for isolated servo bench iteration
├── sdkconfig.defaults       committed defaults; idf.py menuconfig fills creds
└── .gitignore               build/, sdkconfig (creds), managed_components/, *.ref
tests/oled_animate.py        live tkinter face state-machine preview (cat-face design)
tests/oled_preview.py        static PNG + C-array dump for one frame
tests/oled_dump_c.py         regenerate firmware/main/cat_face.h from poses
tools/                       diagnostic helpers (throughput_sink, serial_capture, loopback)
```

## Firmware (ESP-WROOM-32)

ESP-IDF v5.3.5 project at `firmware/`. Build target is `esp32` (LX6
classic, NOT s3). **M1 through M8 all shipped + cat-face OLED** (M5
+ M6 both landed in the same workstation session 2026-05-06:
continuous chat, then OLED polish — boot card, wake splash, live
VU bar, model+tokens speaking screen. M7 + M8 + cat-face all shipped
2026-05-10. Audio-corruption diagnosis and fix — gotcha #9
below — landed later that same day). State of the device:

- OLED renders status text, WiFi STA gets a DHCP lease on enterprise Wi-Fi
  via WPA2-Enterprise (802.1X), INMP441 captures audio with RMS/peak
  responsive to sound.
- **Hands-free wake** (M4): say "hey jarvis", server-side openWakeWord
  fires, device transitions to LISTENING with a **1 s pre-roll** (bumped
  from 512 ms after real-use feedback — 500 ms wasn't enough to catch
  short lead-in words like "what is the X" when wake fires mid-"jarvis").
  BOOT button is preserved as a manual-wake fallback.
- VAD-bounded recording (M3) streams over **HTTP chunked-transfer**
  as it's captured — no heap cap on length. VAD stops on 2.5 s of
  silence (`vad.h`) after voice has been heard, or returns NO_SPEECH
  after 15 s of initial silence, or BUTTON_CANCEL on a ≥1 s long-press.
- Server replies (`/converse?omit_audio_base64=1`) come back as
  small JSON; workstation Realtek output speaks the answer; OLED
  shows transcript + speaking countdown.
- **Continuous mode after wake** (M5): once a turn finishes SPEAKING,
  the device stays open for a follow-up question — no re-wake needed —
  for `WS_CONVERSATION_TIMEOUT_MS` (30 s, in `main.c`). 30 s of silence
  → drop back to /listen wake mode. Mirrors `tests/jarvis.py` Phase 6B.
  Session memory carries across turns via `session_id=esp32`.
- **OLED polish** (M6): boot card (~2 s) shows IP / server / wake
  model / session id; brief "WAKE / score N.NN" splash on each wake
  event (200 ms hold); SPEAKING screen shows model name + per-call
  USD cost + token usage instead of truncated transcript chunks.
  Cost comes from the server via a new `estimated_cost_usd` field
  on `/converse` (computed from `usage.estimated_cost_usd()` so
  prices live in one place). Local Llama replies show `cost  $0.0000`;
  Sonnet/Haiku show real per-turn cost. *Note:* the M6 live VU bar
  on LISTENING was removed on 2026-05-10 (commit `5695da9`) — see
  gotcha #9. The cat-face panel's `?` stroke gives the same "I'm
  listening" feedback without the per-frame I²C contention that
  caused chronic mic-frame recycle.
- All three subsystems (`ENABLE_OLED` / `ENABLE_MIC` / `ENABLE_WIFI`
  in `firmware/main/app_state.h`) are on.
- **Cat-face OLED** (2026-05-10): a second SSD1306 128×64 panel on
  **I²C bus 1** (SDA=GPIO 16, SCL=GPIO 17, addr 0x3C — no soldering
  needed since the buses are isolated, both panels can share the
  default address) drives an animated cat mascot. `oled.c` was
  refactored to support multiple panels via an internal `oled_state_t`
  struct: existing `oled_*` API targets the main status panel
  (unchanged); new `oled_face_init/clear/fill/draw_bitmap/flush`
  target the face panel. A FreeRTOS task `face_task` reads `s_state`
  and plays the matching animation from `cat_face.h` (auto-generated
  from `tests/oled_animate.py` poses via `tests/oled_dump_c.py`).
  Animations: IDLE = gentle blink; LISTENING = `?` traces stroke-by-
  stroke above the right eye; THINKING = three floating dots cycling
  `.` → `..` → `...`; SPEAKING = happy `^_^` eyes with `ω` / `o` mouth
  pulse. `oled_face_init()` is graceful — if the panel is unwired
  or unpowered, it logs a warning and the rest of the firmware boots
  normally (face_task simply isn't spawned).

**v1 firmware is feature-complete.** All planned milestones (M1–M8 +
cat-face panel) shipped on real hardware. The audio-corruption
root-cause fix (gotcha #9 below) is in place; the device runs
arbitrarily long continuous-chat sessions cleanly.

**v2 hardware is arriving 2026-05-12** (today, as of the most recent
CLAUDE.md edit): MAX98357A I²S DAC + 3W speaker (so the device speaks
its own replies without routing through the workstation), plus a
LiPo + USB-C charging board for multi-day runtime. Once the hardware
is on hand, v2 firmware work begins — at minimum: an I²S audio-out
task, PCM playback queue, mid-stream interrupt detection. The
existing v1 code path stays as the fallback (`play_locally=true` on
`/converse` still works through Realtek).

**v3 prototyping has begun on the v1 device** — see the next
subsection. v3 hardware (custom round display, 3D-printed enclosure)
has not been ordered or finalized yet; what's in tree is bench
prototyping of the motion + face-renderer pieces that will eventually
land on v3.

### v3 prototype scaffolding (in progress, Kconfig-gated)

Five commits on 2026-05-11 (between 16:29 and 18:18 local) added
v3-prototype infrastructure to the v1 firmware. Everything is gated behind Kconfig
flags whose defaults preserve v1 behavior — pull this commit on the
v1 device, run `idf.py build flash` without touching menuconfig, and
the device behaves identically to its v1 self.

| Kconfig flag                  | Default            | When to flip                                                                  |
|-------------------------------|--------------------|-------------------------------------------------------------------------------|
| `CONFIG_VOICE_ENABLED`        | `y`                | Set `n` for display/servo iteration without the wake-word stream live         |
| `CONFIG_FACE_RENDERER`        | `ROBO_EYES`        | `CAT_FACE` reverts to the pre-v3 cat-face look (still in tree, still working) |
| `CONFIG_GAZE_COUPLING_DEMO`   | `n`                | Set `y` to spawn the SG90 servo follower task (head follows eye gaze)         |

What landed:

- **[firmware/components/robo_eyes/](firmware/components/robo_eyes/)** — port of
  [FluxGarage RoboEyes V1.1.1](https://github.com/FluxGarage/RoboEyes)
  by Dennis Hoelscher to ESP-IDF C. Two rounded-rectangle animated
  eyes with autoblinker, idle gaze wander, and four moods (DEFAULT /
  TIRED / ANGRY / HAPPY) mapped to the existing app states. Adds one
  read-only accessor (`robo_eyes_get_eye_x_angle()`) so a follower can
  read the eye's current horizontal angle without touching the
  library's motion logic. Design rationale in [notes/face-renderer.md](notes/face-renderer.md).
- **[firmware/components/servo/](firmware/components/servo/)** — LEDC-based SG90
  driver. Easing math (ease-in-out cubic for `servo_move_to`), idle
  drift, four named postures. Wiring + tuning knobs in
  [notes/servo-motion-language.md](notes/servo-motion-language.md).
- **[firmware/examples/servo_test/](firmware/examples/servo_test/)** — standalone IDF
  project that exercises only the servo component, for isolated bench
  iteration without touching the main firmware.
- **Eye-leads / head-follows gaze coupling** in `main.c`'s
  `servo_follow_task` (gated on `CONFIG_GAZE_COUPLING_DEMO`). The
  RoboEyes library runs in its stock autonomous mode (no external
  drive); the follower task samples eye angle every 50 ms, maps
  `[-90°, +90°]` eye → `[-60°, +60°]` servo with a 5° change
  threshold, and eases the servo over 800 ms. Matches the natural
  biological sequence (eyes orient first, head follows). Rationale +
  tuning knobs in [notes/motion-character.md](notes/motion-character.md).

### License (GPL-3.0)

The repo entered GPL-3.0 with the RoboEyes integration (commit
`79dc10e`). Specifically: `firmware/components/robo_eyes/` is a
derivative of FluxGarage RoboEyes V1.1.1, which is licensed
**GPL-3.0-or-later**. Building the firmware with the default
`CONFIG_FACE_RENDERER_ROBO_EYES` produces a binary covered by
GPL-3.0. Selecting `CONFIG_FACE_RENDERER_CAT_FACE` instead does
*not* unlicense the repo — the source tree includes the GPL code
regardless. Upstream copyright is preserved in every ported file.
Full license text in [LICENSE](LICENSE).

This was an eyes-open trade — clean-room "RoboEyes-inspired"
renderer was considered and rejected because the stated goal was
to get a polished animated face running immediately, not to
reinvent the eye shapes.

### Cat-face design + iteration

Iterating on 1-bit pixel art via flash-cycle is brutal, so the design
loop lives entirely in Python. Three tools in `tests/`:

- `oled_animate.py` — live tkinter window at 8× zoom showing the cat
  face animated across all 6 states. Press 1–6 to switch state, Esc
  to quit. Edit pose primitives or the animation generators near the
  top of the file, save, close, re-run; ~50 ms feedback loop.
- `oled_preview.py` — single-frame static PNG renderer + ASCII print
  + dumps a 1024-byte SSD1306 horizontal-page C array.
- `oled_dump_c.py` — regenerates `firmware/main/cat_face.h` (every
  unique pose bitmap + per-state animation tables indexed by
  `app_state_t`). Run after editing `oled_animate.py`:
  `python tests/oled_dump_c.py > firmware/main/cat_face.h`, then
  rebuild + flash.

A `Layout` dataclass in `oled_animate.py` parameterizes positions
relative to canvas dimensions (eye/mouth fractions, indicator scale).
Two presets: `OLED_128x64` (current panel) and `ROUND_240` (placeholder
for the future round display, dimensions guessed — re-tune when real
hardware arrives).

### M4 / wake-word gotchas (worth preserving)

These were all bench-verified during the M4 hardening session
2026-05-05; the device works hands-free for arbitrarily long
sessions of /listen now.

1. **`/listen` cannot use `esp_http_client`** — it's a full-duplex
   pattern (firmware writes chunks indefinitely, server may reply
   mid-stream), and IDF's HTTP client doesn't expose the underlying
   transport handle so we can't `select()` for a mid-stream response.
   `firmware/main/listen_stream.c` uses raw lwIP sockets with a
   manual chunked-encode + recv-until-FIN parser. `/converse` keeps
   `esp_http_client` because its one-shot upload pattern works fine.
2. **`SO_LINGER` with `l_linger=0` on the /listen socket.** Makes
   `close()` send TCP RST instead of FIN. Critical: when something
   forces an unclean close (e.g. transient transport error), there's
   no FIN-queued-behind-unACK'd-data deadlock that would leave the
   server's `request.stream.read` blocked indefinitely. RST is
   appropriate — in-flight audio is already old and the server
   doesn't care about it.
3. **Do NOT set `SO_SNDTIMEO` on /listen.** The socket needs to wait
   indefinitely for the server to drain — sustained streaming has
   normal periods of TCP backpressure. We tried `SO_SNDTIMEO=5s` as
   defense in depth and it caused periodic ~17 s reconnect cycles
   that dropped wake-word audio. The server-side defenses (item 4)
   make a wedged server unable to wedge the firmware, so we don't
   need a firmware-side timeout.
4. **Server hardening for /listen** (`server/app.py` + `wakeword.py`):
   - Pre-warm openWakeWord model in `main()`'s WARMUP sequence so the
     first request doesn't pay the ~1.2 s lazy-load cost.
   - `app.run(threaded=True)` — Werkzeug dev server is single-threaded
     by default; one stuck /listen would queue everything.
   - Custom `FastFailHandler(WSGIRequestHandler)` with `timeout = 5`
     — Werkzeug's default 60 s socket timeout is too long for our
     pattern; 5 s lets dead connections die fast and recycle the
     wakeword lock.
   - `wakeword.stream_predict` acquires `_LOCK` with `timeout=2.0`;
     if contended, returns `{"wake": false, "reason": "busy"}` so the
     firmware retries on its next reconnect cycle without piling up.
   - `read_chunk` wrapped in `try/except (OSError, IOError)` so an
     ECONNRESET (from the firmware's SO_LINGER=0 close) is caught
     and treated as `client_closed`, releasing the lock cleanly.
5. **30-second `listen: alive — N frames streamed (free heap M B)`
   heartbeat** in `run_listen_session`. Silent listen_stream looks
   identical to a stuck listen_stream without it. The heartbeat is
   the diagnostic signal — if frames-streamed stops climbing, the
   device is wedged.

### VAD tuning notes (worth preserving for future-Claude)

`firmware/main/vad.h` defaults differ from `tests/jarvis.py` based on
real-mic real-room behavior at the workstation:

- **`VAD_PEAK_THRESHOLD = 200`** (jarvis.py uses 600). INMP441 has no
  AGC and at typical reading distance (~12+ in.) conversational-speech
  peaks span a wide range. First lowered from 600 → 300 (600 made the
  silence-dwell counter reset only on the loudest syllables, tripping
  mid-utterance). Lowered again to 200 after M5 bench data showed
  conversational peaks declining across a long chat (680 / 669 / 564
  / 430 / 393 / 282 across six turns) — the 282 turn was real speech
  the user said but VAD ignored, dropping out of continuous mode.
  200 keeps ~2× margin over the idle ambient peak (~100 from M1's
  snap test). The trade is more permissive: false-positive (noise
  crosses threshold) costs one wasted /converse upload; false-negative
  drops the user mid-conversation. Tilt toward catching speech.
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

A second-tier gate added during v3 prototyping lives in Kconfig, not
app_state.h: `CONFIG_VOICE_ENABLED` (default `y`) is the **master
switch for the entire voice path** — when off, `i2s_mic_init`,
`listen_stream_init`, and the `/converse` round-trip helpers are
all gated out, but WiFi, OLED, cat-face / RoboEyes face renderer,
gesture, IR, and the BOOT button stay live. Use `=n` only for
display / servo bring-up where you don't want the wake-word stream
hammering the CPU and network. Default `y` keeps every pre-v3 build
behaving exactly as documented. Full menuconfig flow + GC notes in
[notes/build-modes.md](notes/build-modes.md).

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
- `GET /` — self-contained streaming web client (records on phone, plays in-browser via Web Audio; VAD + continuous + interrupt modes built in)
- `GET /health` — model load status, `mode`, per-process `usage` block (tokens + USD-cost-since-boot)
- `POST /transcribe` — multipart `audio` → `{transcript, duration_s, latency_s, model, language}`
- `POST /chat` — JSON `{message, session_id?, force_route?}` → router-dispatched LLM reply with `route`, `router_scores`
- `POST /speak` — JSON `{text}` → synth + blocking play through default output
- `POST /converse` — one-shot voice round-trip → STT → router → LLM → TTS → JSON with `transcript`, `route`, `reply_text`, `audio_base64` (WAV), per-stage `timings`, `estimated_cost_usd`, plus non-blocking local playback. Accepts three upload shapes by `Content-Type`: `multipart/form-data` (params in form fields), `audio/l16; rate=N; channels=N` (raw int16 LE, params in query string — used by the ESP-32 firmware), or `audio/wav`. Add `?omit_audio_base64=1` to drop the WAV from the JSON (saves 100–200 KB per turn for firmware clients).
- `POST /converse/stream` — SSE-streamed voice round-trip; the client plays the audio as PCM chunks arrive. Accepts the same three Content-Types as `/converse`. The web client uses this for sentence-by-sentence playback.
- `POST /listen` — chunked-transfer raw int16 LE @ 16 kHz body; server runs openWakeWord and returns `{"wake": true, ...}` on detection (or `{"wake": false, "reason": "idle_timeout"|"client_closed"|"busy"}`). Long-lived, full-duplex pattern — see "M4 / wake-word gotchas" below. Used by `tests/jarvis.py` and by the ESP-32 firmware in IDLE state. Requires `pip install openwakeword` + `openwakeword.utils.download_models()` on the server.

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

**Auth model:** when `API_KEY` is set, all of `/converse`, `/converse/stream`, `/chat`, `/transcribe`, `/speak`, `/listen` require the matching `X-API-Key` header. `GET /` and `GET /health` stay public. The authoritative list is `PROTECTED_PATHS` in [server/app.py](server/app.py). When `API_KEY` is empty, no auth is enforced (LAN-only mode).
