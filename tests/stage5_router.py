"""Stage 5 router accuracy + latency harness.

Calls server.router directly (no Flask server needed, but Ollama must be running).
Sweeps several MARGIN_S values against a 30-query labeled fixture and prints
the confusion matrix at each margin so YOU can pick the production margin.

CAVEATS:
- Fixture labels below are a first draft. Spot-check and re-label to your taste.
- Several fixture queries paraphrase the example sets in router.py (e.g.
  "What's the capital of Japan?" mirrors the France example). This inflates
  apparent accuracy. For real calibration, swap in held-out queries that have
  no lexical/semantic overlap with the example sets.

Run from repo root after activating .venv:
    python tests/stage5_router.py
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import router

# (query, expected_route)  -- TODO: re-label borderline group to taste
FIXTURE: list[tuple[str, str]] = [
    # --- 10 clearly simple ---
    ("What's the capital of Japan?", "simple"),
    ("How are you today?", "simple"),
    ("Got any good jokes?", "simple"),
    ("What's 17 times 8?", "simple"),
    ("Define the word ennui.", "simple"),
    ("Who wrote Pride and Prejudice?", "simple"),
    ("Convert 100 dollars to euros.", "simple"),
    ("Name three rivers in Europe.", "simple"),
    ("Good evening.", "simple"),
    ("What's the largest planet in our solar system?", "simple"),

    # --- 10 clearly complex ---
    ("Write a Python function that reverses a linked list.", "complex"),
    ("Help me plan a 10-day trip to Italy on a budget.", "complex"),
    ("Compare React vs Vue for a new web app.", "complex"),
    ("Draft an email asking for a salary raise.", "complex"),
    ("Write a haiku about loneliness.", "complex"),
    ("Explain why CPU caches are organized hierarchically.", "complex"),
    ("Refactor this function to be more readable.", "complex"),
    ("Analyze the symbolism of the green light in The Great Gatsby.", "complex"),
    ("Help me debug a NullPointerException in this Java code.", "complex"),
    ("Write a short story about a time traveler with regrets.", "complex"),

    # --- 10 borderline (my best-guess labels — review!) ---
    ("How do I make pasta?", "complex"),                    # recipe = multi-step
    ("Why is the sky blue?", "simple"),                     # short factual
    ("Tell me about quantum physics.", "complex"),          # broad explanation
    ("Hello, how can you help me today?", "simple"),        # meta chat
    ("Can you summarize the French Revolution?", "complex"),# summary task
    ("Translate 'I love you' to Japanese.", "simple"),      # short translate
    ("Explain photosynthesis to a 10-year-old.", "complex"),# tailored explain
    ("What are some healthy breakfast ideas?", "complex"),  # creative list
    ("Is the universe infinite?", "complex"),               # philosophical
    ("How many countries are in Africa?", "simple"),        # factual lookup
]

MARGINS = [0.00, 0.03, 0.05, 0.08, 0.10, 0.15]


def predict(margin: float, score_simple: float, score_complex: float) -> str:
    return "simple" if (score_simple - score_complex) > margin else "complex"


def confusion(pairs: list[tuple[str, str]]) -> dict:
    """pairs is [(expected, predicted), ...]. Returns counts."""
    tp_s = sum(1 for e, p in pairs if e == "simple" and p == "simple")
    fn_s = sum(1 for e, p in pairs if e == "simple" and p == "complex")
    tp_c = sum(1 for e, p in pairs if e == "complex" and p == "complex")
    fn_c = sum(1 for e, p in pairs if e == "complex" and p == "simple")  # false-simple (under-route)
    return {"simple_correct": tp_s, "simple_missed": fn_s, "complex_correct": tp_c, "false_simple": fn_c}


def main() -> int:
    print(f"Warming router (loads {router.config.EMBED_MODEL} into Ollama, embeds {len(router.SIMPLE_EXAMPLES) + len(router.COMPLEX_EXAMPLES)} examples)...")
    t0 = time.perf_counter()
    router.warmup()
    print(f"Warmup done in {time.perf_counter() - t0:.2f}s\n")

    # Embed every fixture query once, capture scores.
    print(f"Classifying {len(FIXTURE)} fixture queries...")
    rows = []  # (query, expected, score_simple, score_complex, latency_s)
    for query, expected in FIXTURE:
        d = router.classify(query)
        rows.append((query, expected, d["score_simple"], d["score_complex"], d["latency_s"]))

    latencies = [r[4] for r in rows]
    print(f"  mean latency: {statistics.mean(latencies)*1000:.1f}ms  "
          f"p50: {statistics.median(latencies)*1000:.1f}ms  "
          f"max: {max(latencies)*1000:.1f}ms\n")

    # Per-query table
    print("Per-query scores (sorted by margin: simple-leaning at top):")
    print(f"  {'expected':<8} {'margin':>7}  {'sScore':>7}  {'cScore':>7}  query")
    for q, exp, ss, sc, _ in sorted(rows, key=lambda r: r[2] - r[3], reverse=True):
        margin = ss - sc
        print(f"  {exp:<8} {margin:+.4f}  {ss:.4f}  {sc:.4f}  {q[:60]}")
    print()

    # Margin sweep
    print("Margin sweep (false_simple = expected complex but predicted simple — the asymmetric error):")
    print(f"  {'margin':>7}  {'acc':>6}  s_correct  s_missed  c_correct  false_simple")
    print("  " + "-" * 60)
    for m in MARGINS:
        pairs = [(exp, predict(m, ss, sc)) for _, exp, ss, sc, _ in rows]
        c = confusion(pairs)
        total_correct = c["simple_correct"] + c["complex_correct"]
        acc = total_correct / len(rows)
        print(f"  {m:>7.2f}  {acc*100:>5.1f}%  "
              f"{c['simple_correct']:>9}  {c['simple_missed']:>8}  "
              f"{c['complex_correct']:>9}  {c['false_simple']:>12}")
    print()

    # Per-margin disagreements (queries that flip)
    print("Disagreements by margin (queries the margin would route AWAY from expected):")
    for m in MARGINS:
        wrongs = [(q, exp) for q, exp, ss, sc, _ in rows if predict(m, ss, sc) != exp]
        if wrongs:
            print(f"  margin {m:.2f}:")
            for q, exp in wrongs:
                print(f"    expected={exp:<8} q={q[:70]}")
        else:
            print(f"  margin {m:.2f}: (no disagreements)")
    print()

    # VRAM residency check — both Llama and nomic-embed-text should be hot
    print("ollama ps (should list both Llama AND nomic-embed-text if both stay resident):")
    try:
        out = subprocess.check_output(["ollama", "ps"], text=True)
        print(out)
    except Exception as exc:
        print(f"  could not run ollama ps: {exc!r}")

    print("Done. Pick a margin, set ROUTER_MARGIN_S in .env (or accept the default 0.05).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
