# Whisper Stack: working instructions

## Start every session here

1. Read [docs/STATUS.md](docs/STATUS.md) for the current request, actual progress,
   verification, and next action.
2. Read [docs/PROJECT.md](docs/PROJECT.md) for goals, decisions, and open questions.
3. Check `git status --short --branch` and recent commits. Preserve existing work.
   Resolve discrepancies between the handoff and checkout before relying on either.
4. Read implementation files or historical notes only as needed for the task.

The user's latest instructions take precedence. Files record context; they do not
authorize new work. A proposed next milestone is not permission to implement it.

## Current boundary

**Publication authorized (D42, 2026-10-28).** The user approved the final README
diagram and explicitly requested committing and pushing the completed backend
milestone to the existing private repository. This supersedes earlier
no-commit/no-push instructions for this publication only. Include the completed
source, mocked checks, synthetic evaluation fixtures/reports, deferred notes and
documentation; keep credentials, environments, generated bundles and raw output
ignored. No new feature, provider evaluation, service restart or hardware work.
Read STATUS.md for publication verification and the next bounded milestone.

**README and milestone closure (D41, 2026-10-28).** The user considers the browser
backend milestone finished for the agreed personal-prototype scope and wants a
polished README before publishing the completed work together. README work is
documentation only: accurately show the browser/ElevenLabs voice path, local
token/memory bridge and hosted Mem0. Device integration and hosting remain open.
Preserve all local changes; do not automatically publish as part of this README
request or start backend features, services, evaluations or hardware work.

**Personal-prototype essentials (D40, 2026-10-28).** The user resumed from D39
and authorized checking easy startup, local access, secret/memory isolation,
saved-data/deletion understanding and basic troubleshooting/usage. Keep this
private and single-owner; consumer accounts, broader diagnostics and deployment
are not required now. See STATUS.md for the completed checks and limits.
Preserve every local edit and untracked report/script. Do not commit/push,
restart existing services, rerun completed live evaluations or reopen the
selected architecture automatically. This scope does not authorize retention
changes, deletion, extra spending, prompt tuning, deployment or hardware work.

**D31's active-only recall change is complete (2026-10-27).** Preserve every
local edit. Do not commit or push; the user wants to publish completed changes
together later. D29/D30's isolated evaluations used only included Mem0 allowance,
with no additional spend, and verified native superseding/active-only reads.
The user then selected step 1: normal recall now uses latest_only=True while
the memory viewer retains its existing history listing. No memory deletion,
historical-query mode, remote agent/audio change or further live evaluation.
Read STATUS.md for verification, the running tester and next proposed action.
The user subsequently authorized D32's bounded actual-assistant answer test;
it is now complete: eight real text-only cases passed the scoped correctness/
abstention review, with unchanged agent/prompt and synthetic-owner recall.
It consumed only included credits/allowance, with no memory writes, purchases
or remote settings changes. See STATUS.md for rough edges and the next proposal.
D38's final bounded context check passed: all three opening details recalled
after 20 intervening exchanges, with zero Mem0 access or target refresh. The
initial small memory/conversation-context evaluation round is complete. D33/D34
stopped before recall; D35–D37 diagnosed/corrected model budgets. Remote/local
agent.json now use 4,096 tokens/10 seconds; the evaluation guard matches.
Keep Haiku, low effort, concise prompt, disabled fallback and all local edits.
Included credits only; no commit/push. Reasoning and brevity mistakes observed
in filler replies are recorded separately; no general reasoning/compaction or
hardware pass is implied. Do not automatically run more evaluations, tune the
prompt, implement deferred features, deploy or start hardware work. Read
STATUS.md for evidence and the proposed main-service readiness review.

The user authorized the audio backend, then selected Claude Haiku 5.5 and
per-exchange cost diagnostics on 2026-10-24. Build the text-response and buffered
STT → Haiku → TTS interaction in backend/, isolated from the legacy implementation.
Local dependencies and tests are within scope; live model checks need locally
configured credentials. Keep provider-reported token usage separate from cost
estimates and distinguish mocked, live-provider, and hardware evidence. Device
programming remains blocked by a missing cable. On 2026-10-25 the user also
authorized the local push-to-talk tester, with human testing deferred, and
publishing the rebuild/backend-tester branch to the existing private repository.
The user subsequently authorized streamed playback, shorter default answers, and
general web search on 2026-10-25, then overlapping Haiku text/TTS generation on
2026-10-26. The streaming route now feeds final-answer words from Messages SSE
into one ElevenLabs text-input WebSocket. Native search remains capped at one
use per exchange. Keep search fees separate from token estimates. Realtime
microphone upload, deployment, and firmware are not automatically in scope.
Follow STATUS.md for the current handoff.
Flash v2.5 is the user-approved TTS default for the custom pipeline; the selected
Agents path currently uses Flash v2. Keep project records focused on
durable decisions, implementation, verification, blockers, and the next action.
The basic isolated ElevenLabs Agents + Mem0 setup and collaborative evaluation
in PROJECT.md D23–D25 are complete by user acceptance, including WebRTC audio,
interruptions, quiet waiting and cross-session memory recall. The user selected
ElevenLabs’ official browser WebRTC client (D24). Use the SDK’s
audio processing; do not add playback-time microphone muting or custom echo
cancellation. The original experiment-only architecture boundary was superseded
on 2026-10-27: the user selected Agents + Haiku 5.5 + hosted Mem0 as the main
backend (PROJECT.md D27). Its code remains in experiments/agents-mem0; preserve
backend/ as the earlier custom pipeline. Do not assume its search, diagnostics,
Flash v2.5 default or PCM contract apply to Agents. This selection and milestone
discussion do not automatically start feature, deployment or hardware work.
The user then authorized only viewing saved memories (D28): an on-demand list
with refresh/paging for the existing owner. Do not infer deletion, retention,
diagnostics or web-search work from that selection. The
user authorized merging the completed rebuild into main (D26). The README now
provides short old/new project descriptions and links to setup and status. Keep
the front page concise; detailed historical instructions belong in the archive.
This documentation work does not authorize hardware or feature work.

## Keep one coherent project

- PROJECT.md owns goals, decisions, and unresolved choices. STATUS.md owns current
  work, verification, and the handoff. Keep this file about how to work.
- Update these records when material facts change, including during long tasks
  and before context compaction or ending a substantive session.
- Keep them concise and self-contained. Do not paste chat transcripts, raw logs,
  or secrets. Add a document only for a distinct purpose and link it from here or
  the appropriate record.
- Distinguish user decisions from agent recommendations. Record the reason for
  consequential implementation choices. If a decision changes, mark the old one
  superseded and explain the replacement instead of silently reversing it.
- Do not restart discovery or reopen settled choices just because a session is
  new. Revisit them when the user changes direction or evidence warrants it.
- Prefer the smallest complete increment. Do not add frameworks, abstractions,
  services, or fallback implementations for hypothetical future requirements.

## Historical implementation

The existing `server/`, `firmware/`, `tests/`, and `tools/` contain the May 2026
prototype. Preserve them as reference unless the user requests changes. Reuse
individual parts deliberately after inspecting and verifying them; do not assume
the rebuild inherits the old architecture, dependencies, or configuration.

The old context is preserved in
[notes/archive/claude-context-2026-05.md](notes/archive/claude-context-2026-05.md).
`plan.md`, the existing notes, and the
[archived README](notes/archive/README-before-overview-2026-10-26.md) describe that
implementation. They are evidence, not the current roadmap or instructions.
Firmware v2/v3 names in those files are historical, not names for the new rebuild.

## Evidence and handoff

- Label results as verified now, historical, mocked/simulated, or unverified.
  Backend tests, firmware builds, flashing, and actual device behavior are
  separate kinds of evidence. Never substitute one for another.
- When implementation resumes, build firmware and backend in complete interaction
  slices. Verify boundaries on hardware before calling a hardware milestone done.
- Before ending a substantive session, update STATUS.md with what changed,
  checks and outcomes, unfinished work, blockers, and one concrete next action.
  Record decisions in PROJECT.md if needed. Replace stale status rather than
  accumulating an ever-growing session diary.
- Include reproducible commands and durable evidence paths when available.
  Temporary files under `/tmp` are not durable handoff evidence.
- Check the diff for accidental edits and stale documentation. State whether
  changes are committed and pushed; never imply another machine has local work.
- Use plain commit messages without Codex co-author or generated-by attribution
  (user request, 2026-10-26).
- Finish the current request without automatically starting the next milestone.
- Begin answers with one short sentence suggesting technical phrasing for the
  user's question; then answer normally (user preference, 2026-10-26).
- End progress reports with the next concrete step and any real blocker. The
  user requested more independence on 2026-10-24: carry routine authorized work
  forward and choose sensible defaults; ask only for necessary information,
  consequential decisions, or actions outside the authorized scope.
