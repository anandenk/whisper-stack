# Native correction follow-up — 2026-10-27

**Mem0's native active-only filter resolves the three captured correction
conflicts in this sample.** All three old records are marked `superseded` and
their `replaced_by` IDs point to the corresponding active replacements.
Default searches include that history; `latest_only=true` excludes it.

This corrects the interpretation of the [initial baseline](baseline-2026-10-27.md):
retaining an old record did not establish failure to supersede it. The first
runner omitted lifecycle fields. In this follow-up, the detail endpoint returned
those fields, while the captured search results themselves did not include them.
Thus simply retaining extra search fields is not the demonstrated fix;
requesting active-only retrieval is.

## Scope and method

Authorized by D30, within the existing included Mem0 allowance. Reused the same
synthetic owner and unchanged questions; zero writes, deletes, configuration
changes, purchases, app changes or extra answer-model/audio sessions.

Started about **61.7 minutes after** the baseline finished. Read the six captured
old/new memory IDs, compared full-history and active-only snapshots, and ran
three paired default/active searches plus three active-only controls. The only
paired search input difference was `latest_only=true`; owner, query and top_k=5
were unchanged. No clock override, reranker or custom temporal rules were used.

The runner stops on errors with no retries: 20 total requests and nine searches
maximum. Actual total: **17 HTTP 200 responses — six detail GETs, two list reads
and nine searches**. The existing personal owner was not queried or modified.

## Results reviewed from captured evidence

| Case | Old record detail | Default new-fact rank | Active-only new-fact rank | Old ID in active-only results |
| --- | --- | --- | --- | --- |
| Rooibos → chamomile | Superseded; linked to chamomile record | 2 | 1 | No |
| Bellmere → Larkhaven | Superseded; linked to Larkhaven record | 2 | 1 | No |
| Thursday → Tuesday | Superseded; linked to Tuesday record | 2 | 1 | No |

- Default list: 33 visible records. Active-only list: 30. Stored history was
  retained, not deleted; only the read selection changed.
- Italian control: correct supporting record still ranked first.
- Unknown dog-name and allergy questions: each still returned five distractors,
  with no supporting answer. Active-only filtering does not establish
  answerability or solve abstention.
- Median paired-query latency: default 336 ms; active-only 300 ms. Three queries
  per mode are insufficient to establish a speed improvement or SLA.

Review was assistant inspection of the captured details and nine result sets,
without an independent human review or judge-model call. This tests retrieval,
not agent tool selection, generated answers, long-session context or scaling.

## Native feature interpretation

[Dream's read semantics](https://docs.mem0.ai/platform/features/dream) distinguish
automatic superseding/merging from opt-in pattern synthesis. Generic pricing
labels alone did not establish which subfeatures work on this account. Actual
Free-account evidence here confirms superseded links and active-only filtering;
it does not establish synthesis/dashboard entitlements.

[Temporal Reasoning](https://docs.mem0.ai/platform/features/temporal-reasoning)
describes date-sensitive ranking and `reference_date` as the search's reference
clock. That addresses time expressions, not the explicit history-versus-active
selection demonstrated here. The [migration guide](https://docs.mem0.ai/migration/platform-v2-to-v3)
also explains additive retention. No temporal-ranking change was necessary to
resolve these three cases with the native lifecycle filter. This does not claim
all temporal queries are correct.

## Budget, checks and evidence

Signed-in Billing before/after: Free, model calls on Mem0; adds **37→37 of
10,000**, retrievals **25→36 of 1,000**. Eleven included retrieval operations
match nine searches plus two list reads; the six detail GETs did not increase
that counter. No additional spend or account change. API token/USD usage
remained unreported; this is an allowance check, not an invoice audit.

All **29 mocked Python checks** pass (five new read-boundary/contract checks,
24 earlier checks). Default plan-only execution, local links and whitespace
also pass. No browser suite, custom-backend suite or hardware tests were rerun.

Ignored local evidence, in the existing baseline output folder:
`experiments/agents-mem0/output/whisper-stack-eval-20261027T191436Z-0c01e00a62fa/`

- `native-corrections-4fcd344e.json`: original live follow-up evidence.
- `native-corrections-reviewed.json`: evidence plus assistant review.
- `native-allowance-before.json` and `native-allowance-after.json`: account UI
  observations recorded separately from provider usage.

Print the plan without loading credentials or contacting providers:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/evaluate_corrections.py
```

Reproduce only after checking the current included allowance; this consumes
another nine searches and two list reads:

```sh
experiments/agents-mem0/.venv/bin/python experiments/agents-mem0/evaluate_corrections.py --live --allowance-confirmed --baseline-report experiments/agents-mem0/output/whisper-stack-eval-20261027T191436Z-0c01e00a62fa/reviewed.json
```

## Recommendation and subsequent implementation

Use native `latest_only=True` for ordinary current-fact conversation recall,
retaining the on-demand viewer's existing history behavior. This is a read-policy
choice: active-only recall excludes superseded history, so explicit historical
questions may need a separately chosen history-aware retrieval policy. Do not
add heuristic date sorting, a context agent or a blanket score cutoff to fix
the demonstrated current-fact issue.

At the end of D30, the app still used its original default search. The user
subsequently authorized D31: the shared recall tool now passes latest_only=True,
with fixed-owner/top_k and viewer history unchanged. Current verification is in
[STATUS.md](../../../docs/STATUS.md). Generated-answer correction/abstention
remains a separate proposed evaluation within an agreed provider budget.
