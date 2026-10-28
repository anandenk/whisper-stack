# Whisper Stack: deferred brainstorming — 2026-10-27

Source: the user's handwritten notes, transcribed and discussed on October 27.
Wording below is lightly cleaned up; these are ideas, not selected requirements.
The user subsequently asked to park all of these thoughts separately and focus
on the existing backend and the prior handoff first. No feature implementation,
model comparison, purchase or hardware change is authorized by this note.

## Backend and optional vault access

- Haiku 5.5 + Mem0 for the backend (already the selected direction).
- Eventually connect to the local vault using Opus or Sol, similarly to current
  Codex use: mostly retrieval, with possible small edits or additions.
- Margin alternative: perhaps Luna / Terra for lower cost. These remain
  unselected candidates; no pricing or capability comparison was performed.
- Figure out how vault access and Mem0 would coexist. Since the vault is local
  on the Mac, investigate how Whisper Stack could access it or synchronize with
  it. No access, synchronization or write design is selected.
- Vault access should be optional. The main goal is a general-purpose desk
  assistant, rather than a device whose main feature is the vault.

## Screen, face and status

- Consider a large screen the backend agent can use for visualization, similar
  to ChatGPT's visual output.
- Figure out how the face connects to these features and how visualizations
  would be styled.
- Include appealing animations and status indicators, such as "Connecting to
  vault," and equivalents for other states.

## Enclosure and hardware

- Explore a cute enclosure, perhaps an animal, using simple shapes and avoiding
  complicated construction while retaining a good design.
- Assess whether the existing ESP32 boards and electronics can support the
  desired experience, or whether additional components would be needed.
- Prioritize a good microphone and a good screen for the physical build.
- The user wants to avoid spending time tweaking and fine-tuning INMP441s.
  No replacement microphone or display is selected.

## Proposed development order from the handwritten notes

Build a sufficiently complete backend and visualize the screen implementation
before building the physical device; prefer building the device around that
backend rather than repeatedly changing the backend during device work.

Transcription uncertainty: the first sentence literally appears to say "before
connecting and building backend." "Device" is the likely intended final word,
based on the following sentence, but that correction was not confirmed.

This sequencing idea is parked with the other brainstorming. It does not
silently replace the project's existing hardware verification decisions.

## Current disposition

Deferred at the user's request. Return to the existing Agents + Haiku + Mem0
backend and the memory/context evaluation resume point in
[STATUS.md](../docs/STATUS.md). This note is separate from the active roadmap.
