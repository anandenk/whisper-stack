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
description or showcase. On 2026-10-27 the user selected this Agents + Mem0
architecture as the main backend (D27). WebRTC is the accepted browser transport;
the physical device transport remains unverified and undecided.

## Deferred brainstorming

The user's October 27 handwritten ideas are preserved separately in
[deferred brainstorming](../notes/brainstorm-2026-10-27.md): optional vault access,
model alternatives, screen/face visualizations, enclosure ideas, microphone and
display priorities, hardware feasibility, and a proposed development order.
The user asked to park these ideas and focus on making the existing backend
solid first, returning to the prior handoff. They are not new implementation
requirements; D29 resumes the bounded memory evaluation from the prior handoff.

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
| D26 | Merge the completed rebuild into main, then rewrite the main README in the next turn (user authorization, 2026-10-26). | The user explicitly requested that the agent create/merge the PR itself after publication and validation. This supersedes D16's earlier no-main-merge boundary. Preserve the commit history and legacy implementation; merging the source does not select a production migration. Defer the README rewrite to the next turn: briefly explain the old project, briefly explain the new project, and choose other relevant information together. |
| D27 | Adopt the accepted ElevenLabs Agents + Haiku 5.5 + hosted Mem0 work as the main backend (user decision, 2026-10-27). | The user explicitly selected the current work after clarifying whether it should replace the custom STT → Haiku → TTS pipeline as the main architecture. This supersedes D23–D26's experiment-only architecture boundary. The implementation currently remains in experiments/agents-mem0; backend/ is retained as the earlier independent implementation. Selection does not mean code has been relocated, feature parity achieved, or deployment/hardware verified. This turn records the decision and remaining milestones; it does not start their implementation. |
| D28 | Build only feature 1: view saved memories (user authorization, 2026-10-27). | The user selected the first feature from the milestone-1 breakdown. Add an on-demand, paginated memory list to the existing tester, scoped to the same fixed owner, with refresh and distinct empty/error states. No deletion, memory-write changes, session statistics, retention changes or search are included. Preserve SDK audio and the working conversation path. |
| D29 | Start the isolated memory evaluation; use existing included Mem0 allowance only, with no additional spend (user authorization, 2026-10-27). | Build and verify the offline runner, then evaluate 30 fabricated facts, three duplicate submissions, three corrections and 16 queries under a new owner after checking account allowance. Keep the application's top_k=5 and provider-default extraction/search settings. Separate lexical diagnostics from semantic review and retrieval from answer generation. No existing-owner writes, automatic retries, deletion, plan changes, additional model/audio calls, larger dataset or deferred features. Preserve all local changes without committing/pushing. Long-session conversation testing remains a separate stage. |
| D30 | Investigate native correction/temporal handling with a small follow-up (user authorization, 2026-10-27). | Inspect the six captured old/new records and compare delayed default reads with native latest_only=true on the existing synthetic owner. Bound the read-only run to 20 requests and nine searches, including three unchanged/unknown controls. Continue using included Mem0 allowance only, without writes, app/agent settings changes, paid upgrades, another model, dataset expansion or commit/push. Preserve lifecycle fields in evaluation evidence so retained history is distinguishable from active contradictions. |
| D31 | Apply active-only retrieval to normal current-fact recall; preserve historical viewing (user authorization, 2026-10-27). | D30 verified the native filter on all three corrections. Add latest_only=True to the shared retrieveMemories search while retaining its fixed owner/top_k=5 and the viewer's default history listing. No deletion, new historical-query mode, audio/agent change, additional live evaluation or commit/push. Explicit historical questions may need a later history-aware recall policy. |
| D32 | Run a bounded actual-assistant evaluation of corrected answers and unknown-answer behavior (user authorization, 2026-10-27). | Use the existing hosted Haiku agent with its unchanged prompt/model/tools and already-permitted text-only session override. Eight fresh sessions, one frozen question each: three corrections, four unknowns and one unchanged fact. Route the shared active-only recall handler to the existing synthetic Mem0 owner; block writes. Included ElevenLabs credits/Mem0 allowance only; no purchases, settings changes, retries, microphone/speaker use or commit/push. Bound to 16 memory searches, 64 HTTP reads/searches, 50 seconds per case and a 1,000-credit observed-delta stop threshold between sessions. Preserve transcripts/tool evidence and distinguish text-answer results from audio/hardware/long-context proof. |
| D33 | Run the final bounded conversation-context test, then close this initial evaluation round if it passes (user authorization, 2026-10-27). | One unchanged hosted Haiku agent, one text-only session: three fabricated details in the opening message, 20 intervening exchanges, then recall. No Mem0 reads/writes, agent changes, microphone/audio, extra spend or commit/push. Cap at 22 user messages/300 seconds, retaining transcript size, elapsed time and included usage. Check the existing session limit and allowance first. This is a bounded early-detail baseline, not context-window capacity or compaction proof; no automatic larger test. |
| D34 | Repeat the same D33 context scenario once after its provider timeout (user authorization, 2026-10-27). | Preserve the original inconclusive attempt. Recheck included credits/configuration and retain all D33 isolation and session bounds. No fixture/prompt/agent changes, Mem0 access, additional cash spend, further automatic retry or commit/push. Review actual transcript/usage; a pass closes this initial evaluation round without starting another milestone. |
| D35 | Diagnose the repeated hosted conversation timeout (user authorization, 2026-10-27). | Inspect existing evidence, current provider settings and SDK/protocol first; use small controlled text probes within existing included credits if needed. First compare simple follow-ups with the unchanged problematic question as the first user message, preserving the current agent/prompt/model and blocking all Mem0 access. No production settings change, model switch/fallback activation, extra cash spend, commit/push or automatic full context rerun. |
| D36 | Raise only the hosted model response wait from four to 10 seconds and check the unchanged problematic question once (user authorization, 2026-10-27). | Test D35's leading timeout hypothesis with one narrow remote PATCH, exact configuration-diff verification and one text-only session within existing included credits. Preserve Haiku, prompt, low effort, output cap, disabled fallback, tools, audio, owner and session/silence settings. No Mem0 access, full context rerun, additional retries, cash spend, commit or push. |
| D37 | Raise only the thinking-plus-answer cap from 1,024 to 4,096 tokens and test the unchanged printer question once (user authorization, 2026-10-27). | Test D36's token-exhaustion hypothesis with one narrow remote PATCH, exact configuration-diff verification, matching local configuration/evaluation guard and a single text-only question within included credits. Preserve the selected 10-second wait, Haiku/low effort, concise prompt, disabled fallback, audio, tools and owner; no Mem0 access, full context rerun, automatic retries, extra cash spend or commit/push. |
| D38 | Run the original frozen context scenario once with the selected 4,096-token/10-second settings (user authorization, 2026-10-27). | Complete the sole unfinished initial check: three fabricated opening details, 20 unrelated exchanges and final recall in one text-only session. Same frozen fixture, no Mem0 access or settings changes. Preserve original attempts; recheck included credits/configuration. Retain D33 session/message/time bounds, no automatic retry, expansion, additional cash spend or commit/push. Close this initial evaluation round if it passes. |
| D39 | Save all progress and resume next session with backend readiness (user request, 2026-10-27). | Stop after the local handoff; preserve every edit, evaluation report and running service without commit/push. The initial memory/context round is complete. Next session begins with a read-only review of fixed-owner/client-access assumptions on the selected Agents path, producing a small readiness gap list and next increment. No automatic repeated evals, architecture switch, new feature, retention/deletion change, spending, deployment or hardware work. |
| D40 | Check the five essentials for a private personal prototype (user authorization, 2026-10-28). | Keep startup simple, access local and owner fixed, secrets unpublished and synthetic tests separate. Understand provider storage/deletion and use existing failure messages/provider usage pages. Make small local fixes for demonstrated gaps; no consumer accounts, custom deletion UI or diagnostics dashboard. Preserve the current service and all edits without commit/push. Retention changes, data deletion, extra spending, deployment and hardware work remain separate choices. |
| D41 | Close the browser-backend milestone and build the project README before publishing the completed work (user direction, 2026-10-28). | Present the working system with one complete architecture diagram, clear setup, scoped verification and the existing device milestones. User review selected a single diagram with each component once; use a checked-in SVG for readable fixed layout instead of the earlier Mermaid variants. Browser backend complete means the agreed personal-prototype scope; device transport, hardware and hosting remain unfinished. Current request is the README; no new backend feature, service restart or publication is performed in this documentation pass. |
| D42 | Commit and push the completed browser-backend milestone and approved README (user authorization, 2026-10-28). | Supersedes the earlier no-commit/no-push hold for this publication. Publish to the existing private main branch, including completed source/tests, synthetic fixtures/evaluation summaries, deferred notes and documentation. Keep secrets, dependencies, generated bundles and raw evidence ignored. No new feature, live evaluation, provider change, service restart, deployment or hardware work. |
| D43 | Review the public-release audit and prepare the AGY remediation plan; retain authorship while removing unnecessary personal/institutional details (user direction, 2026-10-30). | Phase 2 only: separate publication disclosures from current/legacy runtime risks. Proposed history sanitization stays in a separate local candidate; active/remote history and visibility are unchanged. Phase 3 implementation and final asset disposition are not approved by planning. See STATUS.md and project records. |
| D44 | Implement the approved public-release remediation scope in a separate local candidate, delegated primarily to AGY (user approval, 2026-10-30). | Preserve original source/history/remote, author attribution and legacy runtime behavior. Candidate-only commits/history transformations are allowed. Record targeted privacy cleanup, local agent-reference setup, historical warnings and sample provenance; leave independent verification and publication to later phases. Final sample redistribution disposition remains open. |
| D45 | Complete independent source/privacy and mocked runtime verification; prepare a fresh public repository at the canonical project URL (owner decisions, 2026-10-30). | Preserve the private original and intended sanitized history/attribution. The owner explicitly accepted the scoped offline test boundary; 49 Python/10 browser checks passed. Retain the historical sample with documented upstream attribution and caveat. External rename/upload/visibility operations remain subject to final approval. |

### D23 setup choices and boundary (2026-10-26)

The separate native Haiku 5.5 test agent and hosted Mem0 client-tool runner now
exist under [experiments/agents-mem0](../experiments/agents-mem0/README.md).
Authentication is required (signed URL for Python, session token for WebRTC),
and alternate LLM fallback is disabled so the
experiment retains the selected model. Agent defaults: low reasoning effort,
an initial 1,024-token cap (superseded by D37's 4,096), short replies, George
voice, fixed local test-user memory scope.
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
collaborative evaluation are complete. The original experiment-only boundary
was subsequently superseded by D27's selection as the main backend; new feature,
deployment and hardware milestones still require their own implementation scope.
The existing agent's read-only D40 check confirms voice recording and unlimited
conversation retention (`retention_days=-1`); this is its actual configuration,
not a claim about today's provider defaults. Long-term retention remains an open choice. See STATUS.md
for verification and next action.

## Intended component responsibilities

The selected main backend uses ElevenLabs Agents for conversation and audio,
native Claude Haiku 5.5 for replies, and hosted Mem0 for persistent memory.
The official WebRTC SDK handles browser audio. Final device transport remains open.

- **Device:** microphone capture, wake/button interaction, speaker playback,
  display, controls, and connection/error behavior.
- **Main service:** the FastAPI bridge in experiments/agents-mem0 currently
  issues private-agent WebRTC session tokens and forwards memory tools to Mem0,
  keeping keys server-side. It is loopback-only, with a fixed test-owner scope.
  Device authentication, deployment and broader service diagnostics remain work.
  ElevenLabs owns the live conversation; do not rebuild its audio/session engine.
- **Development tester:** the official browser WebRTC client (default port 8766;
  saved running instance on 8767) is the
  accepted main-path tester. Capture/playback and audio processing belong to the
  SDK. The page currently relays memory tool calls to the local bridge; a device
  client must support those calls or use a separately selected server-tool design.
- **Earlier custom backend:** backend/ retains independent STT/Haiku/TTS, the
  /tester push-to-talk page, bounded web search and numeric diagnostics. Those
  features are not automatically available in the selected Agents path. Its
  16 kHz PCM contract is not a verified contract for the main path or hardware.
- **Remote operation:** the bridge/tools must eventually operate without this
  workstation. The provider-hosted agent alone does not remove the current
  local service dependency.

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
  contract. The selected Agents path currently uses Flash v2; D21's Flash v2.5
  default remains specific to the earlier custom backend.
- Exact hardware inventory, wiring, development/flash computer, and tooling.
- Budget, response latency, audio quality, and reliability targets.
- Enclosure dimensions, assembly method, and whether motion belongs in the first
  deliverable. Old servo experiments are not a committed requirement.
- Memory behavior, retention, privacy, and device provisioning.
  D32 observed an offer to save a missing access code; no code was supplied or
  written. Secret-handling and overly long unknown replies are pending policy/
  polish choices, not selected changes.
- Memory/context growth: the user raised large-store recall and a possible
  context-managing agent on 2026-10-27. The inspected bridge requests top_k=5
  per recall, returns text only, and applies no application-level text/token
  budget, relevance threshold override or repeated-result filtering. This is
  not a cap on cumulative conversation context. ElevenLabs owns that context;
  supported history controls must be verified before promising custom trimming.
  Clarification after checking native features: evaluate Mem0's existing
  extraction and ranked retrieval first. [Dream documentation](https://docs.mem0.ai/platform/features/dream)
  distinguishes automatic Supersede/Merge from opt-in Synthesis. D29's generic
  [pricing](https://mem0.ai/pricing) recheck left their Free-plan behavior
  unresolved; **D30 now confirms actual superseded links and working active-only
  reads on this Free account**. Do not infer that all Dream subfeatures require
  an upgrade, or that synthesis/dashboard access is included. The baseline's
  retained old records were expected history, not proof of superseding failure.
  Earlier suggestions to build duplicate/conflict management were premature.
  Evaluate existing
  features and recall/latency first; add custom rules or another model only for
  demonstrated gaps. D29 starts the small isolated baseline; larger datasets
  and a context agent remain unselected. ElevenLabs' cost
  guidance recommends app-managed history summaries/windows; automatic live-call
  compaction has not been confirmed from the checked documentation.

Earlier FastAPI research did not select a framework. D10 supersedes that open
choice for the first backend increment; it does not revive the old server stack.

## Proposed remaining milestones after D27

These describe remaining work; D28 completes the viewer, D29/D30 the retrieval
evaluations, D31 active-only recall and D32 the small hosted text-answer baseline.
D33/D34 stopped before recall; D35–D37 diagnosed and corrected the model
budgets. D38 then passed all three opening details after 20 intervening
exchanges with no Mem0 or target refresh. The initial small memory/context
round is complete. Filler reasoning/instruction-following mistakes are recorded
separately; other work and larger evaluations remain unselected.

1. **Main service readiness:** consolidate the existing startup/configuration
   around the selected path; establish stable owner identity and client access,
   memory inspection/deletion and retention policy, and timing/error/available
   usage diagnostics. Existing provider authentication/token issuance is already
   implemented. D40 narrows the immediate target to the five personal-prototype
   essentials, checked in STATUS.md: local single-owner access and provider
   controls are sufficient now; custom deletion, consumer accounts and broader
   diagnostics remain deferred proposals. General web-search parity is a separate missing feature; preserve
   the earlier one-search bound and distinguish fees from token estimates.
   Proposed memory-growth milestone: test correct recall, changed/outdated facts,
   irrelevant matches, returned text size and latency with increasingly large
   isolated datasets. Use offline checks for application bounds, and separate
   real-provider evaluation for retrieval quality; mocked search cannot establish
   it. Mem0 already provides ranked retrieval, thresholds and optional reranking
   ([search reference](https://docs.mem0.ai/api-reference/memory/search-memories)).
   Separately verify long-session behavior on the hosted ElevenLabs path. A
   separate context agent is an open option, not a selected implementation.
   Proposed first evaluation: a separate test owner with 30–50 fabricated facts
   and fixed questions for recall, changed facts, duplicates and unknown answers.
   Inspect stored records as well as retrieval/answers; wait for asynchronous
   processing before scoring. Measure correctness, latency, returned text size
   and usage; expand to 100/1,000 facts only within an agreed budget. Separately
   test early-detail recall in a long ElevenLabs conversation with that detail
   kept out of Mem0. This tests behavior, not proof of automatic compaction.
   D29 authorizes the initial 30-fact evaluation within existing included Mem0
   allowance only. See [evaluation procedure](../experiments/agents-mem0/evaluation/README.md)
   and [first baseline results](../experiments/agents-mem0/evaluation/baseline-2026-10-27.md).
   That baseline is complete: correct evidence in top five for 12/12 known
   questions, but all three corrections ranked old assertions ahead of new
   facts. The [D30 follow-up](../experiments/agents-mem0/evaluation/native-corrections-2026-10-27.md)
   confirms old records are superseded and latest_only=true ranks each active
   replacement first. D31 now implements that native filter for normal recall
   while the viewer retains history. The [D32 answer baseline](../experiments/agents-mem0/evaluation/answers-2026-10-27.md)
   passed all three corrected questions, four unknowns and one unchanged control
   in fresh real hosted text sessions, with no writes or agent/prompt changes.
   The [D33 context attempt](../experiments/agents-mem0/evaluation/context-2026-10-27.md)
   and its explicitly authorized D34 repeat both stopped on the same provider LLM
   timeout before recall; neither establishes forgetting. The
   [D35 diagnosis](../experiments/agents-mem0/evaluation/timeout-diagnosis-2026-10-27.md)
   confirms simple follow-ups work while the problematic question fails even
   first in a fresh session. D36 then applied only the selected model wait change
   to 10 seconds; the same question failed with “Brain returned no response”.
   The reported 3,072 initiated output tokens suggest exhaustion of the 1,024
   thinking-plus-answer cap across attempts, without exposed stop reasons. A
   cap increase to 4,096 was selected in D37 and restored the same question's
   correct answer at the ten-second wait (1,198 output tokens/4.876 s first byte).
   This supports the budget diagnosis for that case. D38 then passed the original
   frozen context scenario: all three early details after 20 exchanges, no
   Mem0 access or target refresh. The initial small memory/context round is
   complete; no further evaluation automatically. General reasoning/brevity
   issues observed in filler replies are recorded separately. Historical-question
   policy, reliability beyond this bounded trial, multi-hour behavior and
   scaling remain open. Expansion remains unselected.
2. **Device audio slice:** select and test a feasible device transport/audio
   contract, then capture one question and play one answer on the physical board.
   Include session credentials and memory-tool routing. Browser WebRTC acceptance
   does not establish ESP32 transport or speaker echo behavior. Do this before
   expanding the backend further, consistent with D06.
3. **Device interaction:** add the selected activation control, display/status,
   session ending, and network/error recovery; verify interruption behavior on
   the physical microphone/speaker. Wake-word activation is still a choice.
4. **Workstation-independent operation:** choose hosting, deploy the minimal
   bridge/tools with authenticated access, and demonstrate the device working
   with the development computer switched off. No host or spending is selected.
5. **Enclosed desk prototype:** confirm wiring/power, assemble the USB-powered
   enclosure, and verify repeated use, restarts, outages, latency and cost against
   agreed targets. Battery and motion remain deferred/unselected.

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
