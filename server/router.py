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

KNOWN ISSUE (observed 2026-05-06 from the ESP-32 voice client):
Very short queries route to complex more often than they should.
Concrete failure: "1+1" goes to complex. Three things stack:

  1. Lexically alien — closest SIMPLE example is "What's 23 plus 47?"
     (full English question), so a 3-character math expression scores
     low against it.
  2. Semantic adjacency to code — embeddings of "1+1" land closer to
     "Write a Python function..." or "Refactor this function" than to
     anything in SIMPLE, so s_complex is unexpectedly high.
  3. Margin asymmetry — even if s_simple > s_complex, it has to win by
     ROUTER_MARGIN_S (default 0.05). Tight matches go to complex.

Staged improvement plan (none implemented yet):

  TIER 1 — cheap, do first:
    a. Add short-arithmetic / single-word / brief-greeting examples to
       SIMPLE_EXAMPLES — currently 1 of 12 simple examples is math, and
       that one is wordy. Add: "1+1", "5 times 7", "what's 2 plus 2",
       "100 divided by 4", "what's 15 percent of 80".
    b. Add a regex shortcut at the top of classify() for unambiguous-
       simple patterns (mirror is_tool_likely's shape):
         - pure arithmetic: r"^\s*\d+\s*[+\-*/x]\s*\d+\s*=?\s*\??\s*$"
         - single-word lookups: r"^\s*\w+\s*\??\s*$"
       Both auto-route to simple before the embedding path runs.
    c. Optionally lower ROUTER_MARGIN_S from 0.05 to 0.02 (or 0). One
       env-var dial. Risk: more under-routing on borderline cases.

  TIER 2 — measurement before more changes:
    d. Build a real eval set. tests/stage5_router.py's fixture is
       30 queries and self-admits "several paraphrase the example sets",
       which inflates accuracy. Expand to ~100 queries with NO lexical
       overlap with SIMPLE_EXAMPLES / COMPLEX_EXAMPLES. Cover weak
       categories: very short queries, bare facts, conversational
       fillers, short translations.
    e. Re-curate example sets to ~20-30 per class once the eval set
       exposes which neighborhoods are under-represented.

  TIER 3 — only if Tier 1+2 plateau (do NOT skip ahead to here):
    f. Replace embedding classifier with a 2-shot LLM call (Llama 3.1
       8B is already loaded). Costs ~150 ms per turn (added to every
       voice round-trip), and in cloud mode doubles per-turn token
       spend. Worth it only with eval-set data showing the embedding
       approach has fundamental gaps that curation can't close.

Pushback on jumping straight to Tier 3: the embedding router is fast
(~30 ms) and the routing cost-of-being-wrong is bounded (a fraction
of a cent or one weird answer). Tier 1 is five minutes of work and
might solve the observed cases entirely. Measure before paying the
voice-latency tax of an LLM-classifier path.
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
