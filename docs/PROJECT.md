# Whisper Stack: project record

This file owns goals, decisions, and unresolved choices. Read [STATUS.md](STATUS.md)
for current work and [AGENTS.md](../AGENTS.md) for working rules.

## Goal

Build a physical voice assistant with its own microphone, speaker, display,
controls, and enclosure. The first deliverable is a USB-powered desk device that
uses Wi-Fi and cloud processing. In its eventual deployed form it must work with
the workstation switched off. Fully offline AI processing is not a requirement.

## Decisions from the revival discussion

Established with the user on 2026-10-19. These record direction, not a selected
software stack or permission to start implementation.

| ID | Decision | Reason |
|---|---|---|
| D01 | Make continuity between sessions the immediate priority. | Earlier AI sessions accumulated competing assumptions and complexity; the user stopped implementation to establish the workspace first. |
| D02 | Pursue a fresh implementation informed by the May prototype. Preserve the old implementation as reference. | The user does not trust the accumulated code enough to use it wholesale. Hardware findings remain useful. |
| D03 | Target a cloud-connected device physically independent of the workstation. | The deliverable is a complete physical device; internet connectivity is acceptable. |
| D04 | Use USB power for the first enclosed prototype. Battery work is deferred. | The user chose a USB desk device with its own speaker and no longer has the LiPo battery. |
| D05 | The workstation may host the new backend during early development. Keep eventual remote hosting in the design. | Avoid initial hosting expense while testing the intended device/server boundary. AI service usage may still cost money. |
| D06 | Develop firmware and backend together through small end-to-end interaction slices. | Building the whole backend first caused repeated integration work in May. |

## Intended component responsibilities

This is a conceptual boundary. Transport, providers, and frameworks remain open.

- **Device:** microphone capture, wake/button interaction, speaker playback,
  display, controls, and connection/error behavior.
- **Backend:** device authentication, conversation orchestration, session state,
  and AI service integration. Initially runs on a development computer; eventually
  runs remotely. Normal device operation must not require that computer's
  microphone, speakers, or GUI.
- **AI services:** speech recognition, language processing, and speech synthesis.
  Separate services versus a combined speech service is undecided.

## Hardware and environment facts

- The user reports that the original Windows/NVIDIA laptop is still available.
- The user reports having the previous parts except the LiPo battery. An itemized
  inventory and current wiring have not been physically verified.
- Checked-in firmware targets ESP32-S3-DevKitC-1 N8R8 with an INMP441 mic,
  SSD1306 displays, and MAX98357A pins reserved for future speaker output.
  These are repository facts, not confirmation of the physical assembly.
- This checkout is at `~/whisper-stack`. This Mac is not the original
  Windows runtime; its environment does not prove Windows compatibility.

## Choices still open

- New implementation layout and separation from legacy code.
- Backend language/framework, dependency tooling, AI providers/models, hosting,
  and device/server transport and audio contract.
- Exact hardware inventory, wiring, development/flash computer, and tooling.
- Budget, response latency, audio quality, and reliability targets.
- Enclosure dimensions, assembly method, and whether motion belongs in the first
  deliverable. Old servo experiments are not a committed requirement.
- Memory behavior, retention, privacy, and device provisioning.

FastAPI and package versions were looked up before the user stopped implementation.
No framework or dependencies were selected or installed for the rebuild.

## Maintaining decisions

Keep accepted decisions here. Label suggestions as proposed. Record dates and
reasons when decisions change. Do not create a competing specification later.
The existing GPL license remains in place; reuse does not imply a license change.
