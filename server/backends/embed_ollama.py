"""Ollama embedding backend (local-mode router)."""

from __future__ import annotations

import numpy as np
import requests

from server import config


class OllamaEmbedder:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or config.EMBED_MODEL
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def embed(self, texts: list[str]) -> np.ndarray:
        """One round trip to /api/embed. Returns (n, dim) float32 array."""
        r = requests.post(
            config.OLLAMA_EMBED_URL,
            json={"model": self.model, "input": texts},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        self._loaded = True
        return np.asarray(data["embeddings"], dtype=np.float32)

    def warmup(self) -> None:
        """Embedding a single token triggers Ollama to load the embed model."""
        self.embed(["warmup"])
