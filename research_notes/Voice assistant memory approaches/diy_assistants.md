# Memory in DIY voice assistants

## How do real projects preserve a spoken conversation?

### Takeaway
Home Assistant and Xiaozhi maintain explicit application-owned conversation history. Their code separates this recent transcript from a persistent memory summary or memory-service lookup; a stateless model call does not remove the need for application state.

### Cited Findings
- Home Assistant's `ChatLog` stores typed user/assistant/tool content under `conversation_id` in `hass.data`. Reusing an ID retrieves a copy of its existing content; session cleanup removes the associated log. This inspected mechanism is in-memory session history, not a persistent personal profile. — [Chat log implementation](https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/components/conversation/chat_log.py)
- Home Assistant's session helper records `last_updated`, defaults `CONVERSATION_TIMEOUT` to five minutes, and periodically cleans sessions whose last update is older than that threshold. IDs are generated when absent; reused active IDs preserve the session. Cleanup is periodic, so five minutes is a stale threshold rather than an exact deletion deadline. — [Session implementation](https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/helpers/chat_session.py)
- Xiaozhi's `Dialogue.get_llm_dialogue_with_memory` constructs the request from system instructions with injected memory, optional speaker data, and actual conversation messages. It separately repairs interrupted tool-call sequences with an interrupted-result placeholder. — [Dialogue implementation](https://raw.githubusercontent.com/xinnan-tech/xiaozhi-esp32-server/main/main/xiaozhi-server/core/utils/dialogue.py)

### Inferences
- A session ID and a bounded list of recent complete exchanges are a practical first increment for WhisperStack. A user-level persistent profile can remain separate from the session transcript; this follows the separation visible in the inspected code, rather than requiring an entirely new voice framework.

### Gaps
- No code was executed and no hardware behavior was tested. Sources were fetched October 26, 2026 from moving `dev`/`main` branches, not pinned release commits. Xiaozhi's inspected dialogue file does not itself implement a token cap, so it does not establish the whole project's history-bounding policy.

## What do comparable projects use for durable personal memory?

### Takeaway
The unusually close ESP32 comparison is Xiaozhi: it supports both compact file-backed summaries and an external memory service. The choice is a policy and retrieval decision as well as a storage-format decision.

### Cited Findings
- Xiaozhi's local memory provider loads `data/.memory.yaml`, reads/writes a summary under `role_id`, combines earlier memory with user/assistant messages, calls its configured LLM to produce valid JSON, and returns the whole stored summary on `query_memory` regardless of the query. Its prompt asks for identity, events, timestamps, and evolving information. This is summary memory persisted in YAML, with a JSON summary as the value—not a vector search engine. — [Local summary implementation](https://raw.githubusercontent.com/xinnan-tech/xiaozhi-esp32-server/main/main/xiaozhi-server/core/providers/memory/mem_local_short/mem_local_short.py)
- Xiaozhi's Mem0 adapter filters system/tool messages out of saved messages, calls `MemoryClient.add(..., user_id=self.role_id)`, and searches with a matching `user_id` filter. It adds update timestamps and sorts retrieved memories newest first. Thus the application chooses identity scope and explicitly invokes retrieval rather than relying on automatic LLM recall. — [Mem0 adapter](https://raw.githubusercontent.com/xinnan-tech/xiaozhi-esp32-server/main/main/xiaozhi-server/core/providers/memory/mem0ai/mem0ai.py)
- The current project README lists local short memory, Mem0, PowerMem and no-memory options. The source code above independently substantiates the first two mechanisms. Treat its price/free-tier statements as project documentation, not verified current vendor pricing. — [Xiaozhi README](https://github.com/xinnan-tech/xiaozhi-esp32-server/blob/main/docs/readme/README_en.md)

### Inferences
- SQLite facts with timestamps and an optional summary would give WhisperStack more precise updates/deletion than one growing prose file, without requiring hosted memory. A retrieval library becomes useful when memory volume or search requirements justify it; these sources provide no evidence it is necessary for one user.
- “Local memory” describes storage location here. Since the implementation calls the configured LLM to summarize, local persistence alone does not establish local-only processing or zero external data transfer.

### Gaps
- No independent quality/latency comparison between file summaries, SQLite and Mem0 was found in the inspected evidence. No claim that one is universally superior or most common is justified. `role_id` is the chosen implementation scope; it should not be assumed equivalent to a verified human identity.

## What do actual users report going wrong?

### Takeaway
The relevant discussions focus on stale context, reset control, conflicting facts, and retrieval delay. Replacing Markdown with a database does not by itself settle those behaviors.

### Cited Findings
- In an August 12, 2026 Home Assistant discussion, a user requests explicit conversation reset/end controls and shorter configurable session retention. An October 2 reply reports that earlier tool choices were repeated within a retained conversation while a fresh session worked. These are first-person user reports, not a controlled diagnosis or implemented maintainer policy. — [Home Assistant discussion 4568](https://github.com/orgs/home-assistant/discussions/4568)
- A Xiaozhi user asked on August 21, 2026 how to update changing preferences/plans, resolve conflicts, preserve history and avoid response slowdown; the inspected issue has no substantive answer. It demonstrates a practical unresolved concern, not consensus for a third-party database. — [Xiaozhi issue 3334](https://github.com/xinnan-tech/xiaozhi-esp32-server/issues/3334)

### Inferences
- For WhisperStack, prioritize explicit “new conversation” and “forget/update that” behavior, timestamps/source provenance for saved facts, and identity/session isolation. Evaluate lookup and summary work separately from time-to-first-audio. These are recommendations drawn from the failure modes, not new user decisions.

### Gaps
- These examples are not a representative survey. No claims about industry adoption rates or reliable cross-provider performance can be made from them.
