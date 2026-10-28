# Isolated memory and conversation-context evaluations

Authorized by PROJECT.md D29 on 2026-10-27. Budget: existing included Mem0
allowance only; no additional spend. That retrieval stage made no ElevenLabs
sessions, plan changes, purchases, BYOK setup or existing-owner writes/deletes.

The [first baseline results](baseline-2026-10-27.md) are complete, with a concrete
correction-ordering issue. Read them before repeating or expanding a live test.
The [native correction follow-up](native-corrections-2026-10-27.md) subsequently
verified that active-only retrieval resolves those three cases without new
memory writes or another model; its standalone runner is read-only.
The [D32 hosted answer baseline](answers-2026-10-27.md) then verified corrected
answers and unknown-answer behavior in eight isolated text-only conversations,
using the shared D31 recall handler and existing included provider credits.

## Frozen cases and scope

[cases.json](cases.json) contains a fabricated persona: 30 short seed facts,
three duplicate/paraphrase submissions, three explicit corrections and 16 final
questions (12 known-answer and four unknown-answer questions). No real user
facts are included. Cases are frozen before the first live run; each report
records their SHA-256 digest.

The runner uses the same messages/user_id payload and default inference as
run.py, and the same owner-filtered search with top_k=5. It calls the documented
REST endpoints directly with the existing HTTPX dependency so every request,
including status reads, is bounded and recorded. It does not initialize an SDK
that can issue an additional constructor-time authentication request. It adds
no threshold, reranking, extraction instructions or app behavior changes.

This is a service extraction/retrieval baseline. It does not test agent tool
selection, spoken answers, abstention, end-to-end recall, large stores, hardware
or automatic conversation compaction. Mem0 returns memories, not generated
answers; an unknown-answer query returning text is an observation, not proof
that the assistant would hallucinate.

## Run and limits

From repository root, print the plan without reading keys or making requests:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/evaluate_memory.py
```

Before a live run, inspect the signed-in account's plan, remaining add/search
allowance, model-call coverage/BYOK and overage settings. Request caps are not
dollar caps. On October 27 the dashboard showed Free, model calls on Mem0 with
30 days left, 1/10,000 adds and 5/1,000 retrievals used. Do not assume this remains
current for a later run.

After that check and within the user's budget, this command makes real writes:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/evaluate_memory.py --live --allowance-confirmed
```

The flag records the allowance review; it cannot enforce a provider billing cap.
Each invocation creates a new timestamped/random `whisper-stack-eval-*` owner,
checks that it is empty, and reads only MEM0_API_KEY from existing configuration.
It never reuses the application's fixed owner. Hard limits: 36 add attempts,
16 search attempts, 20 event reads per wait, two 50-record pages per snapshot,
280 requests total, 20-second network timeout and 1 MiB per response. No retries,
redirects, automatic resume, cleanup or settings changes. Stop on any error,
unexpected response, failed/pending processing or incomplete snapshot.

The first run used six event reads per write and stopped when one job was still
RUNNING. One explicit later status read confirmed it SUCCEEDED. The revised
wait allows 20 checks, preserving the same cumulative 280-request cap. This is
a documented protocol revision prompted by observed processing delay, not a
failed extraction/retrieval score.

Each write must reach SUCCEEDED before proceeding; event checks are three
seconds apart. A fixed five-second settling delay precedes each phase snapshot.
If processing or visibility lags beyond these bounds, label it inconclusive;
do not silently retry until expected results appear. Temporal ranking enrichment
may lag a successful event. Duplicate and correction behavior is observed at
this elapsed time, not certified as eventual background-cleanup behavior.

For an acknowledged write that exceeded the wait only, an explicit continuation
can keep the same owner and cumulative counters, check that job, then submit
only untouched cases. It refuses unknown transport outcomes, changed fixture
hashes, completed/scored runs and out-of-order case histories. Original evidence
is retained; continuation writes a separate JSON report in the same folder.
There is no automatic resume. Review allowance again and pass the local pending
report with `--live --allowance-confirmed --continue-report <report-path>`.

## Evidence and interpretation

Ignored local `output/<evaluation-owner>/report.json` retains sanitized synthetic
records, IDs, dates/scores when supplied, phase snapshots, write/event outcomes,
request attempts, HTTP status, latency and each search's projected tool-result
size. An attempt is saved before sending; interrupted writes are not replayed.
Provider payloads/metadata/credentials are not logged. Usage is retained only
if numerically reported; absent USD/token usage stays unknown, not zero.

Automated scores are **lexical diagnostics**: expected-answer anchors present
within one retrieved record, rank, seed extraction coverage, old-value mentions,
exact normalized duplicates and unknown-query nonempty results. They are not
semantic accuracy scores. Inspect paraphrases and context manually: "formerly
Bellmere, now Larkhaven" is different from falsely retaining Bellmere as current.
Snapshot count changes alone do not establish deduplication or data loss.

Review every query for supporting evidence, distractors and conflicting/current
versus historical values; inspect seed, duplicate and correction snapshots.
Record the reviewed findings in STATUS.md before deciding on app changes or a
larger test. Failures in this small baseline do not by themselves justify a
second context agent.

## Separate conversation-context stage

D33 authorized one final session, separate from the retrieval script above.
The [first attempt and separately authorized D34 repeat](context-2026-10-27.md)
were both inconclusive: the same provider LLM timeout occurred before the first
intervening answer. No automatic retry was made; diagnose the repeated timeout
before another live run.
The [D35 diagnosis](timeout-diagnosis-2026-10-27.md) subsequently confirmed that
simple follow-ups work and the problematic question also fails as first input.
D36 applied the selected four-to-10-second wait change and checked the same
question once; it still failed with an empty response. The initiated output
usage now points to a possible 1,024-token thinking-plus-answer cap problem.
D37 applied the selected 4,096-token cap and the unchanged question passed
with a correct 68-word answer. Successful usage/timing exceeded both old bounds,
supporting the budget diagnosis.
D38 then ran the [original context scenario](context-2026-10-27.md): 3/3 details
recalled after 20 intervening exchanges, with no Mem0 access/target refresh.
The initial small memory/context evaluation round is complete; no more runs
automatically. Separate filler reasoning/verbosity mistakes are recorded.
The frozen [context fixture](context-cases.json) introduces three fabricated
details only in its first message, then asks 20 unrelated questions before the
final recall question. The standalone evaluate_context.py retains the accepted
hosted Haiku agent/prompt and uses its already-permitted text-only override.
Both memory tools are denied with a generic response containing no test detail;
the HTTP boundary rejects all Mem0 access and every non-GET operation. No
Mem0 client, actual user memory, microphone or audio is used.

Print the plan without keys/network:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/evaluate_context.py
```

Within separately authorized included credits, the live command is:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/evaluate_context.py --live --allowance-confirmed
```

The runner freshly checks an active subscription, at least 10,000 remaining
included credits and disabled credit-limit extension. It checks the existing
provider session limit before starting; no settings are changed. Limits: one
session, 22 user messages, 300 seconds, 40 seconds per turn, 64 HTTP reads/auth
requests, eight blocked-tool attempts and a 1,000-credit observed-account-delta
stop threshold checked after each five intervening exchanges. No retries or
automatic continuation. These bounds are not a quoted monetary price.

Ignored evidence under output/context-evaluation/ contains the exact exchanged
text, provider transcript/usage, actual elapsed time, configuration hashes and
credit checks. Review all intervening assistant replies for premature repetition
of the target facts; if repeated, do not claim an uninterrupted 20-exchange
recall gap. Count the actual provider messages and transcript size. This is a
bounded early-detail baseline; recall alone does not prove automatic compaction,
context-window capacity, multi-hour behavior or large-store reliability.

## Sources checked on 2026-10-27

- [Add Memories](https://docs.mem0.ai/api-reference/memory/add-memories): default
  extraction and asynchronous event tracking.
- [Get Event](https://docs.mem0.ai/api-reference/events/get-event): write
  completion and possible lag in temporal enrichment.
- [Search Memories](https://docs.mem0.ai/api-reference/memory/search-memories):
  owner filters, ranked results and search parameters.
- [Get Memories](https://docs.mem0.ai/api-reference/memory/get-memories): paging.
- [Pricing FAQ](https://docs.mem0.ai/platform/pricing) and
  [current pricing](https://mem0.ai/pricing): included allowances, model coverage,
  BYOK and overages. Current public plans differ from older handoff descriptions
  of Dream entitlements; no free Dream behavior is assumed by this evaluation.
