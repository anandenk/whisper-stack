# Whisper Stack: current status and session handoff

Updated: 2026-10-30 (America/New_York).

## Public-release Phase 3 implementation (2026-10-30)

The user approved Phase 3. AGY implemented the scoped remediation in a separate
local candidate: targeted privacy substitutions across three historical branches
and an annotated tag; owner-local agent reference with a tracked example and
synthetic offline reference fixture; focused legacy warnings; and factual sample
provenance with upstream notice. Author names, chronology, license attribution
and historical implementation logic were retained. Current local-owner behavior
and provider settings were not intentionally changed.

AGY reports static syntax/JSON/history checks completed. No application/browser
suite, build, provider session, audio or hardware check ran in this phase. Those
results are deferred to independent Phase 4 verification; static checks do not
establish runtime correctness or publication readiness. The sample bytes remain
unchanged, with final redistribution disposition still open. Nothing was pushed,
and no remote visibility/ref change occurred.

**Next concrete step:** independently verify the candidate and privacy/ref scope
in Phase 4. Before a public release, resolve the sample disposition and review all
remote/public surfaces. Earlier sections below are dated historical records;
their statements that Phase 3 had not started are superseded by this section.

## Public-release Phase 2 review and plan (2026-10-30)

The user authorized critical review/planning and an eventual AGY Phase 3 handoff;
**remediation has not started**. Owner selected the privacy target: retain
project/author attribution while removing unnecessary personal/institutional
context. This supersedes Phase 1's stop boundary for review/planning only.

The private Phase 2 review and plan
proposed a separate sanitized full-history candidate; active/original repository
and remote remain untouched. Public-main-only cleanup is insufficient: read-only
remote ref query confirms main, two other branches and an annotated tag.
Archived deployment docs also contain a non-generic login identifier, beyond
ordinary institutional comments. Reports omit its value. Legacy benchmark
`/listen` buffers a body, correcting Phase 1's overly broad streaming description.

**Verified now, without application execution:** current main/refs/source unchanged;
137 commit messages pattern-scanned with zero matches; OSV checked 49 distinct
pinned Python package/version pairs (including the overlooked 44-package current
lockfile), zero advisory matches; PyPI confirms all versions exist with no
reported advisories/yanked files. Direct installed METADATA matches all 44/28
pins, excluding pip. These are bounded metadata checks, not runtime or package
integrity proof. `samples/jfk.wav` is byte-identical to whisper.cpp's pinned
initial-release artifact; exact upstream hash/source now recorded. Original
recording rights remain unverified; retain-with-provenance versus omit is a
release decision, not a reason to modernize historical tools.

Proposed work: targeted all-ref privacy cleanup in a separate candidate,
owner-local agent reference/example with minimal setup/test updates, concise
legacy warnings and factual fixture/third-party provenance. No blanket dependency
upgrades, current security redesign, legacy runtime fixes, provider changes or
publication actions. Phase 3 permissions/acceptance criteria are prepared, not
activated. Existing audit artifacts are preserved; new review/evidence and these
records remain local/uncommitted/unpushed. No apps/tests/builds/services/providers,
hardware, installations, history rewrites or AGY implementation session ran.

**Next concrete step:** owner reviews/approves Phase 3 scope; Codex then prepares
the isolated candidate and delegates to AGY. No blocker to reviewing the plan.
Asset redistribution disposition remains an explicit later release gate.

## Public-release Phase 1 audit (2026-10-29) — stopped before remediation

User selected a five-phase publication workflow and authorized **Phase 1 only**,
with Antigravity doing the investigation. Verified CLI model:
`gemini-3.8-flash-high`. Two separate unattended sessions examined an isolated,
read-only tracked-source/history copy; original source/history were protected.
The private Phase 1 audit report
records source ref `4331756`, 172 tracked paths, 137 reachable commits and 626
blobs. Inventory/pattern coverage is broader than manual review; no exhaustive
security or public-release clearance is claimed.

**Results:** no usable credential leak found by bounded regex scans or exact
matching of three locally configured credential values. Two npm lockfile audits
reported zero advisories. Python and firmware advisory coverage remains incomplete;
no app code/tests/builds, live provider/cloud checks or hardware work ran.
Historical author/contact metadata and personal/institutional context require an
intended-disclosure decision. Exact redistribution provenance of `samples/jfk.wav`
remains unresolved. Tracked provider IDs are configuration/privacy choices; no
ID-only access bypass was established. Draft current-CSRF and generic memory
injection claims were not substantiated. Legacy auth/transport/certificate,
resource/error and unsafe DOM observations remain separate static runtime findings;
targeted unsupported/historical warnings are proposed for later approval.

No fixes, warning insertions, config changes, credential rotation, deletion,
history rewrite, commit, push or visibility change. Existing STATUS edits and
2026-10-28 report/evidence were preserved. New report/evidence and this handoff
are local and uncommitted. Raw CLI logs and superseded drafts stay outside the
repository in private scratch.

**Next concrete step:** Phase 2 critical review of privacy/provenance decisions,
coverage gaps and the initial findings, then agree minimal remediation scope.
Stop after Phase 1; no remediation authorized. No blocker to reviewing the report.

## Independent security review (2026-10-28)

Fresh source/history review requested by the user; no prior audit report or audit
skill used. Findings and coverage in the private review include
legacy enterprise-Wi-Fi certificate validation, plaintext device transport,
legacy Flask access/resource/error risks, and public-release privacy review.
Gitleaks found no reachable-history leaks; configured-key matching across all
626 reachable blobs found none. Two intended ignored local credential files
were detected; both are mode 0600. Two npm and two pinned Python advisory scans
returned zero known advisories. 49 mocked selected-path checks passed; an
isolated legacy route test confirmed cross-origin upload processing and raw
exception disclosure. No provider/hardware operations or remediation.
Audit report/evidence and this handoff remain local, uncommitted and unpushed.

Next concrete step: review the public-release privacy findings, then select
remediation scope. No blocker to reviewing the report; live/cloud/hardware
security remains unverified as detailed there.

## Browser-backend milestone publication (D42)

**The user approved the final README/diagram and explicitly requested commit
and push to the existing private repository. The milestone is published.**
Earlier no-commit/no-push holds were superseded for this publication.
Milestone commit: [97e75e6 — Complete browser backend milestone](https://github.com/anandenk/whisper-stack/commit/97e75e66426a2ab359fa14701bf0adc20a4ea4c6).
This packages the completed browser-backend
scope: selected Agents path, memory viewer, active-only recall, evaluated model
budgets, offline setup/errors, synthetic evaluation runners/fixtures/reports,
deferred brainstorming and the README with its single SVG architecture diagram.
Device integration, independent hosting and new features remain unfinished.

**Verification:** build, 49 mocked Python checks, 10 mocked Chrome
browser checks, offline startup and whitespace pass. Exact configured-key and
selected secret-pattern scanning of publishable files found no matches.
Credentials, environments, dependencies, generated bundles and raw evaluation/
preview output remain ignored. No live evaluation or provider request, memory
operation, settings change or purchase was performed for publication.
Remote metadata confirms anandenk/whisper-stack is PRIVATE with main as default;
fetch confirms local/remote main matched at 822f546 before this publication.
Push succeeded to origin/main; `git ls-remote origin refs/heads/main` then matched
the full 97e75e6 milestone SHA. The working tree was clean after that commit/push.
GitHub's contents API separately confirmed the checked-in SVG on main. This
handoff update records that verified result; use git log/status for its revision.

**Runtime checked now:** no listeners on 8766 or 8767; the former tester PID 37914
is absent. No process was stopped or restarted in this publication. Prior running
tester claims below are historical. The documented startup command remains the
way to run it later; ignored local credentials/assets are required.

**Next concrete step, when selected:** review the board's connection to the existing backend and
confirm board/cable access before one physical question/answer audio slice.
No further backend feature work starts automatically. Sections below retain
historical evidence; their old uncommitted/running-service claims are superseded
by this publication handoff.

## README and browser-backend milestone closure (D41)

**The user considers the browser-backend milestone finished and requested a
polished README before publishing the completed changes together.** This closes
the agreed personal-prototype scope, not device integration or deployment.
The current documentation pass does not commit/push or start the next milestone.

README now leads with the project and completed browser milestone, describes
working capabilities, and includes a single SVG architecture diagram. It shows
direct browser/ElevenLabs voice, Haiku inside the hosted agent, the local bridge's
session-token and memory roles, and hosted Mem0. Setup links/commands, scoped
verification, the existing milestone sequence and repository guide follow;
earlier implementations are preserved in a collapsed history section.

The user's latest diagram direction replaces the three-flow Mermaid version
with one complete diagram, showing every component once. The checked-in vector
asset is docs/assets/backend-architecture.svg, linked from README.md. Explicit
coordinates keep labels unwrapped and routes separate: direct browser/agent
voice, browser/bridge setup and tools, two parallel token request/response paths,
and bridge/Mem0 memory. Latest preview copies are preview-v3.html and diagram-v3.png
in the evidence folder below; earlier preview versions remain local evidence.

**Verified now:** SVG renders successfully; a local GitHub-style Markdown
preview was visually reviewed. Desktop and 420 px mobile rendering have no page
overflow or browser errors. SVG XML, text bounds, local file/heading links and
whitespace checks pass. This is local rendering, not a hosted
GitHub readback. Ignored local evidence in
experiments/agents-mem0/output/readme-2026-10-28/ includes standalone preview.html,
diagram.png, desktop/mobile screenshots, render-summary.json and before/ copies.
Preview tools are isolated under that ignored folder; no project dependencies
or lockfiles changed. The standalone SVG contains no scripts or external assets.

Only README.md, AGENTS.md, this handoff, PROJECT.md and the SVG asset changed
across this documentation pass;
all earlier source edits, evaluation runners/reports and notes are preserved.
The guide retains D40's 49 Python/10 browser checks as prior evidence, separate
from user-tested voice and bounded real hosted-text checks. No backend suite or
completed live evaluation rerun; no provider session, account/settings change,
memory access, service restart or hardware action. main remains at 822f546;
all subsequent work remains local, uncommitted and unpushed.

**Next concrete step:** review the README, then publish the completed changes
together under the user's publishing direction. Do not infer publication from
README completion. After publication, scope the device-connection review and
confirm board/cable access before physical audio work.

## Personal-prototype essentials checked (D40)

**The user resumed D39 and narrowed readiness to a private, single-owner
prototype.** The five essentials below are checked at that scope; this is not
consumer/deployment readiness or a guarantee against every secret/data risk.
No new account system, deletion UI, diagnostics dashboard or prompt change.

1. **Easy startup:** `web_server.py --check` now validates local settings,
   the existing agent reference and browser assets without opening a server,
   microphone or provider connection. Missing/invalid setup produces short
   instructions without values/raw tracebacks. `--port` is supported; default
   remains 8766, existing tester remains on 8767. Documentation corrected the
   stale 1,024-token setting and distinguishes default from running port.
2. **Private access:** existing loopback binding, Host restriction, same-origin
   POST/page-token checks and server-side keys are retained. Mocked contracts
   and read-only local HTTP checks passed. Live agent GET confirms required
   authentication. These are local-page protections, not authentication against
   other programs/users on this computer; remote/device access remains unselected.
3. **Keys/memory isolation:** no configured API keys or selected key/private-key
   patterns found in current publishable files or reachable Git patch history;
   no configured keys found in served page/config/assets. This bounded scan is
   not a universal secret detector. Existing `.env` files are ignored/mode 600;
   `.env.*` variants are now ignored, with `.env.example` remaining publishable.
   Personal owner is distinct from synthetic owners. Fixed-owner calls and
   evaluation isolation pass mocked checks. Spoken secrets can still be sent
   to providers/saved by the existing agent; automatic secret filtering was
   neither added nor claimed.
4. **Know what is saved:** read-only ElevenLabs GET confirms `record_voice=true`,
   `retention_days=-1`, `delete_audio=false`, `zero_retention_mode=false`.
   Audio saving/unlimited conversation retention are the existing agent settings,
   not today's public defaults. Mem0 stores submitted extracted facts separately;
   this app sets no expiry. Local evaluation evidence persists in ignored output/.
   Current official retention, audio-saving and individual conversation/memory
   deletion operations are documented in the guide below. No data deleted,
   retention changed or deletion operation exercised; Mem0 account retention/
   backup erasure remains unverified. No automatic-expiry guarantee.
5. **Basic troubleshooting/usage:** existing sanitized session/memory failures,
   pending-save wording and manual retries verified; startup guide now maps
   failures to checks and links provider usage pages. Live subscription GET:
   4,283/131,000 credits used, 126,717 remaining, extension disabled. Mem0 billing
   numbers were not refreshed. No account polling/new dashboard.

Guide: [personal essentials and provider controls](../experiments/agents-mem0/README.md#personal-prototype-essentials).

**Verified now:** 49 mocked Python checks and 10 Chrome browser checks pass;
offline local setup check passes. Five existing local routes return HTTP 200
with no-store; invalid Host and unauthorized session POST return 403. Only two
authenticated provider GETs (existing agent and subscription); zero conversations,
generation, Mem0 requests, memory writes/deletion, settings changes or purchases.
Provider readback reconfirms Haiku low effort/4,096 tokens/10-second model wait.
No completed live evaluation, earlier backend or hardware suite rerun.

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/web_server.py --check
experiments/agents-mem0/.venv/bin/python -m unittest discover -s experiments/agents-mem0 -p 'test_*.py'
npm --prefix experiments/agents-mem0 test
```

**Saved local state:** main still at 822f546 tracking origin/main, with all prior
edits/untracked evaluations/notes preserved; nothing committed or pushed.
Tester http://127.0.0.1:8767, PID 37914, unchanged and still listening. New startup
behavior applies on the next invocation; no restart required for this audit.
Ignored local evidence: experiments/agents-mem0/output/readiness-2026-10-28/
contains local-summary.json, provider-summary.json and before/ source/doc copies.
Remote model changes remain only the previously selected D36/D37 settings.

**Next concrete step:** confirm data-cable/board access before selecting one
physical question/answer audio slice. The missing cable is the recorded hardware
blocker, not rechecked here. Hardware work is not started/authorized by this audit.
Remote access, hosting, retention changes, secret-filtering policy, reply polish,
search parity, large-store/compaction work and brainstorming remain deferred/open.

## Completed evaluation — bounded context passed; initial round complete (D38)

**All three opening details were recalled correctly after 20 intervening
exchanges, with no Mem0 access or target refresh.** One completed text-only
session used the unchanged frozen fixture, accepted agent/prompt, Haiku low
effort, selected 4,096-token/10-second settings and disabled fallback. Exact
provider messages match the captured 22 user questions and 22 answers plus
greeting. Final answer: Cinder Finch, ultramarine, 7462. No judge-model calls.

Actual session 116.607 s; provider duration 117 s. Transcript: 45 messages,
19,812 characters/UTF-8 bytes and 3,324 whitespace-separated words, including
greeting. Before/after agent hashes match each other and verified D37 settings.
Zero memory tools/requests, ASR/TTS, microphone/audio, settings change, client
retry or service restart. Twelve successful reads/auth requests including the
final allowance check; one WebSocket session. No new feature or larger test.

- **Included usage:** 416 credits (352 platform + 64 LLM), matching settled
  subscription 3,867→4,283/131,000; 126,717 remain. No purchase/upgrade/extension
  or extra cash spend. Nominal USD/token usage stays distinct from credits;
  initiated/irreversible token ledgers are not added together.
- **Scope:** initial small memory/conversation-context round complete. D32's
  eight-case answer baseline remains prior evidence. This is a bounded recall
  pass, not capacity/compaction, general reasoning, voice or hardware proof.
- **Observed rough edges:** library filler split groups despite the constraint;
  recipe filler gave an incorrect flour-batching total. Eleven of 20 filler
  replies exceeded the requested 80-word ceiling (range 66–98). Logged as
  separate reasoning/instruction-following findings; no tuning or new test.
- **Verification:** D37's 44 passing mocked Python checks remain prior evidence;
  D38 changed records only, so they were not repeated. Current whitespace and
  local links pass. No browser/earlier backend/audio/hardware suite. Tester on
  port 8767 remains running on PID 37914.
- **Evidence:** [final context results and historical attempts](../experiments/agents-mem0/evaluation/context-2026-10-27.md),
  [budget diagnosis/fixes](../experiments/agents-mem0/evaluation/timeout-diagnosis-2026-10-27.md).
  Ignored output/context-evaluation/ under experiments/agents-mem0 retains
  context-70664525.json (original) and context-reviewed-70664525.json (review,
  counts, secondary observations, settled usage). D33–D37 evidence preserved.

**Subsequent action:** D39 saved this evaluation; D40 completed the narrowed
personal-prototype readiness checks above without another live evaluation.
Brainstorming remains parked; retention/secret handling, reply-policy choices,
large stores, compaction, hardware and deployment remain open. All local changes
are preserved, uncommitted and unpushed. Current remote/local model settings:
4,096 tokens, 10-second wait, low effort, concise prompt, disabled fallback.

## Prior answer baseline — complete (D32)

**Eight of eight scoped answer cases passed assistant review of real hosted
output:** three corrected facts, four unknowns and one unchanged fact. The
existing native Haiku agent chose its own recall queries and used the shared
D31 active-only tool against the synthetic owner. Each question had a fresh
text-only conversation/test user, one user message and exactly one recall.
No answer keys, facts or special system prompt were injected.

Provider transcripts match captured answers; all eight conversations report
done/text-only and native claude-haiku-5-5 usage, with zero ASR/TTS audio.
Agent configuration hashes before/after match. No memory write was attempted,
no remote/app setting changed, and the current browser service was not restarted.
Unknown answers explicitly admitted missing evidence even with distractors.

- **Included usage:** 132 ElevenLabs credits (128 platform + four LLM), matching
  subscription usage 3,556→3,688 of 131,000; 127,312 remain. Credit-limit extension
  stayed disabled. Mem0 reads 37→45 of 1,000; adds stayed 37/10,000. No additional
  cash payment/purchase/upgrade. Provider nominal USD price fields are retained
  separately from included credits, tokens and actual payment.
- **Observed rough edges:** employer/access-code replies exceeded the preferred
  30 words (40/41); one offered to save an access code. No code was supplied or
  stored. Brevity/secret-handling policy remains open; no tuning was performed.
- **Verified now:** all 36 mocked Python checks pass, including seven new
  runner-isolation checks; offline default, whitespace and local links pass.
  D31's 10 browser checks remain historical; no new browser/hardware suite.

Full [answer findings, commands and limits](../experiments/agents-mem0/evaluation/answers-2026-10-27.md).
Ignored evidence folder: experiments/agents-mem0/output/answer-evaluation/;
answers-c381b8b5.json preserves original replies/tool traces/provider usage,
answers-reviewed.json adds semantic review, and allowance-after.json plus
mem0-allowance.json record the account checks. Eight sessions/35 runner HTTP
requests (27 ElevenLabs reads/auth and eight Mem0 searches); no retries.

**Subsequent action:** D33/D34 stopped before recall, D35–D37 corrected the
model budgets, and D38 passed the bounded context scenario. One trial is not
general reliability, multi-hour, scaling or compaction proof. Brainstorming
and reply-policy refinements remain deferred; nothing is committed or pushed.

## Current implementation — active-only recall (D31)

The user selected step 1 after the memory-evaluation recap. **Normal recall now
passes latest_only=True** to Mem0 in run.py's shared retrieveMemories handler.
Owner scope, top_k=5 and the response format are unchanged. The viewer's get_all
call retains default history, refresh and paging; no memory is deleted or
rewritten. Historical-question recall remains a separate policy choice.

**Verified in D31:** all **29 Python checks and 10 browser checks pass**, with mocked
providers/synthetic input. The existing contract check confirms caller arguments
cannot disable active-only recall or change the owner; the viewer check includes
a superseded record and still requires the unchanged history-list call. Local
page/assets/config on port 8767 return HTTP 200, and whitespace/local links pass.
No live memory request, provider session, microphone access or generation was
initiated in D31; no remote agent setting, dependency or audio behavior changed.
The earlier custom backend/hardware suites were not rerun.

**Running tester:** http://127.0.0.1:8767. The prior 8766/8767 listeners were no
longer present when checked. Started one updated loopback server on 8767,
PID 37914, using the command in Current state below. It is left running.
Reload any previously opened page before using it. Ignored local verification
summary: experiments/agents-mem0/output/active-only-recall-verification.json.

**Prior live evidence (D30):** all three old facts were confirmed superseded and
linked to active replacements. Default reads still included history after about
an hour; latest_only=true excluded all three old IDs and ranked each current
fact first. Full-history/active-only counts were 33/30. This was native behavior
on the Free account, not a failed superseding mechanism or an upgrade requirement.
Unknown-answer controls still returned distractors; active-only retrieval does
not establish answerability. See [native findings/evidence](../experiments/agents-mem0/evaluation/native-corrections-2026-10-27.md).

The actual-assistant answer test is complete in D32 and bounded context passed
in D38 above. Multi-hour context/scaling remain untested. Preserve every local edit; nothing is committed
or pushed.

## First retrieval baseline — 2026-10-27 (D29, historical)

**First isolated retrieval baseline completed and assistant-reviewed.** The user
selected existing included Mem0 allowance only, with no additional spend.
The standalone runner defaults to a plan with no key reads or network requests.
It submitted 30 fabricated facts, three repeats and three corrections under a
fresh owner, then ran 16 searches using the app's top_k=5/provider defaults.

- **Findings:** all 30 seed facts represented on inspection; duplicate phase
  kept the same 30 ID/text records. Corrections added three records (33 total),
  preserving the outdated assertions. Correct evidence appeared in top five
  for all 12 known-answer queries; unchanged facts ranked first in 9/9, but
  **all three corrections ranked the old assertion first and new fact second**.
  All four unknown-answer questions returned distractors. This is retrieval
  evidence, not proof of the agent answering correctly or hallucinating.
- **Timing/size:** median search 304 ms, maximum 413 ms; largest current tool
  result 339 UTF-8 bytes. Tiny synthetic sample; no scale/context-size bound.
- **Processing delay:** the original run stopped at write 23 after six status
  checks. A later explicit read confirmed completion. A tested explicit
  continuation submitted only untouched cases, preserving the owner and
  cumulative caps; no write replay. Revised bounded wait: 20 status checks.
- **Allowance:** signed-in Billing before/after showed Free, model calls on
  Mem0, adds 1→37 of 10,000 and retrievals 5→25 of 1,000. Usage stayed included.
  Ledger: 36 adds, 70 event reads, four lists, 16 searches (126 total, cap 280).
  Actual token/USD usage was not returned; it remains unknown.
- **Verified now:** 24 mocked Python checks pass (13 evaluation + 11 existing).
  Plan-only execution, whitespace and local links pass. No browser suite,
  earlier custom-backend suite or hardware check was rerun for this CLI addition.

Full findings, commands and durable evidence filenames:
[baseline results](../experiments/agents-mem0/evaluation/baseline-2026-10-27.md)
and [procedure](../experiments/agents-mem0/evaluation/README.md). Ignored local
evidence is in experiments/agents-mem0/output/whisper-stack-eval-20261027T191436Z-0c01e00a62fa/;
reviewed.json retains the review, original/continued reports preserve the run.
The evaluation owner and 33 records remain; no deletion was performed.

**Original next action (completed by D30 above):** investigate native correction/temporal handling against the
three captured conflicts and define a small follow-up to distinguish delayed
enrichment from persistent ambiguity. No tuning, follow-up provider calls,
larger dataset or additional context agent was started. Long-session recall
and answer abstention remain untested; they need separate evaluation scope.
Brainstorming stays [parked](../notes/brainstorm-2026-10-27.md). App/agent settings,
audio, credentials and existing services are unchanged. All local changes are
uncommitted and unpushed; the user's no-commit/no-push instruction still applies.

## Historical paused handoff — 2026-10-27 (superseded only by D29)

**Paused at the user's request. Preserve every current edit; do not commit or
push.** The user wants to commit/push the completed changes together later.

- **Saved state:** main at 822f546, with 12 modified tracked files. Architecture
  docs and the memory viewer are all saved locally, uncommitted and unpushed.
  No changes were discarded, reverted or stashed. Changed files are AGENTS.md,
  README.md, docs/PROJECT.md, this handoff, and experiments/agents-mem0/{README.md,
  web_server.py, test_web_server.py, web/client.js, web/client.test.mjs,
  web/index.html, web/style.css, web/mock_server.py}.
- **Decisions/completed work:** D27 selects Agents + Haiku 5.5 + hosted Mem0 as
  the main backend; D28's view-only saved-memory list is implemented and
  user-accepted. It uses the existing owner, explicit loading/refresh, 25-item
  pages and distinct empty/error states. Preserve official SDK audio processing.
- **Tests already run:** build passed; 11 Python and 10 Chrome browser checks
  passed with mocked providers. One live read returned/rendered 1 memory with
  no session start, microphone access or memory write. Desktop/mobile screenshots
  were reviewed. Evidence: experiments/agents-mem0/output/memory-view-verification.json
  and memory-view-{desktop,mobile}.png (ignored/local). No runtime tests were
  rerun for this pause; final whitespace/link checks pass.
- **Processes:** lsof at pause confirms listeners on 8766 (PID 83076, older
  server) and 8767 (PID 94123, updated viewer). Left both running. Use
  http://127.0.0.1:8767 for the feature; recheck processes after resuming.
- **Remaining work:** deletion, revised save feedback, session stats, retention
  settings and web search are not built. Memory-growth testing is only proposed;
  no test harness, large dataset or extra context agent was created. Prefer
  Mem0's native retrieval/cleanup before custom management. Dream capabilities
  were checked in docs, not this account; automatic ElevenLabs live-context
  compression remains unconfirmed. Hardware/deployment remain unfinished.
- **Next step on resume:** review the small isolated-memory test plan in
  PROJECT.md and agree live-test scope/budget before any provider writes. Begin
  with 30–50 fake facts and fixed recall/duplicate/change/missing-fact questions;
  expand only if useful. Test long conversation recall separately from Mem0.
  Do not resume implementation or commit/push until the user asks.

## Current state

The user selected **ElevenLabs Agents + native Haiku 5.5 + hosted Mem0 as the
main backend** on October 27 (PROJECT.md D27). The accepted browser client uses
ElevenLabs' official WebRTC SDK. This resolves the architecture choice; the code
still lives in experiments/agents-mem0, and the bridge remains loopback-only.
The earlier custom pipeline in backend/ is preserved independently. Its general
web search, token/cost ledger, Flash v2.5 setting and measured latency do not
automatically apply to the selected Agents path (currently Flash v2).

D28 is complete: **Saved memories** on the existing tester loads an owner-scoped
list only when View is clicked. Refresh starts at page 1; Previous/Next browse
25 memories per page. Loading, empty and failed states are separate. The list
returns text/counts only, displays plain text safely and keeps provider metadata,
URLs and credentials server-side. It adds no memory writes/deletes or audio changes.

**Current runnable version: http://127.0.0.1:8767.** D31 found the old 8766/8767
processes absent and started a fresh updated server on 8767 (PID 37914). It uses
the existing configured owner and provider resources. Check processes before reuse.

```sh
experiments/agents-mem0/.venv/bin/python -m uvicorn web_server:create_app --factory --app-dir experiments/agents-mem0 --host 127.0.0.1 --port 8767 --no-access-log
```

Verified in D28: the build, **11 Python tests** and **10 Chrome browser tests** passed,
using mocked providers/synthetic input. New checks cover fixed-owner listing,
paging, access checks, empty/error states, plain-text rendering and no automatic
memory/audio requests. Existing conversation/tool/worklet checks still pass.
A separate live browser check returned HTTP 200 with **1 saved memory**, rendered
successfully after one list request, with zero session-token requests, microphone
requests or browser errors. This was a read-only Mem0 check, with no conversation,
speech generation or memory write. Counts-only live evidence is at ignored
experiments/agents-mem0/output/memory-view-verification.json. Mocked desktop/mobile
screenshots at output/memory-view-desktop.png and memory-view-mobile.png were
visually reviewed; the 375 px browser check found no horizontal overflow.

The earlier architecture documentation changes were already local at session
start. All those changes plus D28 remain uncommitted and unpushed on main at
822f546. During D28 no provider settings, dependencies, earlier backend/ code or
firmware changed. D36/D37 later changed only the selected model wait/cap as
recorded above. The earlier custom-backend suite was not rerun.

**Discussion before D29:** the user accepted the viewer and raised memory/context
growth as a major concern, asking whether a separate context manager would help.
Read-only inspection confirms recall uses Mem0 search with top_k=5 and returns
only memory strings, discarding scores/dates; there is no application-level
returned-text budget. Five per lookup is not a bound on accumulated tool results
or live conversation history. Current Mem0 docs describe ranked hybrid search,
relevance thresholds and optional reranking. Proposed next investigation:
evaluate recall/latency at increasing isolated store sizes, add bounded retrieval
rules where needed, and verify ElevenLabs' long-session/context controls before
adding another model. No context agent, dataset creation, live call or provider
change was authorized or performed in that discussion. D29 subsequently
authorizes the bounded baseline described at the top of this handoff.

Earlier documentation described Dream Supersede/Merge on all plans. D29's
generic-pricing recheck left entitlement unresolved because Dream was listed
under Custom. D30's actual Free-account reads now confirm superseded links
and working latest_only filtering. Pricing labels did not establish that the
native lifecycle features were absent. Synthesis/dashboard entitlements remain
unverified. Prefer these demonstrated native controls before custom management.
ElevenLabs' previously checked guidance asks applications to implement history
summaries/windows; automatic live-context compaction remains unconfirmed.

## Selected Agents + Mem0 path (D23–D25 basic validation complete; D27 adopted)

**Basic setup and collaborative validation are complete.** The user accepted
speech recognition, natural interruptions, quiet waiting after answers, and
hosted Mem0 preference recall in a new session. These are user-reported live
results, not independent acoustic, latency or memory-reliability benchmarks.
The completed rebuild was merged into main through PR #1 (D26), verified on
GitHub and in the local checkout. The subsequent README rewrite was published
on main. The previous 459-line README is preserved in
[the archive](../notes/archive/README-before-overview-2026-10-26.md), with relative
links rebased. The saved project one-liner is retained verbatim.

### Current implementation

- Separate agent **Whisper Stack - D23 Haiku 5.5 + Mem0 test**
  (configured in `agent-reference.json`): native `claude-haiku-5-5`, low effort,
  4096-token thinking/answer cap, 10-second model wait, disabled alternate LLM
  fallback, required authentication, and
  response-returning `addMemories`/`retrieveMemories` client tools.
- Currently running local tester: **http://127.0.0.1:8767**, served by
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
  sanitizes failures, and exposes no CORS. Memory initializes on the first explicit memory
  action (tool call or list request). No application-level retries; vendor transport recovery retains defaults.
- Native `conversation_config.turn.turn_timeout=-1` disables seven-second idle
  check-ins (D25). The session stays available for user-initiated follow-ups;
  the unchanged maximum duration is 600 seconds. PATCH/GET readback verified only
  the intended setting changed. Local agent.json reflects current configuration.
- The English-agent API rejected Flash v2.5; this test uses Flash v2 and George.
  WebRTC negotiates its own audio transport. The existing backend remains on its
  approved Flash v2.5 default. D24 made no custom-backend, firmware or deployment changes.
- Mem0 V3 pending saves are reported as submitted, not confirmed available.
  The user separately confirmed recall after starting a new session. Browser
  transcripts remain in page memory; numeric backend diagnostics do not cover
  this separate hosted Agents path.
- ElevenLabs defaults record voice and retain conversations indefinitely
  (retention_days=-1); built-in user memory is disabled. Mem0 persists submitted
  facts. Long-term retention policy remains undecided. Credentials, provider
  readbacks, recordings, generated bundles and dependencies are local/ignored.

### Verified and retained context

**Historical pre-D28 publication checks:** 7 experiment Python checks and 8 Chrome browser
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
machines. D27 subsequently selected this path as the main backend; the proposed
remaining milestones are recorded in PROJECT.md. D28 adds the memory viewer;
other proposed features remain unstarted.

The Anthropic key was generated on
October 25 with a user-reported 30-day expiry, expected around **November 24, 2026**;
Console's exact timestamp takes precedence. No scheduled reminder was requested.

Read [PROJECT.md](PROJECT.md) for accepted decisions, [AGENTS.md](../AGENTS.md) for
working boundaries, and [backend/README.md](../backend/README.md) for setup/API commands.

## Earlier custom backend (preserved)

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

## Blockers and next action (see current handoff above)

D28's view-only memory list is complete, live-checked and user-accepted. No blocker
remains for this feature. D29/D30 evaluations led to D31's implemented native
active-only recall. D32 passed the eight hosted answer cases, D36/D37 corrected
the model limits, and D38 passed bounded context recall. The initial evaluation
round is complete; see the top for the saved backend-readiness starting point.
The viewer remains at http://127.0.0.1:8767; its new listener was confirmed in D31.
New saves may take time to appear; Refresh is
manual and no polling/retries run automatically. This list shows currently
available memories using Mem0's default listing behavior, not pending save jobs.

The user authorized the viewer, D29/D30 evaluations and D31 recall change. Deletion,
changed save feedback, session statistics, retention settings and web search
remain unimplemented/unselected for the next build. SDK audio processing and
existing session behavior are preserved. Do not automatically start feature 2.

The device transport and memory-tool routing remain open. The last reported
missing data cable blocks flashing, not local backend work. Exact board variants,
wiring and parts are unverified. Deployment and workstation-independent operation
remain unfinished. The milestone sequence is in PROJECT.md.

The viewer's earlier build, 11 Python checks, 10 browser checks and one live
memory-list read remain historical evidence; D37's 44 mocked checks and D38's live context result are the latest verification above.
The source, decision records and handoff are local only,
uncommitted and unpushed. Generated bundles and output evidence remain ignored.

## Git and historical context

[PR #1](https://github.com/anandenk/whisper-stack/pull/1) is **merged** into
**main** in the existing private https://github.com/anandenk/whisper-stack
repository. Merge commit: **e1e064ec92f4fef668183d8283d9d2067edb21de**,
2026-10-26 (America/New_York). GitHub reported MERGED, and this checkout was
switched to main and fast-forwarded to the remote merge commit. Continue on main.
The rebuild branch and all incremental history are preserved; no squash or
history rewrite. After that merge, the user started the separate README rewrite.
The user authorized publication of the README/archive/handoff update, committed
and pushed to main as e487536. The documentation increment is complete.

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
