# Voice framework memory patterns

Research checked October 26, 2026. Official documentation and a maintainer-owned runnable example; these establish supported implementation patterns, not adoption rates or independent performance results. Documentation pages do not provide publication dates.

Project scope supplied by the coordinator: WhisperStack currently offers mute, not true user barge-in. The interruption findings below are relevant design guidance for eventual memory bookkeeping, not a proposal to add barge-in or realtime microphone upload now.

## How do voice frameworks retain turns and handle interruptions?

### Takeaway
LiveKit Agents and Pipecat maintain an explicit application-side conversation object. Voice history additionally needs to reflect what was actually spoken when an answer is interrupted. [LiveKit chat context](https://docs.livekit.io/agents/logic/chat-context/), [Pipecat context management](https://docs.pipecat.ai/pipecat/learn/context-management), [Pipecat interruptions](https://docs.pipecat.ai/pipecat/fundamentals/interruptions)

### Cited Findings
- LiveKit's `ChatContext` is ordered history sent to the LLM on each turn. Agents/tasks start empty unless initialized or passed context; `session.history` holds the session's complete history. User data can be loaded before session start, and per-turn retrieval can inject database/API results. [LiveKit chat context](https://docs.livekit.io/agents/logic/chat-context/)
- Pipecat's shared `LLMContext` and user/assistant context aggregators collect transcribed user messages and assistant output. The documented pipeline puts the user aggregator after STT and assistant aggregator after transport output. [Pipecat context management](https://docs.pipecat.ai/pipecat/learn/context-management)
- Pipecat's output transport emits text synchronized with audio playback; on interruption, only the spoken portion is committed as the assistant message. Generated-but-unplayed text is excluded. [Pipecat interruptions](https://docs.pipecat.ai/pipecat/fundamentals/interruptions)
- LiveKit likewise truncates assistant conversation history to the speech heard before interruption. [LiveKit turns overview](https://docs.livekit.io/agents/logic/turns/)

### Inferences
- A small custom voice backend can adopt ordered session history without adopting an entire framework. For streamed replies, distinguish generated text, delivered audio, and played audio; otherwise the next turn may assume the user heard something they did not. This follows the aggregator and interruption designs above, rather than a benchmarked recommendation. [Pipecat interruptions](https://docs.pipecat.ai/pipecat/fundamentals/interruptions)

### Gaps
- These sources do not establish how often independent hobbyist projects use each framework, or which storage backend is most common. They demonstrate available implementations, not a popularity survey.
- Browser delivery alone cannot prove acoustic playback or comprehension. The documentation's spoken-word accounting should not be interpreted as a physical-speaker guarantee.

## How is history kept bounded?

### Takeaway
Both bounded recent history and summarization are established framework mechanisms. Summarization can happen outside the response's critical path. [LiveKit chat context](https://docs.livekit.io/agents/logic/chat-context/), [Pipecat summarization](https://docs.pipecat.ai/pipecat/fundamentals/context-summarization)

### Cited Findings
- LiveKit `truncate()` keeps recent items while preserving system instructions and stripping leading function-call items to avoid orphaned tool results. [LiveKit chat context](https://docs.livekit.io/agents/logic/chat-context/)
- Pipecat has optional automatic context summarization, disabled by default. Token or new-message thresholds trigger an asynchronous background summary. It reconstructs context from a summary plus recent unsummarized messages and protects incomplete function sequences. A dedicated summarization LLM can be configured separately from the conversation LLM. [Pipecat summarization](https://docs.pipecat.ai/pipecat/fundamentals/context-summarization)

### Inferences
- For WhisperStack, first preserve recent exact turns; only add background summaries when session length requires them. Summary work need not delay every spoken reply. These are design recommendations derived from the mechanisms, not measured latency claims. [Pipecat summarization](https://docs.pipecat.ai/pipecat/fundamentals/context-summarization)

### Gaps
- No source here demonstrates summarization fidelity for this project's Haiku/ElevenLabs combination. Thresholds and summary quality need project-specific testing.

## How is memory carried across sessions?

### Takeaway
Pipecat documents persistent memory as a separate service layered onto session context; the integration has both hosted and self-managed options. [Pipecat Mem0 service](https://docs.pipecat.ai/api-reference/server/services/memory/mem0), [maintainer example](https://github.com/pipecat-ai/pipecat/blob/main/examples/rag/rag-mem0.py)

### Cited Findings
- `Mem0MemoryService` stores conversation information and retrieves relevant prior context across sessions. Its configuration separates `user_id`, `agent_id`, and `run_id`, with a retrieval result limit and similarity threshold. The service supports a cloud API key or `local_config`. These are documented capabilities, not independent accuracy findings. [Pipecat Mem0 service](https://docs.pipecat.ai/api-reference/server/services/memory/mem0)
- Pipecat's runnable `examples/rag/rag-mem0.py` uses a shared conversation context alongside a separate memory processor before the LLM. It shows hosted Mem0 and an alternative self-hosted configuration, and retrieves memories for a personalized greeting. The local configuration example still uses external LLM/embedding providers, so "self-hosted memory" does not imply fully local inference. The example includes ElevenLabs TTS. [Maintainer example](https://github.com/pipecat-ai/pipecat/blob/main/examples/rag/rag-mem0.py)

### Inferences
- The useful separation is session context versus durable user-associated memory. A database-backed fact store and selective retrieval can reproduce that architecture at small scale; Mem0 is an existing alternative if its added dependency and data processing are acceptable. This does not require replacing the working STT/LLM/TTS pipeline. [Pipecat Mem0 service](https://docs.pipecat.ai/api-reference/server/services/memory/mem0), [maintainer example](https://github.com/pipecat-ai/pipecat/blob/main/examples/rag/rag-mem0.py)

### Gaps
- These sources do not show automatic durable personal memory supplied merely by using Anthropic's standard Messages API. Provider-managed realtime sessions are a different integration; their retention/resumption guarantees require provider-specific checking.
- A runnable example is not proof of production reliability or suitable privacy/retention defaults. This research did not execute the frameworks, install libraries, or make paid inference calls.
