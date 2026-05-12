# whisper stack: Local-First Pipeline

> **Historical doc — May 2026.** This is the original 7-stage build
> plan. All seven stages shipped. The Llama-Claude routing layer
> described here was later removed in the Gemini migration
> (single-LLM Gemini 2.5 Flash); the two-mode follow-up
> ([notes/two-mode-plan.md](notes/two-mode-plan.md)) is also
> superseded. For the current shape of the codebase, see `CLAUDE.md`
> § "Stack".

> **Sequel:** [notes/two-mode-plan.md](notes/two-mode-plan.md) — refactoring this server to support a cloud-mode deployment alongside local. *(Superseded.)*

## Context

Building the workstation-side server for a future ESP-32 voice device. End-to-end pipeline: **ESP-32 (INMP441 mic) → workstation (faster-whisper STT → router → local Llama 3.1 8B *or* Claude API → Piper TTS) → Bose Bluetooth speaker (system audio out) + ESP-32 OLED text display.**

This plan covers only the workstation server. The ESP-32 firmware is a later, separate project. We will build in 7 sequential stages with a **hard gate (check-in with you) at each stage transition**. Each stage leaves behind a standalone test script in `tests/` so any subsystem can be re-verified later.

---

## What's already on this box (probed)

- **GPU:** RTX 5070 Ti **Laptop** GPU — 12 GB VRAM (9.7 GB free at probe time), Blackwell `sm_120` (compute cap 12.0), driver 596.21
- **Python:** 3.13.13 available via `py -3.13` (default `python` is 3.14.0 — we'll avoid that one)
- **Ollama 0.22.1** with these models already pulled:
  - `llama3.1:8b` (Q4_0, 4.9 GB)
  - `llama3.1:8b-instruct-q5_K_M` (5.7 GB) — better quality, will use this
  - `nomic-embed-text` (274 MB) — useful for embedding-based router classification (stage 5)
- **No ffmpeg** (will install — required for audio decoding)
- **No CUDA toolkit** installed system-wide — fine, PyTorch/CTranslate2 ship CUDA libs via pip
- **Empty workspace** at `C:\whisper-stack`

---

## Architectural pushback before we start

1. **Python 3.14 is too new for ML wheels.** PyTorch and CTranslate2 wheels for cu12.x lag on 3.14. We will use Python 3.13 explicitly via `py -3.13 -m venv .venv`. Python 3.14 will be ignored entirely.

2. **Blackwell `sm_120` + CTranslate2 (faster-whisper's backend) is the riskiest single piece in this stack.** CTranslate2 added Blackwell support relatively recently. If wheels fail or kernels error on the 5070 Ti, the **fallback is `openai-whisper` (PyTorch-based)** — slower but mature on Blackwell. We will not let stage 1 stall on this.

3. **Laptop GPU, not desktop.** Same silicon family but lower TGP and aggressive thermal throttling under sustained load. Expect ~70–85% of desktop benchmarks. Not a blocker — just calibrate expectations.

4. **VRAM is tight but workable.** Whisper large-v3-turbo (FP16) ~2 GB + Llama 3.1 8B Q5_K_M ~6 GB = ~8 GB resident with both models hot. Fits in 12 GB with headroom for workspace + Windows overhead. Both can stay loaded across requests.

5. **Bluetooth audio adds 150–300 ms latency** (SBC codec; AAC ~120 ms). Unavoidable cost of going wireless. Acceptable for conversational use, but worth calling out — wired output would shave ~200 ms end-to-end.

6. **Flask is fine for the prototype.** If stage 7 latency feels bad and you want streaming TTS (start playback while LLM is still generating), we'll consider migrating to FastAPI + WebSockets at that point. Not a now-decision.

7. **`/converse` will be stateless initially** — no conversation history. That's a deliberate scope cut; multi-turn context is a stage-7+ enhancement.

---

## Project layout

```
C:\whisper-stack\
├── .venv\                    # Python 3.13 virtualenv
├── requirements.txt          # pinned versions
├── .env                      # ANTHROPIC_API_KEY (gitignored)
├── .env.example              # placeholder
├── samples\                  # sample audio fixtures
│   └── jfk.wav               # 11s public-domain clip for STT testing
├── tests\                    # standalone per-stage verification scripts
│   ├── stage1_whisper.py
│   ├── stage2_ollama.py
│   ├── stage3_anthropic.py
│   ├── stage4_flask.py
│   ├── stage5_router.py
│   ├── stage6_tts.py
│   └── stage7_e2e.py
├── server\
│   ├── __init__.py
│   ├── app.py                # Flask app + routes
│   ├── stt.py                # faster-whisper wrapper (singleton)
│   ├── llm.py                # Ollama + Anthropic clients
│   ├── router.py             # query classification (added stage 5)
│   ├── tts.py                # Piper wrapper (added stage 6)
│   └── config.py             # env loading, defaults
└── notes\
    └── router-design.md      # written at stage 5 before any code
```

---

## Stage 1 — GPU stack: faster-whisper on Blackwell

**Goal:** Confirm faster-whisper transcribes on the 5070 Ti (not CPU). Report real-time factor (RTF) on a sample clip.

**Steps:**
1. Install **ffmpeg** (`winget install Gyan.FFmpeg` or scoop) — required for audio decoding
2. `py -3.13 -m venv C:\whisper-stack\.venv` and activate
3. `pip install faster-whisper` (if numpy/CTranslate2 ABI errors appear, retry with `pip install "numpy<2"` — recent CTranslate2 supports numpy 2.x but older builds did not)
4. Download a small public-domain WAV (JFK clip, ~11s) into `samples\`
5. Write `tests/stage1_whisper.py`:
   - Loads `large-v3-turbo` with `device="cuda"`, `compute_type="float16"`
   - Transcribes the sample, times it
   - Prints model name, device, audio duration, transcription text, processing time, **RTF = audio_duration / processing_time** (higher is better; >5 is good on this GPU)
6. Run with `nvidia-smi -l 1` open in another shell to visually confirm the Python process appears in the GPU process list during transcription

**Fallback if CTranslate2 fails on Blackwell:** Pivot to `openai-whisper` with the latest PyTorch CUDA wheels (the exact `--index-url` for Blackwell will be whatever is current at install time on pytorch.org/get-started — we'll grab it then, not guess now). Accept slower performance; revisit faster-whisper later.

**Test script left behind:** `tests/stage1_whisper.py` — re-runnable smoke test for STT subsystem.

**Gate:** Stop. Show RTF, confirm GPU was used, decide before stage 2.

---

## Stage 2 — Ollama + Llama 3.1 8B benchmark

**Goal:** Confirm Ollama is using the GPU, measure tokens/sec, sample response quality.

**Steps:**
1. Use the already-pulled `llama3.1:8b-instruct-q5_K_M` (better quality than the Q4_0)
2. Verify GPU offload: after firing one query, `ollama ps` should show `100% GPU` in the PROCESSOR column
3. Write `tests/stage2_ollama.py`:
   - POSTs to `http://localhost:11434/api/generate` with `stream=false`
   - Sends 5 fixed prompts: factual ("capital of France"), arithmetic reasoning ("if I have 3 apples and give away 2, then buy 5..."), conversational ("how was your day"), short summarization ("summarize: ..."), explanation ("explain quantum tunneling in 2 sentences")
   - Computes TPS from response fields `eval_count` / `eval_duration` (nanoseconds)
   - Prints prompt → response → TPS for each; mean TPS at end

**Test script left behind:** `tests/stage2_ollama.py` — re-runnable Ollama TPS + quality smoke.

**Gate:** Stop. Show TPS + responses. Confirm Llama 8B quality is acceptable for "simple" queries; if not, we adjust the router taxonomy at stage 5.

---

## Stage 3 — Anthropic API smoke test

**Goal:** Confirm your API key works and you're getting Claude responses.

**Steps:**
1. `pip install anthropic python-dotenv`
2. You add your key to `C:\whisper-stack\.env` as `ANTHROPIC_API_KEY=sk-ant-...` (I'll create `.env.example` as the template). You'll add credits separately at console.anthropic.com.
3. Write `tests/stage3_anthropic.py`:
   - Loads key from `.env`
   - Calls `claude-sonnet-4-6` (best price/perf for "complex" route — we can swap to `claude-opus-4-7` later if you want max quality)
   - Sends one prompt, prints reply, latency, input/output token counts

**Test script left behind:** `tests/stage3_anthropic.py` — env-driven smoke test.

**Gate:** Stop. Confirm API works, note credit balance.

---

## Stage 4 — Minimal Flask server (`/transcribe` + `/chat`)

**Goal:** Two endpoints, both real. `/chat` hardcoded to Claude for now (router added at stage 5).

**Endpoints:**
- `POST /transcribe` — `multipart/form-data` audio upload → `{transcript, duration_s, latency_s, model}`
- `POST /chat` — `application/json {message}` → `{reply, model, latency_s, input_tokens, output_tokens}`
- `GET /health` — `{status, gpu_available, models_loaded}`

**Steps:**
1. `pip install flask python-dotenv`
2. `server/stt.py` — singleton `WhisperModel` wrapper, lazy-loaded on first transcribe
3. `server/llm.py` — Anthropic client now; Ollama path stubbed for stage 5
4. `server/app.py` — three routes, multipart parsing, JSON validation, error handling that returns proper HTTP codes (400 bad request, 500 server error)
5. Run with `python -m server.app` on `127.0.0.1:5000`
6. Write `tests/stage4_flask.py`:
   - Boots a `requests` client, hits both endpoints with the stage 1 WAV and a fixed prompt
   - Asserts response shapes
   - Prints curl-equivalent commands so you can poke manually:
     ```
     curl -F 'audio=@samples/jfk.wav' http://127.0.0.1:5000/transcribe
     curl -H 'Content-Type: application/json' -d '{"message":"hello"}' http://127.0.0.1:5000/chat
     ```

**Test script left behind:** `tests/stage4_flask.py` — full client-side exercise.

**Gate:** Stop. Confirm both endpoints return real results from real audio + real Claude.

---

## Stage 5 — Router (DESIGN DISCUSSION FIRST, then implementation)

**This stage is two gates:** design discussion, then implementation review.

**Goal:** Classify each query as `simple` (→ Ollama) or `complex` (→ Claude). The router itself must be fast (≤100ms ideally) so it doesn't erode the local-Ollama latency advantage.

**Step 5a — I write `notes/router-design.md`, no code.** The doc covers four approaches:

| Approach | Latency | Setup cost | Maintenance | Accuracy ceiling | Failure mode |
|---|---|---|---|---|---|
| **A. Keyword/regex heuristics** | <1ms | low | high (brittle) | ~75% | misses paraphrases, false positives |
| **B. Embedding similarity** (use already-pulled `nomic-embed-text`, compare query embedding to curated example sets per class) | ~30–80ms | medium | low (just edit example list) | ~85–90% | borderline queries get flipped by example bias |
| **C. Ollama self-classify** (one short Ollama call: `"classify this as simple/complex: ..."` → expect 1 token) | ~100–300ms | low | low | ~90% | adds Ollama call latency to *every* request — hurts the local-fast-path speed |
| **D. Hybrid: short-circuit on obvious keywords ("translate", "code", "summarize") → fall through to embedding** | ~5–80ms | medium | medium | ~88% | best balance, but two code paths to reason about |

I lean **B (embedding similarity)** as the default — it's the closest fit to your existing setup (you've already pulled `nomic-embed-text`), latency is acceptable, and the example list is trivial to tune. We discuss, you pick, I implement.

**Step 5b — Implementation (after you pick):**
1. `server/router.py` — `classify(query: str) -> Literal["simple", "complex"]`
2. **If the choice is embedding-based (B or D):** pre-compute and cache the example-set embeddings **once at server startup**, not per request. Per-request work is just one query embedding + cosine similarity. Otherwise we'd N+1 the Ollama embed endpoint and tank latency.
3. Wire `/chat` to call `classify()` and dispatch to Ollama or Claude accordingly
4. Response payload gets a `route` field showing which path was taken
5. Write `tests/stage5_router.py`: fixture of ~30 labeled queries (simple vs complex), prints accuracy + per-query latency + confusion matrix

**Test script left behind:** `tests/stage5_router.py` — re-runnable accuracy harness with editable fixture.

**Gate:** Stop twice — once for design discussion, once after implementation review.

---

## Stage 6 — Piper TTS to system audio (Bose BT speaker)

**Goal:** Synth WAV from text and play through the default Windows audio output (which routes to your Bose speaker if it's set as default).

**Steps:**
1. `pip install piper-tts sounddevice soundfile`
2. Download a Piper voice model (`en_US-amy-medium`, ~30 MB, fast and pleasant) into a `models/piper/` dir
3. **Pre-flight:** in Windows Sound settings, set the Bose speaker as default output. Confirm visible:
   ```
   python -c "import sounddevice; print(sounddevice.query_devices(kind='output'))"
   ```
4. `server/tts.py` — `synth(text: str) -> bytes` (WAV bytes); `synth_and_play(text: str)` for direct playback
5. Add `POST /speak` debug endpoint to Flask: `{text}` in, plays audio locally, returns `{latency_s}`
6. Write `tests/stage6_tts.py`: synth a fixed sentence, time it, play it, prompt user to confirm it was audible

**Test script left behind:** `tests/stage6_tts.py` — synth + playback + latency report.

**Gate:** Stop. Confirm audible playback through the Bose speaker.

---

## Stage 7 — End-to-end integration

**Goal:** One request = full pipeline. Audio in → text + audio out, router picks the model.

**New endpoint:**
- `POST /converse` — multipart audio upload → `{transcript, route, model, reply_text, audio_base64, timings: {stt, route, llm, tts, total}}`. Audio also plays locally on the workstation as a side effect (so the Bose speaker speaks).

**Steps:**
1. Wire `/converse` end-to-end: STT → router → LLM → TTS → response
2. Write `tests/stage7_e2e.py`: 3 sample audio files (one factual → expect Ollama, one nuanced → expect Claude, one conversational → either is fine), POSTs each, asserts route was sensible, prints full timing breakdown
3. Sanity expectation: short query end-to-end ~2–5s. If higher than 6s, instrument and report which stage dominates.

**Test script left behind:** `tests/stage7_e2e.py` — full-pipeline smoke.

**Gate:** Stop. Show timing breakdown. Discuss optimizations (streaming TTS? warm-up requests? smaller Whisper model? VAD trimming?) before we move to ESP-32 firmware as a separate project.

---

## Out of scope (deliberate cuts)

- **ESP-32 firmware** — separate project, separate plan
- **WebSocket streaming / streaming TTS** — revisit at stage 7 if latency is unacceptable
- **Wake-word detection** — defer to ESP-32 phase (probably push-to-talk button initially)
- **Voice activity detection (VAD)** — faster-whisper has built-in Silero VAD; can enable in stage 1 if needed for trimming silence; otherwise ESP-32 phase
- **Multi-turn conversation context / history** — stage 7 keeps `/converse` stateless; can add later
- **Auth, TLS, multi-user** — single-user LAN-only for now
- **Persistent logging / observability** — stdout only; structured logging is a later concern

---

## Verification scripts that will exist after this plan

| Script | Verifies |
|---|---|
| `tests/stage1_whisper.py` | GPU available + faster-whisper transcribes correctly + RTF |
| `tests/stage2_ollama.py` | Ollama on GPU + TPS + quality samples |
| `tests/stage3_anthropic.py` | Claude API key works + sample call |
| `tests/stage4_flask.py` | `/transcribe` + `/chat` endpoints functional |
| `tests/stage5_router.py` | Router accuracy on labeled fixture + latency |
| `tests/stage6_tts.py` | Piper TTS synth + system-audio playback |
| `tests/stage7_e2e.py` | Full pipeline + per-stage timing |

Each is runnable standalone after `.venv` activation to spot-check that subsystem at any later point.

---

## Critical files and where reuse is expected

- **`server/stt.py`** — used by `/transcribe` (stage 4) and `/converse` (stage 7). Singleton model load, FP16 on CUDA.
- **`server/llm.py`** — Anthropic client used by `/chat` (stage 4 onward); Ollama client added at stage 5; both used in `/converse` (stage 7).
- **`server/router.py`** — only stage 5+; consumed by `/chat` and `/converse`.
- **`server/tts.py`** — only stage 6+; consumed by `/converse` and `/speak` debug route.
- **`server/config.py`** — env loading via `python-dotenv`, model paths, defaults; imported by all server modules.

---

## Stage gate summary (where I will stop and check in with you)

1. After **stage 1** — RTF + GPU confirmation
2. After **stage 2** — TPS + Llama quality
3. After **stage 3** — API key works
4. After **stage 4** — Flask endpoints return real data
5. **Twice** at stage 5 — router design discussion, then implementation review
6. After **stage 6** — audible Bose playback
7. After **stage 7** — full timing breakdown and optimization discussion
