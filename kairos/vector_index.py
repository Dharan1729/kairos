"""Approximate nearest-neighbour search over one readable item set (hnswlib).

Indexes are cached in-process, keyed by (tenant, scope chain, fingerprint of
the readable items' ids and versions). Any write to a readable scope changes
the fingerprint, so a stale index is never queried; it simply ages out of the
bounded LRU.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

import hnswlib
import numpy as np

MAX_CACHED_INDEXES = 256
# Candidates handed to ranking. Items beyond this never reach ranking; they
# are reported in the response's `dropped` list with reason
# "retrieval_limit". At the scale Kairos targets (hundreds to low thousands of
# items per readable set) this does not fire.
DEFAULT_TOP_K = 500


class ScopeVectorIndex:
    def __init__(self):
        self._index: hnswlib.Index | None = None
        self._id_map: list[str] = []

    def build(self, items: list[tuple[str, np.ndarray]]) -> None:
        if not items:
            self._index, self._id_map = None, []
            return
        dim = int(items[0][1].shape[0])
        index = hnswlib.Index(space="cosine", dim=dim)
        index.init_index(max_elements=len(items), ef_construction=200, M=16)
        index.add_items(np.stack([vec for _, vec in items]).astype(np.float32), list(range(len(items))))
        index.set_ef(max(50, len(items)))
        self._index = index
        self._id_map = [item_id for item_id, _ in items]

    def query(self, query_vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        """[(item_id, cosine similarity)], nearest first. Plain Python types
        (numpy scalars are not JSON-serializable)."""
        if self._index is None or not self._id_map:
            return []
        k = min(k, len(self._id_map))
        labels, distances = self._index.knn_query(query_vector.astype(np.float32), k=k)
        pairs = zip(labels[0], distances[0], strict=True)
        return [(str(self._id_map[label]), float(1.0 - dist)) for label, dist in pairs]


class _LruIndexCache:
    """Thread-safe: FastAPI runs sync endpoints in a thread pool."""

    def __init__(self, max_size: int):
        self.max_size = max_size
        self._store: OrderedDict[tuple, ScopeVectorIndex] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_build(self, key: tuple, items: list[tuple[str, np.ndarray]]) -> ScopeVectorIndex:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                return self._store[key]
        index = ScopeVectorIndex()
        index.build(items)  # outside the lock: a slow build must not block other lookups
        with self._lock:
            self._store[key] = index
            self._store.move_to_end(key)
            if len(self._store) > self.max_size:
                self._store.popitem(last=False)
        return index

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_cache = _LruIndexCache(MAX_CACHED_INDEXES)


def top_k_similar(
    cache_key: tuple, items: list[tuple[str, np.ndarray]], query_vector: np.ndarray, k: int = DEFAULT_TOP_K
) -> list[tuple[str, float]]:
    return _cache.get_or_build(cache_key, items).query(query_vector, k)


def reset_cache_for_tests() -> None:
    _cache.clear()
