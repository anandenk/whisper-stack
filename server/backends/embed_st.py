"""sentence-transformers embedding backend (cloud mode).

Loads the all-MiniLM-L6-v2 model in-process (~90 MB on disk + ~50 MB
runtime) so the cloud-mode router doesn't need a separate embedding
service. Returns (n, 384) float32 — the router normalizes.

Cold start downloads the model from HuggingFace to the local cache the
first time; the Phase 9 Dockerfile pre-bakes it into the image so prod
boot doesn't pay the download.
"""

from __future__ import annotations

import numpy as np


class SentenceTransformerEmbedder:
    """Singleton-style wrapper. Model is shared via a class-level cache so
    re-instantiating in tests doesn't reload weights."""

    _model = None  # SentenceTransformer | None — lazy-typed to avoid import at module top

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        self.model_name = model_name

    def _get_model(self):
        if SentenceTransformerEmbedder._model is None:
            # Late import: keeps `import server.modes` cheap when MODE=local
            # (sentence-transformers + torch ≈ 250 MB of import-time overhead).
            from sentence_transformers import SentenceTransformer
            SentenceTransformerEmbedder._model = SentenceTransformer(self.model_name)
        return SentenceTransformerEmbedder._model

    def is_loaded(self) -> bool:
        return SentenceTransformerEmbedder._model is not None

    def embed(self, texts: list[str]) -> np.ndarray:
        model = self._get_model()
        # convert_to_numpy returns float32 by default; normalize_embeddings=False
        # because the router does its own normalization.
        vecs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=False)
        return np.asarray(vecs, dtype=np.float32)

    def warmup(self) -> None:
        """Trigger model load + one forward pass so first user request is warm."""
        self.embed(["warmup"])
