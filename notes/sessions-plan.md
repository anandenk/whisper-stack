# Multi-turn session memory — plan

## Context

`/converse` and `/chat` are stateless today. Every request is independent;
the LLM has no idea what was said 30 seconds ago. The product is a future
ESP-32 voice device, and a voice assistant that can't carry a thread for
two utterances is a noticeably worse product.

This plan adds **server-side conversation memory** so a follow-up like
"and what's its population?" resolves against "France" from the prior turn.
The Anthropic API and Ollama's `/api/chat` endpoint both speak `messages`
arrays natively — the model isn't the problem. State management is.

Single user, single primary client (ESP-32). No multi-tenancy, no DB,
no auth-derived identity. The mechanism is dirt-simple and stays that way.

---

## Decisions at a glance

| Decision | Pick | Why |
|---|---|---|
| Identity | Client-supplied `session_id`, opt-in. ESP-32 hardcodes a constant; dev clients ephemeral by default. | One device in production. No client-side UUID generation needed. |
| Session boundary | 10 min idle → new session file. Lazy check at request time, no daemon. | Voice conversations naturally end; idle gap = new conversation. |
| History window | Last 30 turns inside an active session. | Idle reset already caps real conversations; window is a safety belt. |
| Storage | One markdown file per session at `sessions/<stream>-<ISO-timestamp>.md`. | Human-readable, greppable, no schema, trivially backed up, Obsidian-importable. |
| Protocol change | Add optional `history: list[dict] \| None` kwarg to `LLM.chat()`. Backward compatible. | Existing one-shot calls keep working. |
| Ollama endpoint | Switch from `/api/generate` (single prompt) to `/api/chat` (messages). | Native multi-turn shape; matches Anthropic's. |
| Routes wired | Both `/converse` and `/chat`. | `/chat` lets you reproduce voice bugs over `curl`. |
| Server timer | None. Idle is checked lazily on each request. | Server can be off for 3 days; the "new session" trigger fires on the next request. |

---

## Identity model

The server distinguishes a **stream** (the device that's talking — stable
identity) from a **session** (one conversation thread inside that stream —
rolls over on idle).

- ESP-32 firmware hardcodes `session_id=esp32-main` and sends it on every
  `/converse` request. That string is the stream ID. Burned in firmware,
  never changes.
- Dev clients (`talk.py`, web page) default to **no session_id at all**.
  Every request is ephemeral, no memory loaded, no turn appended. Existing
  behavior preserved exactly.
- To exercise memory from a dev client, opt in by setting
  `VOICE_AI_SESSION_ID=test1` (talk.py) or typing into the localStorage-backed
  "Session ID" field on the web page. Server treats `test1` as a separate
  stream from `esp32-main` — dev sessions never collide with real ones.

The client doesn't track session-rollover state. It always sends the same
stream ID; the server figures out which underlying file is currently active.

---

## Session boundary (the lazy idle check)

On every request that includes a `session_id`:

1. `glob("sessions/<session_id>-*.md")`, sort by filename (ISO timestamps
   sort lexically), take the most recent.
2. Read its last-turn timestamp from inside the file (NOT mtime — mtime
   gets clobbered by backups, rsync, git checkouts).
3. If `now - last_turn_timestamp <= SESSION_IDLE_TIMEOUT_MIN`: append to it.
4. Otherwise: create a new file `sessions/<session_id>-<now-iso>.md` and
   append the first turn there.

No background timer. No daemon. The "10 minutes elapsed → new session"
rule fires when a request arrives, not when 10 minutes pass. Server can
be off, the workstation can be asleep, doesn't matter.

`SESSION_IDLE_TIMEOUT_MIN` defaults to 10. Override in `.env` per deploy.

The server includes the active session filename in the `/converse` and
`/chat` JSON response so you can grep for it later.

---

## Storage format

One markdown file per session. ISO timestamp in the filename so files
sort by creation order.

**Filename:** `sessions/<stream_id>-<YYYYMMDDTHHMMSSZ>.md`
(no colons — Windows filesystems reject them.)

**File contents:**

```markdown
# Session esp32-main-20260503T194523Z
created: 2026-05-03T19:45:23Z
last_turn: 2026-05-03T19:46:12Z

## turn 1 — 2026-05-03T19:45:23Z
**user:** What's the capital of France?
**assistant** route=simple model=llama3.1:8b-instruct-q5_K_M latency=1.2s in=12 out=8
The capital of France is Paris.

## turn 2 — 2026-05-03T19:46:12Z
**user:** And its population?
**assistant** route=complex model=claude-sonnet-4-6 latency=2.1s in=47 out=64
Paris has approximately 2.1 million in the city proper, ~12 million metro.
```

The `last_turn:` line at the top is updated in place each time a turn lands.
That's the field the idle check reads — single line lookup, no full parse.

The `**assistant** route=... model=... latency=... in=... out=...` line
is observability metadata. Body of each turn is everything between that
line and the next `## turn` header (or EOF).

Parser is hand-written, tolerant. ~50 lines. Doesn't try to validate
structure — just splits on `## turn` and pulls the user/assistant blocks.

`sessions/` goes in `.gitignore`. These are personal conversations.

---

## Window inside a session

After loading the active file, take the **last 30 turns** (each turn = one
user message + one assistant reply). Build the messages array as:

```python
[
    {"role": "user", "content": turn[-30].user_text},
    {"role": "assistant", "content": turn[-30].reply_text},
    ...
    {"role": "user", "content": turn[-1].user_text},
    {"role": "assistant", "content": turn[-1].reply_text},
    {"role": "user", "content": current_user_text},  # the new request
]
```

Pass to the LLM. Response goes back, gets appended to the file as turn N+1.

`SESSION_WINDOW_TURNS` env var, default 30. With idle reset doing the real
work, you'll rarely hit this; it's protection against runaway exchanges.

---

## LLM Protocol evolution

`server/protocols.py`:

```python
class LLM(Protocol):
    def chat(self, message: str, max_tokens: int = 1024,
             system_prompt: str | None = None,
             history: list[dict] | None = None) -> dict:
        """history is [{role, content}, ...] in chronological order, NOT
        including the current user message. None = stateless one-shot."""
```

Backward compatible: `history=None` is the default and behaves identically
to today.

**`ClaudeLLM`**: prepend history to `messages` before the new user turn.
One-line change.

**`OllamaLLM`**: switch from `/api/generate` (single prompt) to `/api/chat`
(messages array). Slight response-shape adjustment (`message.content` instead
of `response`). Verify TPS metrics still come back the same way.

---

## Route handler changes

`/chat` and `/converse` both gain an optional `session_id`:

- `/chat`: JSON field.
- `/converse`: form field, alongside the existing `force_route` and `play_locally`.

Handler flow when `session_id` is present:

```python
store = SessionStore("sessions/")
session = store.active_or_new(session_id)        # lazy idle check
history = session.recent_window(SESSION_WINDOW_TURNS)
llm_result = modes.get_llm(route).chat(
    user_message,
    history=history,
    max_tokens=...,
    system_prompt=config.VOICE_SYSTEM_PROMPT,
)
session.append_turn(user_message, llm_result)
# ...response includes "session_file": session.path.name
```

When `session_id` is absent: skip all of the above, behave like today.
Test stage4/5/7 keep passing without changes.

---

## Configuration surface

Three new env vars, all optional with sensible defaults:

```bash
# [both] session memory
SESSION_IDLE_TIMEOUT_MIN=10        # idle gap that triggers a new session file
SESSION_WINDOW_TURNS=30            # max turns of history sent to the LLM
SESSION_DIR=sessions               # where to store .md files (relative to repo root)
```

Plus an optional client-side env var for `talk.py`:

```bash
VOICE_AI_SESSION_ID=               # if set, talk.py sends this on every request
```

---

## Phasing

Each phase is one logical commit. Local-mode tests must pass at every commit.

### Phase A — `sessions.py` + storage, no app wiring

- `server/sessions.py`: `SessionStore`, `Session`, `Turn` dataclasses.
  Methods: `active_or_new(stream_id) -> Session`, `Session.recent_window(n)`,
  `Session.append_turn(user, llm_result)`.
- `tests/stage8_sessions.py`: write a turn, append another, verify file
  contents, simulate a 1-second idle timeout (`SESSION_IDLE_TIMEOUT_MIN`
  override), verify a new file is created on the next call.
- No changes to `app.py`, `protocols.py`, or backends yet.

**Gate.**

### Phase B — extend `LLM` protocol with `history`

- `server/protocols.py`: add `history` kwarg to `chat()`.
- `server/backends/llm_claude.py`: prepend `history` to `messages`.
- `server/backends/llm_ollama.py`: switch `/api/generate` → `/api/chat`,
  pass `messages` array. Update response parsing.
- Re-run `stage2_ollama.py`-style smoke (or add a `tests/stage8_llm_history.py`)
  to confirm both backends produce coherent multi-turn replies.
- All existing tests (where `history=None`) must still pass unchanged.

**Gate.**

### Phase C — wire `/chat`

- Optional `session_id` JSON field.
- Test by curling three sequential `/chat` requests with `"session_id": "test"`:
  1. `"My name is Anand."` → reply.
  2. `"What's my name?"` → reply must contain "Anand".
  3. Inspect `sessions/test-*.md` to confirm the turns landed.
- `stage4_flask.py` adds a "with session_id" probe alongside the existing
  stateless probe.

**Gate.**

### Phase D — wire `/converse`

- Optional `session_id` form field.
- Update `talk.py`: read `VOICE_AI_SESSION_ID` env var, send as form field
  if set. Print active session filename from the response so the user can
  grep for it.
- Update `server/web/index.html`: add a Session ID text field next to the
  API key, persist to localStorage, send as form field if non-empty.
- `stage7_e2e.py` gains a multi-turn probe: send "France's capital?", then
  "And its population?" with the same session_id; assert the second reply
  references Paris/France.

**Gate.**

### Phase E — TTL / cleanup (optional)

- A `SESSION_TTL_DAYS` env var (default unset = no cleanup).
- A `server/sessions.py:prune_older_than(days)` function.
- Called from `app.py:main()` once at startup. No background daemon.
- Gate on whether you actually want this; the disk cost is trivial for
  one user. Probably skip and revisit if `sessions/` ever grows past your
  comfort level.

---

## Test strategy

Three new tests, no new test framework:

| Test | Coverage |
|---|---|
| `tests/stage8_sessions.py` | Phase A: file IO, idle rollover (with override timeout), window slicing. Pure Python, no server. |
| `tests/stage8_llm_history.py` | Phase B: each LLM backend produces a coherent reply when given a 2-turn history. Hits Ollama + Anthropic. ~$0.001/run. |
| `tests/stage8_e2e_session.py` | Phase D: multi-turn `/converse` against the running server with a session_id. Asserts memory works end-to-end and that the right file gets written. |

`stage4_flask.py` and `stage7_e2e.py` get small additions for the
session-aware paths, but their existing stateless probes stay intact.

---

## Risks and pushback

### Real

1. **STT errors compound.** If turn 1's transcript is garbled
   ("what's the capital of brance"), the model carries the garble into
   turn 2's context. Don't try to "fix" past turns — store the raw
   transcript. The user clarifies in the next turn; that's the correction
   mechanism.

2. **System prompt + history interact.** The voice prompt says "no markdown."
   If a past assistant reply *did* slip markdown in, feeding it back as
   context could nudge the next reply toward markdown too. Mitigation:
   the system prompt re-asserts on every request, and `to_speakable_text`
   strips before TTS regardless. Probably fine, worth measuring once memory
   is live.

3. **Ollama `/api/chat` migration.** Different response shape than
   `/api/generate`. Need to re-verify TPS reporting (`eval_count` /
   `eval_duration` should still be there but in a slightly different
   spot). Re-run `stage2_ollama.py`-equivalent after Phase B.

4. **Idle cliff is hard, not soft.** Sit quiet for 10:00.01 → fresh
   session, no memory of the prior thread. Predictable but occasionally
   annoying. Mitigate by raising `SESSION_IDLE_TIMEOUT_MIN` if it bites
   in practice. Don't add a "soft fade" — that's the kind of magic that
   confuses you when you can't predict it.

5. **mtime is not the timestamp source.** Reading file mtime would be
   easier but it's wrong: backups, git operations, rsync, antivirus
   scans, and the Windows filesystem itself can rewrite mtime. Always
   parse the `last_turn:` line from inside the file. One-line read.

6. **Cloud-mode persistence.** When MODE=cloud lands on Fly.io (Phase 9
   of the two-mode plan), `sessions/` lives on the VM's ephemeral disk.
   A redeploy wipes it. Fly.io volumes are the answer; document in
   the deploy notes when Phase 9 lands. Not a concern for local mode.

### Pushback on alternative framings

1. **"Why not summarize older context instead of dropping it?"** A
   summarizer that runs on every Nth turn is one extra API call's worth
   of latency and cost per N turns, and the summary can drop the one
   detail that mattered. With voice replies capped at 96 tokens and
   real conversations rarely exceeding 10 turns, the last-30 window
   already holds a generous superset of what's useful. Revisit if you
   ever do extended exchanges where the model demonstrably forgets
   something it should have kept.

2. **"Why not SQLite / JSON?"** SQLite is overkill for one user with
   sequential writes. JSON gives you programmatic access at the cost of
   readability — but the primary "consumer" of these files when something
   goes weird is a human with `cat` and `grep`, not a tool. Markdown wins
   on the only axis that matters here.

3. **"Why not auto-title sessions?"** Asking Sonnet to title each new
   session adds an API call per session. Not expensive but not free.
   The filename already contains a timestamp — that's enough to find
   the right session by date. Skip.

4. **"Should past turns count toward `CONVERSE_MAX_TOKENS`?"** No — that
   limit caps the *new reply*, not the input. History grows the input
   side; the model still produces ≤96 token replies.

---

## Out of scope

- **Multi-user / multi-tenancy.** One user. The API_KEY gate is all the
  authz that exists.
- **Cross-session retrieval (RAG over past sessions).** "Remember when we
  talked about X last week?" — possible later via embedding lookup, but
  not in this plan.
- **Web UI for browsing sessions.** Use a text editor / Obsidian. The
  files are markdown.
- **Session search.** `grep -r foo sessions/` is the search interface.
- **ESP-32 firmware.** Out of scope until hardware arrives. The firmware
  change to support sessions is one constant string.
- **Streaming responses.** Memory works the same way regardless of whether
  the response streams; this plan doesn't touch streaming.

---

## Files touched

| Phase | File | Action |
|---|---|---|
| A | `server/sessions.py` | new |
| A | `tests/stage8_sessions.py` | new |
| A | `.gitignore` | edit: add `sessions/` |
| B | `server/protocols.py` | edit: add `history` kwarg |
| B | `server/backends/llm_claude.py` | edit: prepend history to messages |
| B | `server/backends/llm_ollama.py` | edit: switch to `/api/chat`, parse new shape |
| B | `tests/stage8_llm_history.py` | new |
| C | `server/app.py` | edit: `/chat` accepts `session_id` |
| C | `server/config.py` | edit: add `SESSION_*` env vars |
| C | `tests/stage4_flask.py` | edit: session_id probe |
| D | `server/app.py` | edit: `/converse` accepts `session_id` + returns `session_file` |
| D | `tests/talk.py` | edit: read `VOICE_AI_SESSION_ID`, send if set, print returned `session_file` |
| D | `server/web/index.html` | edit: Session ID input + localStorage |
| D | `tests/stage8_e2e_session.py` | new |
| E | `server/sessions.py` | edit: `prune_older_than(days)` |
| E | `server/app.py` | edit: optional cleanup at boot |
| E | `server/config.py` | edit: `SESSION_TTL_DAYS` |

---

## Verification (end-to-end after Phase D)

```
.venv/Scripts/python.exe -m server.app   # terminal 1

# terminal 2
$env:VOICE_AI_SESSION_ID = "verify"
.venv/Scripts/python.exe tests/talk.py
> Press Enter to start recording...   <Enter>
"My favorite color is blue."           <Enter to send>
# (reply plays; `session_file: verify-20260503T201500Z.md` printed)

> Press Enter to start recording...   <Enter>
"What's my favorite color?"            <Enter to send>
# reply must say blue

# inspect
cat sessions/verify-20260503T201500Z.md
# shows both turns
```

Then test idle rollover:

```
# in .env: SESSION_IDLE_TIMEOUT_MIN=1
# restart server
# do one turn, wait 90s, do another
# expect TWO files in sessions/ for stream "verify"
```

Then test stateless default (no session_id):

```
Remove-Item Env:\VOICE_AI_SESSION_ID
.venv/Scripts/python.exe tests/talk.py
# behaves exactly like today; no session file written
```

If those three pass, Phase D is done.
