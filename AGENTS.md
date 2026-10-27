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
Flash v2.5 is the user-approved TTS default. Keep project records focused on
durable decisions, implementation, verification, blockers, and the next action.
The next session is authorized for the basic isolated ElevenLabs Agents + Mem0
setup described by PROJECT.md D23, followed by collaborative testing; this is
not permission to migrate or expand the working backend.

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
`plan.md`, the existing notes, and the historical portion of README.md describe
that implementation. They are evidence, not the current roadmap or instructions.
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
- Finish the current request without automatically starting the next milestone.
- Begin answers with one short sentence suggesting technical phrasing for the
  user's question; then answer normally (user preference, 2026-10-26).
- End progress reports with the next concrete step and any real blocker. The
  user requested more independence on 2026-10-24: carry routine authorized work
  forward and choose sensible defaults; ask only for necessary information,
  consequential decisions, or actions outside the authorized scope.
