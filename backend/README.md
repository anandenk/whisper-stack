# Local voice backend: ElevenLabs and Haiku

The rebuild backend implements recorded audio to text, Haiku replies, and text
to speech through a local HTTP service, with local token/cost diagnostics.
It is independent of the historical `server/` and
does not install its GPU, model, or playback dependencies. Current direction and
verification live in [PROJECT.md](../docs/PROJECT.md) and
[STATUS.md](../docs/STATUS.md).

## Setup

Run from the repository root. Verified environment: Python 3.14.7 on macOS arm64;
other platforms have not been tested.

```sh
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
cp -n backend/.env.example backend/.env
chmod 600 backend/.env
```

The environment was created in the October 24 session. ElevenLabs CLI browser
onboarding subsequently saved the project key in **backend/.env**; do not replace
the file or display its contents. It is ignored by Git. The app loads it with
python-dotenv, and shell environment values take precedence. Do not use the old
root `.env`. Check connection/configuration through onboarding, not by printing
the credentials file:

```sh
cd backend
npx -y @elevenlabs/cli@latest onboard check --answered --env-path .env
```

Return to the repository root for the remaining commands. For a new checkout
without credentials, run `npx -y @elevenlabs/cli@latest onboard init` from backend/
and follow its browser authorization flow.

The official ElevenLabs Python SDK handles STT/TTS through a bounded HTTPX
transport. Voice listing uses the same HTTP transport. Actual entitlement, remaining allowance,
and student-plan expiry must be checked in your account. No API call runs at
startup or during `/health`; only explicit voice/speech/transcription requests
contact ElevenLabs. STT and TTS requests consume the account's allowance. Obtain
the user's consent to the short live test before starting the app or generating
speech as part of this onboarding flow.

## Configure Haiku

Claude Haiku 5.5 is the user-selected reply model. No extra package is required:
the backend calls Anthropic's Messages API through its existing HTTPX dependency.
Create a personal API key scoped to your development workspace (Default is fine)
in the [Claude Console](https://platform.claude.com/settings/keys). Account access
and API credits must be available. The working balance is the user's reported
$3 on October 25; it has not been independently verified. The earlier $10
discussion was hypothetical. Use mocked checks and minimal live requests; no
purchase, auto-reload, or hard account-wide spending limit is configured.

From the repository root, enter the key locally at a hidden prompt:

```sh
backend/.venv/bin/python -m backend.configure_anthropic
```

This adds ANTHROPIC_API_KEY to backend/.env without displaying existing secrets,
preserves the ElevenLabs key, and sets file permissions to 0600. It does not
verify account access or make a provider request. Keys must begin sk-ant- and
contain no whitespace; incorrect formats are rejected before saving or calling
a provider. Restart the backend afterward. /health exposes a format-valid flag
without displaying the key; format validation is not proof of provider access.

## Start and choose a voice

```sh
backend/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

Use a second terminal for requests:

```sh
curl --fail-with-body http://127.0.0.1:8765/health
curl --fail-with-body http://127.0.0.1:8765/voices
```

`/health` reports local configuration only. `/voices` lists the first 100 names
and IDs from the account, plus `has_more` if further voices exist. Choose an ID
and configure `ELEVENLABS_VOICE_ID` locally without displaying the key file.
Restart after any configuration change. Interactive API docs are at
<http://127.0.0.1:8765/docs>.

Initial models are `scribe_v2` for STT and `eleven_multilingual_v2` for TTS.
These are test defaults, not a settled quality or latency choice.

## Local push-to-talk tester

Start the backend with the command above, then open
<http://127.0.0.1:8765/tester> in Chrome or Safari on this Mac. Use this same
origin for the page and API; do not open index.html directly from disk.

1. Click **Record** and allow microphone access when the browser asks.
2. Speak, then click **Stop recording**. Recording also stops after 30 seconds.
3. Listen back using the preview. **Clear** discards it without uploading.
4. Click **Send question** to run one buffered STT → Haiku → TTS exchange.
5. Press Play on the reply. Review stage timings, token counts, and estimated
   Haiku cost, with a link to the exchange's full numeric diagnostics.

Record/Stop is a toggle, so you do not need to keep the button held down. Each
question starts fresh: audio conversation history is not implemented. Microphone
tracks stop when recording ends; leaving the tab discards an active recording.
Cancelling an unanswered permission prompt prevents a late permission grant from
starting capture. Opening the page only checks local configuration; it does not
activate the mic or call a provider. A valid configuration flag does not prove
that a provider key is still valid or has credit.

Only **Send question** consumes provider allowance. The recording can be sent
once, controls are locked during processing, and failures are not retried. A
lost connection or timeout may still incur charges: check Usage diagnostics
before starting another exchange. If speech synthesis fails after Haiku finishes,
the tester displays any available Haiku usage/cost and diagnostic link. Diagnostic
failure does not discard an otherwise successful audio reply. No auto-play is
requested; playback is manual.

Browser audio stays in memory and is cleared when replaced, cleared, or the page
closes. The browser sends WebM/Opus, MP4, or OGG/Opus according to recording
support, capped at 10 MiB. API keys remain on the backend; no external fonts,
scripts, UI runtime dependencies, local storage, or cookies are required. Assets
are served through an explicit allowlist with no-store and restrictive CSP.
Provider-side retention still follows your account settings.

The automated browser suite uses real Chrome MediaRecorder with a **synthetic**
audio source and a separate backend whose two provider transports are mocked.
It does not access the physical microphone, play sound through speakers, use
your credentials, or write to your real usage ledger. Actual human microphone
quality, Safari behavior, and physical playback remain unverified.

Implementation references: [MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder)
and [microphone permission/capture](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

## First live speech test

```sh
mkdir -p backend/output
curl --fail-with-body --output backend/output/hello.wav \
  -H 'Content-Type: application/json' \
  --data '{"text":"Hello from Whisper Stack."}' \
  http://127.0.0.1:8765/speak
```

Only if curl succeeds, play the file on this Mac:

```sh
afplay backend/output/hello.wav
```

`POST /speak` accepts JSON with `text` (1–1000 characters). It requests
`pcm_16000` and wraps the returned signed 16-bit little-endian mono samples in a
16 kHz WAV container. No resampling or MP3 decoder is involved. Provider audio
is capped at 8 MiB; the JSON request body at 8 KiB. This is buffered synthesis,
not streaming. Audio format and actual entitlement need a live test.

## First live transcription test

Record a short sentence (around 5–10 seconds) with a local recorder and export
the file into ignored `backend/output/`. For an M4A recording named recording.m4a:

```sh
curl --fail-with-body \
  -H 'Content-Type: audio/mp4' \
  --data-binary @backend/output/recording.m4a \
  http://127.0.0.1:8765/transcribe
```

For WAV, use `Content-Type: audio/wav` and the WAV file path. The local endpoint
accepts **raw file bytes**, not multipart. Supported content types are audio/wav,
audio/x-wav, audio/mpeg, audio/mp4, audio/x-m4a, audio/webm, audio/ogg, audio/flac.
The backend forwards the recording to ElevenLabs as multipart and returns
`text` plus `language_code`. It caps actual received bytes at 10 MiB, including
requests without Content-Length, and does not write uploads or transcripts to
disk. This initial endpoint trusts the declared media type; invalid audio is
rejected by the provider. Keep live test recordings short.

## Haiku reply and voice-loop tests

```sh
curl --fail-with-body -H 'Content-Type: application/json' \
  --data '{"text":"Introduce yourself in one sentence."}' \
  http://127.0.0.1:8765/chat
```

POST /chat returns `reply` plus a diagnostic record. Text is limited to 1000
characters. Optional `history` is a list of complete user/assistant pairs, at
most 12 messages (six exchanges), each up to 1000 characters. For example:

```json
{"text":"What did I just ask?","history":[{"role":"user","content":"What is gravity?"},{"role":"assistant","content":"Gravity is the attraction between masses."}]}
```

History is supplied by the client and is not persisted. Oversized/malformed
history is rejected rather than silently discarded; chat JSON is capped at
64 KiB. The fixed model is claude-haiku-5-5, low effort, adaptive thinking,
1024 maximum billed output tokens, standard service, global inference,
API version 2026-10-21. The prompt asks for short spoken replies. Partial,
empty, or over-1000-character responses fail without an automatic retry; any
reported token usage is still recorded. No tools or search are enabled.
The request omits `speed`: Haiku 5.5 rejects that parameter, even when set to
standard. The backend uses Haiku's normal inference path.

For the complete **single-turn** recorded-audio → STT → Haiku → TTS path:

```sh
curl --fail-with-body --output backend/output/conversation.wav \
  -H 'Content-Type: audio/wav' --data-binary @backend/output/backend-hello.wav \
  --dump-header backend/output/conversation-headers.txt \
  http://127.0.0.1:8765/converse
```

Use any short supported recording (the earlier generated backend-hello.wav is
a known input). The response is a WAV, with an X-Diagnostics-ID header linking
to the local numeric diagnostics. It does not return/store the transcript or
reply text. Audio history/session memory is not implemented. Missing credentials
or voice configuration is rejected before calling STT; blank or overlong
transcripts stop before Haiku. If TTS fails after a successful Haiku call, that
Haiku usage is retained and the error includes X-Diagnostics-ID.

## Cost and latency diagnostics

```sh
curl --fail-with-body http://127.0.0.1:8765/diagnostics
```

GET /diagnostics returns lifetime and current UTC-month counts/totals plus the
20 most recent Haiku records. GET /diagnostics/{id} retrieves one record.
Storage is local ignored backend/output/diagnostics.sqlite3, using SQLite
transactions to survive restarts. Records contain IDs, model, timings, status,
provider-reported token counts, and estimated cost; no keys, prompts, transcripts,
reply text, or reasoning text. Full voice-loop records also include STT/TTS and
total elapsed milliseconds. No provider is contacted by diagnostics requests.

Cost estimates use the standard/global Haiku prices checked 2026-10-24:
$0.10 input / $0.50 output per million tokens at up to 100K prompt tokens;
five times those rates above 100K. Cache-read and 5m/1h cache-write counters are
priced separately if returned; this increment does not request caching. Thinking
is already included in the provider's output_tokens and is never charged twice.
Unknown/malformed usage or unexpected pricing modifiers produces a null cost,
not an assumed free request. Totals sum known estimates and flag incompleteness
via requests_without_cost_estimate and cost_estimate_complete. Timed-out requests
can still be billable; no automatic retries are made. These totals exclude
ElevenLabs, taxes, other account use, discounts, and actual account balance.
No credit purchase, auto-reload, or hard spending limit has been configured.

Sources: [Haiku specifications/pricing](https://platform.claude.com/docs/en/models/haiku-5-5/overview),
[Messages contract](https://platform.claude.com/docs/en/api/messages/create),
[effort guidance](https://platform.claude.com/docs/en/build-with-claude/effort).

## Local verification

```sh
backend/.venv/bin/python -m pytest backend/test_app.py backend/test_chat.py backend/test_tester.py -q
backend/.venv/bin/python -m pip check
```

For the browser checks, install the isolated test-only dependency (Node 20+,
Chrome installed locally), then run:

```sh
npm --prefix backend/ui-tests ci --ignore-scripts
npm --prefix backend/ui-tests test
```

Playwright is pinned under ui-tests/ and is not a backend runtime dependency.
The suite starts/stops its own mocked server on a free loopback port. Set
TEST_BROWSER_CHANNEL=chromium to use a separately installed Playwright Chromium;
the default is your installed Chrome, in a temporary headless profile.
An optional UI_SCREENSHOT absolute path saves a desktop screenshot locally.

Tests use HTTPX MockTransport, a fake key, and synthetic audio. They check the
provider request contracts, actual WAV header/sample preservation, the complete
voice pipeline, history validation, cost/cache/thinking accounting, diagnostic
privacy/restarts, limits, error sanitization, timeouts, and configuration. They do
not verify real recognition, synthesis, account access, or physical playback.

## Boundaries and troubleshooting

- Keep this service on loopback. It has no device authentication yet; LAN access
  and deployment are outside this increment. Host validation accepts localhost
  and 127.0.0.1. CORS is not enabled.
- A 503 means missing local credentials/voice configuration; restart after edits.
  A provider authentication/permission denial becomes 502 with a safe message;
  429 indicates provider usage/rate limits, and 504 indicates a timeout.
- Requests are not retried automatically, preventing duplicate billable work.
  Upstream diagnostics and submitted content are not included in error messages.
- Generated recordings/audio under output/ and credentials are ignored. The
  application has no persistent session or conversation storage. Provider-side
  retention follows your ElevenLabs account; no zero-retention claim is made.
- Current live-provider evidence and key-expiry metadata are in STATUS.md and
  PROJECT.md; credentials must be valid for the configured account. Human microphone
  testing, audio-session memory, realtime streaming, ESP32 firmware, and hardware
  playback are not implemented in this increment.

API contracts checked against the official [transcription reference](https://elevenlabs.io/docs/api-reference/speech-to-text/convert),
[speech reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert),
and [voice-list reference](https://elevenlabs.io/docs/api-reference/voices/search).
