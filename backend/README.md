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
and API access must be available. Use mocked checks and bounded live requests;
no purchase, auto-reload, or hard account-wide spending limit is configured.

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

Current models are `scribe_v2` for STT and `eleven_flash_v2_5` for TTS. The user
approved Flash v2.5 after the October 25 comparison and listening check. George
remains the initial test voice.

## Local push-to-talk tester

Start the backend with the command above, then open
<http://127.0.0.1:8765/tester> in Chrome or Safari on this Mac. Use this same
origin for the page and API; do not open index.html directly from disk.

1. Click **Record** and allow microphone access when the browser asks.
2. Speak, then click **Stop recording**. Recording also stops after 30 seconds.
3. Listen back using the preview. **Clear** discards it without uploading.
4. Click **Send question**. Speech recognition finishes, then Haiku feeds its
   answer text into ElevenLabs while still generating. The reply plays as audio
   arrives. **Mute live reply** silences
   playback while receipt continues. A complete WAV replay is available afterward.
5. Compare **First audio received** with **Total processing**, and review tokens,
   search count, estimated combined cost, source links, and exchange diagnostics.

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
failure does not discard an otherwise successful audio reply. Sending explicitly
starts a Web Audio context for streamed playback. If audio initialization fails,
no provider request is sent and the recording remains available for another Send.
An interrupted/failed stream stops playback and never presents partial audio as
a complete replay. Starting a new recording or clearing the page stops old audio.

Browser audio stays in memory and is cleared when replaced, cleared, or the page
closes. The browser sends WebM/Opus, MP4, or OGG/Opus according to recording
support, capped at 10 MiB. API keys remain on the backend; no external fonts,
scripts, UI runtime dependencies, local storage, or cookies are required. Assets
are served through an explicit allowlist with no-store and restrictive CSP.
Provider-side retention still follows your account settings.

The automated browser suite uses a deterministic synthetic WAV recording fixture
and a separate backend with mocked Messages SSE, STT HTTP, and TTS WebSocket.
Playback scheduling uses real Web Audio in a muted headless Chrome profile.
It does not access the physical microphone, play sound through speakers, use
your credentials, or write to your real usage ledger. The user subsequently
reported one successful browser microphone exchange: correct recognition and a
coherent answer, with 6.18 s total backend processing. See STATUS.md for the
measured stages. Broad microphone/voice quality, Safari behavior, and ESP32
capture/playback remain unverified.

Implementation references: [MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder)
and [microphone permission/capture](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

## Streamed audio and short answers

The tester uses **POST /converse/stream** with the same raw recording body and
10 MiB input cap as /converse. It returns application/x-ndjson, with X-Diagnostics-ID
and no-store. Each line is one event:

- `start`: diagnostic ID, pcm_s16le encoding, 16000 Hz mono, and initially empty sources.
- `audio`: base64 PCM bytes, always whole 16-bit samples, capped at 8 MiB decoded.
  Each event contains at most 16 KiB decoded PCM; larger provider frames are split
  so base64/JSON lines stay below the browser's 64 KiB event limit.
- `done`: successful completion and cited source links. An `error` event instead
  marks incomplete audio.

Validation/STT failures before streaming are normal JSON HTTP errors. After STT,
the endpoint returns HTTP 200 with a diagnostic ID, then model/speech failures are
explicit error events. Clients must require done, not treat EOF as success.
One Haiku Messages request streams SSE while one ElevenLabs text-input WebSocket
accepts complete-word final-answer fragments and returns PCM. Eight-item queues,
a 256 KiB SSE cap, 512 KiB socket frames and an 8 MiB decoded audio cap bound the
exchange. Speech generation uses a 50-character schedule. WebSocket redirects
are rejected and neither provider is retried automatically. The already pinned
websockets dependency handles async input because the installed speech SDK only
offers synchronous text-input streaming; HTTP STT/TTS retain the official SDK.
Disconnect cancels both model and speech work. The numeric ledger retains known
complete token/search costs after TTS failure; an interrupted model stream with
incomplete usage has null final cost and usage_complete=false. SSE cumulative
usage snapshots replace previous counts, not add to them.
No audio, text, sources, queries, or retrieved pages are stored in that ledger.

The browser schedules PCM chunks using Web Audio with a small initial buffer;
it does not wait for a complete WAV. It builds the replay WAV locally after done.
**First audio received** is browser elapsed time from Send to a playable chunk.
**Playback scheduled** estimates its scheduled start; it is not a physical-speaker
measurement. **Total processing** is backend time through synthesis/delivery
completion and can include client backpressure. **First answer text** is the first
final-answer word ready for speech; **First text sent to voice** records its send
to ElevenLabs. Model/TTS windows overlap and do not sum to total processing.
Buffer underruns count occasions
when playback runs out of scheduled samples. These numbers are distinct from
time spent recording, reviewing, or listening. A slower search can add latency.

The system instruction now targets one sentence, at most 30 words by default,
without introductions or offers of further help. Explicit requests for detail
allow up to three sentences/80 words. This is a model instruction, not arbitrary
word cutting or a claim that every live answer satisfies it. The existing 1024
output-token safety cap and 1000-character validation remain. Streaming final
answers use a `<voice>...</voice>` protocol; the markers, reasoning, tool JSON and
unmarked search preambles are never sent to speech. Tool calls after speech has
started, missing markers, malformed SSE, or incomplete turns fail explicitly.
Words already heard cannot be retracted if a later model error occurs; partial
audio is never presented as a completed replay. The buffered /chat and /converse
contracts are preserved. Recorded STT still finishes before model work, and
realtime microphone upload is not implemented.

The October 26 silent live overlap check reached first audio at **2.539 s** versus
**2.737 s** in the earlier sequential check using the same question file. Audio
arrived before the model's terminal event, proving live overlap. Different-time
single samples do not establish typical latency; the 1–1.5 s target is not reached.
See STATUS.md for the measured stages and local evidence paths.

References: [ElevenLabs audio streaming](https://elevenlabs.io/docs/api-reference/text-to-speech/stream),
[text-input WebSocket](https://elevenlabs.io/docs/api-reference/text-to-speech/v-1-text-to-speech-voice-id-stream-input),
[Messages SSE](https://platform.claude.com/docs/en/build-with-claude/streaming),
[Web Audio scheduling](https://developer.mozilla.org/en-US/docs/Web/API/AudioBufferSourceNode/start).

## Compare speech models

The comparison helper makes **two paid ElevenLabs generations**, one per model,
only when explicitly run with --live. It does not change the configured model,
call STT/Anthropic/search, open the microphone, or play audio through speakers:

```sh
backend/.venv/bin/python -m backend.compare_tts --live
```

Both requests use the configured voice, the same short synthetic sentence
(including a place name and numeric temperatures), pcm_16000, 1024-byte SDK
chunks, fresh connections and no retries. Results/WAV files go to a timestamped
ignored backend/output/tts-comparison-*/ directory. Metadata reports HTTP headers,
first playable PCM chunk, full generation time, audio duration, and selected
provider cost/region headers. Character-cost headers are not dollar amounts.

The authorized October 25 comparison measured **1675.754 ms** first PCM for
Multilingual v2 versus **221.072 ms** for Flash v2.5. See STATUS.md for evidence.
One sample per model does not establish statistical latency or voice quality;
the user subsequently listened and approved Flash as the new default.

The helper's free mocked checks are:

```sh
backend/.venv/bin/python -m pytest backend/test_compare_tts.py -q
```

## General web search

Haiku receives Anthropic's native web_search_20250305 tool with **max_uses=1**.
The prompt asks it to search for current/changing facts or explicit search
requests, and answer stable knowledge/greetings directly. Search selection is
model-driven, not a guarantee every weather question triggers it. No local search
service, weather-specific tool, code execution, or additional API key is added.
For local weather without a city, the assistant should ask for the city rather
than infer your location. Native search access may need enabling in your Claude
organization; a Models API capability check succeeded and the user's subsequent
weather test returned one live search and a displayed citation. The agent did not
run paid search or inference. Answer accuracy and broader quality remain unverified.

Search is **$10 per 1000 uses ($0.01 each), plus token charges**, checked October
25. Max uses prevents repeated searches in a request; pause_turn is rejected
without automatic continuation. Provider-reported web_search_requests determines
the fee estimate. Missing/malformed search usage after an observed search yields
unknown combined cost, not zero. Diagnostics separate estimated_token_cost_usd,
estimated_search_cost_usd and estimated_cost_usd (combined); older rows retain
their historical token-only costs. These estimates exclude ElevenLabs and actual
account balance. Failed searches with a reported count of zero add no search fee.

Search preambles are excluded from the spoken answer. Cited HTTP(S) URLs/titles
are returned in /chat's sources or the stream's done event and shown as safe
links in the tester; source content remains untrusted data. Search queries and
results are processed by Anthropic and its search infrastructure. Browser source
links stay in page memory and are cleared with the exchange.

Reference: [Anthropic web search configuration and pricing](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool).

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
reported token usage is still recorded. General web search is enabled as described
above; no action tools are enabled.
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
provider-reported token/search counts, and estimated costs; no keys, prompts, transcripts,
reply text, reasoning text, or source/query text. Full voice-loop records also include STT/TTS and
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
backend/.venv/bin/python -m pytest backend/test_app.py backend/test_chat.py backend/test_tester.py backend/test_streaming.py backend/test_compare_tts.py backend/test_overlap.py -q
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
  quality testing, audio-session memory, realtime microphone upload, ESP32 firmware, and hardware
  playback are not implemented in this increment.

API contracts checked against the official [transcription reference](https://elevenlabs.io/docs/api-reference/speech-to-text/convert),
[speech reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert),
and [voice-list reference](https://elevenlabs.io/docs/api-reference/voices/search).
