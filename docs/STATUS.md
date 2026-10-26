# Whisper Stack: current status and session handoff

Updated: 2026-10-26 (America/New_York).

## Current state

The user listened to the comparison and approved Flash v2.5. The application and
local configured TTS default is **eleven_flash_v2_5**. Scribe v2, Haiku 5.5, and the
George voice remain. Implementation and handoff are published on the rebuild
branch. The local server is not running. The next verification is a full voice-loop
exchange using Flash; the completed comparison tested TTS separately.

The Anthropic key was generated on
October 25 with a user-reported 30-day expiry, expected around **November 24, 2026**;
Console's exact timestamp takes precedence. No scheduled reminder was requested.

Read [PROJECT.md](PROJECT.md) for accepted decisions, [AGENTS.md](../AGENTS.md) for
working boundaries, and [backend/README.md](../backend/README.md) for setup/API commands.

## Implemented

- New implementation is isolated in backend/. Legacy server/, firmware/, tests/,
  tools/, root dependencies and root example configuration remain reference.
- Python 3.14.7 / macOS arm64, FastAPI/HTTPX, ElevenLabs SDK 2.74.1, standard-library
  SQLite. No production Node/UI runtime dependency. Browser checks use isolated
  pinned Playwright 1.64.1 under backend/ui-tests/.
- GET /health checks configuration without provider calls; GET /voices lists
  available voice names/IDs. POST /transcribe accepts recorded audio; POST /speak
  returns mono PCM16/16 kHz WAV. Flash v2.5 is now the TTS default in app.py,
  .env.example and local ignored backend/.env; existing credentials are preserved.
- POST /chat returns a short Haiku answer, numeric diagnostics, and cited sources.
  Optional client history is at most six complete exchanges; no persistent text memory.
- POST /converse returns a buffered WAV. POST /converse/stream runs recorded STT
  and Haiku first, then streams ElevenLabs PCM as NDJSON start/audio/done/error
  events. The client requires done; partial audio is not a complete replay.
  Cancellation closes upstream and preserves known model/search cost.
- GET /tester records only after Record, previews locally, and uploads only after
  Send. Thirty-second recording cap, tracks released after capture/cancellation/
  leaving the tab. Send starts Web Audio playback as PCM arrives; mute and final
  in-memory WAV replay are available. Blocked playback initialization sends no
  paid request. No microphone or provider call on page load.
- First audio received (browser), estimated playback scheduling, backend first
  audio, completion, stage timings, and buffer underruns are distinct metrics.
  Current implementation does not overlap Haiku text generation with TTS or
  stream microphone input in real time.
- Haiku model claude-haiku-5-5: adaptive thinking, low effort, max_tokens=1024,
  standard service/global routing, API version 2026-10-21. Omit speed: live API
  rejected it. System instruction defaults to one sentence/30 words; explicit
  detail requests allow three sentences/80 words. No arbitrary word truncation.
- General web_search_20250305 with max_uses=1, model-selected for current facts
  or explicit search requests. No weather-specific tool or inferred location.
  Search preambles are not spoken; safe HTTP(S) citations are shown in the tester.
  pause_turn fails without automatic continuation. No action tools or retries.
- GET /diagnostics and /diagnostics/{id} persist numeric model/timing/token/search
  records in ignored output/diagnostics.sqlite3. No prompts, transcripts, replies,
  reasoning text, search queries/pages/source links, or keys in that ledger.
  Token and search fees have separate subtotals and a combined estimate. Thinking
  is included in output tokens, never double-charged. Unknown usage is not free.
  Historical rows retain token-only estimates. Totals flag missing cost/search usage.
- Checked prices: Haiku $0.10 input / $0.50 output per million tokens through 100K
  prompt tokens, fivefold above; native search $0.01/use plus tokens. Estimates
  exclude ElevenLabs, taxes, other account usage, discounts, and actual balance.
- Bounds: 10 MiB audio input, 64 KiB chat JSON, 8 KiB speech JSON, 8 MiB generated
  PCM, 256 KiB model response. Loopback/Host validation, no CORS or device auth.
  Tester assets are allowlisted with restrictive CSP/no-store. No deployment.
- configure_anthropic.py provides hidden local key entry. Never print/open .env
  to inspect credentials. compare_tts.py requires explicit --live for two paid
  TTS samples; importing or testing it does not generate speech.

## Verification and durable evidence

**Local checks:** final Python suite **106 passed**, providers mocked:

```sh
backend/.venv/bin/python -m pytest backend/test_app.py backend/test_chat.py backend/test_tester.py backend/test_streaming.py backend/test_compare_tts.py -q --tb=line
```

The existing Starlette TestClient HTTPX deprecation warning remains; the pinned
combination passes. Earlier pip check passed; dependencies are unchanged.
The latest browser suite passed **14 checks** in Chrome 155.0.8059.85 / Node 26.10.0,
including PCM scheduling before delayed provider completion, mute/replay, safe
sources/search costs, interruptions, permissions, and 320–1280 px layouts.
Browser code did not change for adoption; it was not rerun for the default change.
Commands: npm --prefix backend/ui-tests ci --ignore-scripts, then
npm --prefix backend/ui-tests test. No live provider calls in either test suite.
Git whitespace/credential-ignore checks pass. macOS .DS_Store metadata is ignored.

**Live TTS comparison, October 25:** one sample/model, fixed order, fresh connections,
same George voice JBFqnCBsd6RMkjVDRZzb, 123-character synthetic sentence with
Ulaanbaatar and -5/2 degree example temperatures, pcm_16000 / 1024-byte chunks.

| Model | Headers | First PCM | Completion | Provider character-cost header |
|---|---:|---:|---:|---:|
| Multilingual v2 | 1671.240 ms | 1675.754 ms | 1754.509 ms | 54 |
| Flash v2.5 | 212.567 ms | 221.072 ms | 410.320 ms | 27 |

Both HTTP 200 from us-central1, valid distinct WAVs, 260,064 PCM bytes / 130,032
frames / 8.127 s each; full ffmpeg decode passed. Flash first PCM was 7.58x faster
in this sample. Character-cost headers are not USD or a verified balance.
The user subsequently listened and approved Flash. This is one latency sample and
user-reported quality acceptance, not a statistical benchmark or a full voice-loop
check with Flash. No new paid call was made during adoption/stop preparation.
Local evidence: **backend/output/tts-comparison-20261025T214205Z/results.json** and
the two model-named WAVs. Reproduce only with explicit authorization:
backend/.venv/bin/python -m backend.compare_tts --live.

**Earlier live voice/search evidence:**

- User browser search test: cited Ulaanbaatar weather source, one search, no buffer
  underruns; browser first audio 6.77 s. Ledger ID
  af2d6de4-5539-4180-841b-ddfd5ff57fff: STT 510.486 ms, Haiku including search
  4331.570 ms, TTS 1962.117 ms, first backend PCM 6739.958 ms, completion
  6824.833 ms. Only 84.875 ms streaming head start with Multilingual v2.
  Usage 15,292 input / 496 output, including 348 thinking; tokens $0.001777200 +
  search $0.010000000 = $0.011777200 estimate, excluding ElevenLabs.
- User's first buffered microphone test: correct recognition/coherent answer by
  user assessment. Ledger ID 86178dfe-4386-488d-83ef-914015c78415: STT 522.481 ms,
  Haiku 1267.014 ms, TTS 4378.051 ms, total 6180.061 ms; 133/113 tokens,
  estimated model cost $0.000069800. Screenshot showed a 19 s spoken reply.
- Earlier agent /chat and synthesized-input /converse live checks passed with
  combined 253 input / 101 output tokens, estimated $0.000075800. Voice-loop
  output voice-loop-live.wav is 11.610 s; evidence JSON remains under output/.
- ElevenLabs onboarding, /voices (21 voices), TTS and STT passed. The earlier
  backend-hello.wav transcript matched its known synthesized words. Numeric
  usage ledger retains earlier failed attempts with unknown cost, not zero.

Recordings, screenshots, metrics JSON, SQLite data and secrets are ignored/local
only. Their measured summaries are preserved here for other machines. Human
assessments are not independent STT/voice benchmarks; Safari/ESP32 remain unverified.

## Blockers and next action

Start by reading this handoff and checking Git/configuration. The local server
is **not running**. The next check is one short Flash-backed /tester exchange,
comparing first audio against the previous results.
TTS switching cannot remove the separate search/model delay. Overlapping Haiku
text/TTS generation, realtime input, firmware, deployment, and main merge remain
outside this increment. Hardware programming still needs a micro-USB data cable.

The user reports ESP32-S3-WROOM-1 and ESP32 DevKit V1 plus other checklist parts;
exact variants/specifications/wiring are unverified. No firmware build/flash,
ESP32 capture/playback, or GPU validation was performed. Eventual remote hosting,
final voice, memory/retention, provisioning, and hardware contract remain open.

## Git and historical context

Publication branch: **rebuild/backend-tester** in the existing private
https://github.com/anandenk/whisper-stack repository. Main is not merged or changed
by this work. Earlier published commits include c4542b9 (stream/search) and
73c3463 (TTS comparison) and 9cdb93b (Flash adoption). Code/docs are committed and
pushed to the same branch. No PR or new repo.
Credentials, environments, generated audio/data and installed test dependencies
stay local/ignored; next session must check git status --short --branch and
recent commits before assuming remote state.

Original baseline c7d050d and continuity commit ea1591b are preserved. Historical
May context: [notes/archive/claude-context-2026-05.md](../notes/archive/claude-context-2026-05.md).
Historical builds/hardware claims are reference, not evidence for this rebuild.
