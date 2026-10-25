# Whisper Stack: current status and session handoff

Updated: 2026-10-25 (America/New_York).

## Current phase and latest request

The user accepted keeping the existing private anandenk/whisper-stack repository
and publishing the rebuild on **rebuild/backend-tester** on October 25. The local
push-to-talk tester is implemented and passes automated browser checks. Human
testing is deferred because the user is unavailable. Main and the May reference
implementation are preserved; no merge or new repository is requested.

The buffered STT → Haiku 5.5 → TTS backend now passes live checks with known
synthesized speech. Token/cost diagnostics work with real provider usage. Human
microphone capture, physical playback, and hardware behavior remain unverified.

The user generated a fresh Anthropic key on October 25 with a **30-day expiry**,
expected around **November 24, 2026**. Exact expiry time is user-reported/unverified;
use the Console timestamp. The configured key authenticated and inference
succeeded. No scheduled reminder was requested or created. The user-reported
$3 credit balance remains the working constraint: use mocks and minimal live
checks; no purchases, auto-reload, or automatic retries.

Read [PROJECT.md](PROJECT.md) for decisions and [AGENTS.md](../AGENTS.md) for the
workflow. Reproducible setup/API commands are in [backend/README.md](../backend/README.md).

## Implemented

- backend/ is isolated from the May server/, firmware/, tests/, tools/, and root
  configuration/dependencies. Legacy implementation remains unchanged.
- Python 3.14.7 on this Mac; pinned FastAPI/HTTPX and ElevenLabs SDK 2.74.1.
  Haiku uses the Messages API through existing HTTPX. Diagnostics use SQLite
  from the Python standard library; no extra dependency added for them.
- GET /health (configuration only), GET /voices, POST /speak (buffered 16 kHz
  mono 16-bit WAV), and POST /transcribe (raw recorded file → text/language).
  ElevenLabs defaults: multilingual v2 TTS, Scribe v2 STT, George test voice.
- POST /chat: short Haiku reply plus numeric diagnostics. Optional client history
  is at most six complete exchanges, 1000 characters per message. No persistent
  server conversation memory.
- POST /converse: single-turn recorded audio → STT → Haiku → TTS → WAV, with
  X-Diagnostics-ID linking to usage/timings. Preflights local key/voice settings;
  blank/overlong transcripts stop before Haiku. TTS failure retains known LLM cost.
- Haiku: claude-haiku-5-5, low effort/adaptive thinking, max_tokens=1024, standard
  service, global routing, API version 2026-10-21. **Omit speed**: a live 400
  response established that Haiku rejects this parameter even at standard.
  No tools/search or automatic retries. Incomplete/overlong replies fail explicitly.
- GET /diagnostics and /diagnostics/{id}: model, status, token counts (thinking
  included in output), estimated Haiku cost, and timings. UTC-month/lifetime
  totals persist in ignored backend/output/diagnostics.sqlite3. No prompt,
  transcript, reply, reasoning text, or secrets are stored in the ledger.
- Prices checked October 25: $0.10 input / $0.50 output per million tokens through
  100K prompt tokens, fivefold above 100K. Cache counters priced separately if
  returned; no caching requested. Unknown usage/modifiers produce null costs
  and explicit incomplete totals. Estimates exclude ElevenLabs, taxes, discounts,
  other account use, and actual balance. No hard account-wide budget configured.
- Limits: 10 MiB recorded input, 64 KiB chat JSON, 8 KiB speech JSON, 8 MiB generated
  PCM, 256 KiB Haiku response. Loopback only, local Host validation, no CORS or
  device authentication.
- Hidden local key-entry helper: backend/configure_anthropic.py. Requires sk-ant-
  format, preserves ElevenLabs settings, and saves mode 0600. Format is only a
  preflight, not proof of access. Never print/open backend/.env to inspect keys.
- GET /tester: a same-origin local browser tester, with explicit Record/Stop,
  local recording preview, separate Send, manual WAV reply playback, and stage
  timing/token/cost diagnostics. Capture stops at 30 seconds; input is capped at
  10 MiB. Tracks stop after recording, cancellation, or leaving the tab. Pending
  permission cancellation stops a late stream without recording. No provider
  request on page load; Send is consumed once, with no automatic retry. Numeric
  diagnostics remain available after TTS failure; diagnostic failure preserves
  a successful audio reply. Single turn, in-memory browser audio, no local storage.
  HTML/CSS/JS use explicit asset allowlisting, no-store and restrictive CSP. No
  new Python/UI runtime dependency. Playwright is a separate pinned test-only
  dependency in backend/ui-tests/; node_modules is ignored.

## Verification

**Current local tests:**

- `backend/.venv/bin/python -m pytest backend/test_app.py backend/test_chat.py backend/test_tester.py -q --tb=line`:
  **89 passed**, including regression assertion that Haiku requests omit speed
  and tester asset privacy/headers.
  Tests cover provider contracts, WAV and pipeline ordering, bounds, key handling,
  no retries, errors, cost/cache/thinking accounting, privacy, and diagnostic
  persistence/monthly totals. Providers are mocked in this suite.
- Existing Starlette TestClient HTTPX-adapter deprecation warning remains; the
  pinned combination passes. Current pip check passes; Python requirements unchanged.
- `npm --prefix backend/ui-tests test`: **10 passed** with Chrome 155.0.8059.85,
  Node 26.10.0 and Playwright 1.64.1. Real browser MediaRecorder uses a synthetic
  oscillator; a separate ephemeral backend uses fake credentials and two HTTPX
  MockTransport providers. Checks cover one complete mocked pipeline, no mic/spend
  on load, preview before upload, duplicate-send prevention, denied/pending mic
  permission, leaving the tab, duration cap, missing setup/unsupported browser,
  provider/connection/diagnostic failures, and 320/375/414/768/1280 px layouts.
  Desktop screenshot visually reviewed: backend/output/tester-desktop.png (ignored).
  No physical mic/speaker, live-provider calls, real ledger writes, or paid usage
  occurred in this tester increment. Safari and human listening remain unverified.
- `git diff --check` and local Markdown link checks pass. Credentials, virtual
  environment, generated audio, and SQLite data are ignored/local-only.

**Current live provider checks (October 25):**

- Read-only Anthropic /v1/models returned 200; Haiku 5.5 was listed.
- After removing unsupported speed, POST /chat returned **200** and a complete
  short reply. Provider usage: **121 input, 35 output, 0 thinking tokens**;
  estimated cost **$0.000029600**; Haiku request elapsed **776.221 ms**.
  Local evidence: backend/output/haiku-chat-live-result.json.
- POST /converse with backend/output/backend-hello.wav returned **200** through
  all three live providers/stages. Provider Haiku usage: **132 input, 66 output,
  0 thinking tokens**; estimated Haiku cost **$0.000046200**. Timing: STT
  1175.573 ms, Haiku 1211.647 ms, TTS 1631.954 ms, total backend **4025.584 ms**
  (client wall time 4.049 s). This is buffered, not time to first streamed audio.
- Output backend/output/voice-loop-live.wav: **371,564 bytes**, mono 16-bit PCM,
  16 kHz, 185,760 frames, **11.610 seconds**. Full ffmpeg decode passed. Evidence:
  backend/output/voice-loop-live-result.json and diagnostics-after-live-checks.json.
- The two successful Haiku calls total **253 input / 101 output tokens** and
  estimated **$0.000075800**, excluding ElevenLabs. Actual remaining $3 balance
  was not queried. Do not call this the provider's invoice or account balance.
- The persistent ledger also retains four earlier failed attempts (two rejected
  keys and two request-format failures). No generated output/usage was reported
  for those; cost remains unknown rather than assumed zero. Therefore the known
  cost subtotal is explicit and cost_estimate_complete remains false. No automatic
  retries occurred. Live checks are now complete; stop paid testing for this slice.

**Earlier live ElevenLabs evidence (October 24):**

- CLI onboarding and approved TTS test succeeded; /voices listed 21 voices and
  George was selected temporarily. backend/output/elevenlabs-onboarding.mp3 is
  a valid 1.760 s MP3.
- /speak generated backend/output/backend-hello.wav (3.994 s, mono PCM16/16 kHz),
  taking 1.116 s. /transcribe recognized its spoken words with minor punctuation/
  hyphenation differences in 0.567 s. Supporting JSON remains under output/.
- These inputs are synthesized speech. No human microphone test, physical device
  playback, or broad STT/LLM quality benchmark is established.

## Current server and remaining boundaries

The updated backend was left running on 127.0.0.1:8765. Open
http://127.0.0.1:8765/tester when ready for human testing. Check /health before
relying on process state. Restart after key changes. Reproducible startup:

```sh
backend/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

The fresh API key expires after 30 days (expected November 24); exact time is in
PROJECT.md's user-reported credential note and should be verified in Console.
ElevenLabs plan allowance/expiry and the reported $3 balance remain unverified.
No key is recorded in project notes. No deployment, firmware build/flash, GPU
validation, human mic capture, or physical playback was performed.

The user reports ESP32-S3-WROOM-1 and ESP32 DevKit V1 boards and other checklist
parts. Exact board/memory/connectors, speaker impedance, soldering, and wiring are
unverified. Programming remains blocked by a missing micro-USB data cable.

## Next action

When the user is available, open /tester and run one short human microphone
exchange to assess recognition, audible reply quality, and latency. The user
explicitly deferred that test; do not start capture or more paid checks now.
Hardware flashing still needs the micro-USB data cable. Keep final voice and
conversation retention choices open; no automatic realtime streaming, deployment,
firmware work, or main-branch merge.

## Historical context and Git

Original baseline: c7d050d (May); continuity commit: ea1591b. Original context is
[notes/archive/claude-context-2026-05.md](../notes/archive/claude-context-2026-05.md).
October 19 audit parsed 36 Python files and passed mocked legacy Flask checks;
historical hardware claims and legacy defects remain rebuild evidence, not a
repair queue. Old /tmp audit probes are not durable evidence.

Implementation commit **38f5d00** is committed and pushed to the existing private
repository's **rebuild/backend-tester** branch. Remote refs were verified after
push; remote main remains c7d050d. The local continuity commit ea1591b is included
in the rebuild branch. No merge or pull request was created. This status-only
follow-up records the completed publication; it is published on the same branch.
Credentials, virtual environments, audio, screenshots, usage ledger, and installed
test dependencies stay local/ignored. Check `git status --short --branch`,
`git log -3 --oneline`, and the upstream branch at the next session start.
