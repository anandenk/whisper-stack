# Whisper Stack: current status and session handoff

Updated: 2026-10-19 (America/New_York).

## Read this first

**Current request:** Build the workspace first. Continuity between sessions is
the user's highest priority. The user explicitly stopped the server scaffold.

**Current phase:** Workspace foundation. No rebuild application code exists.
Backend and hardware implementation have not been resumed.

Read [PROJECT.md](PROJECT.md) for decisions and [AGENTS.md](../AGENTS.md) for the
session workflow. Use these records and the checkout rather than ask the user
to repeat the revival discussion.

## Completed

- Cloned and audited the original repository. Legacy baseline: `c7d050d`
  (last implementation commit, May 2026).
- Established the direction: fresh implementation, cloud-connected USB desk
  device, workstation hosting during development, firmware/backend developed
  together. See PROJECT.md D01-D06 for reasons and boundaries.
- Created the shared startup instructions, project record, and this handoff.
- Preserved old CLAUDE.md unchanged in `notes/archive/claude-context-2026-05.md`.
  Root CLAUDE.md points to the shared instructions. README.md identifies the
  current workspace entry points above its historical implementation description.
- Verified the archived context matches the original byte for byte, all local
  links in the startup records resolve, and `git diff --check` passes. Existing
  application and firmware files are unchanged. Runtime tests were not repeated
  for this documentation-only change.

The continuity setup is saved as a local documentation commit titled
`docs: establish revival workspace and session handoff`. It has not been pushed.
Use Git to confirm that state before resuming or switching machines.

## Audit evidence and limits

Reviewed legacy source, firmware configuration, tests, Git history, and notes.
GitHub had no issues, pull requests, or workflow runs when inspected.

- **Verified in this session:** all 36 existing Python files parsed. Isolated
  Flask-client checks passed for authentication, basic validation, response
  shape, and SSE audio/final events using fake STT/LLM/TTS backends.
- **Reproduced in isolated temporary tests:** session ID `desk` can select the
  file for `desk-aux`; concurrent session writes can lose a turn; invalid session
  IDs on both voice endpoints leave uploaded temporary files behind.
- **Observed in source:** unpinned Python dependencies; ignored firmware component
  lock; no upload size cap; Wi-Fi retries stop after eight failures; legacy
  loopback analysis does not evaluate the newer `afe_drops` counter.
- **Historical only:** May hardware successes and performance figures. These
  have not been reverified on current hardware.
- **Not performed:** real STT/LLM/TTS inference, GPU validation, firmware builds,
  flashing, physical playback, paid API calls, or cloud deployment.
- Audit probes used `/tmp/whisper-stack-audit-20261019`; they are not a committed
  regression suite and may disappear. Source locations below are durable leads.

| Legacy finding | Source to inspect if needed |
|---|---|
| Session matching and concurrent writes | `server/sessions.py`: `SessionStore.active_or_new`, `Session.append_turn` |
| Temporary-file cleanup and upload handling | `server/app.py`: `_capture_audio_upload`, `converse`, `converse_stream` |
| Reconnect behavior | `firmware/main/wifi.c`: `on_wifi_event` |
| Audio loss diagnostics | `firmware/main/vad.c`, `tools/loopback_analyze.py` |

These findings inform the rebuild. They are not a queue to repair the legacy app.

## Proposed later work, not started

A previously suggested first device slice is a connection check followed by a
known sound downloaded from a local server and played by the device. Microphone
upload and AI integration would follow. This remains a proposal. The latest user
instruction is to establish the workspace first.

## Next action

Review the continuity setup with the user and incorporate workspace preferences.
Keep working within workspace scope. Do not start the proposed server or hardware
slice unless the user resumes implementation.

The prior question about which computer will connect to the ESP32 is unanswered
and no longer needed for the workspace task. Do not repeat it now.

## Resume and maintain

For a new chat opened on this repository, a sufficient starter is:

> Read AGENTS.md and docs/STATUS.md, then continue within the current scope.

At the next substantive handoff, replace stale status, record what actually ran,
and leave one concrete next action. Check `git status --short --branch` and
`git log -3 --oneline` for the actual commit and uncommitted changes. Another
checkout will not have local-only work; sync the relevant commit before using
these records on a different machine.
