"""Stage 2 verification: Ollama + Llama 3.1 8B GPU benchmark.

Sends 5 fixed prompts to the local Ollama daemon, computes tokens/sec from
the eval_count / eval_duration fields of each response, and reports response
quality so we can decide if Llama 8B is good enough for the 'simple' route.

Run from repo root after activating .venv:
    python tests/stage2_ollama.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import requests

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"  # never 'localhost' on Windows — 2s DNS penalty
MODEL = "llama3.1:8b-instruct-q5_K_M"

PROMPTS = [
    ("factual",        "What is the capital of France? Answer in one sentence."),
    ("arithmetic",     "If I have 3 apples and give away 2, then buy 5 more, how many do I have? Show your reasoning briefly."),
    ("conversational", "How was your day? Reply in a friendly, casual tone in 2-3 sentences."),
    ("summarization",  "Summarize this in one sentence: The Industrial Revolution, beginning in late 18th-century Britain, transformed agrarian societies into industrial economies through mechanization, factory systems, and steam power, profoundly reshaping labor, urbanization, and global trade."),
    ("explanation",    "Explain quantum tunneling in 2 short sentences for a curious adult."),
]


def call_ollama(prompt: str) -> dict:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 256},
    }
    t0 = time.perf_counter()
    r = requests.post(OLLAMA_URL, json=payload, timeout=120)
    wall_time = time.perf_counter() - t0
    r.raise_for_status()
    data = r.json()
    data["_wall_time"] = wall_time
    return data


def main() -> int:
    print(f"Model: {MODEL}")
    print(f"Endpoint: {OLLAMA_URL}")
    print()

    results = []
    for i, (label, prompt) in enumerate(PROMPTS, 1):
        print(f"--- [{i}/{len(PROMPTS)}] {label} ---")
        print(f"Q: {prompt}")
        try:
            data = call_ollama(prompt)
        except Exception as exc:
            print(f"FAILED: {exc!r}")
            continue

        reply = data.get("response", "").strip()
        eval_count = data.get("eval_count", 0)
        eval_dur_ns = data.get("eval_duration", 1)  # nanoseconds
        prompt_eval_count = data.get("prompt_eval_count", 0)
        wall_time = data["_wall_time"]
        tps = eval_count / (eval_dur_ns / 1e9) if eval_dur_ns > 0 else 0.0

        print(f"A: {reply}")
        print(f"   prompt_tokens={prompt_eval_count}  output_tokens={eval_count}  "
              f"tps={tps:.1f}  wall={wall_time:.2f}s")
        print()
        results.append({"label": label, "tps": tps, "wall": wall_time, "tokens": eval_count})

    print("=" * 64)
    if results:
        mean_tps = sum(r["tps"] for r in results) / len(results)
        total_tokens = sum(r["tokens"] for r in results)
        total_wall = sum(r["wall"] for r in results)
        print(f"  Mean output TPS:  {mean_tps:.1f}")
        print(f"  Total tokens:     {total_tokens}")
        print(f"  Total wall time:  {total_wall:.2f}s")
    print("=" * 64)

    # Verify GPU offload — should show '100% GPU' in PROCESSOR column
    print()
    print("--- ollama ps (should show 100% GPU) ---")
    try:
        out = subprocess.check_output(["ollama", "ps"], text=True)
        print(out)
    except Exception as exc:
        print(f"could not run ollama ps: {exc!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
