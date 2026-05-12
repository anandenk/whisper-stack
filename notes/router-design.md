# Router design — stage 5a

> **⚠ SUPERSEDED — May 2026.** The two-LLM routing layer was retired
> during the Gemini migration. The codebase now sends every query to
> a single LLM (Gemini 2.5 Flash). This doc and the Tier 1 history it
> describes are kept as a historical record of the routing approach
> that preceded the migration. See `CLAUDE.md` § "Stack" for the
> current shape and § "Ollama resurrection" if you ever need the
> pre-migration backends + router back.

**Goal:** Given a transcript, decide whether to send it to local Llama 3.1 8B (`simple`) or to Claude (`complex`). Wrong routes have **asymmetric cost**:

- `complex → simple` (under-routed): Llama gives a weak answer to a question it can't handle. **High user-visible cost.**
- `simple → complex`: Claude answers a trivial question. Wastes a fraction of a cent. **Low cost.**

The router should therefore **bias toward `complex` on uncertainty**.

---

## Class taxonomy (this is the most important decision)

Router quality is bounded by class definition quality. Embedding examples / keyword lists / classifier prompts all reduce to: "what do we mean by simple?"

### `simple` — keep it local
- Factual lookups: *"capital of France"*, *"who wrote Hamlet"*
- Casual chat / pleasantries: *"how's it going"*, *"good morning"*, *"tell me a joke"*
- Definitions: *"what does aphorism mean"*
- Trivial arithmetic: *"what's 23 plus 47"*
- Short lists: *"name three planets"*
- Simple commands the LLM doesn't need to reason about: *"convert 5 miles to km"*

### `complex` — send to Claude
- Code: *"write a Python function that flattens a nested dict"*
- Multi-step reasoning: *"compare the tradeoffs between SQLite and Postgres for X"*
- Writing tasks: *"draft a polite reply to my landlord about the leak"*
- Creative open-ended: *"a short story about a lighthouse keeper"*
- Domain expertise / nuanced explanation: *"why does Kubernetes use etcd specifically"*
- Long inputs (>~30 words) — even simple intent often gets nuance from length
- **Default for borderline / unclear**

This taxonomy is editable later. Stage 5 is about **wiring the mechanism**, not nailing the perfect line.

---

## Latency budget

Where the router sits in `/converse` (stage 7):

| Stage | Latency |
|---|---|
| STT (whisper, warm) | ~0.4s |
| **Router** | **? (this doc)** |
| Simple → Ollama generate | ~2–4s |
| Complex → Claude API | ~1–2s |
| TTS (piper) + audio I/O | ~0.5–1s |
| **Total target** | **~3–6s** |

So a router that costs 30–100ms is **rounding error** vs the LLM stage. A router that costs 300ms+ starts being noticeable on the simple path.

---

## Approaches

### A. Keyword / regex heuristics
- **How:** Hand-rolled patterns: *if query starts with "write code" → complex; "what is the capital of" → simple*; etc.
- **Latency:** <1ms.
- **Cost to set up:** Low.
- **Maintenance:** High. Brittle. Every paraphrase you didn't anticipate is a miss.
- **Accuracy ceiling:** ~75%.
- **Failure mode:** *"How would I write a function that…"* → starts with "how would" (looks open-ended) but is a code task. Patterns miss intent.

### B. Embedding similarity (recommended)
- **How:**
  1. Curate ~10–15 example queries per class.
  2. At server startup, embed every example via `nomic-embed-text` (already pulled). Cache the vectors.
  3. Per query: embed once → cosine-similarity vs every cached example → take **max similarity per class** → pick the higher class. Apply an **ambiguity margin** so close calls go to `complex`.
- **Latency:** One embedding call per request. nomic-embed on this GPU should be ~30–60ms warm. Cosine similarity over 30 vectors is microseconds.
- **Cost to set up:** Medium (curate examples, write the cache + similarity function).
- **Maintenance:** Low. To re-tune, edit the example list and restart the server.
- **Accuracy ceiling:** ~85–90%, in the literature for two-class intent routing with curated examples.
- **Failure mode:** Borderline queries flipped by example bias. Mitigated by the ambiguity margin (route ambiguous → `complex`).

### C. Ollama self-classify
- **How:** Send a tiny prompt to Llama: *"Reply with one word, simple or complex: <query>"* with `num_predict=1`.
- **Latency:** ~100–300ms minimum. The 1-token output is fast (~10ms), but model context setup + queueing isn't free, and you pay Ollama load contention every request.
- **Cost to set up:** Low.
- **Maintenance:** Low.
- **Accuracy ceiling:** ~90% — LLMs are decent at this.
- **Failure mode:** **Adds Ollama latency to *every* request, including ones we'd then route to Claude.** Defeats the point of the local-fast path. Also: when Ollama is mid-generation on a previous simple query, the next classify call queues behind it.

### D. Hybrid (keyword short-circuit → embedding fallback)
- **How:** Run a small set of unambiguous regex rules first (*"write a function" → complex*, *"what is the capital of" → simple*). If none match, fall through to approach B.
- **Latency:** ~5ms when keyword hits, ~30–60ms when it doesn't.
- **Cost to set up:** Medium (curate keywords AND examples — strictly more work than B).
- **Maintenance:** Medium. Two code paths.
- **Accuracy ceiling:** ~88–92% if both paths agree on philosophy.
- **Failure mode:** Subtle bugs at the seam — a keyword you thought was unambiguous turns out to have edge cases the embedding path would have caught.

---

## Recommendation: **B (embedding similarity)**

Reasoning:

1. **Latency budget has room.** 30–60ms in a 3–6s pipeline is invisible. There's no need to optimize for the keyword shortcut path that D adds.
2. **Already have the dep.** `nomic-embed-text` is pulled. Zero new infrastructure.
3. **Cheapest to tune.** Adjusting an embedding example list is editing a Python list. Adjusting keyword regex is whack-a-mole.
4. **Avoids C's biggest issue:** classification doesn't share the LLM with generation, so it doesn't queue behind whatever Llama is currently doing. (Different Ollama model — nomic-embed-text — and tiny: ~274 MB, fits alongside Llama in VRAM with current ~3.5 GB headroom.)
5. **D buys nothing here.** The latency win it offers is only meaningful if embedding ran into hundreds of milliseconds, which it doesn't on this hardware.

---

## Concrete implementation sketch (if you pick B)

**`server/router.py`** — public API: `classify(query: str) -> Literal["simple", "complex"]` plus a `warmup()` for startup.

**Module-level state:**
- `_simple_examples: list[str]` (~12 entries spanning factual / chat / definitions / trivial-math)
- `_complex_examples: list[str]` (~12 entries spanning code / reasoning / writing / nuance)
- `_simple_vecs: np.ndarray` (n_simple × dim) — populated at warmup
- `_complex_vecs: np.ndarray` (n_complex × dim)

**Embedding source:** `POST http://localhost:11434/api/embed` with `{"model": "nomic-embed-text", "input": query}` → returns `embeddings: [[...]]`. ~768-dim float vector.

**Decision rule:**
```
q  = embed(query)                         # one Ollama call
s_simple  = max(cos(q, v) for v in _simple_vecs)
s_complex = max(cos(q, v) for v in _complex_vecs)

if s_complex - s_simple > MARGIN:    return "complex"   # MARGIN ≈ 0.0 — i.e. complex wins ties
if s_simple  - s_complex > MARGIN_S: return "simple"    # MARGIN_S ≈ 0.05 — simple needs to clearly win
return "complex"                                          # ambiguous → complex (asymmetric cost)
```

`MARGIN_S` is the **safety margin**: simple wins only when it clearly wins. This encodes the asymmetric error cost from the top of the doc as one knob.

**Wiring into `/chat`:**
- Add `route` field to the response: `{reply, model, route, latency_s, ...}` so we can see what the router decided per request.
- For `complex`: existing Claude path.
- For `simple`: new Ollama generate call (similar shape to `chat_claude`, just hits the local daemon).

**Warmup:**
- Embed all examples at server boot (alongside `stt.warmup()`). One Ollama embed call returns batch embeddings if we send all examples in a list — that's one round trip, not 24.
- Plus one dummy classify call to make sure nomic-embed-text is loaded and warm in Ollama.

**Test (`tests/stage5_router.py`):**
- Fixture of ~30 labeled queries (mix of clear-simple, clear-complex, borderline).
- Reports overall accuracy, per-class precision/recall, confusion matrix, mean classify latency.
- Also asserts the **decision rule does not crash** on edge inputs (empty string, very long string, non-English, all-emoji).

---

## Open questions for you (stage 5a gate)

1. **Approach: B as recommended, or do you want to argue for D / something else?**
2. **Which Claude model on the `complex` route?** Currently `claude-sonnet-4-6`. We could move to `claude-opus-4-7` for max quality at higher cost+latency, or stay on Sonnet. (No-op for stage 5 mechanics — just sets `ANTHROPIC_MODEL`.)
3. **Safety margin tuning:** start with `MARGIN_S = 0.05` and the 30-query fixture as the calibration target? Or do you want a bigger labeled set first?
4. **Should the router decision be overridable?** e.g., `/chat` accepts an optional `force_route: "simple" | "complex"` for debugging / fallback. Cheap to add, useful when iterating, but adds API surface.
5. **Multi-turn context:** the plan keeps `/chat` and `/converse` stateless for now. Confirming we're routing on the **single current message**, not on conversation history.
