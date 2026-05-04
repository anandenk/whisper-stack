# Voice + agent capabilities — plan

## Context

The current product:
- Multi-turn voice conversation with markdown-backed memory (sessions plan
  shipped end-to-end).
- Two-mode backend abstraction (local-mode shipped; cloud-mode partial).
- A push-to-talk + tap-to-talk loop where every interaction is "tap, talk,
  tap, wait 2-12s, hear" — and the assistant can answer from training data
  but can't *do* anything (no real-time info, no actions).

Six features, in dependency-aware order, that take this from "smart parrot
with memory" to "always-listening agent that can actually do things":

1. **Auto-titling sessions** — `sessions/` becomes browsable.
2. **Tool use** — web search + time. Unblocks "what's the weather", "what's
   in the news today", etc. The single biggest practical jump.
3. **Streaming TTS** — assistant starts speaking as it generates. Cuts
   perceived reply latency from "wait then hear" to "hear as it forms."
4. **Client-side VAD** — auto-stop on silence. One less button press.
5. **Continuous + interrupt mode** — assistant finishes speaking, server
   listens again automatically; user can cut off mid-reply by talking over.
6. **Wake word** — `"hey jarvis"`-driven workstation client. Hands-free.

Each milestone leaves the system fully usable. Stop anywhere.

---

## Decisions at a glance

| Decision | Pick | Why |
|---|---|---|
| Web search backend | **Tavily** (free tier 1k/mo, $0.005/query after) | AI-tuned snippets, works with both Sonnet and Llama, easy swap if needed. |
| Tool calling on Ollama | **Auto-escalate "tool-likely" queries to Sonnet** | Llama 3.1 8B's tool calling is unreliable; cheap to bump to Sonnet for the small fraction of queries that need it. |
| Streaming protocol | **Server-Sent Events** at `/converse/stream` | One-way, browser-native, ESP-32-friendly. `/converse` keeps existing JSON contract for back-compat. |
| Continuous-mode state | **Client-side state machine** | Reuses streaming endpoint; server stays stateless-per-request; ESP-32 firmware will be the same shape. |
| Wake-word library | **openWakeWord** | CPU-only, MIT, ships with `"hey jarvis"` pre-trained. |
| Auto-title model | **Claude Haiku always** (regardless of MODE) | Cheap (~$0.00002/title), fast, generates clean titles. |
| Tool loop location | **Inside the LLM backend's `chat()`** | Route handlers stay clean; backend hides the tool_use → tool_result → continue loop. |

---

## Architecture overview

```
                          ┌────────────────────────────────────┐
                          │ Wake word → VAD → /converse/stream │
                          │  (workstation Python client)       │
                          └────────────────────────────────────┘

Web/talk client ─audio─►  /converse/stream  ─SSE─►  Web/talk client
                                  │                       │
                                  ▼                       ▼
                          Existing pipeline:        - text chunks
                          STT → router → LLM        - audio chunks (PCM)
                                  │                  - tool_call events
                                  ▼                  - final summary
                          ┌──────────────┐            - session_file
                          │  Tool loop   │
                          │ (in backend) │ ◄─── server/tools.py registry
                          └──────────────┘            (web_search, get_time)
                                  │
                                  ▼
                          Streaming TTS (Piper / OpenAI)
                                  │
                                  ▼
                          Session append (raw reply only)
```

What the user sees:

- Today: tap, talk, tap, wait, hear full reply, optional tap again.
- After Milestone 5: tap once. Talk. Stop talking → assistant starts replying
  in ~1s, you hear the words appearing as the model generates them. Want to
  interrupt? Just start talking — assistant cuts off, listens to you.
- After Milestone 6: don't tap at all. Say `"hey jarvis"`, talk, get a reply,
  conversation continues until you stop talking long enough.

---

## Milestone 1 — Auto-titling sessions

### What

When a session file's first turn lands, generate a 3-5 word title via Claude
Haiku and rename the file to include the slug. Filename:

```
test-1-20260504T120000Z.md  →  test-1-20260504T120000Z-pasta-help.md
```

The internal `# Session ...` header gains a `title:` line. Lexical sort still
works — the prefix is the timestamp, slug is suffix.

### Implementation

- `server/sessions.py:Session.set_title(title)`: writes `title: <slug>` into
  the file header, renames the path, returns the new path.
- `server/sessions.py:_slugify(text)`: lowercase, alphanumeric + hyphens,
  max 30 chars.
- New helper `server/titler.py`: one function `title_for_turn(user, reply) -> str`.
  Uses `ClaudeLLM(model=ANTHROPIC_HAIKU_MODEL)` with a tight system prompt
  ("Generate a 3-5 word title. Return only the title."). Bypasses MODE
  branching — Haiku is right for this regardless of MODE.
- Hook in `app.py` /chat and /converse: after `session.append_turn()`, if
  `len(session.all_turns()) == 1` and no title yet, call titler synchronously.
  Adds ~400-700ms to turn 1's total time. Hidden inside the existing /converse
  latency.
- Failures are silent. A title is cosmetic; a network blip on Anthropic
  shouldn't 500 the user's first conversation turn.

### Phases

**1A — slug + Session.set_title + parser updates.** Standalone. Update
`tests/stage8_sessions.py` to cover round-trip rename.

**1B — titler module + /chat and /converse hookup.** New `tests/stage8_autotitle.py`
that exercises the synchronous title flow once and asserts the rename happened.

**Pause point.** System is fully usable; titles are a quality-of-life
improvement.

---

## Milestone 2 — Tool use

### What

The model can call functions during a reply. v1 ships two tools:

- **`get_current_time(timezone?)`** — server-side, free, instant.
- **`web_search(query, max_results=5)`** — Tavily API; returns top snippets.

Asking "what's the weather in Chicago" or "what's in the news today" or
"how old is Greta Thunberg" now produces a real, current answer.

### Architecture

```
server/
├── tools/
│   ├── __init__.py        # public API: REGISTRY, register_tool, tool schemas
│   ├── base.py            # Tool dataclass, schema-to-Anthropic + schema-to-Ollama converters
│   ├── time.py            # get_current_time
│   └── search.py          # web_search (Tavily)
└── backends/
    ├── llm_claude.py      # tool loop: messages.create → tool_use → run → tool_result → repeat
    └── llm_ollama.py      # same loop on /api/chat
```

`Tool` dataclass shape:

```python
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict  # JSON Schema
    handler: Callable[[dict], str]  # input → text result
```

The LLM Protocol gains:

```python
def chat(self, message, max_tokens=1024, system_prompt=None,
         history=None, tools: list[Tool] | None = None) -> dict:
    ...
```

Returned `dict` gains:

```python
"tools_used": [{"name": "web_search", "input": {...}, "output_chars": 412}, ...]
```

Per-tool logging so we can see what got called and grep for it.

### Tool loop (inside ClaudeLLM.chat)

```
1. Call messages.create with messages + tools list
2. Inspect response content blocks:
   - All text/no tool_use → return final reply
   - Has tool_use blocks → run each handler, build tool_result content blocks
3. Append assistant message + user-with-tool-result message to messages
4. Loop back to step 1
```

Anthropic and Ollama have similar shapes, just different field names. The
tool-loop logic is duplicated in each backend (small, worth the clarity over
sharing).

Hard cap: **max 5 tool-call rounds per request**. Prevents runaway. Final
round forces text output (`tool_choice="none"` or equivalent) so the model
doesn't loop forever.

### Routing — auto-escalation

`server/router.py` gains `is_tool_likely(query: str) -> bool` — keyword test
on a small list:

```python
TOOL_LIKELY_KEYWORDS = {
    "weather", "temperature", "forecast",
    "news", "headlines", "today", "yesterday",
    "current", "latest", "right now",
    "search", "look up", "find out",
    "time in", "what time",
    "stock", "price of", "score",
}
```

In `/chat` and `/converse`, after the embedding-based classify:

```python
if route == "simple" and is_tool_likely(query):
    route = "complex"  # auto-escalate
    decision["auto_escalated"] = True
```

The decision dict surfaces this so observability is preserved.

### Voice mode behavior

`/converse` runs the tool loop server-side; only the FINAL text is sent to
TTS. User hears silence during tool execution (typically 0.5-2s). Optional
follow-up: a small "thinking" earcon after 800ms — not in v1.

### Configuration

```bash
# [both] tool use
TAVILY_API_KEY=                  # required for web_search; tool silently disabled when unset
TOOL_MAX_ROUNDS=5                # safety cap on tool-call iterations per request
```

When `TAVILY_API_KEY` is unset, `web_search` is removed from the tool list
sent to the model. The model just uses what it has (training data + time).

### Phases

**2A — Tool registry + `get_current_time`.** Just the registry + one trivial
tool. Both backends gain the `tools=` kwarg as no-op when None. Smoke test
via curl /chat.

**2B — Anthropic tool loop.** Implement the messages-create → tool_use →
tool_result → repeat loop in `ClaudeLLM`. Test with `get_current_time`.

**2C — Ollama tool loop.** Same for `OllamaLLM` on /api/chat. Tools format
adapter handles the protocol differences. Test.

**2D — Web search via Tavily.** New `server/tools/search.py`. Add `tavily-python`
dep (or just use raw HTTP). Tests/cloud-style smoke against Tavily.

**2E — Auto-escalation.** Router gains `is_tool_likely`; route handlers
honor it. Test via /chat with various queries.

**2F — Wire into /converse + session storage.** /converse runs the tool loop;
the Turn schema gains an optional `tools_used` metadata line for observability.
Session file format expansion documented in stage8 tests.

**Pause point.** This is the biggest jump in real usefulness. After Milestone 2,
asking "weather in Chicago" works.

---

## Milestone 3 — Streaming TTS

### What

`/converse/stream` route. Same inputs as /converse (audio + optional
session_id + force_route + play_locally). Output is an SSE stream. Client
plays audio chunks as they arrive instead of waiting for the full reply.

### Protocol

SSE event types, in order of arrival:

```
event: ready
data: {"mode": "local", "session_file": "test-1-...md"}

event: transcript
data: {"transcript": "What's the weather in Chicago?", "duration_s": 0.42}

event: route
data: {"route": "complex", "model": "claude-sonnet-4-6", "auto_escalated": true,
       "router_scores": {...}}

event: tool_call           # zero or more, one per call
data: {"name": "web_search", "input": {"query": "Chicago weather"}}

event: tool_result
data: {"name": "web_search", "output_chars": 412, "latency_s": 0.6}

event: text                # one or more, incremental
data: {"delta": "It's currently 64°F in Chicago "}

event: audio               # one or more, base64 PCM frames at announced sample_rate
data: {"sample_rate": 22050, "pcm_b64": "..."}

event: final
data: {"reply_text": "...", "audio_duration_s": 4.2,
       "timings": {...}, "tools_used": [...], "llm_tokens": {...}}
```

Errors: `event: error` with `{"error": "..."}` and connection closes.

### Sentence-boundary chunking

LLM streams text as small deltas (e.g. 5-30 chars each). TTS engines work
better on whole sentences than on partial words. Pipeline:

```
LLM text deltas → sentence buffer → on punctuation+space:
  yield text event for the whole sentence
  feed sentence to TTS → yield audio events for each PCM chunk
```

Boundary regex: `[.!?][\s\n]+|[.!?]$`. Last sentence (no trailing punctuation)
flushes at LLM stream-end.

### LLM Protocol additions

```python
class LLM(Protocol):
    def chat_stream(self, message, max_tokens=1024, system_prompt=None,
                    history=None, tools=None) -> Iterator[StreamEvent]:
        """Yields StreamEvent dicts: {type: "text", delta: str} or
        {type: "tool_call", ...} or {type: "final", ...}.
        Tool loop runs inside; tool_call/tool_result events surface for the route."""
```

For Anthropic: SDK's `messages.stream(...)` already yields events.
For Ollama: `/api/chat` with `"stream": true` returns NDJSON; parse line by line.

### TTS Protocol additions

```python
class TTS(Protocol):
    def synth_stream(self, text_iter: Iterable[str]) -> Iterator[tuple[bytes, int]]:
        """Yields (pcm_chunk, sample_rate) per sentence. Caller pushes complete
        sentences; impl may further chunk audio for low-latency."""
```

Piper: pipe `voice.synthesize(sentence)` per input → already yields chunks.
OpenAI tts-1: `response_format="pcm"` already streams; we wrap.

### Phases

**3A — `/converse/stream` route returning hardcoded SSE events.** Just the
plumbing. No real LLM streaming yet — emit fake text + reuse the existing
non-streaming TTS once. Validates the SSE wiring end-to-end.

**3B — LLM `chat_stream()` for ClaudeLLM.** Real streaming of text deltas.
Tool loop still works inside (yielding tool_call/tool_result events).

**3C — LLM `chat_stream()` for OllamaLLM.**

**3D — Sentence-boundary chunker + TTS `synth_stream` for Piper.** Real audio
streams to the client as sentences complete.

**3E — Web client switches to /converse/stream via EventSource.** Audio
chunks queued through Web Audio API and played in order. The existing
non-streaming flow stays for talk.py.

**Pause point.** Reply latency drops dramatically. talk.py still on the old
endpoint; that's fine.

---

## Milestone 4 — Client-side VAD

### What

Replace "tap to start, tap to stop" with "tap to start, talk, *auto-stops on
silence*."

### Implementation

Two clients to update:

**Web client.** Use Web Audio API to monitor mic peak amplitude. When peak
< threshold for ≥1500ms, automatically call `stopRecording()`. User can
still tap to stop manually (override).

```javascript
// inside the recording loop
const analyser = audioCtx.createAnalyser();
mediaSource.connect(analyser);
const buf = new Uint8Array(analyser.fftSize);
let silenceStart = null;
const checkSilence = () => {
  analyser.getByteTimeDomainData(buf);
  const peak = Math.max(...buf.map(v => Math.abs(v - 128)));
  if (peak < THRESHOLD) {
    if (!silenceStart) silenceStart = performance.now();
    else if (performance.now() - silenceStart > 1500) stopRecording();
  } else { silenceStart = null; }
  if (mediaRecorder.state === 'recording') requestAnimationFrame(checkSilence);
};
```

**talk.py.** Same idea but in Python. Monitor `sd.InputStream` callback
data, track peak per chunk, auto-set `stop` event after the silence threshold.

Threshold: `VAD_PEAK_THRESHOLD=600` (int16 abs), `VAD_SILENCE_MS=1500`.
Both env-tunable.

### Phases

**4A — Web client peak-threshold VAD.** Auto-stop on silence; manual tap
still works. New row in the UI: "VAD: on/off" toggle that defaults to on.

**4B — talk.py peak-threshold VAD.** Same logic, persisted via
`VOICE_AI_VAD=1` env var (default on).

**Pause point.** Conversations feel snappier — no more "did I tap stop yet?"

---

## Milestone 5 — Continuous + interrupt mode

### What

After the assistant finishes speaking, automatically start listening again.
While the assistant is speaking, if the user starts talking, abort the
current reply (stop audio playback + abort the in-flight fetch + tear down
the SSE connection) and immediately switch to listening mode for the new
utterance.

### State machine (web client)

```
       ┌──────────────────────────────────────────────────────────────┐
       │                                                              │
       ▼                                                              │
    IDLE ──user enables continuous──► LISTENING ──VAD silence──► THINKING
       ▲                                  ▲                         │
       │                                  │ (audio playback ends)   │
       │                                  │                         ▼
       │                            SPEAKING ◄───first audio─── (waiting)
       │                                  │                         
       │                                  │ ◄── user talks (VAD detects voice)
       └──── user disables continuous ────┘     INTERRUPT:
                                                  - audio.pause()
                                                  - abort fetch
                                                  - go to LISTENING
                                                  - the new utterance starts
                                                    a fresh /converse/stream
```

### Interrupt detection

While the assistant is speaking, the mic is STILL OPEN, and VAD is checking
for voice (peak > threshold). Browser `getUserMedia({ echoCancellation: true })`
prevents the assistant's own voice from triggering VAD in most cases. On
detection: stop playback, abort fetch, transition.

### Implementation

Web client gains a top-level state variable + a "Continuous mode" toggle. The
existing tap-to-talk button becomes "Start conversation" / "Stop conversation"
when continuous is on.

Key audio object handles:
- `currentAudio` — the currently-playing audio element. `currentAudio.pause()`
  on interrupt.
- `currentAbort` — `AbortController` passed to the EventSource fetch. `.abort()`
  on interrupt.

talk.py: similar in Python. More involved (threading), but mechanically
the same. v1 ships continuous mode in the web client only; talk.py optional
later.

### Phases

**5A — Web client continuous loop, no interrupt.** Plays reply, then
auto-starts a new recording. User can disable to return to single-turn.

**5B — Interrupt detection.** While SPEAKING, monitor VAD; on voice detected,
abort playback + fetch and transition to LISTENING with the new utterance.

**Pause point.** This is the "ChatGPT Voice" feel. No buttons during a
conversation. tap once at the start, talk freely.

---

## Milestone 6 — Wake word

### What

A new workstation-only client `tests/jarvis.py` that listens passively for
`"hey jarvis"`, then runs the same continuous-mode flow. Always-on assistant
without any tap.

### Implementation

```
openWakeWord ──audio chunks──► detection score per frame
                                   │
                                   ▼  (when >threshold)
                              chime.wav (small confirm sound)
                                   │
                                   ▼
                              VAD-bounded recording
                                   │
                                   ▼
                              POST /converse/stream
                                   │
                                   ▼
                              play reply
                                   │
                                   ▼
                              continuous mode loop
                                   │
                                   ▼
                              (after 30s of silence)
                                   │
                                   ▼
                              return to wake-word listening
```

`openwakeword` ships several pre-trained wake words including `hey_jarvis`.
CPU-only inference, ~50ms per frame.

### Configuration

```bash
WAKEWORD_MODEL=hey_jarvis           # bundled model name
WAKEWORD_THRESHOLD=0.5              # 0..1; lower = more sensitive
WAKEWORD_CHIME=samples/chime.wav    # plays on detection; optional
WAKEWORD_CONVERSATION_TIMEOUT_S=30  # idle in continuous mode → back to listening
```

### Phases

**6A — `tests/jarvis.py` v1: wake-word detection + single-turn.**
Detects `"hey jarvis"`, plays chime, records one VAD-bounded utterance,
plays reply, returns to wake-word listening. Single-turn per wake.

**6B — Continuous mode after wake word.** After the reply, stay in
continuous mode for `WAKEWORD_CONVERSATION_TIMEOUT_S` seconds of silence,
then return to wake-word listening.

**Final pause point.** Workstation is a hands-free assistant. Wake-word
client is a prototype for the eventual ESP-32 firmware behavior.

---

## Risks and pushback

### Real

1. **Tool calls fail or hallucinate args.** Llama 3.1 8B in particular sometimes
   produces malformed tool_use blocks. Defensive parsing: catch JSON errors,
   skip the malformed call, let the model re-try in the next loop iteration.
   If it loops 5 times without producing a final text reply, return the last
   intermediate text (or a fixed apology).

2. **Web search costs scale with adoption.** Tavily free tier is 1k queries/mo.
   Solo use is well under that. If the assistant develops a habit of always
   searching, instrument with daily counters and fall back to model-only
   responses past a threshold. Not in v1.

3. **Echo cancellation might not be perfect on continuous mode.** If the
   browser's echo cancellation fails, the assistant's TTS output triggers
   the mic VAD, which interrupts the assistant's own reply. Mitigation:
   require `echoCancellation: true` in getUserMedia constraints (most
   browsers default this on already). If it bites: add a short post-audio
   "cooldown" window where VAD is suppressed (~200ms after each played
   audio chunk).

4. **Streaming + tool use interact.** During a tool call, the LLM's text
   stream pauses. The client has to handle "no audio for 2 seconds" gracefully
   — show a "thinking..." indicator, don't time out. SSE keeps the connection
   open, so the connection itself isn't an issue; just UX.

5. **Wake word false positives.** `"hey jarvis"` is rare in normal speech
   but household sounds occasionally cross threshold. Setting threshold too
   high → wake word never triggers. Default 0.5 is openWakeWord's recommended
   starting point; tune per-environment.

6. **Sentence chunking truncates code blocks.** Markdown code fences span
   sentences. If the LLM produces `"Here's the code: \`\`\`python\ndef foo():\n    pass\n\`\`\`"`,
   sentence boundaries inside the code block become wrong. For voice replies
   this is fine (the system prompt says no code/markdown), but tool-rich
   replies sometimes leak. Mitigation: skip TTS on text inside fenced code
   blocks; emit it as a `text` SSE event for clients to display but not speak.

7. **Auto-escalation keyword list will get tweaked.** Some "tool-likely"
   queries don't match the keyword list ("Greta Thunberg's age", "the new
   iPhone"). Some matching queries don't actually need tools ("what's the
   meaning of news?"). Acceptable failure modes. Iterate the list as
   patterns emerge.

8. **Filename rename + concurrent read.** Auto-titling renames the session
   file shortly after turn 1 lands. If turn 2 arrives during the rename
   window, the SessionStore glob may briefly miss the file. Synchronous
   titling before /converse returns the response side-steps this — the
   user has the reply text before turn 2 could possibly land.

9. **Rate-limiting Anthropic during the tool loop.** Each tool round =
   one Anthropic call. With cap=5 and average ~2 rounds for a search query,
   a "what's the weather" voice query is 2-3 Anthropic calls. Cost ~$0.005.
   Anthropic's per-minute rate limit is well above this; not a concern at
   solo-user scale.

### Pushback on alternative framings

1. **"Why not use Anthropic's built-in web search tool?"** Considered. It's
   Sonnet/Haiku-only, won't work in local mode. Tavily works for both.
   Easy to swap to Anthropic's tool later if it becomes attractive (drop a
   tool implementation, change the registry).

2. **"Why server-side state for continuous mode would be cleaner."** It would.
   But ESP-32 firmware is going to be a client-side state machine regardless
   (a microcontroller with audio I/O). Building the web client as a
   client-side state machine first means the same logic translates to
   firmware. Server-side state is a future refactor if multi-user ever
   matters (it doesn't here).

3. **"Why not skip auto-titling and just rely on the timestamp?"** You can.
   It's the smallest, lowest-risk, lowest-value of the six. We do it first
   precisely because it's a cheap warm-up that exercises the
   "lightweight Anthropic-Haiku side calls" pattern that auto-escalation
   and tool-thinking-spinners may also use later.

4. **"Why not stream TTS in talk.py too?"** Could. Adds ~50 lines of
   sounddevice queue management. The workstation hears the reply either
   way, so streaming there is purely a "how soon does playback start"
   improvement. v1 = web only. Backport if it turns out to matter.

---

## Out of scope

- **Custom wake word training.** openWakeWord supports it but training a
  better-than-default model is a project. v1 ships the bundled `hey_jarvis`.
- **Multilingual.** Whisper handles many languages; the system prompt and
  tools (Tavily included) are English-only here.
- **Persistent tool memory.** Tools are stateless; "remind me in 5 minutes"
  would need a scheduler. Could add later as a `set_timer` tool plus a
  background watcher; not v1.
- **Vision tools.** Sonnet supports image input. Phone could send a photo +
  voice query. Real product opportunity, separate plan.
- **Per-tool rate limiting.** All tools share a global per-conversation
  cap of 5 rounds. Per-tool throttles are a Phase 9-style concern.
- **Streaming STT.** Whisper-streaming exists but adds significant complexity.
  Current STT is ~0.5s warm; not the bottleneck.
- **Cost telemetry / dashboards.** Per-request logs already include
  input/output tokens. A real dashboard is its own project.

---

## Files touched

| Milestone | Phase | File | Action |
|---|---|---|---|
| 1 | A | server/sessions.py | edit: set_title + _slugify + parse `title:` header |
| 1 | A | tests/stage8_sessions.py | edit: title round-trip test |
| 1 | B | server/titler.py | new |
| 1 | B | server/app.py | edit: post-first-turn title hook in /chat and /converse |
| 1 | B | tests/stage8_autotitle.py | new |
| 2 | A | server/tools/__init__.py | new (registry) |
| 2 | A | server/tools/base.py | new (Tool dataclass + schema converters) |
| 2 | A | server/tools/time.py | new |
| 2 | A | server/protocols.py | edit: LLM.chat tools= kwarg |
| 2 | B | server/backends/llm_claude.py | edit: tool loop |
| 2 | B | tests/stage9_tools_claude.py | new |
| 2 | C | server/backends/llm_ollama.py | edit: tool loop on /api/chat |
| 2 | C | tests/stage9_tools_ollama.py | new |
| 2 | D | server/tools/search.py | new (Tavily) |
| 2 | D | server/config.py | edit: TAVILY_API_KEY + TOOL_MAX_ROUNDS |
| 2 | D | requirements-cloud.txt | edit: add `tavily-python` (or `requests`-only impl) |
| 2 | D | .env.example | edit: TAVILY_API_KEY |
| 2 | D | tests/cloud_tools_search.py | new |
| 2 | E | server/router.py | edit: is_tool_likely + TOOL_LIKELY_KEYWORDS |
| 2 | E | server/app.py | edit: auto-escalation in /chat + /converse |
| 2 | F | server/sessions.py | edit: Turn schema gains tools_used |
| 2 | F | server/app.py | edit: thread tools through /converse |
| 3 | A | server/app.py | new /converse/stream route (SSE skeleton) |
| 3 | B | server/protocols.py | edit: LLM.chat_stream |
| 3 | B | server/backends/llm_claude.py | edit: stream impl |
| 3 | C | server/backends/llm_ollama.py | edit: stream impl |
| 3 | D | server/protocols.py | edit: TTS.synth_stream |
| 3 | D | server/backends/tts_piper.py | edit: synth_stream |
| 3 | D | server/streaming.py | new (sentence chunker + audio pipeline) |
| 3 | E | server/web/index.html | edit: EventSource + Web Audio queue |
| 4 | A | server/web/index.html | edit: peak-threshold VAD on mic |
| 4 | B | tests/talk.py | edit: VAD auto-stop |
| 5 | A | server/web/index.html | edit: continuous-mode state machine |
| 5 | B | server/web/index.html | edit: interrupt detection during SPEAKING |
| 6 | A | tests/jarvis.py | new |
| 6 | A | requirements-cloud.txt + local install notes | edit: openwakeword + chime sample |
| 6 | A | server/config.py | edit: WAKEWORD_* env vars |
| 6 | B | tests/jarvis.py | edit: continuous-mode loop |
| - | - | CLAUDE.md | edit: Plans index updated, MODE table maybe gains tools row |
| - | - | notes/voice-agent-plan.md | this file |

---

## Verification at the end of each milestone

| Milestone | Verification |
|---|---|
| 1 | `cat sessions/<id>-*.md` shows a `title: <slug>` header on new sessions; filename has the slug suffix. |
| 2 | `curl /chat -d '{"message":"what time is it"}'` returns a real time. With `TAVILY_API_KEY` set: "what's the weather in Chicago" returns current weather, response includes `tools_used: [{"name":"web_search",...}]`. |
| 3 | Web client: tap, talk, tap → first audio chunk plays within ~1s of LLM start (vs ~3s today for full reply). SSE event log shows interleaved text/audio events. |
| 4 | Tap to start, talk, *don't* tap stop. After 1.5s of silence, request auto-fires. |
| 5 | Continuous toggle on. Tap once, full conversation flows without further taps. Talking over the assistant cuts it off. |
| 6 | `python tests/jarvis.py`. Say "hey jarvis, what's the weather". Hear chime, then reply. Continues for 30s of silence, then back to wake-word listening. |
