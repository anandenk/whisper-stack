"""Cloud-mode smoke test: SentenceTransformerEmbedder.

Loads MiniLM-L6, embeds the router example set, asserts shape (24, 384)
and float32 dtype, and prints cold + warm latency. Free; no network call
after the model is cached locally.

Run from repo root after activating .venv:
    python tests/cloud_embed_st.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.backends.embed_st import SentenceTransformerEmbedder
from server.router import COMPLEX_EXAMPLES, SIMPLE_EXAMPLES


def must(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAIL: {msg}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    examples = SIMPLE_EXAMPLES + COMPLEX_EXAMPLES
    print(f"Loading SentenceTransformerEmbedder + embedding "
          f"{len(examples)} router examples (cold)...")
    embedder = SentenceTransformerEmbedder()
    must(not embedder.is_loaded(), "is_loaded() should be False before first call")

    t0 = time.perf_counter()
    vecs = embedder.embed(examples)
    cold_s = time.perf_counter() - t0

    must(isinstance(vecs, np.ndarray), f"embed() returned {type(vecs)}, expected ndarray")
    must(vecs.dtype == np.float32, f"dtype {vecs.dtype}, expected float32")
    must(vecs.shape == (len(examples), 384),
         f"shape {vecs.shape}, expected ({len(examples)}, 384)")
    must(embedder.is_loaded(), "is_loaded() should be True after first call")
    print(f"  cold (load + embed {len(examples)}): {cold_s*1000:.0f}ms")

    # Warm: re-embed the same set so the model + tokenizer are hot.
    t0 = time.perf_counter()
    _ = embedder.embed(examples)
    warm_s = time.perf_counter() - t0
    print(f"  warm (embed {len(examples)}):         {warm_s*1000:.0f}ms")

    # Single-query timing (matches the router's per-request shape).
    t0 = time.perf_counter()
    _ = embedder.embed(["What's the capital of France?"])
    single_s = time.perf_counter() - t0
    print(f"  warm (embed 1):          {single_s*1000:.0f}ms")

    print()
    print(f"shape: {vecs.shape}  dtype: {vecs.dtype}  "
          f"first row mean/std: {vecs[0].mean():+.4f} / {vecs[0].std():+.4f}")
    print()
    print("MiniLM-L6 wired — cloud-mode embedder is functional.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
