# Historical Server (May 2026 Prototype)

> [!WARNING]
> **Unsupported Historical Code**: This directory contains the Flask server from the May 2026 prototype (Whisper → Gemini 2.5 Flash → Kokoro). It is preserved for technical reference and historical continuity only. It is not maintained and has several known architectural and security limitations:
>
> - **Authentication**: When `API_KEY` is not set in `.env`, authentication is completely disabled (`_gate_protected_routes` in `server/app.py`). Any client capable of reaching the server can trigger transcription, synthesis, and billable model calls.
> - **Resource Handling**: Routes such as `/converse` and `/listen` buffer raw audio bodies in memory without stream size limits or backpressure controls.
> - **Error Exposure**: Raw Python exception tracebacks and runtime errors may be reflected in HTTP responses.
> - **Unsafe DOM Rendering**: The legacy web client (`server/web/index.html`) renders messages and tool outputs directly into the DOM using `innerHTML` without sanitization.
> - **Dependency Scope**: Root-level manifests (`requirements.txt`, `requirements-cloud.txt`) belong to this legacy server rather than the modern backend.

### Recommended Path

For current work, use the local single-owner browser backend in [`experiments/agents-mem0/`](../experiments/agents-mem0/README.md). Note that neither the legacy server nor the local bridge in `experiments/agents-mem0/` is hardened for public network hosting, port exposure, or public tunnels.
