# Whisper Stack: current status and session handoff

Updated: 2026-10-26 (America/New_York).

## Current state

The user listened to the comparison and approved Flash v2.5. The application and
local configured TTS default is **eleven_flash_v2_5**. Scribe v2, Haiku 5.5, and the
George voice remain. Implementation and handoff are published on the rebuild
branch. The user then authorized overlapping Haiku text generation and speech.
The streaming route now sends final-answer words to ElevenLabs before Haiku
finishes. One silent live check reached first client audio at **2.539 s** and
completion at **3.164 s**; audio arrived before the model terminal event. The
earlier sequential sample was 2.737 s / 3.311 s. This is a modest single-sample
improvement, not an achieved 1–1.5 s target or a statistical benchmark. Both
checks use the same synthetic question file; no physical mic/playback was used.
Replies are saved for later review. The local server is running on
127.0.0.1:8765; check /health before relying on process state.

The user's subsequent microphone attempt hit “Invalid streaming event.” The
WebSocket adapter forwarded provider-sized PCM frames as single NDJSON events,
which could exceed the browser's 64 KiB line cap. The adapter now splits PCM into
16 KiB events before base64 encoding, preserving all samples and the existing
browser cap. The local server was restarted with the fix. The user subsequently
retried with their own voice and reported that it “works pretty great,” confirming
successful browser use and user-accepted spoken quality after the correction.
This is user-reported acceptance, not an independent quality/latency benchmark.
The agent's correction checks used mocked providers without audible playback.

## D23–D25 isolated Agents + Mem0 experiment (complete)

**Basic setup and collaborative validation are complete.** The user accepted
speech recognition, natural interruptions, quiet waiting after answers, and
hosted Mem0 preference recall in a new session. These are user-reported live
results, not independent acoustic, latency or memory-reliability benchmarks.
The completed rebuild was merged into main through PR #1 (D26), verified on
GitHub and in the local checkout. The subsequent README rewrite is complete
locally: 88 lines introducing the old project, current rebuild, validation,
startup, repository layout and license. The previous 459-line README is preserved
in [the archive](../notes/archive/README-before-overview-2026-10-26.md), with
relative links rebased. The saved project one-liner is retained verbatim.

### Current implementation

- Separate agent **Whisper Stack - D23 Haiku 5.5 + Mem0 test**,
  `agent_placeholder_000000000000000000`: native `claude-haiku-5-5`, low effort,
  1024-token cap, disabled alternate LLM fallback, required authentication, and
  response-returning `addMemories`/`retrieveMemories` client tools.
- Preferred local tester: **http://127.0.0.1:8766**, served by
  `experiments/agents-mem0/web_server.py`. Check process state before reuse.
  Start activates microphone/speaker and obtains a private-agent WebRTC token;
  End closes it. Page load makes no provider call or microphone request.
- Official `@elevenlabs/client` 1.31.0 owns capture/playback; its LiveKit defaults
  enable echo cancellation, noise suppression and gain control. No custom DSP,
  raw-PCM browser transport, playback-time microphone muting or fallback transport.
  The SDK's unmodified worklet is served locally for CSP compatibility.
- A loopback-only FastAPI bridge keeps both API keys server-side and forwards
  memory tools to hosted Mem0 using fixed USER_ID `whisper-stack-d23-test`.
  It checks Host/Origin/local-page token, bounds tool arguments, disables caching,
  sanitizes failures, and exposes no CORS. Memory initializes on the first tool
  call. No application-level retries; vendor transport recovery retains defaults.
- Native `conversation_config.turn.turn_timeout=-1` disables seven-second idle
  check-ins (D25). The session stays available for user-initiated follow-ups;
  the unchanged maximum duration is 600 seconds. PATCH/GET readback verified only
  the intended setting changed. Local agent.json reflects current configuration.
- The English-agent API rejected Flash v2.5; this test uses Flash v2 and George.
  WebRTC negotiates its own audio transport. The existing backend remains on its
  approved Flash v2.5 default. No backend, legacy firmware or deployment changes.
- Mem0 V3 pending saves are reported as submitted, not confirmed available.
  The user separately confirmed recall after starting a new session. Browser
  transcripts remain in page memory; numeric backend diagnostics do not cover
  this separate hosted Agents path.
- ElevenLabs defaults record voice and retain conversations indefinitely
  (retention_days=-1); built-in user memory is disabled. Mem0 persists submitted
  facts. Long-term retention policy remains undecided. Credentials, provider
  readbacks, recordings, generated bundles and dependencies are local/ignored.

### Verified and retained context

**Final checks for publication:** 7 experiment Python checks and 8 Chrome browser
checks pass with mocked providers/synthetic input. The unchanged working-backend
Python suite passes **120 tests**, with its existing Starlette/HTTPX deprecation
warning. The existing backend browser suite also passes all 14 checks on the
merge candidate. Both Python environments pass pip check. Read-only live verification
passes Mem0 ping, native Haiku agent authentication and both tool definitions;
no paid model/audio generation or memory writes were initiated for publication.

Browser tests load the real bundled SDK and official worklet under CSP with
external requests blocked. Mocked sessions verify WebRTC selection, supported
signalling option, no playback muting, safe transcript display, memory tools,
End, cancellation and token/permission/connection failures with redacted errors.
The real ready page also loaded without errors or external requests. These
checks do not substitute for the user's live acceptance or ESP32 verification.

Chrome initially failed with “could not establish signal connection: Failed to
fetch.” Token auth and server-side validation succeeded; Edge worked. The user
then disabled multiple Chrome privacy extensions, including Privacy Badger among
those disabled, and reported that Chrome works too. This is extension
interference; the specific extension/rule was not isolated. Do not attribute the
failure specifically to Privacy Badger. The supported SDK option
`webRtc.singlePeerConnection=false` remains from diagnosis, but did not fix
Chrome on its own and its necessity is unverified. No further diagnosis is needed.

The earlier Python tester is preserved as reference. Its local `audio.py`
correction fixes the ElevenLabs 2.74.1 microphone-thread loop handoff; two
synthetic regressions cover it. The user confirmed input worked after that fix
but reported speaker feedback, prompting the official WebRTC client (D24).

### Resume and reproduce

Setup, pinned dependencies, existing remote resource IDs, credentials, test
commands, accepted test procedure and troubleshooting are in the
[experiment README](../experiments/agents-mem0/README.md). From repository root:

```sh
npm --prefix experiments/agents-mem0 ci
npm --prefix experiments/agents-mem0 run build
npm --prefix experiments/agents-mem0 test
experiments/agents-mem0/.venv/bin/python -m unittest discover -s experiments/agents-mem0 -p 'test_*.py' -v
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/run.py --verify
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/web_server.py
```

The final command starts only the local page; the user must click Start for a
live conversation. Keep existing hosted resources; checkout does not provision
another agent. Local ignored readback evidence includes
`experiments/agents-mem0/output/idle-checkins-verification.json` and
`webrtc-verification.json`; the relevant outcomes are summarized above for other
machines. No further feature, hardware, migration or deployment work follows
from completing or publishing this experiment.

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
- POST /converse returns a buffered WAV. POST /converse/stream runs recorded STT,
  then overlaps Haiku Messages SSE with an ElevenLabs text-input WebSocket. Text
  and audio queues are capped at eight items. One request/connection per provider,
  no retries, 50-character TTS generation schedule, complete-word input fragments.
  NDJSON start/audio/done/error is preserved; citations now arrive on done.
  Provider PCM is split into at most 16 KiB per audio event, safely below the
  browser's 64 KiB event limit after base64/JSON encoding.
  The client requires done; partial audio is not a complete replay. Cancellation
  closes both upstreams; known complete model/search usage is preserved. Missing
  model completion/usage yields unknown final cost with usage_complete=false.
- Streaming voice protocol uses `<voice>...</voice>` only around final answer text.
  Tags, reasoning/signatures, search queries/results and unmarked search preambles
  are not sent to TTS. Tool calls after speech starts, missing tags, malformed or
  truncated SSE, overlong speech, missing speech final frames, and pause_turn
  produce explicit errors without retries. Speech already delivered cannot be
  retracted if the model later fails; it is never offered as a complete replay.
- GET /tester records only after Record, previews locally, and uploads only after
  Send. Thirty-second recording cap, tracks released after capture/cancellation/
  leaving the tab. Send starts Web Audio playback as PCM arrives; mute and final
  in-memory WAV replay are available. Blocked playback initialization sends no
  paid request. No microphone or provider call on page load.
- First audio received (browser), estimated playback scheduling, backend first
  audio, completion, stage timings, and buffer underruns are distinct metrics.
  First answer text, first text sent to voice, and model terminal-event time are
  also recorded. Model and TTS durations overlap and do not add to total time.
  Current implementation does not stream microphone input in real time.
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
  PCM, 256 KiB model response, 512 KiB speech WebSocket frames. WebSocket redirects
  are rejected to protect key headers. Loopback/Host validation, no CORS or device auth.
  Tester assets are allowlisted with restrictive CSP/no-store. No deployment.
- configure_anthropic.py provides hidden local key entry. Never print/open .env
  to inspect credentials. compare_tts.py requires explicit --live for two paid
  TTS samples; importing or testing it does not generate speech.

## Verification and durable evidence

**Local checks:** final Python suite **120 passed**, providers mocked:

```sh
backend/.venv/bin/python -m pytest backend/test_app.py backend/test_chat.py backend/test_tester.py backend/test_streaming.py backend/test_compare_tts.py backend/test_overlap.py -q --tb=line
```

The existing Starlette TestClient HTTPX deprecation warning remains; the pinned
combination passes. Current pip check passes; no packages were added. Existing
websockets/anyio pins are now direct dependencies of the async streaming adapter.
New checks prove text/audio before a gated model tail, whole-word/tag handling,
reasoning/search preamble exclusion, final citations, cumulative usage accounting,
known/unknown costs, missing final events, bounds, redirects, and disconnect cleanup.
The latest browser suite passed **14 checks** in Chrome 155.0.8059.85 / Node 26.10.0,
including PCM scheduling before delayed provider completion, mute/replay, safe
sources/search costs, interruptions, permissions, and 320–1280 px layouts.
Current browser capture is a deterministic synthetic WAV recorder fixture; the
earlier suite exercised native MediaRecorder separately. Playback uses real Web
Audio in a muted headless Chrome profile. No physical microphone or audible sound.
The current suite verifies citations delivered on done and both new text timings.
Large-frame regression checks reproduced rejection before the correction, then
passed with 67,774-byte and 300,000-byte provider PCM frames. They verify event
sizes, sample alignment and byte-for-byte preservation. Browser fixtures now use
67,774-byte provider frames, and verify successful playback scheduling plus a
replay WAV duration matching the sample count reported by backend diagnostics.
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

- **October 26 user acceptance after the event-size fix:** the user retried the
  tester with their own voice and reported that it “works pretty great.” This
  resolves the pending human playback/quality check for the updated stream; no
  new timing measurements or independent transcription assessment were supplied.
- **October 26 user microphone failure:** ledger ID
  **ef09f3f9-3ff8-441d-bc38-458466d53020** completed model work but recorded
  pipeline_status=disconnected after 67,774 PCM bytes. STT **630.182 ms**, first
  backend audio **1838.355 ms**, known model cost **$0.000336800**, zero searches.
  The browser reported “Invalid streaming event”; large provider frames reproduce
  its size-limit rejection. This attempt is not successful browser playback or
  listening evidence. No user recording/transcript was retained in the ledger.
- **October 26 silent overlapped voice-loop check:** same question.wav as the
  prior seasons test. One POST /converse/stream, HTTP 200, valid start/audio/done.
  First client PCM **2538.791 ms**, completion **3163.637 ms**. Ledger ID
  **b1a0da51-65f8-4709-8a12-8b9b4414dd7d**: STT **1112.044 ms**, first final-answer
  words **2311.581 ms**, first text sent to TTS **2312.604 ms**, first backend PCM
  **2531.988 ms**, model terminal event **2574.806 ms**, total **3154.407 ms**.
  Observed model duration **1453.180 ms** and overlapping TTS window **2035.496 ms**
  do not add to total. text_sent_before_llm_complete and audio_before_llm_complete
  are true. Usage **3228 input / 69 output / 0 thinking**, zero search, estimated
  model cost **$0.000357300**; ElevenLabs is separate. Reply **334,368 PCM bytes /
  10.449 s**, valid WAV, full ffmpeg decode passed. Evidence:
  **backend/output/overlap-voice-loop-20261026T205324Z/result.json** and reply.wav.
  No sound, browser player, or physical microphone was used for the live check.
  Search/citation handling is mocked in the new overlapping path, not newly
  live-tested. The previous search check below used the prior implementation.
- **October 26 silent full Flash voice-loop check:** the user authorized the
  agent to supply a question and required no sound playback. macOS say output-file
  mode generated “What causes the seasons on Earth? Please answer in one short
  sentence.” AIFF was converted to mono PCM16/16 kHz WAV using ffmpeg. One POST
  /converse/stream returned HTTP 200 with start/audio/done; no retry or web search.
  First client audio **2736.687 ms**, client completion **3310.728 ms**. Ledger ID
  **2ba867fe-fae0-437a-b23b-a4b03f2cd154**: STT **839.398 ms**, Haiku **1418.254 ms**,
  TTS **1039.333 ms**, first backend PCM **2732.581 ms**, total **3306.098 ms**.
  Model usage **3140 input / 59 output / 0 thinking**, estimated token cost
  **$0.000343500**, zero search fee; ElevenLabs usage is separate. Pipeline ok.
  Reply **320,992 PCM bytes / 10.031 s**, valid WAV and full ffmpeg decode passed.
  Evidence **backend/output/flash-voice-loop-20261026T180126Z/result.json**,
  question.txt, question.aiff, question.wav, and reply.wav (ignored, local only).
  No player/browser or physical audio output was opened. This verifies the live
  backend stream, not browser scheduling, human mic accuracy, or independently
  judged reply content. It is a direct-answer sample; the earlier 6.77 s sample
  included a search, so it is not an isolated model-switch speed comparison.
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

The current increment is complete: WebRTC interaction, interruption, quiet
waiting and hosted memory recall across sessions are accepted by user testing.
No browser-startup or credential blocker is currently reported. The user
requested and completed merge through
[PR #1](https://github.com/anandenk/whisper-stack/pull/1).
The next requested documentation increment is now ready for review: the main
README gives short old/new descriptions and links to the detailed guides. Local
links and whitespace checks pass; archive verification confirms the full prior
README is retained apart from link rebasing. No code, configuration, service or
provider state changed, so no runtime tests were rerun for this documentation-only
edit. Next action: review the new README with the user. Do not implicitly start
hardware, migration or additional features.
Broader retention policy, migration, web-search parity and deployment remain
choices to revisit together.

The user accepted their own-voice browser test after the event-size correction.
The overlapping backend and large-frame browser path also pass automated checks.
The local backend is running; check /health before reuse.
Recommended next project milestone is a minimal
device capture/playback interaction once the board/parts and data cable are
available; no firmware work is authorized by this recommendation.
The latest sample waited about 1.11 s for STT and another 1.20 s before final-answer
text, so overlap alone has not reached the proposed 1–1.5 s target. Realtime input,
firmware and deployment remain outside this increment. Main integration is
authorized by D26. Hardware
programming still needs a micro-USB data cable.

The user reports ESP32-S3-WROOM-1 and ESP32 DevKit V1 plus other checklist parts;
exact variants/specifications/wiring are unverified. No firmware build/flash,
ESP32 capture/playback, or GPU validation was performed. Eventual remote hosting,
final voice, memory/retention, provisioning, and hardware contract remain open.

## Git and historical context

[PR #1](https://github.com/anandenk/whisper-stack/pull/1) is **merged** into
**main** in the existing private https://github.com/anandenk/whisper-stack
repository. Merge commit: **e1e064ec92f4fef668183d8283d9d2067edb21de**,
2026-10-26 (America/New_York). GitHub reported MERGED, and this checkout was
switched to main and fast-forwarded to the remote merge commit. Continue on main.
The rebuild branch and all incremental history are preserved; no squash or
history rewrite. After that merge, the user started the separate README rewrite.
Current README/archive/handoff edits are local, uncommitted and unpushed on main;
the published main remains c4b0999 until this documentation increment is reviewed.

Published rebuild commits include c4542b9 (stream/search), 73c3463 (TTS comparison),
9cdb93b (Flash adoption), 125afe0 (overlap), bef3f53 (large PCM frames), and
c9064cc (Agents + Mem0). All 149 local checks passed on the reviewed source;
GitHub had no configured Actions checks and reported the PR cleanly mergeable.
Commit/PR messages omit co-author and generated-by
attribution at the user's request. Credentials, environments, generated audio/data
and installed test dependencies remain local/ignored; they do not transfer with
checkout. Check git status and recent commits on every session.

Original baseline c7d050d and continuity commit ea1591b are preserved. Historical
May context: [notes/archive/claude-context-2026-05.md](../notes/archive/claude-context-2026-05.md).
Historical builds/hardware claims are reference, not evidence for this rebuild.
