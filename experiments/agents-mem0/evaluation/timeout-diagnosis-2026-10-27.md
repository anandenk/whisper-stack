# Hosted response failure investigation — 2026-10-27 (D35–D37)

**Latest (D37):** increasing only the cap from 1,024 to 4,096 at the selected
10-second wait restored a correct answer to the unchanged question. Its 1,198
output tokens and 4.876-second first byte exceed the old limits, strongly
supporting the budget diagnosis on this case. Original context recall
completed in D38 with a 3/3 pass after 20 exchanges and no Mem0 access. See
[final context results](context-2026-10-27.md) and the D37 result below.

**Prior (D36):** the authorized model-wait change to 10 seconds was applied,
but one unchanged question still failed with an empty-response error. Its
initiated usage of 3,072 output tokens points to the 1,024-token cap as a
stronger working hypothesis. See the D36 result below. No full context rerun.

**Prior D35 finding: generic follow-up handling works. The problematic question also fails as the
first user message in a fresh session.** This localizes the observed failure
to answering that question through the hosted model path, rather than a general
second-message bug or accumulated memory/context. At this stage the existing four-second model-response timeout was a plausible
cause; no settings were changed during D35.

## Evidence and controlled comparison

The user authorized diagnosis after [two inconclusive context attempts](context-2026-10-27.md).
Read-only inspection found:

- The runner's user_message/text payload matches the installed official Python
  SDK serializer. Its ping/pong and text-only initialization match the
  [WebSocket reference](https://elevenlabs.io/docs/eleven-agents/api-reference/eleven-agents/websocket)
  and [chat-mode instructions](https://elevenlabs.io/docs/eleven-agents/guides/chat-mode).
- Both old transcripts contain the exact second question, with no interruption,
  ignored backchannel, tool invocation or audio. It reached the service.
- Current remote settings: native claude-haiku-5-5, low reasoning effort,
  max_tokens=1024, cascade_timeout_seconds=4.0, fallback disabled, session limit
  600 seconds, silence/re-engagement and soft-timeout filler disabled. The
  model timeout is separate from session length and silence behavior.

Two diagnostic sessions used the same unchanged agent, prompt, tools and client
as the failed context attempts. They used fresh test-user IDs, permitted
text-only overrides and denied memory tools; no Mem0 client was created.

| Probe | Input | Observed result |
| --- | --- | --- |
| Simple follow-ups | Three successive requests: reply Ready, reply Next, two plus two | Ready., Next., 4; provider done, all three exact expected answers |
| Problematic question first | Exact original printer-scheduling question, with no preceding user history | Same LLM Cascade Error: TimeoutError; provider failed, no generated answer |

The first session lasted seven seconds; provider time to first sentence for
its three answers was 0.358, 0.302 and 0.420 seconds. The two-second local quiet
interval is part of each captured turn duration, not model response latency.
The second session lasted 14 seconds. Provider transcripts match every user
message and completed answer. Both sessions used only native Haiku, no tools,
zero ASR/TTS, and unchanged before/after agent configuration hashes.

This comparison rejects the broad hypothesis that follow-up messages cannot
work through this client. It does not rule out every client/provider interaction,
establish that all detailed questions fail or identify the model's internal
work. The particular question triggers the same reported failure in either
position. No reasoning trace or failed-generation token/time breakdown was
returned, so do not claim a measured thinking duration.

## Likely mechanism and proposed next step

The [official cascading documentation](https://elevenlabs.io/docs/eleven-agents/customization/llm/llm-cascading)
describes a default four-second wait per model attempt, configurable from two
to 15 seconds, and internal retry behavior. Consequently a 14–15 second failed
session does not imply a single 15-second model deadline. Actual per-attempt
timings/retry counts are not exposed in these reports. Fallback remains disabled
to preserve the selected model; no other model was tried by this runner.

**D35 proposal, subsequently applied/tested in D36 below:** change only
conversation_config.agent.prompt.cascade_timeout_seconds from 4.0 to 10.0,
retaining Haiku, low effort, existing prompt, token cap and disabled fallback.
First verify the unchanged problematic question once. A successful answer would
support insufficient wait time as the cause; if it still fails, stop and inspect
the model/provider integration rather than repeatedly rerunning the context
scenario. The full context check remains inconclusive and was not rerun here.

## Usage, checks and evidence

Two sessions/four user messages consumed **67 included credits**: 51 for simple
follow-ups (48 platform + three LLM), 16 for the failed question (16 platform +
zero reported LLM credits). Account usage settled 3,753→3,820 of 131,000, matching
the sum; 127,180 remain. This is separate from D33/D34's prior 65 credits.
No cash purchase, plan upgrade or credit-limit extension. Nominal provider USD
fields remain in local evidence and are separate from credits/cash invoices.
No Mem0 reads/writes, agent mutation, audio, microphone or service restart.

The runner has two fixed sessions, four user messages, 120 seconds per session,
40 seconds per turn, 64 HTTP reads/authentication operations, and a 500-credit
observed-delta stop threshold between probes. All nine runner HTTP operations
and the two separate preflight reads succeeded. No automatic generation retries;
these were distinct controlled questions, not another full context attempt.

All **43 mocked Python checks passed**, including two new checks for offline
execution without keys/network and retaining provider diagnostics after failed
generation. Current whitespace and local-link checks pass. No new browser,
audio or hardware suite. The local tester remains running on port 8767.

Offline plan (no key reads/network):

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/diagnose_timeout.py
```

The authorized live invocation used --live --allowance-confirmed; it is not
automatic authorization for future generation. Ignored local evidence under
experiments/agents-mem0/output/timeout-diagnosis/:

- preflight.json: selected remote settings and subscription check.
- probes-276dc59c.json: original probe reports, provider transcripts/errors,
  timing, usage, request ledger and before/after configuration hashes.
- probes-reviewed.json: copy plus reviewed conclusions and the unapplied
  one-field recommendation.

Conversation IDs: conv_6101m5zkh4c0e3pva90v9n2s2fcn (simple follow-ups),
conv_2901m5zkhj7mfx9sf0j3vvgqpd68 (problematic question first).
Provider retention is unchanged; no cleanup or deletion. All source changes
remain local, uncommitted and unpushed.

## D36 — authorized 10-second wait check

The user selected the one-field change. A fresh subscription/configuration
check preceded a single PATCH using this exact body:

```json
{"conversation_config":{"agent":{"prompt":{"cascade_timeout_seconds":10.0}}}}
```

The [Update agent API](https://elevenlabs.io/docs/eleven-agents/api-reference/agents/update)
returned HTTP 200. Independent GET readback matched the saved settings exactly
except for 4.0→10.0 at this field (conversation, platform, name, tags, workflow
and procedures compared). The local agent.json now records 10.0. Low effort,
Haiku, prompt text, max_tokens=1024, disabled fallback, tools and audio/silence
settings are unchanged. Before/after hashes also match during the live probe.

The original printer question was sent once, unchanged, in a fresh text-only
session: conv_6901m5zkvn66fhwryy3gfzv0zy1q. Provider status failed after 17 seconds
with error type llm_error/code 1002 and reason
`LLM Cascade Error: RuntimeError: Brain returned no response`.
Its transcript contains the greeting and exact user question, no answer/tool.
There were no Mem0 requests, ASR/TTS, microphone use or additional session.

The new usage evidence is material:

- Initiated-generation output_total: **3,072 tokens** for native Haiku 5.5.
- Configured max_tokens: **1,024**; 3,072 equals three such caps.
- Irreversible-generation model usage: empty. No final answer was returned.
- No per-attempt stop reason, thinking/text split or reasoning trace was exposed.

Anthropic's [Haiku 5.5 migration guide](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide)
explains that adaptive thinking is on by default and shares max_tokens with the
reply. A small cap can terminate generation after thinking, before any answer
text. The observed token total and empty answer are therefore **consistent with
attempts exhausting the cap on thinking**. This is an inference, not a directly
observed stop_reason or proof that precisely three identical attempts occurred.
A ten-second wait alone did not fix the failure; the earlier four-second-only
explanation is insufficient.

**Current settings remain 10 seconds and 1,024 tokens.** The next proposal is to
raise only max_tokens to 4,096, retaining low effort and the short-answer prompt.
That gives thinking and reply text more room without asking for a longer answer.
Update the evaluation guard that currently requires 1,024 alongside any selected
cap change, then try this same question once. No cap change or further session
is authorized by this result; the full context test remains inconclusive.

Usage: **26 included credits** (16 platform + 10 LLM), account usage settled
3,820→3,846/131,000; 127,154 remain. Initial postflight showed 3,836 before LLM
accounting settled. Nominal prices: $0.003 platform and $0.0019067235 LLM,
separate from credits/cash payments. No purchases, plan changes or additional
cash spend. Initiated usage is retained even though no answer was delivered;
it must not be discarded or combined with the empty irreversible ledger.

All 43 mocked Python checks pass; current whitespace/local links pass. No new
browser/hardware tests or service restart. No commit or push. The original
report is output/timeout-adjustment/check-49883dfd.json under experiments/agents-mem0;
check-reviewed.json adds the review and settled allowance. Ten read/auth HTTP
operations plus one explicit PATCH, no automatic client retry. Prior evidence
is untouched. For reproduction, check the selected field with GET, preserve the
before state, apply only the JSON body above if still authorized/needed, verify
the diff, then use diagnose_timeout.one_probe with only the original
scenario()["intervening"][0] question and a Mem0-blocking ContextRunner. Do not
repeat the change or model generation automatically.

## D37 — selected token-cap increase restores the answer

The user selected max_tokens 1,024→4,096, preserving the 10-second wait and all
other settings. One remote PATCH applied only this field:

```json
{"conversation_config":{"agent":{"prompt":{"max_tokens":4096}}}}
```

Current configuration was checked against the saved D36 state before mutation.
PATCH returned 200; fresh readback exactly matched the expected one-field diff
across conversation/platform/name/tags/workflow/procedures. Local agent.json and
the shared evaluation guard were updated to 4,096. A new mocked guard check
accepts that cap and rejects both the superseded 1,024 and unselected 8,192.
Haiku low effort, concise prompt, disabled fallback, memory/audio and silence
settings are unchanged. Configuration hashes match during the probe.

**The unchanged printer question passed in one fresh text-only session.**
Provider status done, no error, conversation conv_7901m5zm4xh8eq9rwejkk298z5c6.
Assistant review confirmed the arithmetic and feasibility: A handles all three
large jobs in 126+10=136 minutes; B handles all five small jobs in 90+10=100;
both finish before 180 minutes, leaving 44 and 80 spare minutes. The answer
explains the buffer correctly in 68 words/three sentences. Exact user/assistant
messages match the provider transcript; no judge-model call was used.

Provider metrics: 4.876 seconds to first byte, 4.916 to first sentence and 5.274
to final sentence. Total provider duration eight seconds includes the local
quiet interval/session lifecycle. Native Haiku reports **1,198 output tokens**
and reasoned=true. Initiated and irreversible ledgers match; count one ledger,
not both. No reasoning/text token split was supplied.

At the unchanged ten-second wait, the cap increase alone restored the answer.
Successful total output exceeded the old 1,024 cap, and first-byte latency also
exceeded the old four-second wait. Together with D36's empty answer/3,072
initiated output tokens, this strongly supports insufficient budgets on this
case. It does not establish an exact failed-attempt stop reason or a general
reliability rate; the original 20-intervening-exchange recall test has not run.

Usage: **21 included credits**, 16 platform + five LLM; account settled from
3,846 to 3,867 of 131,000, matching the charge; 127,133 remain. Nominal prices
$0.003 platform + $0.0008453775 LLM remain separate from credits/cash payments.
Zero Mem0 requests/tools, ASR/TTS or microphone/audio; no purchase, upgrade,
extension, automatic client retry, second session or full context rerun.
Eight HTTP reads/auth requests plus the single authorized PATCH succeeded.

All **44 mocked Python checks passed**; current whitespace/local links pass.
No new browser, earlier backend, audio or hardware suite. The existing local
8767 tester remains running. No commit/push. Ignored local evidence under
output/token-cap-adjustment/ in experiments/agents-mem0:
check-f9359bf4.json preserves the original configuration/answer/usage report;
check-reviewed.json adds semantic review and outcome. Prior reports unchanged.

**Current settings:** 4,096 tokens/10-second wait, low effort, same concise
prompt and disabled fallback. **Next concrete step:** return to the original
frozen context test, with three fabricated opening details, 20 intervening
exchanges, final recall and no Mem0 access. That sole unfinished initial check
is not automatically started by this result; no larger evaluation is selected.

## D38 — final context check completed

The user subsequently authorized the original frozen context scenario once.
It passed all three opening details after 20 intervening exchanges, with zero
Mem0 access or target refresh at the corrected model limits. The initial small
memory/context evaluation round is complete; [full results and limits](context-2026-10-27.md).
No more evaluation, settings changes or feature implementation automatically.
