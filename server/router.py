"""Embedding-based query router.

Classifies a query as 'simple' (-> simple-route LLM) or 'complex' (-> complex-route
LLM) using cosine similarity to curated example sets per class.

Decision rule:
    s_simple  = max cosine(query, example) over SIMPLE_EXAMPLES
    s_complex = max cosine(query, example) over COMPLEX_EXAMPLES
    route = "simple" iff (s_simple - s_complex) > MARGIN_S, else "complex"

Asymmetric: ties go to complex, because under-routing (the simple model
answers a question it can't handle) costs the user a lot more than
over-routing (the strong model answers a trivial query and we burn a
fraction of a cent).

The embedder is injected via attach_embedder() so the router stays
mode-independent. The Phase 3 factory wires either OllamaEmbedder
(local) or SentenceTransformerEmbedder (cloud).

Examples below are a starting draft. Re-label / re-curate against the
test fixture in tests/stage5_router.py — that fixture is also editable.

KNOWN ISSUES — measured against tests/router_eval.py 2026-05-06
================================================================

A 95-query held-out fixture (tests/router_eval.py — designed to NOT
paraphrase the example sets below) puts the embedding router at:

    Production margin ROUTER_MARGIN_S=0.05:    68.4% accuracy
    Best margin in the sweep (0.02):           75.8% accuracy

Per-category accuracy at production margin reveals the weak spots:

    100%  code, drafting, debug, time_location
     83%  creative, deep_explain
     63%  conversational  (5/8)
     60%  short_arith     (6/10)  <-- the user-observed "1+1" issue
     60%  planning        (3/5)
     50%  quick_lookup    (6/12)  -- short translations / conversions / definitions
     50%  bare_facts      (5/10)  -- "Population of Tokyo", "Speed of light", etc.
     47%  borderline      (7/15)  -- expected (judgment calls)

Two distinct failure modes in the data:

  A. Short / factual queries narrowly miss the 0.05 margin. "1+1" lands
     at margin +0.047 (just shy of 0.05), "100 / 4" at +0.042, "what's
     2 plus 2" at +0.019. The embedding actually scores them simple-ish,
     but the asymmetric tiebreak rejects them. Lowering ROUTER_MARGIN_S
     to 0.02 fixes the bulk of these without code changes.

  B. A handful of complex queries leak to simple even at margin 0.00.
     Examples (with computed margins):
       "Write a song chorus about Monday mornings"  +0.094
       "Help me prepare for a job interview..."     +0.052
       "How would I start a podcast from scratch?"  +0.054
       "Discuss the origins of World War 1"         +0.062
       "Tell me about the Eiffel Tower"             +0.054
       "Best way to learn Python?"                  +0.058
     These are curation gaps in COMPLEX_EXAMPLES, not margin issues.
     "Write a song chorus about Monday mornings" hits s_simple=0.748
     because "Good morning." is in SIMPLE_EXAMPLES — the embedding
     latches onto "morning". Mode-B failures need new COMPLEX examples
     covering "tell me about" broad topics, multi-step planning ("help
     me prepare..."), and pedagogical advice ("best way to learn...").

Staged improvement plan, recalibrated to the measurements:

  TIER 1 — cheap, high leverage:
    a. Lower ROUTER_MARGIN_S from 0.05 to 0.02. Single env-var change,
       buys ~7 points of held-out accuracy. Net trade: +3 under-routings
       (8->11), -10 over-routings (22->12). Asymmetric tiebreak was
       too strict for the actual data.
    b. Add short-arithmetic / single-word examples to SIMPLE_EXAMPLES:
       "1+1", "5 times 7", "what's 2 plus 2", "100 divided by 4". Pulls
       the simple cluster centroid toward the bare_facts/short_arith
       neighborhoods that scored 50-60%.
    c. Add ~5 examples to COMPLEX_EXAMPLES targeting Mode B above:
       "tell me about [topic]", multi-step planning, "best way to do X",
       pedagogical-advice queries.
    d. Add a regex shortcut at the top of classify() for unambiguous-
       simple patterns (mirror is_tool_likely's shape):
         - pure arithmetic: r"^\s*\d+\s*[+\-*/x]\s*\d+\s*=?\s*\??\s*$"
         - single-word lookups: r"^\s*\w+\s*\??\s*$"
       Both auto-route to simple before the embedding path runs.

  TIER 2 — DONE: tests/router_eval.py is the held-out fixture.
    Re-run it after any router change to confirm no regression.

  TIER 3 — only if Tier 1 plateaus (do NOT skip ahead to here):
    f. Replace embedding classifier with a 2-shot LLM call (Llama 3.1
       8B is already loaded). Costs ~150 ms per turn (added to every
       voice round-trip), and in cloud mode doubles per-turn token
       spend. Hard to justify at 76% baseline + Tier 1 gains
       unmeasured — the embedding router is fast (~30 ms; eval shows
       p95 41 ms) and the routing cost-of-being-wrong is bounded
       (a fraction of a cent or one weird answer per misroute).
"""

from __future__ import annotations

import time
from typing import Literal

import numpy as np

from server import config
from server.protocols import Embedder

SIMPLE_EXAMPLES: list[str] = [
    "What's the capital of France?",
    "Hey, how are you?",
    "Tell me a joke.",
    "Good morning.",
    "What's 23 plus 47?",
    "Define the word 'aphorism'.",
    "Convert 5 miles to kilometers.",
    "What time is it in Tokyo right now?",
    "Name three planets.",
    "Who wrote Hamlet?",
    "What does gregarious mean?",
    "How do you say hello in Spanish?",
]

COMPLEX_EXAMPLES: list[str] = [
    "Write me a Python function that flattens a nested dictionary.",
    "Compare SQLite and Postgres for a small SaaS app.",
    "Help me draft a polite reply to my landlord about the leak.",
    "Write a short story about a lighthouse keeper who discovers a secret.",
    "Explain why Kubernetes uses etcd specifically rather than another database.",
    "I'm getting a NoneType error in this code, can you help me debug it?",
    "Plan me a week-long trip to Japan on a budget of three thousand dollars.",
    "What are the philosophical implications of free will under determinism?",
    "Refactor this function to be more efficient and readable.",
    "Write a SQL query that joins three tables and groups by month.",
    "Analyze the pros and cons of remote work for software engineers.",
    "Write me a poem in the style of Robert Frost about climate change.",
]

# "Tool-likely" keyword set. When a query that the embedding router classifies
# as `simple` contains any of these substrings, the route handler in app.py
# auto-escalates to `complex` (Sonnet) — Llama 3.1 8B's tool calling is
# unreliable, and we'd rather pay a fraction of a cent extra on Sonnet than
# have the assistant answer "I don't know" from training data when a tool
# could have answered correctly. List is intentionally small; the embedding
# router is the primary mechanism. Adjust as patterns emerge.
TOOL_LIKELY_KEYWORDS: frozenset[str] = frozenset({
    # current-state lookups
    "weather", "temperature", "forecast",
    "news", "headlines", "today", "yesterday", "tomorrow",
    "current", "latest", "right now", "currently",
    # explicit search asks
    "search", "look up", "find out", "google",
    # time / location lookups
    "time in", "what time", "what's the time",
    # money / market
    "stock", "price of", "exchange rate",
    # sports / live events
    "score", "scores", "who won",
})


def is_tool_likely(query: str) -> bool:
    """Heuristic: does the query smell like it needs a real-time tool?

    Used by the route handler to auto-escalate `simple` -> `complex` when
    a simple-route query is asking about something time-sensitive. The
    keyword list is small on purpose — false positives just spend a
    fraction of a cent extra on Sonnet, false negatives leave the user
    with a "my training cutoff" answer.
    """
    q = query.lower()
    return any(kw in q for kw in TOOL_LIKELY_KEYWORDS)


_embedder: Embedder | None = None
_simple_vecs: np.ndarray | None = None  # (n, dim), L2-normalized
_complex_vecs: np.ndarray | None = None


def attach_embedder(embedder: Embedder) -> None:
    """Inject the embedder used by warmup() / classify(). Call once at boot,
    before warmup()."""
    global _embedder
    _embedder = embedder


def _normalize(v: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return v / norms


def warmup() -> None:
    """Embed and cache example vectors. Call once at server startup AFTER
    attach_embedder().

    Single batch call covers embed-model load + all example embeddings.
    """
    global _simple_vecs, _complex_vecs
    if _embedder is None:
        raise RuntimeError("router.warmup() called before attach_embedder()")
    all_examples = SIMPLE_EXAMPLES + COMPLEX_EXAMPLES
    vecs = _embedder.embed(all_examples)
    vecs = _normalize(vecs)
    _simple_vecs = vecs[: len(SIMPLE_EXAMPLES)]
    _complex_vecs = vecs[len(SIMPLE_EXAMPLES):]


def is_loaded() -> bool:
    return _simple_vecs is not None and _complex_vecs is not None


def classify(query: str, margin_s: float | None = None) -> dict:
    """Embed query and return route + diagnostic scores.

    Always reports raw scores so callers (and tests) can re-derive routes
    at different margins without re-embedding.
    """
    if not is_loaded():
        warmup()

    if margin_s is None:
        margin_s = config.ROUTER_MARGIN_S

    t0 = time.perf_counter()
    q = _normalize(_embedder.embed([query]))[0]
    s_simple = float(np.max(_simple_vecs @ q))
    s_complex = float(np.max(_complex_vecs @ q))
    elapsed = time.perf_counter() - t0

    margin = s_simple - s_complex
    route: Literal["simple", "complex"] = "simple" if margin > margin_s else "complex"

    return {
        "route": route,
        "score_simple": s_simple,
        "score_complex": s_complex,
        "margin": margin,
        "margin_threshold": margin_s,
        "latency_s": elapsed,
    }
