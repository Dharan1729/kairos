"""Unit tests for kairos/vector_index.py — the hnswlib-based ANN search
that replaced the brute-force per-row cosine loop in api.py's
_retrieve_candidates (the clearest scaling bottleneck flagged in the
enterprise-grade gap review)."""

import numpy as np
import pytest

from kairos.vector_index import (
    MAX_CACHED_INDEXES,
    ScopeVectorIndex,
    reset_cache_for_tests,
    top_k_similar,
)


def _unit_vector(*coords) -> np.ndarray:
    vec = np.array(coords, dtype=np.float32)
    return vec / np.linalg.norm(vec)


@pytest.fixture(autouse=True)
def _reset():
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


class TestScopeVectorIndex:
    def test_query_on_empty_index_returns_empty(self):
        index = ScopeVectorIndex()
        index.build([])
        assert index.query(_unit_vector(1, 0, 0), k=5) == []

    def test_query_finds_nearest_neighbor(self):
        index = ScopeVectorIndex()
        items = [
            ("close", _unit_vector(1.0, 0.01, 0.0)),
            ("far", _unit_vector(0.0, 0.0, 1.0)),
        ]
        index.build([(item_id, np.tile(vec, 128)[:384]) for item_id, vec in items])
        results = index.query(np.tile(_unit_vector(1, 0, 0), 128)[:384], k=2)
        assert results[0][0] == "close"
        assert results[0][1] > results[1][1]  # closer item has higher similarity

    def test_similarity_scores_are_plain_python_floats(self):
        # Regression test: hnswlib returns numpy.float32, which broke JSON
        # serialization (cache payloads, API responses) the first time
        # this was wired up.
        index = ScopeVectorIndex()
        vec = np.tile(_unit_vector(1, 0, 0), 128)[:384]
        index.build([("item-1", vec)])
        results = index.query(vec, k=1)
        assert type(results[0][1]) is float
        assert isinstance(results[0][0], str)


class TestTopKSimilarCaching:
    def test_reuses_index_for_same_key(self):
        vec = np.tile(_unit_vector(1, 0, 0), 128)[:384]
        items = [("item-1", vec)]

        first = top_k_similar(("tenant-a", "scope-x", 1), items=items, query_vector=vec, k=5)
        # Pass an EMPTY items list the second time — if the cache weren't
        # reused, this would build an empty index and return nothing.
        second = top_k_similar(("tenant-a", "scope-x", 1), items=[], query_vector=vec, k=5)

        assert len(first) == 1
        assert first == second  # same cached index, items= ignored on the cache hit

    def test_rebuilds_on_knowledge_version_change(self):
        vec = np.tile(_unit_vector(1, 0, 0), 128)[:384]
        top_k_similar(("tenant-a", "scope-x", 1), items=[("item-1", vec)], query_vector=vec, k=5)
        # Different knowledge_version -> different cache key -> must
        # rebuild from the new items, not reuse the stale index.
        result = top_k_similar(("tenant-a", "scope-x", 2), items=[("item-2", vec)], query_vector=vec, k=5)
        assert result[0][0] == "item-2"

    def test_tenant_isolation_in_index_cache(self):
        # Same scope_key/knowledge_version, different tenant -> different
        # cache entries. Mirrors the tenant_id filtering already required
        # of the bundle cache.
        vec = np.tile(_unit_vector(1, 0, 0), 128)[:384]
        top_k_similar(("tenant-a", "scope-x", 1), items=[("tenant-a-item", vec)], query_vector=vec, k=5)
        result = top_k_similar(("tenant-b", "scope-x", 1), items=[("tenant-b-item", vec)], query_vector=vec, k=5)
        assert result[0][0] == "tenant-b-item"

    def test_lru_eviction_bounds_cache_size(self):
        vec = np.tile(_unit_vector(1, 0, 0), 128)[:384]
        for i in range(MAX_CACHED_INDEXES + 10):
            top_k_similar((f"tenant-{i}", "scope-x", 1), items=[(f"item-{i}", vec)], query_vector=vec, k=1)
        # The earliest-inserted entries should have been evicted — querying
        # with empty items for one of them must not find the old data.
        result = top_k_similar(("tenant-0", "scope-x", 1), items=[], query_vector=vec, k=1)
        assert result == []

    def test_concurrent_access_does_not_raise(self):
        # Regression test: FastAPI runs sync endpoints in a thread pool, so
        # this module-level cache is genuinely accessed from multiple
        # threads. Before _LruIndexCache gained a lock, a hit racing an
        # eviction (move_to_end vs. popitem on the same key from different
        # threads) could raise KeyError from inside a request path.
        import threading

        vec = np.tile(_unit_vector(1, 0, 0), 128)[:384]
        errors = []

        def _hammer(i):
            try:
                for _ in range(20):
                    top_k_similar((f"tenant-{i % 8}", "scope-x", 1), items=[(f"item-{i}", vec)], query_vector=vec, k=1)
            except Exception as e:  # pragma: no cover - only on failure
                errors.append(e)

        threads = [threading.Thread(target=_hammer, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
