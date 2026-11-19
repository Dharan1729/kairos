"""Text embedders.

Two implementations behind one small protocol:

- ``SentenceTransformerEmbedder`` (default): a local sentence-transformers
  model, ``all-MiniLM-L6-v2`` unless ``KAIROS_EMBEDDING_MODEL`` says
  otherwise. The model (about 90 MB) is downloaded from Hugging Face on first
  use and cached under ``~/.cache/huggingface``. Nothing is sent anywhere
  after that.
- ``HashingEmbedder``: a dependency-free bag-of-words hashing embedder. No
  download, no semantic understanding (paraphrases do not match). Useful for
  air-gapped smoke tests; not for real use.

Every stored knowledge item carries the vector of the embedder that was
active when it was written. Changing the embedder means re-embedding
(``kairos reembed``).
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from typing import Protocol

import numpy as np

from kairos.config import load_settings


class Embedder(Protocol):
    name: str

    def embed(self, text: str) -> np.ndarray:
        """Returns a unit-length float32 vector."""
        ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        self.name = f"st:{model_name}"
        self._model_name = model_name

    @property
    def _model(self):
        return _load_sentence_transformer(self._model_name)

    def embed(self, text: str) -> np.ndarray:
        vec = self._model.encode([text], normalize_embeddings=True)[0]
        return np.asarray(vec, dtype=np.float32)


@lru_cache(maxsize=4)
def _load_sentence_transformer(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


class HashingEmbedder:
    def __init__(self, dim: int = 384):
        self.name = f"hashing:{dim}"
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec


@lru_cache(maxsize=8)
def _build(kind: str, model_name: str) -> Embedder:
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "sentence-transformers":
        return SentenceTransformerEmbedder(model_name)
    raise ValueError(f"unknown KAIROS_EMBEDDER {kind!r} (expected 'sentence-transformers' or 'hashing')")


def get_embedder() -> Embedder:
    s = load_settings()
    return _build(s.embedder, s.embedding_model)


def embed(text: str) -> np.ndarray:
    return get_embedder().embed(text)


def to_json(vec: np.ndarray) -> str:
    return json.dumps([round(float(x), 7) for x in vec])


def from_json(blob: str) -> np.ndarray:
    return np.asarray(json.loads(blob), dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else 0.0
