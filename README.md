# Whisper Stack

A voice assistant project for a small, cloud-connected desk device with its own
microphone, speaker, display and controls. The current working prototype runs
in a browser; the standalone device is still the goal.

## The original project

The May 2026 prototype used **Whisper → Gemini 2.5 Flash → Kokoro**, with a
workstation handling the conversation. It included browser and terminal clients,
ESP32/ESP32-S3 firmware, wake-word detection, OLED displays and local audio
processing. Conversation history was stored in Markdown files.

That implementation is preserved as reference. Its setup, hardware milestones
and earlier architecture are in the [archived README](notes/archive/README-before-overview-2026-10-26.md)
and [May technical notes](notes/archive/claude-context-2026-05.md).

## The current rebuild

> Haiku 5.5 + Mem0, using ElevenLabs’ WebRTC audio.

The latest validated prototype uses **ElevenLabs Agents** for voice conversation,
**Claude Haiku 5.5** for replies, and **hosted Mem0** for memory across sessions.
The official WebRTC client handles microphone capture and playback with its
existing audio processing. The assistant answers a question, waits quietly,
and accepts follow-up questions or interruptions.

This prototype lives in [experiments/agents-mem0/](experiments/agents-mem0/README.md).
A small local service keeps API keys on the server and connects the memory tools.

The earlier October rebuild also remains available in [backend/](backend/README.md):
a FastAPI service with ElevenLabs transcription and speech, Haiku replies,
overlapping streamed playback, bounded web search, and token/cost diagnostics.
It runs independently of the Agents prototype; its search and diagnostics are
not part of the Agents path.

## Current status

- **User-tested:** speech recognition, spoken replies, interruptions, quiet
  waiting, and a saved preference recalled in a fresh session.
- **Automated checks:** 120 backend Python tests, 14 backend browser checks,
  7 experiment Python checks, and 8 experiment browser checks pass. Provider
  calls are mocked in these suites; live user tests are separate evidence.
- **Device work remains:** the rebuild has not been validated on the ESP32
  microphone/speaker hardware. Browser success does not establish device audio
  quality, firmware readiness or standalone operation.
- **Deployment and long-term memory/retention choices remain open.** The current
  browser tester uses a local service and cloud providers.

## Run the voice prototype

For a new checkout, follow the [experiment setup guide](experiments/agents-mem0/README.md#reproduce-and-verify)
to install dependencies and configure credentials. From the repository root,
with that setup complete:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/web_server.py
```

Open **http://127.0.0.1:8766**, click **Start conversation**, and allow microphone
access. **End conversation** stops the session. Opening the page leaves the
microphone off; starting a conversation uses provider credits.

Chrome and Edge have worked in user testing. Privacy extensions blocked the
initial Chrome connection; see [startup troubleshooting](experiments/agents-mem0/README.md#startup-diagnostics)
if the WebRTC session cannot connect.

Credentials stay in ignored local `.env` files. Conversation and memory retention
follow the providers' settings; see the experiment guide for the current defaults.
For the separate push-to-talk backend, use the [backend setup guide](backend/README.md).

## Repository guide

| Location | Purpose |
| --- | --- |
| [experiments/agents-mem0/](experiments/agents-mem0/README.md) | Current WebRTC voice and persistent-memory prototype |
| [backend/](backend/README.md) | Independent HTTP voice backend, push-to-talk tester and diagnostics |
| [server/](server/), [firmware/](firmware/), [tests/](tests/), [tools/](tools/) | Historical May implementation |
| [docs/PROJECT.md](docs/PROJECT.md) | Goals, accepted decisions and open choices |
| [docs/STATUS.md](docs/STATUS.md) | Current handoff, verification and blockers |
| [notes/archive/](notes/archive/) | Historical setup and technical context |

Coding agents should start with [AGENTS.md](AGENTS.md).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). The historical firmware includes a port
of [FluxGarage RoboEyes](https://github.com/FluxGarage/RoboEyes) by Dennis Hoelscher.
