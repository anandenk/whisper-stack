# Dedicated memory libraries and practical alternatives

Researched 2026-10-26. Research only: no packages installed, provider calls made, or implementations run. Repository examples demonstrate design choices, not adoption prevalence or independently verified reliability.

## What do actual voice projects implement?

### Takeaway
Concrete projects combine bounded recent dialogue with durable facts and conversation summaries. A SQL-backed approach has real implementations; a knowledge graph is an alternative, not a prerequisite.

### Cited Findings
- **Alexsay is a close hardware-project comparison.** Its speaker project offers an embedded-SQLite server and editable memory UI. Its memory code loads five recent conversations, eight ending turns from the latest finished conversation, and stored facts capped at 6,000 characters. After a conversation ends, a separate text-model request produces a two-sentence summary and schema-constrained add/update/remove operations. New facts retain their source conversation ID; updates/deletes are scoped by home ID. During conversation, instructions expose remember/forget and history recall tools. These are inspected source-code behaviors, not measured latency or memory accuracy. [Project](https://github.com/andreasbros/alexsay); [memory implementation](https://github.com/andreasbros/alexsay/blob/main/api/src/memory.ts)
- **VoiceAssist demonstrates the smaller SQLite pattern.** Its code has raw messages and summary tables keyed by session ID, recent-message retrieval, summary upsert, and a clear-session operation deleting both. Summarization calls an LLM once message count exceeds ten; it compresses all but the last four messages. The code does not enforce the README's implied “every ten turns” cadence: repeated calls after the threshold can repeatedly summarize. The summary is session-specific, so cross-session personal memory is not established by this module alone. [Inspected implementation](https://github.com/Osaid10/voiceassist/blob/master/app/memory.py)
- **Plucky demonstrates the graph route.** The author describes an OpenAI Realtime voice assistant with Graphiti/Neo4j, loading recent history/profile on startup, saving conversation episodes, and semantic retrieval in later sessions. It also exposes “forget everything.” This is a small project example, not evidence of industry-wide practice or independently tested quality; code beyond the README was not verified. [Project and architecture](https://github.com/athrael-soju/Plucky)

### Inferences
- Alexsay's separate post-conversation consolidation is a practical pattern for avoiding a memory-writing model request before the first spoken answer. Copy the design principle rather than its whole platform. The inspected memory module alone does not prove the caller schedules consolidation asynchronously. [Code](https://github.com/andreasbros/alexsay/blob/main/api/src/memory.ts)

### Gaps
- No independent latency, long-term recall accuracy, or deployment-scale evidence was established for these projects. Do not treat README descriptions such as “production-quality” as verification.

## What do Mem0, Graphiti and Letta add?

### Takeaway
These systems automate extraction, retrieval and maintenance, with extra infrastructure/model work. They serve different purposes: a pluggable fact-retrieval layer, a temporal relationship graph, or a stateful agent platform.

### Cited Findings
- **Mem0** has an explicit retrieve → inject memories → answer → add new conversation pattern. Its OSS defaults add a fact-extraction/update LLM, an embedding model, on-disk Qdrant and SQLite history; providers are configurable, including Anthropic/local options. Its official reference exposes user/agent/run scoping, `update`, `delete`, `delete_all`, and per-memory change history. It includes a voice-assistant example that searches memories before generating the reply. This does not replace the application's exact current-turn dialogue history. [Quickstart and defaults](https://docs.mem0.ai/open-source/python-quickstart); [official API and voice example](https://github.com/mem0ai/mem0/blob/main/LLM.md)
- Mem0's hosted integration architecture distinguishes application-owned in-flight conversation, run-scoped session facts, and user-scoped durable memory. It documents background processing for hosted async adds. That should not be confused with merely using the OSS `AsyncMemory` class: asynchronous Python I/O is not itself proof that extraction finishes later in a background job. The documented numeric latency ranges are vendor claims, not benchmarks for WhisperStack. [Hosted architecture](https://github.com/mem0ai/mem0/blob/main/integrations/mem0-plugin/skills/mem0/references/architecture.md)
- **Graphiti/Zep** represents entities, relationships and facts changing over time. The OSS repository documents graph backends (Neo4j, FalkorDB, Neptune), inference/embedding clients and temporal fact invalidation. Current repository guidance deprecates Kuzu and recommends Neo4j or FalkorDB for new projects; older tutorials mentioning Kuzu can therefore mislead. Ingestion uses LLM extraction and is sensitive to schema quality/rate limits. Retrieval combines semantic, keyword and graph signals. Vendor latency/benchmark claims were not reproduced. [Current Graphiti repository](https://github.com/getzep/graphiti); [installation and concurrency guidance](https://help.getzep.com/graphiti/getting-started/quick-start)
- **Letta** provides an agent platform rather than only a memory lookup library. Its separate experimental ai-memory-sdk can attach background learning to another chatbot: `add_messages` invokes a Letta agent asynchronously, and `wait_for_run` blocks for completion. The maintainers explicitly advise batching five–ten messages or processing evicted turns to reduce cost. Subject/user IDs map to memory agents; developers can manage blocks, search archived messages if vector storage was enabled, and delete a user's agent/data. [SDK reference and cost warning](https://github.com/letta-ai/ai-memory-sdk)
- Letta also publishes an actual LiveKit voice integration using a Letta server/cloud backend, Deepgram STT and Cartesia TTS. Its self-hosted example adds a Docker-hosted service and persistent PostgreSQL data. This demonstrates voice integration, but is a larger architecture change than adding memory to the existing FastAPI pipeline. [Letta voice example](https://github.com/letta-ai/letta-voice)

### Inferences
- Mem0 is the more direct candidate if automatic personal-fact extraction and semantic recall become required. Graphiti is justified when relationships, provenance and changing facts materially improve the assistant; Letta fits a deliberate shift to a stateful agent runtime. None has independently demonstrated a speed advantage for this project's short voice turns.

### Gaps
- No head-to-head test using Haiku 5.5, this Mac, and the current ElevenLabs pipeline was found. Exact cost/latency depends on chosen models, local/cloud storage, retrieval policy and write schedule. Authentication, retention, backups and full erasure still require application design; a `user_id` filter alone is not authentication.

## What should WhisperStack take from this evidence?

### Takeaway
Recommendation, not an implementation decision: keep a bounded exact conversation, persist editable facts/summaries in SQLite, and add selective retrieval only when demonstrated needs exceed that design.

### Cited Findings
- A close voice-speaker implementation already uses bounded prompt context plus post-conversation fact reconciliation and explicit remember/forget tools; a smaller project uses straightforward SQL message/summary tables. Dedicated libraries introduce additional extraction, embedding or agent calls. [Alexsay code](https://github.com/andreasbros/alexsay/blob/main/api/src/memory.ts); [VoiceAssist code](https://github.com/Osaid10/voiceassist/blob/master/app/memory.py); [Mem0 defaults](https://docs.mem0.ai/open-source/python-quickstart); [Letta cost warning](https://github.com/letta-ai/ai-memory-sdk)

### Inferences
- First implement recent session dialogue and explicit reset/resume semantics. Then add persistent facts with timestamps, source turns and edit/delete controls; consolidate after replies or at session end. Keep immediate follow-up context in exact recent turns so a background memory update is not required before the next question. Evaluate semantic retrieval later against concrete questions that SQL/profile loading cannot answer well.
- Markdown's drawback here is not its extension; the consequential choices are bounded retrieval, update/conflict rules, provenance, concurrent writes and forgetting. Database rows offer a convenient way to manage these, but do not make extracted facts automatically correct.

### Gaps
- The user has not yet chosen retention periods, automatic versus explicit learning, or whether conversation history should survive restarts. Research is not authorization to retain voice transcripts or implement memory.
