"""Router evaluation harness — held-out fixture, ~95 queries.

Purpose
-------
Replaces the smaller `tests/stage5_router.py` fixture (30 queries, several
of which paraphrase the example sets in router.py) with a measurement
fixture designed for actual classifier evaluation. Queries here were
deliberately chosen to NOT paraphrase any entry in
router.SIMPLE_EXAMPLES / COMPLEX_EXAMPLES — natural overlap on common
function words ("what", "how") is unavoidable, but content nouns,
verbs, and specific entities don't reuse example-set wording.

The test set is organized into categories so per-category accuracy
surfaces specific weak spots. Categories that score low are the ones
the embedding router is failing on — those are the targets for
example-set curation (Tier 1) or regex shortcuts.

Methodology notes
-----------------
- Labels are author best-effort; "borderline" category collects calls
  that reasonable labelers might disagree on, with the chosen label
  representing what the *server* should do (simple = local Llama,
  complex = Sonnet).
- Auto-escalation via `is_tool_likely()` is intentionally NOT exercised
  here — we're measuring the embedding classifier in isolation. Queries
  like "time in London" are labeled simple even though they'd later be
  bumped to complex by the keyword check in app.py.
- Margins swept include 0.02 (loosened from current default 0.05) so
  we can see the impact of relaxing the asymmetric tiebreak.

Run from repo root after activating .venv (Ollama must be running):
    .venv/Scripts/python.exe tests/router_eval.py
"""

from __future__ import annotations

import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config, modes, router

# (query, expected_route, category)
FIXTURE: list[tuple[str, str, str]] = [
    # ---------- SHORT / SINGLE-WORD / ARITHMETIC (all simple) ----------
    # The bucket the user explicitly hit ("1+1" routing to complex).
    # Tests whether very short queries cluster correctly.
    ("1+1", "simple", "short_arith"),
    ("5 times 7", "simple", "short_arith"),
    ("100 / 4", "simple", "short_arith"),
    ("what's 2 plus 2", "simple", "short_arith"),
    ("hi", "simple", "short_arith"),
    ("hey", "simple", "short_arith"),
    ("yo", "simple", "short_arith"),
    ("thanks", "simple", "short_arith"),
    ("ok", "simple", "short_arith"),
    ("alright", "simple", "short_arith"),

    # ---------- BARE FACTS (all simple) ----------
    # Single-concept factual lookups. The kind of thing local Llama
    # 8B handles cleanly with no reasoning needed.
    ("How tall is Mount Everest?", "simple", "bare_facts"),
    ("Who painted the Mona Lisa?", "simple", "bare_facts"),
    ("Population of Tokyo", "simple", "bare_facts"),
    ("Speed of light?", "simple", "bare_facts"),
    ("Currency of Brazil", "simple", "bare_facts"),
    ("How many continents are there?", "simple", "bare_facts"),
    ("Largest ocean", "simple", "bare_facts"),
    ("Year World War 2 ended", "simple", "bare_facts"),
    ("Tallest building in the world", "simple", "bare_facts"),
    ("When was the Eiffel Tower built?", "simple", "bare_facts"),

    # ---------- CONVERSATIONAL FILLERS (all simple) ----------
    # Greetings, idle chat, meta. Should never go to Sonnet.
    ("How's your day going?", "simple", "conversational"),
    ("What's up?", "simple", "conversational"),
    ("Are you a robot?", "simple", "conversational"),
    ("Can you understand me?", "simple", "conversational"),
    ("I'm bored.", "simple", "conversational"),
    ("I'm tired.", "simple", "conversational"),
    ("Are you still there?", "simple", "conversational"),
    ("Goodnight", "simple", "conversational"),

    # ---------- QUICK LOOKUPS (all simple) ----------
    # Translations, conversions, short definitions. One-shot
    # factual answers — local model territory.
    ("What's 'merci' in English?", "simple", "quick_lookup"),
    ("Synonym for happy", "simple", "quick_lookup"),
    ("12 inches in cm", "simple", "quick_lookup"),
    ("Square root of 144", "simple", "quick_lookup"),
    ("What's 30 percent of 250?", "simple", "quick_lookup"),
    ("How many ounces in a pound?", "simple", "quick_lookup"),
    ("Pi to 5 digits", "simple", "quick_lookup"),
    ("Roman numeral for 9", "simple", "quick_lookup"),
    ("What is RAM?", "simple", "quick_lookup"),
    ("What is gravity?", "simple", "quick_lookup"),
    ("What's a meme?", "simple", "quick_lookup"),
    ("What is DNA?", "simple", "quick_lookup"),

    # ---------- TIME / LOCATION (all simple) ----------
    # Note: these labels reflect EMBEDDING classification only; the
    # tool-likely keyword check in app.py auto-escalates "time in"
    # queries to complex separately. We're testing the embedding
    # router here, so the label is what it should classify as in
    # isolation.
    ("What day is it?", "simple", "time_location"),
    ("What's the date today?", "simple", "time_location"),
    ("Time in London?", "simple", "time_location"),
    ("Where is Galesburg?", "simple", "time_location"),
    ("What's tomorrow?", "simple", "time_location"),

    # ---------- CODE TASKS (all complex) ----------
    # Code generation, code explanation, technical depth. Sonnet's
    # bread and butter.
    ("Write a function that checks if a string is a palindrome", "complex", "code"),
    ("How do I deploy a Flask app to production?", "complex", "code"),
    ("Show me a React component that fetches user data", "complex", "code"),
    ("What's the difference between async and threading in JavaScript?", "complex", "code"),
    ("Give me regex for validating email addresses", "complex", "code"),
    ("How do you implement binary search?", "complex", "code"),
    ("Explain the visitor pattern", "complex", "code"),
    ("Write a bash script that monitors disk usage", "complex", "code"),

    # ---------- CREATIVE WRITING (all complex) ----------
    ("Write a limerick about a programmer", "complex", "creative"),
    ("Compose a sonnet about coffee", "complex", "creative"),
    ("Tell me a fairy tale about a lonely robot", "complex", "creative"),
    ("Write a dialogue between a cat and a vacuum cleaner", "complex", "creative"),
    ("Make up an origin story for a superhero called Static Sam", "complex", "creative"),
    ("Write a song chorus about Monday mornings", "complex", "creative"),

    # ---------- MULTI-STEP PLANNING (all complex) ----------
    ("Help me prepare for a job interview at Google", "complex", "planning"),
    ("Walk me through setting up a home network", "complex", "planning"),
    ("How would I start a podcast from scratch?", "complex", "planning"),
    ("Help me decide between two job offers", "complex", "planning"),
    ("Plan my wedding seating chart", "complex", "planning"),

    # ---------- DEEP EXPLANATIONS (all complex) ----------
    # Multi-paragraph answers, real reasoning required. Local Llama
    # often gets the gist but Sonnet does it cleanly.
    ("Explain how blockchain consensus works", "complex", "deep_explain"),
    ("What are the trade-offs of nuclear vs solar energy at scale?", "complex", "deep_explain"),
    ("Discuss the origins of World War 1", "complex", "deep_explain"),
    ("Why do programming languages have different paradigms?", "complex", "deep_explain"),
    ("How does machine learning differ from traditional programming?", "complex", "deep_explain"),
    ("What's the difference between weather and climate?", "complex", "deep_explain"),

    # ---------- PERSUASIVE DRAFTING (all complex) ----------
    ("Help me write an apology to a friend", "complex", "drafting"),
    ("Compose a cover letter for a software engineer position", "complex", "drafting"),
    ("Write a thank-you note for a wedding gift", "complex", "drafting"),
    ("Help me convince my boss for a remote work day", "complex", "drafting"),
    ("Draft a complaint letter about a hotel stay", "complex", "drafting"),

    # ---------- DEBUGGING / TROUBLESHOOTING (all complex) ----------
    ("My WiFi keeps disconnecting, what should I check?", "complex", "debug"),
    ("Why won't my React app build?", "complex", "debug"),
    ("Trace through this recursive function: fib(5)", "complex", "debug"),
    ("Why is my Docker container exiting immediately?", "complex", "debug"),
    ("How do I figure out why my laptop is slow?", "complex", "debug"),

    # ---------- BORDERLINE (mixed labels — judgment calls flagged) ----------
    # Reasonable labelers might disagree on these. Label represents
    # what the SERVER should do; rationale in comments.
    ("Who is Elon Musk?", "simple", "borderline"),                       # factual, single-entity -> simple
    ("What's a good restaurant in Chicago?", "complex", "borderline"),   # subjective rec, ideally tool -> complex
    ("How does the immune system work?", "complex", "borderline"),       # broad explanation
    ("Recommend a book", "complex", "borderline"),                       # personalized rec
    ("What's the best programming language?", "complex", "borderline"),  # opinion + reasoning
    ("When did dinosaurs die out?", "simple", "borderline"),             # factual one-liner
    ("What's a good gift for a 7-year-old?", "complex", "borderline"),   # rec w/ reasoning
    ("How do you tie a tie?", "complex", "borderline"),                  # multi-step instruction
    ("What is photosynthesis?", "simple", "borderline"),                 # textbook def -> simple (vs. "explain to a 10-year-old" which is complex)
    ("Tell me about the Eiffel Tower", "complex", "borderline"),         # "tell me about" -> broader
    ("What does this error mean: KeyError", "complex", "borderline"),    # debugging
    ("Is the earth round?", "simple", "borderline"),                     # factual yes/no
    ("Why do leaves change color?", "simple", "borderline"),             # one-line factual
    ("Best way to learn Python?", "complex", "borderline"),              # pedagogical advice
    ("How do you spell necessary?", "simple", "borderline"),             # spelling lookup
]


MARGINS = [0.00, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15]


def predict(margin: float, score_simple: float, score_complex: float) -> str:
    return "simple" if (score_simple - score_complex) > margin else "complex"


def main() -> int:
    embedder = modes.get_embedder()
    embedder_label = type(embedder).__name__
    n_examples = len(router.SIMPLE_EXAMPLES) + len(router.COMPLEX_EXAMPLES)
    print(f"MODE={config.MODE}  embedder={embedder_label}")
    print(f"Fixture: {len(FIXTURE)} held-out queries across "
          f"{len(set(c for _, _, c in FIXTURE))} categories")
    print(f"Warming router (embeds {n_examples} examples)...")
    t0 = time.perf_counter()
    router.attach_embedder(embedder)
    router.warmup()
    print(f"Warmup done in {time.perf_counter() - t0:.2f}s\n")

    # Embed every fixture query, capture scores + per-query latency.
    print(f"Classifying {len(FIXTURE)} queries...")
    rows = []  # (query, expected, category, score_simple, score_complex, latency_s)
    for query, expected, category in FIXTURE:
        d = router.classify(query)
        rows.append((query, expected, category,
                     d["score_simple"], d["score_complex"], d["latency_s"]))

    latencies = [r[5] for r in rows]
    print(f"  mean: {statistics.mean(latencies)*1000:.1f}ms  "
          f"p50: {statistics.median(latencies)*1000:.1f}ms  "
          f"p95: {sorted(latencies)[int(len(latencies)*0.95)]*1000:.1f}ms  "
          f"max: {max(latencies)*1000:.1f}ms\n")

    # ---- Margin sweep, overall accuracy + asymmetric error breakdown ----
    print("Overall accuracy by margin:")
    print(f"  {'margin':>7}  {'acc':>6}  {'simple_correct':>14}  {'simple_missed':>13}  "
          f"{'complex_correct':>15}  {'false_simple':>12}")
    print("  " + "-" * 80)
    best_margin = None
    best_acc = -1.0
    for m in MARGINS:
        pairs = [(exp, predict(m, ss, sc)) for _, exp, _, ss, sc, _ in rows]
        s_correct = sum(1 for e, p in pairs if e == "simple" and p == "simple")
        s_missed  = sum(1 for e, p in pairs if e == "simple" and p == "complex")
        c_correct = sum(1 for e, p in pairs if e == "complex" and p == "complex")
        false_s   = sum(1 for e, p in pairs if e == "complex" and p == "simple")
        acc = (s_correct + c_correct) / len(rows)
        if acc > best_acc:
            best_acc = acc
            best_margin = m
        print(f"  {m:>7.2f}  {acc*100:>5.1f}%  "
              f"{s_correct:>14}  {s_missed:>13}  {c_correct:>15}  {false_s:>12}")
    print(f"\n  Best margin: {best_margin:.2f} -> {best_acc*100:.1f}%  "
          f"(current default ROUTER_MARGIN_S = {config.ROUTER_MARGIN_S:.2f})\n")

    # ---- Per-category accuracy at the current production margin ----
    prod_margin = config.ROUTER_MARGIN_S
    print(f"Per-category accuracy at production margin = {prod_margin}:")
    cat_buckets: dict[str, list[bool]] = defaultdict(list)
    for q, exp, cat, ss, sc, _ in rows:
        cat_buckets[cat].append(predict(prod_margin, ss, sc) == exp)
    for cat in sorted(cat_buckets, key=lambda c: sum(cat_buckets[c]) / len(cat_buckets[c])):
        bucket = cat_buckets[cat]
        n_correct = sum(bucket)
        n_total = len(bucket)
        bar = "#" * int(20 * n_correct / n_total) + "." * (20 - int(20 * n_correct / n_total))
        print(f"  {cat:<18} [{bar}] {n_correct:>2}/{n_total}  ({n_correct/n_total*100:>5.1f}%)")
    print()

    # ---- Failures at production margin, grouped by category ----
    print(f"Failures at margin {prod_margin} (queries the router got wrong):")
    failures_by_cat = defaultdict(list)
    for q, exp, cat, ss, sc, _ in rows:
        pred = predict(prod_margin, ss, sc)
        if pred != exp:
            failures_by_cat[cat].append((q, exp, pred, ss, sc))
    if not failures_by_cat:
        print("  (none — clean sweep)")
    else:
        for cat in sorted(failures_by_cat):
            print(f"\n  [{cat}]")
            for q, exp, pred, ss, sc in failures_by_cat[cat]:
                margin = ss - sc
                print(f"    expected={exp:<8} got={pred:<8} margin={margin:+.4f}  q={q[:64]}")
    print()

    # ---- Asymmetric-error spotlight (false-simple = under-routing) ----
    # These are the dangerous failures: the cheap local model was given
    # a query it can't handle. False-complex (the inverse) is just money.
    print(f"Under-routing spotlight at margin {prod_margin} (expected complex, got simple):")
    under_routed = [(q, ss, sc) for q, exp, _, ss, sc, _ in rows
                    if exp == "complex" and predict(prod_margin, ss, sc) == "simple"]
    if not under_routed:
        print("  (none — no expected-complex queries leaked to simple)\n")
    else:
        for q, ss, sc in under_routed:
            print(f"  margin={ss-sc:+.4f}  s={ss:.4f}  c={sc:.4f}  q={q[:60]}")
        print()

    print(f"Done. Compare against tests/stage5_router.py — that fixture has "
          f"paraphrases of the example sets and reports inflated accuracy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
