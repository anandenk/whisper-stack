# Whisper Stack: project record

This file owns goals, decisions, and unresolved choices. Read [STATUS.md](STATUS.md)
for current work and [AGENTS.md](../AGENTS.md) for working rules.

## Goal

Build a physical voice assistant with its own microphone, speaker, display,
controls, and enclosure. The first deliverable is a USB-powered desk device that
uses Wi-Fi and cloud processing. In its eventual deployed form it must work with
the workstation switched off. Fully offline AI processing is not a requirement.

## Saved project one-liner

> Haiku 5.5 + Mem0, using ElevenLabs’ WebRTC audio.

Saved at the user's request on 2026-10-26 for a future finished-project
description or showcase. This wording currently describes the isolated Agents
experiment; final project architecture remains subject to later decisions.

## Decisions from the revival discussion

Initial decisions established with the user on 2026-10-19; updates are dated
below. These record accepted direction. Proposed milestones still need to be
distinguished from work the user has asked to implement.

| ID | Decision | Reason |
|---|---|---|
| D01 | Keep continuity between sessions a priority. The workspace-first implementation pause was superseded by D07 on 2026-10-24. | Earlier AI sessions accumulated competing assumptions and complexity; the user stopped implementation to establish the workspace first. |
| D02 | Pursue a fresh implementation informed by the May prototype. Preserve the old implementation as reference. | The user does not trust the accumulated code enough to use it wholesale. Hardware findings remain useful. |
| D03 | Target a cloud-connected device physically independent of the workstation. | The deliverable is a complete physical device; internet connectivity is acceptable. |
| D04 | Use USB power for the first enclosed prototype. Battery work is deferred. | The user chose a USB desk device with its own speaker and no longer has the LiPo battery. |
| D05 | The workstation may host the new backend during early development. Keep eventual remote hosting in the design. | Avoid initial hosting expense while testing the intended device/server boundary. AI service usage may still cost money. |
| D06 | Develop firmware and backend together through small end-to-end interaction slices. | Building the whole backend first caused repeated integration work in May. |
| D07 | Resume project work, beginning with a current-state walkthrough and next-step discussion (2026-10-24). The initial review-only scope was subsequently superseded by D09. | The user wants to begin work and recover context from the separate groundwork session. |
| D08 | Use ElevenLabs for text-to-speech synthesis (2026-10-24). | The user reports redeeming the ElevenLabs API student plan and explicitly selects it for speech output. Access and working synthesis were initially unverified; subsequent CLI onboarding/test evidence is in STATUS.md. Plan allowance and expiry remain unverified. |
| D09 | Start a bounded backend increment with ElevenLabs for both recorded-audio STT and TTS (2026-10-24). | The user accepted the proposed audio tests while flashing is blocked. This adapts D06: verify backend boundaries now, then device interaction when cable access returns. LLM and realtime streaming remain outside this increment. |
| D10 | Use Python with FastAPI, HTTPX, and a dedicated backend/ virtual environment for this increment (agent implementation choice, 2026-10-24). Direct HTTPX speech calls were subsequently superseded by D11. | A small HTTP service provides validation and interactive API docs without the legacy GPU/audio dependencies. Pin the installed environment and test provider requests with a mock transport. |
| D11 | Follow the user-requested ElevenLabs CLI onboarding and use the official Python SDK for STT/TTS (2026-10-24). | Browser authorization creates the local project key without copying it through chat. The SDK uses python-dotenv-loaded configuration and the existing bounded HTTPX transport; public local endpoints and audio contract are preserved. |
| D12 | Use George (JBFqnCBsd6RMkjVDRZzb) as the initial backend test voice (agent test choice, 2026-10-24). | The account's voice listing confirmed availability, and the user authorized beginning voice selection and the backend TTS test. This is a replaceable test setting, not the user's final assistant-voice choice. |
| D13 | Use Claude Haiku 5.5 and add per-exchange token/cost diagnostics (user decision, 2026-10-24). | The user accepted the cost/intelligence recommendation and wants to measure actual use. This supersedes the open LLM choice and D09's initial exclusion of LLM work. Estimates must use provider-reported usage, include reasoning in billed output, and remain distinct from actual account balance and ElevenLabs charges. |
| D14 | Use low effort/adaptive thinking, a 1024-token output cap, bounded client-supplied text history, and a local SQLite diagnostic ledger (agent implementation choices, 2026-10-24). | Keep voice replies short, avoid unbounded repeated context, and retain usage across restarts without storing conversation text. Existing HTTPX calls the Messages API; SQLite uses Python's standard library. The first audio loop is single-turn; server conversation memory is deferred. |
| D15 | Use mocked checks and bounded live verification (2026-10-25). | Verify provider contracts without repeated billable calls, then use short live checks for behavior mocks cannot establish. Avoid automatic retries, purchases, and auto-reload. Diagnostics are estimates, not an account wallet. |
| D16 | Keep the existing private anandenk/whisper-stack repository and publish this increment on rebuild/backend-tester (user acceptance, 2026-10-25). | Preserve the project's history and May reference code while developing the isolated rebuild. No new repository or main-branch merge is needed for this increment. |
| D17 | Build a local browser push-to-talk tester; defer human microphone testing until the user is available (2026-10-25). | Use the existing backend and single-turn audio loop while the device cable is unavailable. Agent defaults: click Record/Stop, preview locally, explicit Send, 30-second cap, in-memory audio, manual reply playback, and per-exchange numeric diagnostics. Automated verification uses synthetic audio and mocked providers, with no additional live spend. |
| D18 | Add streamed playback and generally shorter replies (user decision, 2026-10-25). | The user's first successful browser exchange took 6.18 s, of which 4.38 s was synthesis. Stream ElevenLabs PCM into browser playback after Haiku finishes, measure first audio separately, and preserve a final WAV replay. Default system instruction: one sentence and at most 30 words; explicit detail requests may use up to three sentences and 80 words. These are model instructions, not destructive word truncation. This supersedes D17's manual-only initial playback. |
| D19 | Add general web search, without a weather-specific tool (user decision, 2026-10-25). | Use Anthropic's native web_search_20250305 with max_uses=1, selected by the model for current facts or explicit search requests. Haiku 5.5 capability support was verified by the Models API; the user's subsequent weather test verified one live search and a displayed citation (see STATUS.md). Keep cited links in the response/UI, not the numeric ledger, and exclude search preambles from spoken answers. Search is $0.01 per use plus tokens at the checked price; report token/search subtotals and combined estimates. Do not infer a location for local weather. |
| D20 | Test Eleven Flash v2.5 against the current Multilingual v2 (user authorization, 2026-10-25). | One short live sample per model using the same voice/text/format and fresh connections. Flash reached the first PCM chunk in 221 ms versus 1676 ms for Multilingual in this sample; evidence and caveats are in STATUS.md. The initial pending quality/default choice was subsequently resolved by D21. No Anthropic credits were used by this TTS-only comparison. |
| D21 | Adopt Eleven Flash v2.5 as the TTS default (user decision, 2026-10-25). | The user listened and approved Flash. Set eleven_flash_v2_5 in the application default, example configuration, and local ignored configuration. The default is verified locally; an October 26 synthetic-input live voice exchange passed with first client audio at 2.737 s and completion at 3.311 s. Browser/human listening validation for that full Flash exchange remains separate; see STATUS.md. |
| D22 | Overlap Haiku answer generation and TTS (user authorization, 2026-10-26). | Use Messages SSE and one ElevenLabs text-input WebSocket for /converse/stream. Send whole-word final-answer fragments before Haiku completes; exclude reasoning and unmarked search preambles through a voice-marker protocol. Bounded queues, capped responses, no retries/automatic continuation, and explicit partial-stream errors preserve usage evidence. The installed ElevenLabs SDK offers synchronous text-input streaming only, so this async adapter uses the already pinned websockets dependency; HTTP speech endpoints retain the SDK. Keep /chat and buffered /converse contracts. One silent live check reached first audio at 2.539 s versus the earlier 2.737 s sample, and audio preceded the model terminal event. These separate single samples are not a statistical speed benchmark. |
| D23 | In the next session, do only a basic ElevenLabs Agents + hosted Mem0 integration, then collaborate with the user (user authorization, 2026-10-26). | Create a separate test agent using native Haiku 5.5, whose catalog availability was verified, and configure Mem0 save/retrieve tools using the documented integration. The user accepts Mem0's advertised free allowance for initial evaluation; account/key availability remains unverified. Preserve the working backend and existing agents. Stop after basic setup and work with the user on live testing and refinement; no automatic paid conversation tests, audio playback, purchases, broader feature work or production migration. This authorizes an isolated experiment alongside D14's single-turn backend, without replacing it. |
| D24 | Try ElevenLabs’ official browser WebRTC client for the isolated Agents + Mem0 experiment (user authorization, 2026-10-26). | After the Python microphone correction, the user confirmed input/output worked but reported the assistant hearing its own speaker output repeatedly. They rejected muting capture during playback and custom echo-cancellation integration, first requested planning, then selected the official WebRTC client. Use the SDK/browser’s existing audio processing, preserving the same test agent and Mem0 tools. A minimal loopback page/credential-and-tool bridge is within this test; the working backend, device firmware, deployment and production migration remain unchanged/out of scope. |
| D25 | Answer one-off questions, then wait quietly for user-initiated follow-ups (user decision, 2026-10-26). | The user accepted the WebRTC audio test in Edge but does not want repeated “are you still there?” prompts during silence. Disable the test agent's native silence re-engagement timer (`conversation_config.turn.turn_timeout=-1`, previously 7 seconds). Keep follow-up questions/context within the open session; this is not an auto-disconnect or wake-word implementation. Other settings, memory tools and working backend remain unchanged. |

### D23 setup choices and boundary (2026-10-26)

The separate native Haiku 5.5 test agent and hosted Mem0 client-tool runner now
exist under [experiments/agents-mem0](../experiments/agents-mem0/README.md).
Authentication is required (signed URL for Python, session token for WebRTC),
and alternate LLM fallback is disabled so the
experiment retains the selected model. Agent defaults: low reasoning effort,
1024-token cap, short replies, George voice, fixed local test-user memory scope.
The English Agents API rejected `eleven_flash_v2_5`, so the experiment uses
`eleven_flash_v2` (agent setup choice); D21's working-backend default is unchanged.
These are replaceable experiment settings, not a backend migration or final
voice/memory policy. The user supplied a local Mem0 key and authentication plus
agent/tool definition checks passed. Basic setup is complete. The user tested the
Python runner and reported successful listening but speaker feedback; D24 selects
the official WebRTC client. The user subsequently accepted its speech recognition
and speaker behavior in Microsoft Edge, including interruptions. The user later
reported Chrome also works after disabling multiple privacy extensions; the
specific extension/rule was not isolated. D25 disables idle re-engagement.
The user confirmed that a saved preference was recalled in a new session,
validating the basic hosted Mem0 persistence/recall flow. D23–D25 setup and
collaborative evaluation are complete; this does not authorize migration or
further feature work.
ElevenLabs creation defaults currently record voice and retain conversation data
indefinitely; long-term retention policy remains an open choice. See STATUS.md
for verification and next action.

## Intended component responsibilities

ElevenLabs is selected for speech recognition and synthesis. The first backend
uses Python/FastAPI with Claude Haiku 5.5; final device transport remains open.

- **Device:** microphone capture, wake/button interaction, speaker playback,
  display, controls, and connection/error behavior.
- **Backend:** device authentication, conversation orchestration, session state,
  and AI service integration. Initially runs on a development computer; eventually
  runs remotely. Normal device operation must not require that computer's
  microphone, speakers, or GUI.
  backend/ implements local health, voice listing, recorded-file transcription,
  buffered and streamed synthesis, Haiku chat with bounded general search,
  a single-turn voice loop, and numeric usage
  diagnostics. Device authentication and server conversation sessions are future work. It runs on
  loopback and returns 16 kHz mono 16-bit PCM in WAV for initial audio testing;
  that is not a hardware-verified audio contract.
- **Development tester:** a local browser page at /tester records short questions
  only after an explicit action and uploads only after Send. It plays streamed
  speech as it arrives, offers mute/replay, and shows source links and timings.
  Recorded STT finishes first, then Haiku text and TTS overlap. It is not device
  firmware or a deployed client.
- **AI services:** speech recognition, language processing, and speech synthesis.
  Use ElevenLabs for speech recognition/synthesis and Claude Haiku 5.5 for replies.

## Hardware and environment facts

- On 2026-10-25 the user generated a fresh Anthropic API key with a 30-day
  expiry, expected around **2026-11-24**. Exact expiry time is unverified and
  should be checked in the Claude Console. The configured key subsequently
  passed a read-only authentication check. No key value is recorded here and
  no scheduled reminder was requested or created.
- The user reports that the original Windows/NVIDIA laptop is still available.
- On 2026-10-24 the user reports having an ESP32-S3-WROOM-1 board, an ESP32
  DevKit V1, and the other checklist items (amplifier, speaker, microphone,
  displays, breadboard, and wires). The S3 development-board variant, N8R8
  marking, component specifications, assembly, and wiring remain unverified.
- The user lacks a micro-USB data cable and reports this blocks programming the
  boards for now. Exact board connectors and flashing access are unverified.
  The user subsequently authorized a bounded backend increment during this
  hardware blocker (D09).
- Checked-in firmware targets ESP32-S3-DevKitC-1 N8R8 with an INMP441 mic,
  SSD1306 displays, and MAX98357A pins reserved for future speaker output.
  These are repository facts, not confirmation of the physical assembly.
- This checkout is at `~/whisper-stack`. This Mac is not the original
  Windows runtime; its environment does not prove Windows compatibility.

## Choices still open

- Final assistant voice, hosting, and the final device/server transport and audio
  contract. Flash v2.5 is the accepted current TTS default (D21).
- Exact hardware inventory, wiring, development/flash computer, and tooling.
- Budget, response latency, audio quality, and reliability targets.
- Enclosure dimensions, assembly method, and whether motion belongs in the first
  deliverable. Old servo experiments are not a committed requirement.
- Memory behavior, retention, privacy, and device provisioning.

Earlier FastAPI research did not select a framework. D10 supersedes that open
choice for the first backend increment; it does not revive the old server stack.

**Accepted LLM choice, 2026-10-24 (D13):** Claude Haiku 5.5
(`claude-haiku-5-5`) for concise conversational replies. The earlier proposal is
now accepted; GPT-6 Luna remains an unselected comparison. Official short-prompt
standard rates checked today:
both $0.10 input / $0.50 output per million tokens. Haiku's low rate applies up
to 100K prompt tokens. Provider benchmarks do not establish our conversational
quality or latency; test those on the actual loop. Sources:
[Anthropic](https://www.anthropic.com/claude-haiku-5-5),
[OpenAI](https://developers.openai.com/api/docs/models/gpt-6-luna).

## Maintaining decisions

Keep accepted decisions here. Label suggestions as proposed. Record dates and
reasons when decisions change. Do not create a competing specification later.
The existing GPL license remains in place; reuse does not imply a license change.
