# Voice AI Server

A self-hosted voice assistant backend. Speak into a phone or terminal, the
server transcribes, routes the query to the right LLM, runs tools when
needed, synthesizes speech, plays it back. Designed as the workstation-side
brains for a future ESP-32 + INMP441 voice device — but also fully usable
today through a web page or push-to-talk CLI.

```
audio in → STT → router → simple-LLM or complex-LLM → TTS → audio out
                            ↓                  ↓
                    (Llama 3.1 / Haiku)  (Claude Sonnet
                                          + tools, when needed)
```

## What works today

- **Two-stage routing.** Cheap local LLM (Llama 3.1 8B via Ollama) for
  conversational + factual queries; Claude Sonnet for anything complex
  (writing, code, multi-step reasoning). Embedding-similarity classifier
  decides per-query in ~30 ms. Tool-likely queries auto-escalate to Sonnet.
- **Multi-turn conversation memory.** Markdown-backed sessions, idle-bounded
  (10-min default), one file per conversation under `sessions/`. Files
  auto-titled by Claude Haiku after the first turn.
- **Tools.** Real-time `get_current_time` (server-side, free) and
  Tavily-backed `web_search` (free tier 1k queries/mo). Web search is a
  drop-in: set `TAVILY_API_KEY` in `.env` and "what's the weather in Chicago"
  starts working. Tool calls are recorded per-turn in the session file for
  observability.
- **Two-mode architecture.** A single `MODE` env var picks the backend set:
  `local` (workstation, GPU, Ollama) or `cloud` (CPU-only, ready for a
  $5–10/mo VPS). Local mode is fully wired; cloud mode is partial — the
  factory raises `NotImplementedError` for backends that aren't shipped yet
  (Deepgram STT, OpenAI TTS).
- **Voice clients.** Self-contained web page at `GET /` (records on phone,
  posts to `/converse`, plays the reply in-browser); push-to-talk CLI
  (`tests/talk.py`); optional Cloudflare Tunnel for remote access without
  port-forwarding.
- **Auth.** Optional `API_KEY` shared-secret. `GET /` and `/health` stay
  public so you can load the page on a phone and paste your key in.

## Quick start

You'll need:

- **Python 3.13.x** in a venv. The system Python on Windows defaults to 3.14
  which lacks the ML wheels we need. Don't use it.
- **Ollama** running on `http://127.0.0.1:11434` with `llama3.1:8b-instruct-q5_K_M`
  pulled (or whatever you set `OLLAMA_MODEL` to).
- **An NVIDIA GPU** for local mode (faster-whisper inference). The cloud-mode
  branch is CPU-only.
- **An Anthropic API key** in `.env` as `ANTHROPIC_API_KEY=sk-ant-...`.

### Install (local mode)

```powershell
git clone https://github.com/anandenk/voice-ai-server.git
cd voice-ai-server

# Create the 3.13 venv. Adjust the path if your py launcher is elsewhere.
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install flask python-dotenv requests numpy anthropic sounddevice piper-tts faster-whisper

# Copy and fill in your keys
cp .env.example .env
notepad .env

# Download a Piper voice model (en_GB-alan-medium is the project default)
mkdir -Force models\piper | Out-Null
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium"
curl.exe -sSL -o models\piper\en_GB-alan-medium.onnx      "$base/en_GB-alan-medium.onnx"
curl.exe -sSL -o models\piper\en_GB-alan-medium.onnx.json "$base/en_GB-alan-medium.onnx.json"
```

### Run

```powershell
.\.venv\Scripts\python.exe -m server.app
```

Wait for `Listening on http://127.0.0.1:5000`. First boot is slow (~15s) —
faster-whisper does a PTX JIT compile on Blackwell GPUs and Ollama loads the
LLM into VRAM. Subsequent boots are warm.

### Talk to it

**Browser** (laptop or phone over the same network):
- Open `http://127.0.0.1:5000`
- (If you set `API_KEY`) paste it into the API key field
- (Optional) type a session ID into the Session field for memory across requests
- Tap "Tap to talk", talk, tap again

**Push-to-talk CLI**:
```powershell
.\.venv\Scripts\python.exe tests\talk.py
# Enter to start, Enter again to send. Set VOICE_AI_SESSION_ID for memory.
```

**Remote** (phone on different WiFi, ESP-32 testing):
```powershell
# install once: winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:5000
# prints a https://<random>.trycloudflare.com URL — open on your phone
```

## Configuration

All config is via env vars, loaded from `.env` at process boot. See
[.env.example](.env.example) for the full list with `[both]` / `[local]` /
`[cloud]` tags. Highlights:

- `MODE=local | cloud` — backend set selection (default `local`)
- `API_KEY=...` — shared-secret auth on protected routes (default unset = LAN-only)
- `PIPER_VOICE=en_GB-alan-medium` — TTS voice (any from the [Piper voice catalog](https://github.com/rhasspy/piper/blob/master/VOICES.md))
- `PIPER_LENGTH_SCALE=0.85` — speech rate; lower = faster (sane range 0.7–1.1)
- `WHISPER_LANGUAGE=en` — pin STT language (empty = auto-detect, prone to misfires)
- `SESSION_IDLE_TIMEOUT_MIN=10` — gap that triggers a new session file
- `TAVILY_API_KEY=...` — enables `web_search` tool ([free tier](https://tavily.com))
- `ROUTER_MARGIN_S=0.05` — embedding-router margin; higher → more queries to Sonnet

## Project layout

```
.venv/                       Python 3.13 virtualenv (gitignored)
samples/jfk.wav              public-domain STT fixture
models/piper/                Piper voice .onnx + .json (gitignored)
sessions/                    conversation history, one .md per session (gitignored)
tests/stageN_*.py            per-feature smoke tests (run after server start)
tests/cloud_*.py             cloud-backend smoke tests
tests/talk.py                push-to-talk client
server/
├── app.py                   Flask routes
├── config.py                env loading + MODE validation
├── modes.py                 backend factory: get_stt / get_llm / get_tts / get_embedder
├── protocols.py             STT / LLM / TTS / Embedder Protocols
├── router.py                embedding-similarity classifier + tool-likely heuristic
├── sessions.py              markdown session store
├── titler.py                Haiku-backed session auto-titling
├── tools/
│   ├── __init__.py          registry
│   ├── base.py              Tool dataclass + Anthropic/Ollama schema converters
│   ├── time.py              get_current_time
│   └── search.py            web_search (Tavily)
├── markdown.py              to_speakable_text (markdown/LaTeX stripper)
├── audio.py                 pcm_to_wav, synth_to_wav (mode-independent)
├── audio_out.py             local sounddevice playback; cloud no-op
├── web/index.html           browser/phone client
└── backends/                STT/LLM/TTS/Embedder implementations
plan.md                      original 7-stage build plan
notes/two-mode-plan.md       local vs cloud refactor (mostly shipped)
notes/sessions-plan.md       multi-turn conversation memory (shipped)
notes/voice-agent-plan.md    streaming, VAD, continuous mode, wake word, tools
CLAUDE.md                    project instructions for Claude Code agents
```

## Routes

- `GET /` — web client
- `GET /health` — `{status, mode, gpu_available, models_loaded}`
- `POST /transcribe` — multipart `audio` → `{transcript, duration_s, latency_s, model, language}`
- `POST /chat` — JSON `{message, session_id?, force_route?}` → routed LLM reply with `route`, `auto_escalated`, `router_scores`, optional `tools_used` and `session_file`
- `POST /speak` — JSON `{text}` → synth + blocking play through default output
- `POST /converse` — multipart `audio` (+ optional `session_id`, `force_route`, `play_locally` form fields) → STT → router → LLM → TTS, returns `{transcript, route, model, reply_text, audio_base64, audio_sample_rate, audio_duration_s, timings, router_scores, llm_tokens, auto_escalated, tools_used?, session_file?}`

When `API_KEY` is set, all routes except `/` and `/health` require an
`X-API-Key` header. The web page persists this in localStorage; `talk.py`
reads it from the loaded `.env`.

## What's planned but not shipped

- **Phase 5 of two-mode plan** — Deepgram STT for cloud mode (blocked on
  signup; backend factory raises `NotImplementedError` until it lands)
- **Phase 7 of two-mode plan** — OpenAI tts-1 TTS for cloud mode (same)
- **Phase 9 of two-mode plan** — Fly.io Dockerfile + deploy notes (same)
- **Voice-agent plan** — streaming TTS, voice-activity detection,
  continuous + interrupt mode, "hey jarvis" wake word

See [`CLAUDE.md`](CLAUDE.md) and the per-plan docs in
[`notes/`](notes/) for the full roadmap and decisions log.

## Why this exists

Built as the server side of an eventual ESP-32 voice device — a small,
always-on, internet-connected button that talks to you. The codebase is
structured so the hardware client doesn't need to do anything clever:
post audio, get audio back. Two-mode architecture lets the same server run
on a workstation with a GPU OR on a cheap VPS, depending on whether you're
willing to pay for cloud STT/TTS in exchange for not running a 24/7 GPU
machine.

It's also a pretty nice way to talk to Claude.
