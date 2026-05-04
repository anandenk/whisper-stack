# Two-mode refactor — `MODE=local` vs `MODE=cloud`

> Note: the user requested this be written to `notes/two-mode-plan.md`. Plan-mode constraints route the file here first; on approval it gets copied to `notes/two-mode-plan.md` as the first implementation step.

## Context

Today the server runs only on the workstation: faster-whisper + Ollama/Llama 3.1 8B + Claude Sonnet + nomic-embed router + Piper TTS. All seven stages are shipped and verified ([CLAUDE.md](CLAUDE.md), [plan.md](plan.md)).

The next step is to support a **second deployment shape** from the same codebase — a portable, cloud-only stack that can live on a $5–10/month VPS, with an ESP-32 client connecting via the public internet instead of LAN. The workstation product stays exactly as it is today.

This refactor introduces an STT/TTS/LLM/Embedder abstraction layer so a single `MODE` env var selects the implementation set at process boot. **One process serves one mode** — no runtime switching, no per-request flexing.

Goal: ship cloud mode without ever breaking local mode, with the existing test suite passing at every commit.

---

## Decisions at a glance

| Decision | Pick | Runner-up | Why |
|---|---|---|---|
| Cloud STT | **Deepgram nova-3** | OpenAI gpt-4o-mini-transcribe | Voice-first API, ~200–300 ms TTLB vs ~500–1000 ms; ~$0.0043/min. |
| Cloud LLM (simple) | **Claude Haiku 4.5** | (user-specified, no contender) | Reuses Anthropic SDK already in code. |
| Cloud LLM (complex) | **Claude Sonnet 4.6** | — | Same as local mode; identical implementation. |
| Cloud TTS | **OpenAI `tts-1`** (PCM) | ElevenLabs Flash | $15/1M chars vs ~$300; quality is "good enough" for voice device. |
| Cloud embedder | **sentence-transformers all-MiniLM-L6-v2** | OpenAI text-embedding-3-small | User-specified in-process; tradeoff flagged in Risks. |
| Mode selection | Env var `MODE`, validated at boot | — | Smallest mechanism that works. |
| File layout | `server/backends/*` for implementations, `server/protocols.py` for interfaces | — | Keeps existing top-level imports clean. |
| VPS | **Fly.io** | Hetzner CPX11 | Auto-HTTPS, secrets mgmt, single-command deploy. |

---

## Interfaces (`server/protocols.py`)

Four `typing.Protocol` classes, structurally typed. Methods return plain dicts where the existing code already does — no point inventing dataclasses for this migration.

### `STT`

```python
class STT(Protocol):
    def transcribe(self, audio_path: str | Path) -> dict:
        """Returns {transcript, duration_s, latency_s, model, language}.

        `audio_path` is a path the implementation may read. Flask writes the
        upload to a temp file and passes the path — keeps the contract simple
        (no streaming/bytes shuffling) and matches both faster-whisper AND
        Deepgram (which both accept file uploads).
        `language` may be None for cloud providers that don't detect it.
        """
    def warmup(self) -> None: ...
    def is_loaded(self) -> bool: ...
```

### `LLM`

```python
class LLM(Protocol):
    def chat(self, message: str, max_tokens: int = 1024,
             system_prompt: str | None = None) -> dict:
        """Returns {reply, model, latency_s, input_tokens, output_tokens, stop_reason}.

        `tps` (currently in chat_ollama only) becomes optional —
        present when the backend reports it, absent otherwise.
        """
    def warmup(self) -> None: ...
    def is_loaded(self) -> bool: ...
```

### `TTS`

```python
class TTS(Protocol):
    def synth(self, text: str) -> tuple[bytes, int]:
        """Returns (raw int16 mono PCM bytes, sample_rate_hz).

        IMPORTANT: bytes MUST be raw int16 mono PCM, not WAV/MP3/Opus.
        Cloud providers return MP3 by default — implementation MUST request
        PCM and may need to resample. We standardize on int16 mono so
        `pcm_to_wav` and sounddevice playback work without per-backend
        special cases. The sample rate may differ per backend (Piper 22050,
        OpenAI tts-1 24000) — callers must not hardcode it.
        """
    def warmup(self) -> None: ...
    def is_loaded(self) -> bool: ...
```

The standalone helpers `pcm_to_wav`, `synth_to_wav` move to `server/audio.py` (pure functions on protocol output, not part of the interface). `synth_and_play` and `synth_play_nonblock` move to `server/audio_out.py` and become **no-ops in cloud mode** via a `LOCAL_PLAYBACK_AVAILABLE` flag set by the factory. `to_speakable_text` moves to `server/markdown.py` — mode-independent and shouldn't sit inside `tts.py`.

### `Embedder`

```python
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """Returns (n, dim) float32 array. Dim is implementation-defined
        (Ollama nomic-embed-text = 768, MiniLM-L6 = 384). Vectors are NOT
        required to be L2-normalized — the router normalizes."""
    def warmup(self) -> None: ...
    def is_loaded(self) -> bool: ...
```

The user's framing is right: only the embedder needs abstracting. The router algorithm (max-cosine-per-class + asymmetric margin) is pure linear algebra and stays in `router.py`. `router.warmup()` calls the configured embedder's `embed()` once on the example sets, caches the resulting `(n, dim)` array, then `classify()` runs on whatever embedder shape it got.

### Error contract

All four protocols: implementations raise on failure; the route handler in `app.py` already wraps in `try/except` and returns 500 with the `repr`. We don't introduce a custom exception hierarchy — the existing handler's traceback log + `repr`-in-JSON is enough for a single-user system. Network errors in cloud backends propagate naturally as `requests.HTTPError` / SDK-specific exceptions.

---

## Module restructure

### Target layout

```
server/
├── __init__.py
├── app.py                   # routes; calls modes.get_*() once at warmup
├── config.py                # adds MODE; validates at boot
├── modes.py                 # factory functions: get_stt/get_llm/get_tts/get_embedder
├── protocols.py             # 4 Protocol classes
├── markdown.py              # to_speakable_text (extracted from tts.py)
├── audio.py                 # pcm_to_wav, synth_to_wav (mode-independent)
├── audio_out.py             # local sounddevice playback; cloud-mode no-op
├── router.py                # accepts Embedder via attach_embedder()
└── backends/
    ├── __init__.py
    ├── stt_whisper.py       # local — WhisperLocalSTT
    ├── stt_deepgram.py      # cloud — DeepgramSTT
    ├── llm_ollama.py        # local-simple — OllamaLLM
    ├── llm_claude.py        # both modes — ClaudeLLM(model=...) handles Sonnet AND Haiku
    ├── tts_piper.py         # local — PiperTTS
    ├── tts_openai.py        # cloud — OpenAITTS
    ├── embed_ollama.py      # local — wraps existing _embed
    └── embed_st.py          # cloud — sentence-transformers
```

### File moves

| From | To | Notes |
|---|---|---|
| `server/stt.py` | `server/backends/stt_whisper.py` | Wrap singleton in `WhisperLocalSTT` class. Keep DLL bootstrap at module top. |
| `server/llm.py:chat_claude` | `server/backends/llm_claude.py` | Becomes `ClaudeLLM(model_name)` — instantiate once for Sonnet, once for Haiku. |
| `server/llm.py:chat_ollama` | `server/backends/llm_ollama.py` | `OllamaLLM` class. |
| `server/tts.py:synth/warmup` | `server/backends/tts_piper.py` | `PiperTTS` class. |
| `server/tts.py:to_speakable_text` | `server/markdown.py` | Pure function; mode-independent. |
| `server/tts.py:pcm_to_wav,synth_to_wav` | `server/audio.py` | Pure helpers. |
| `server/tts.py:synth_and_play,synth_play_nonblock` | `server/audio_out.py` | Local-only side-effect helpers; cloud no-op. |
| `server/router.py` | unchanged in place | Refactored to depend on `Embedder` injected at warmup. |
| `server/router.py:_embed` | `server/backends/embed_ollama.py` | `OllamaEmbedder` class. |

`llm_claude.py` is shared. `ClaudeLLM(model="claude-sonnet-4-6")` and `ClaudeLLM(model="claude-haiku-4-5")` instantiate the same class with different models. The simple/complex distinction lives in the factory, not in the implementation.

---

## Mode selection mechanism

In `config.py` (additions):

```python
MODE = os.environ.get("MODE", "local").strip().lower()
if MODE not in ("local", "cloud"):
    raise SystemExit(f"MODE must be 'local' or 'cloud', got {MODE!r}")
```

In `modes.py`:

```python
@functools.lru_cache(maxsize=1)
def get_stt() -> STT:
    if config.MODE == "local":
        from server.backends.stt_whisper import WhisperLocalSTT
        return WhisperLocalSTT()
    from server.backends.stt_deepgram import DeepgramSTT
    return DeepgramSTT(api_key=config.DEEPGRAM_API_KEY)

@functools.lru_cache(maxsize=2)
def get_llm(route: Literal["simple", "complex"]) -> LLM:
    if route == "complex":
        from server.backends.llm_claude import ClaudeLLM
        return ClaudeLLM(model=config.ANTHROPIC_MODEL)
    if config.MODE == "local":
        from server.backends.llm_ollama import OllamaLLM
        return OllamaLLM()
    from server.backends.llm_claude import ClaudeLLM
    return ClaudeLLM(model=config.ANTHROPIC_HAIKU_MODEL)

# similarly: get_tts, get_embedder
```

Five factory functions, each `lru_cache`-memoized to one instance per arg. `app.py` imports them and calls each at startup (warmup) and from each route. The router takes the embedder at warmup:

```python
# in app.py main() warmup block:
router.attach_embedder(modes.get_embedder())
router.warmup()
```

Backend-specific imports live INSIDE the factory branches — no `import sentence_transformers` happens in local mode (saves ~250 MB import-time torch overhead) and no `import faster_whisper` happens in cloud mode (avoids the CTranslate2 DLL bootstrap on a Linux VPS). **This is the only place I justify late imports** — the cost of unconditional top-level imports is large enough to break cloud-mode boot on a small VPS, and conditional imports at the module top would be uglier than at the factory branch.

### Validation

Config keys are read but not validated at import time. Validation happens at backend construction:
- `WhisperLocalSTT()` — checks `nvidia` site-package dir exists; raises if not.
- `DeepgramSTT(api_key)` — raises `RuntimeError` if `api_key` is empty.
- `ClaudeLLM(model)` — Anthropic SDK raises if `ANTHROPIC_API_KEY` is missing.
- `SentenceTransformerEmbedder()` — model is bundled in the Docker image; raises if not found.

The factory raises clearly, with a message naming the missing env var. Boot fails fast and informatively. `/health` reports the active mode + which models loaded.

---

## Configuration surface

**One `.env`, all keys, unused ignored.** Confirmed against user's framing. `.env.example` documents every key with a `[local]`, `[cloud]`, or `[both]` tag in the comment.

```bash
# [both] always required
ANTHROPIC_API_KEY=sk-ant-...
API_KEY=                     # shared-secret; required when MODE=cloud and exposed publicly

MODE=local                   # local | cloud

# [local] faster-whisper
WHISPER_MODEL=large-v3-turbo
WHISPER_DEVICE=cuda
WHISPER_COMPUTE=float16

# [local] Ollama (simple LLM + embedder)
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1:8b-instruct-q5_K_M
EMBED_MODEL=nomic-embed-text

# [local] Piper TTS
PIPER_VOICE=en_US-amy-medium
TTS_OUTPUT_DEVICE=          # optional substring of device name

# [cloud] STT
DEEPGRAM_API_KEY=

# [cloud] TTS
OPENAI_API_KEY=
OPENAI_TTS_MODEL=tts-1
OPENAI_TTS_VOICE=alloy

# [cloud] simple-route LLM
ANTHROPIC_HAIKU_MODEL=claude-haiku-4-5

# [both] router behavior + voice prompt
ROUTER_MARGIN_S=0.05         # may need re-tuning per mode (different embedders)
CONVERSE_MAX_TOKENS=96
```

Local-mode users ignore the cloud block; cloud-mode users ignore the local block. No splitting `.env` into `.env.local`/`.env.cloud` — one user, one machine, one file.

---

## Test strategy

The existing 7 stage tests pin specific behaviors. The split:

| Test | Status after refactor |
|---|---|
| `tests/stage1_whisper.py` | **Local-only.** Tests faster-whisper. Already implicitly local-only. No change. |
| `tests/stage2_ollama.py` | **Local-only.** Tests Ollama TPS. No change. |
| `tests/stage3_anthropic.py` | **Both modes.** Tests Anthropic API directly. Already mode-agnostic. No change. |
| `tests/stage4_flask.py` | **Both modes.** Hits `/transcribe` and `/chat` against running server. Add a `print` of `/health.mode` at the top so the user knows which mode the server claims. |
| `tests/stage5_router.py` | **Both modes.** Algorithm is mode-independent. Embedder differs → absolute scores differ → optimal margin differs. Test reads `MODE` and prints a margin-sweep recommendation per mode. |
| `tests/stage6_tts.py` | **Local-only.** Tests Piper. |
| `tests/stage7_e2e.py` | **Both modes.** Hits `/converse` against running server. Existing assertions on shape and route-name validity stay. The model-name assertion (currently `"llama" in d["model"]` for simple route) parameterizes over mode read from `/health`: in cloud mode, simple route returns Claude Haiku, so the assertion becomes `"haiku" in d["model"].lower()`. |

### New tests

| Test | Coverage |
|---|---|
| `tests/cloud_stt_deepgram.py` | Hit Deepgram directly with `samples/jfk.wav`. ~$0.001/run. |
| `tests/cloud_tts_openai.py` | Synth a fixed sentence, dump WAV, assert int16 mono PCM at the canonical rate. ~$0.001/run. |
| `tests/cloud_embed_st.py` | Embed example set, assert `(24, 384)` shape, log mean cold + warm latency. Free. |
| `tests/cloud_llm_haiku.py` | Smoke `ClaudeLLM(model="claude-haiku-4-5").chat("hi")`. ~$0.001/run. |

**Cost honesty:** A full cloud-mode test pass (`stage3 + stage4 + stage5 + stage7 + 4 cloud_*`) is approximately **$0.05–$0.15**. Trivially cheap on demand; not something to auto-run in CI loops.

**Risk:** Network flakiness can fail cloud tests intermittently. They print a clear "this is a cloud-API test, network failure ≠ logic bug" line on failure.

No pytest. Stays as standalone scripts — matches existing pattern.

---

## Migration path

Each phase is one logical commit (or a small few). At every commit, **the local-mode test suite passes**. Cloud-mode tests become available from Phase 5 onward. Local mode is the default at every step.

### Phase 1 — extract pure helpers (no behavior change)

- Create `server/markdown.py` — move `to_speakable_text` and its regexes from `tts.py`. Re-export from `tts.py` to keep import paths working.
- Create `server/audio.py` — move `pcm_to_wav` and `synth_to_wav`. Re-export from `tts.py`.
- Create `server/audio_out.py` — move `synth_and_play` and `synth_play_nonblock`. They still call `tts.synth()` directly; will route through factory in Phase 3.
- Run stages 1, 4, 5, 6, 7 — all pass.

**Gate.**

### Phase 2 — protocols + backends/ tree (still no behavior change)

- Add `server/protocols.py` with the four Protocol classes.
- Create `server/backends/` package.
- Move `server/stt.py` body into `server/backends/stt_whisper.py`, wrap module-level singleton in `WhisperLocalSTT` class. **`server/stt.py` becomes a thin shim** that imports from the new module so `from server import stt` still works.
- Same for `llm.py` → `backends/llm_ollama.py` + `backends/llm_claude.py` (split). `server/llm.py` keeps shim.
- Same for `tts.py` → `backends/tts_piper.py`. `server/tts.py` keeps shim.
- Move the `_embed` function from `router.py` into `backends/embed_ollama.py` as `OllamaEmbedder`.
- Update `router.py` to accept an `Embedder` via `attach_embedder()`. **`router.warmup()` now requires an embedder be attached first.**
- Edit `tests/stage5_router.py` — it imports `router` directly, calls `router.warmup()`. Add `router.attach_embedder(OllamaEmbedder())` at the top of `main()` before `warmup()`. Without this, the test breaks at this phase. (Phase 8 layers per-mode margin sweep on top.)
- `app.py` is unchanged at this phase — it still imports from `server.stt`/`llm`/`tts`, which are now shims pointing to backends. No factory yet.
- Run all stages — all pass.

**Gate.**

### Phase 3 — `modes.py` factory + `MODE` config

- Add `MODE` to `config.py`, default `local`. Validate at import.
- Add `server/modes.py` with the factory functions, all branching on `MODE`. Cloud branches raise `NotImplementedError` for now.
- Rewire `app.py` to call `modes.get_*()` instead of constructing backends directly via shims.
- Rewire `audio_out.py` to source `synth()` from `modes.get_tts()` instead of `tts.synth()` (the shim still works, but going through the factory keeps the cloud-mode no-op story consistent).
- Delete the shim files `server/stt.py`, `server/llm.py`, `server/tts.py`. All callers now go through `modes.get_*()` or directly from `server.backends.*`. Verify with `grep "from server import (stt|llm|tts)"` returns nothing.
- `audio_out.LOCAL_PLAYBACK_AVAILABLE = (config.MODE == "local")`. Guard `play_pcm()` on it.
- `/health` reports `mode` field.
- Run all stages — all pass.

**Gate.**

### Phase 4 — `.env.example` + docs + `requirements-cloud.txt`

- Update `.env.example` with the full key set + `[local]/[cloud]/[both]` tags.
- Update `CLAUDE.md` with a "MODE selection" section.
- Create `requirements-cloud.txt` (initially just inherits from `requirements.txt` minus `faster-whisper`/`piper-tts`/CUDA wheels; cloud-specific packages added as Phases 5–8 land). Lets the user start iterating in a slim cloud env.
- No code changes.

### Phase 5 — Deepgram STT

- `pip install deepgram-sdk` (or use raw `requests` — decide at impl time; SDK is small).
- Implement `backends/stt_deepgram.py:DeepgramSTT`. Returns the same dict shape as Whisper. `language` may be None.
- Wire into `modes.get_stt()` for cloud.
- Add `tests/cloud_stt_deepgram.py`.
- Test: run server with `MODE=cloud`, call `/transcribe` against `jfk.wav`. Local-mode tests still pass with `MODE=local` default.

**Gate.**

### Phase 6 — Claude Haiku for cloud-simple

- `backends/llm_claude.py` already supports `model` param. Cloud factory passes `claude-haiku-4-5`.
- **Verify the alias before locking it as default.** `claude-sonnet-4-6` works as a date-less alias in the existing code, but Haiku may require the dated form `claude-haiku-4-5-20251001`. Smoke `ClaudeLLM(model="claude-haiku-4-5").chat("hi")` once; if it 400s on `model_not_found`, fall back to the dated ID. Update the `ANTHROPIC_HAIKU_MODEL` default accordingly.
- `modes.get_llm("simple")` returns Haiku in cloud, Ollama in local.
- Add `tests/cloud_llm_haiku.py`.

**Gate.**

### Phase 7 — OpenAI TTS

- `pip install openai`.
- Implement `backends/tts_openai.py:OpenAITTS`. Request `response_format="pcm"` (24 kHz mono int16). Same `(bytes, sample_rate)` return shape.
- `modes.get_tts()` branches.
- Add `tests/cloud_tts_openai.py`.
- `audio_out` already no-ops in cloud (Phase 3). Confirm `play_locally=true` on a cloud server doesn't crash.

**Gate.**

### Phase 8 — sentence-transformers embedder

- `pip install sentence-transformers`. (Note: pulls torch CPU ~250 MB.)
- Implement `backends/embed_st.py:SentenceTransformerEmbedder`. Loads `all-MiniLM-L6-v2`, returns `(n, 384)` float32.
- `modes.get_embedder()` branches.
- Re-run `tests/stage5_router.py` with `MODE=cloud` — generate a margin sweep specific to cloud embeddings. Document the recommended `ROUTER_MARGIN_S` separately for cloud mode.
- Add `tests/cloud_embed_st.py`.

**Gate.**

### Phase 9 — cloud deployment

- Write `Dockerfile` (slim Python image, install only cloud-mode deps from `requirements-cloud.txt`).
- Pre-bake the MiniLM model into the image (avoid first-request stall).
- Document Fly.io deploy steps in `notes/deploy-cloud.md`.
- Smoke-test by deploying to Fly and pointing the existing web client + `talk.py` at the public URL.

**Gate.** Ship.

---

## Cloud deployment (sketch)

**Provider: Fly.io.** Why: built-in HTTPS via Anycast TLS termination, secrets management via `fly secrets set`, deploy from Dockerfile in one command, free allowance covers a single small machine. Runner-up Hetzner CPX11 ($4.59/mo) is cheaper but requires manual Caddy/systemd setup.

Sketch:

1. `Dockerfile` — Python 3.13 slim, `pip install` from `requirements-cloud.txt` (no torch/CUDA build, no faster-whisper, no piper). Includes `sentence-transformers` (pulls ~250 MB torch CPU). Pre-download MiniLM weights into the image during build.
2. `fly.toml` — single machine, **1 GB RAM minimum** (MiniLM ~100 MB + torch runtime ~200 MB + Python + Flask). Fly's smallest 1 GB machine is ~$5.70/mo.
3. `fly secrets set ANTHROPIC_API_KEY=... DEEPGRAM_API_KEY=... OPENAI_API_KEY=... API_KEY=<token> MODE=cloud` — keys never touch the image or git.
4. `fly deploy` — push, build, switch traffic. Fly issues a `*.fly.dev` HTTPS hostname.
5. ESP-32 / phone web client / `talk.py` point at the Fly URL with the API key set.

No deployment scripts in this plan. The Dockerfile and `fly.toml` are Phase 9 deliverables.

---

## Out of scope

- **ESP-32 firmware** — doesn't exist yet. Mode-switching at the device end is theoretical until firmware lands. The server speaks HTTP — same protocol either way; the device just changes its base URL.
- **Session persistence / multi-turn context** — parked entirely. If cloud mode eventually wants per-user history, file storage on the VPS is enough for single-user; no DB needed.
- **Streaming audio (STT or TTS)** — current sync model preserved. Streaming TTS is a future optimization across both modes; out of scope here.
- **Removing Ollama from local mode** — explicitly rejected by user; Ollama stays.
- **Hosting the cloud LLM/STT/TTS ourselves** — defeats the point of cloud mode being deployable to a $5 VPS.
- **Cost telemetry / per-request billing tracking** — useful eventually, not part of this refactor.
- **Wake word, VAD, noise suppression** — orthogonal to mode.
- **CI** — current test harness is "run scripts manually". Stays manual.

---

## Risks and pushback

### Real interface leaks the abstraction can hide

1. **Sample rate / format drift.** Piper outputs 22050 Hz mono int16. OpenAI TTS defaults to MP3. The TTS protocol REQUIRES int16 mono PCM with `sample_rate_hz` returned — implementations that return MP3 or 24 kHz will silently break `pcm_to_wav` or `sounddevice.play()`. **Mitigation:** OpenAI TTS backend explicitly requests `response_format="pcm"` (24 kHz int16 mono raw). We accept the rate change — `pcm_to_wav` already takes the rate as a parameter, the web client and ESP-32 must too. The unit test for cloud TTS asserts the sample rate to catch regressions.

2. **Latency profile flips.** In local mode, STT (~0.4 s warm) dominates and TTS is essentially free. In cloud mode, every stage adds network. Total stays in 1–4 s range but the per-stage timing breakdown reported by `/converse` looks completely different. Anyone reading the breakdown to optimize must not assume the local-mode shape.

3. **Embedder dim and absolute-score differences.** nomic-embed-text is 768-dim, MiniLM is 384-dim. Cosine-similarity scales work either way, but absolute scores from MiniLM tend to be higher across the board (less spread). The 0.05 margin tuned for nomic likely under-routes in cloud mode (more queries get classified `simple`, including some that should have gone `complex`). **Mitigation:** Phase 8 re-runs the margin sweep and documents a per-mode `ROUTER_MARGIN_S` default. May require splitting into `ROUTER_MARGIN_S_LOCAL` and `ROUTER_MARGIN_S_CLOUD` env vars; we'll know after measuring.

4. **Cloud TTS streaming.** OpenAI `tts-1` returns the full audio when the call resolves. ElevenLabs supports streaming. We're picking non-streaming for simplicity — but if cloud-mode latency is bad, streaming TTS is a real next step. Don't bake "synth returns full bytes" so deeply that streaming becomes impossible to add later. The current `synth() -> (bytes, rate)` is fine for non-streaming; if we want streaming, add a parallel `synth_stream() -> Iterator[bytes]` rather than overloading.

5. **`audio_out` no-op in cloud.** A cloud server has no audio device. `play_locally=true` from a misconfigured client must NOT crash the cloud server. The factory sets `audio_out.LOCAL_PLAYBACK_AVAILABLE = (config.MODE == "local")`, and `audio_out.play_pcm()` becomes a guarded no-op when false. The web client already sends `play_locally=false`; the PTT CLI sends `true` — that path now silently no-ops on a cloud server. **Follow-up:** `talk.py` should learn to play `audio_base64` client-side when running against a cloud server. Trivial change, can be done at Phase 9.

6. **Network errors look identical to logic errors.** Currently the `/converse` route handler catches all exceptions and returns 500 with the repr. A Deepgram timeout and a JSON parse bug both return 500. Acceptable for now; a structured error hierarchy is a future cleanup.

### Push-back on user framing

1. **Embedder choice.** The user proposed sentence-transformers. I'd weakly prefer **OpenAI text-embedding-3-small** (already have OpenAI as a TTS dep, no new vendor) — saves ~250 MB of torch in the cloud image and ~30 s of cold-start. BUT the user explicitly chose in-process to avoid extra network round-trips per query, which is reasonable. **Going with the user's choice.** Flagged here in case fat Docker image becomes painful operationally — falling back to OpenAI embeddings is a one-file swap.

2. **"$5–10/mo VPS"** is achievable but tight. With sentence-transformers loaded, we're committed to ~500 MB resident (torch CPU runtime + MiniLM + Python + Flask). $5 plans on most providers are 512 MB RAM, which would OOM. Realistic floor is **$5.70 (Fly 1 GB)** or **$4.59 (Hetzner CPX11, 2 GB)**. Still in the user's range, but plans below 1 GB are out.

3. **Two LLM factory calls could be one.** Both `get_llm("simple")` and `get_llm("complex")` return `ClaudeLLM` instances in cloud mode, just with different model strings. Some readers will look at this and ask "why two factories?" The reason is symmetry with local mode (where they're genuinely two different implementations). Keeping the symmetric API.

4. **Embedder Protocol vs callable.** The router could just take a callable `embed: Callable[[list[str]], np.ndarray]` — even less ceremony than a Protocol. But Protocol gives `warmup()` and `is_loaded()` a home, and `/health` already calls those for the other modules — symmetry wins. Keeping Protocol.

### Things that are genuinely load-bearing in current code

- The `_nvidia_root` DLL bootstrap in `stt.py` MUST run before `from faster_whisper import ...`. The `WhisperLocalSTT` class must preserve this — keep the side-effecting block at module top of `backends/stt_whisper.py`, NOT inside the class init.
- `load_dotenv(REPO_ROOT / ".env", override=True)` — already documented in CLAUDE.md as load-bearing. The `override=True` flag is non-negotiable.
- `127.0.0.1` not `localhost` — applies to all backends that talk to local services. Cloud backends are fine since they're public hostnames.
- The current shape of `/converse` response (`route`, `model`, `reply_text`, `audio_base64`, `timings`, `router_scores`, `llm_tokens`) is consumed by `tests/stage7_e2e.py` and the web client. Refactor must preserve every key.
- Markdown stripping order matters (block-level before inline; LaTeX before bold/italic). Don't reorganize the regex order during the move to `markdown.py`.
- `synth_play_nonblock` returns `(raw, sr, synth_s)` — `app.py:converse` uses all three. Preserve the tuple shape when moving to `audio_out.py`.

---

## Files touched

| Phase | File | Action |
|---|---|---|
| 1 | `server/markdown.py` | new |
| 1 | `server/audio.py` | new |
| 1 | `server/audio_out.py` | new |
| 1 | `server/tts.py` | edit: re-export markdown/audio/audio_out helpers |
| 2 | `server/protocols.py` | new |
| 2 | `server/backends/__init__.py` | new |
| 2 | `server/backends/stt_whisper.py` | from stt.py |
| 2 | `server/backends/llm_ollama.py` | from llm.py |
| 2 | `server/backends/llm_claude.py` | from llm.py |
| 2 | `server/backends/tts_piper.py` | from tts.py |
| 2 | `server/backends/embed_ollama.py` | from router.py |
| 2 | `server/router.py` | edit: accept embedder via attach_embedder |
| 2 | `server/{stt,llm,tts}.py` | edit: become thin shims importing from backends |
| 2 | `tests/stage5_router.py` | edit: attach OllamaEmbedder before warmup |
| 3 | `server/config.py` | edit: add MODE + cloud keys |
| 3 | `server/modes.py` | new |
| 3 | `server/app.py` | edit: use factories + report MODE in /health |
| 3 | `server/audio_out.py` | edit: source synth() via modes.get_tts(); guard on LOCAL_PLAYBACK_AVAILABLE |
| 3 | `server/{stt,llm,tts}.py` | delete (shim files; all imports now via modes/backends) |
| 4 | `.env.example` | edit: add cloud keys with tags |
| 4 | `CLAUDE.md` | edit: document MODE |
| 4 | `requirements-cloud.txt` | new (initial: existing minus local-only deps) |
| 5 | `server/backends/stt_deepgram.py` | new |
| 5 | `tests/cloud_stt_deepgram.py` | new |
| 5 | `requirements-cloud.txt` | edit: add deepgram-sdk |
| 6 | `tests/cloud_llm_haiku.py` | new |
| 7 | `server/backends/tts_openai.py` | new |
| 7 | `tests/cloud_tts_openai.py` | new |
| 7 | `requirements-cloud.txt` | edit: add openai |
| 8 | `server/backends/embed_st.py` | new |
| 8 | `tests/cloud_embed_st.py` | new |
| 8 | `tests/stage5_router.py` | edit: per-mode margin sweep |
| 8 | `tests/stage7_e2e.py` | edit: parameterize model-name assertion via /health.mode |
| 8 | `requirements-cloud.txt` | edit: add sentence-transformers |
| 9 | `Dockerfile` | new |
| 9 | `fly.toml` | new |
| 9 | `notes/deploy-cloud.md` | new |

---

## Verification (end-to-end)

After Phase 9, the following should both pass cleanly from a single checkout:

**Local mode (workstation, GPU, Ollama running):**
```
MODE=local .venv/Scripts/python.exe -m server.app   # terminal 1
.venv/Scripts/python.exe tests/stage4_flask.py      # terminal 2
.venv/Scripts/python.exe tests/stage5_router.py
.venv/Scripts/python.exe tests/stage7_e2e.py
```

**Cloud mode (same machine, no GPU/Ollama use):**
```
MODE=cloud .venv/Scripts/python.exe -m server.app   # terminal 1
.venv/Scripts/python.exe tests/stage4_flask.py      # terminal 2
.venv/Scripts/python.exe tests/stage5_router.py
.venv/Scripts/python.exe tests/stage7_e2e.py
.venv/Scripts/python.exe tests/cloud_stt_deepgram.py
.venv/Scripts/python.exe tests/cloud_tts_openai.py
.venv/Scripts/python.exe tests/cloud_llm_haiku.py
.venv/Scripts/python.exe tests/cloud_embed_st.py
```

**Deployed cloud (Fly.io):**
- Web client at `https://<app>.fly.dev` works end-to-end with API key.
- `talk.py` (with API key in env, `BASE` overridden to public URL) returns transcripts and reply text. Audio playback handled client-side (post-Phase 9 talk.py update).
