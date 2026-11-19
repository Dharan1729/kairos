"""Candidate retrieval: the readable items, with their similarity to the task.

Only items that are valid now (bi-temporal filter) and that live in one of
the resolved scopes are ever loaded. Similarity comes from an ANN index
(kairos/vector_index.py); if that fails, brute-force cosine is used instead.
Graph centrality (kairos/graph.py) is attached for the ranker. Both
enrichments fail open: losing them degrades ranking, never the response.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import numpy as np
from sqlalchemy.orm import Session as OrmSession

from kairos.embeddings import cosine, from_json
from kairos.graph import compute_centrality
from kairos.models import KnowledgeItem
from kairos.pipeline.types import Candidate, ResolvedScope, RetrievalResult
from kairos.vector_index import DEFAULT_TOP_K, top_k_similar


def _valid_now():
    now = datetime.now(UTC)
    return (KnowledgeItem.valid_until.is_(None)) | (KnowledgeItem.valid_until > now)


def readable_versions(db: OrmSession, scope_ids) -> list[tuple[str, str, int]]:
    """(item id, scope id, knowledge_version) of every item valid now in the
    given scopes. Light query, no content or embeddings."""
    if not scope_ids:
        return []
    return [
        (row.id, row.scope_id, row.knowledge_version)
        for row in db.query(KnowledgeItem.id, KnowledgeItem.scope_id, KnowledgeItem.knowledge_version)
        .filter(KnowledgeItem.scope_id.in_(list(scope_ids)), _valid_now())
        .all()
    ]


def fingerprint(versions: list[tuple[str, str, int]]) -> str:
    """Changes whenever any readable item is added, removed, superseded,
    expires or is edited through the ORM."""
    h = hashlib.sha256()
    for item_id, scope_id, version in sorted(versions):
        h.update(f"{item_id}\x1f{scope_id}\x1f{version}\x1e".encode())
    return h.hexdigest()


class VectorRetriever:
    def __init__(self, top_k: int = DEFAULT_TOP_K):
        self.top_k = top_k

    def retrieve(self, db: OrmSession, scope: ResolvedScope, query: np.ndarray) -> RetrievalResult:
        if not scope.chain:
            return RetrievalResult([])
        rows = db.query(KnowledgeItem).filter(KnowledgeItem.scope_id.in_(list(scope.scope_ids)), _valid_now()).all()
        if not rows:
            return RetrievalResult([])
        by_id = {row.id: row for row in rows}

        try:
            centrality = compute_centrality(db, set(by_id))
        except Exception:
            centrality = {}

        vectors: list[tuple[str, np.ndarray]] = []
        unembedded: list[str] = []
        for row in rows:
            if not row.embedding_ref:
                unembedded.append(row.id)
                continue
            try:
                vectors.append((row.id, from_json(row.embedding_ref)))
            except Exception:
                unembedded.append(row.id)  # one corrupt embedding must not take out the request

        key = (
            scope.tenant_id,
            tuple((r.layer, r.scope_id) for r in scope.chain),
            fingerprint([(r.id, r.scope_id, r.knowledge_version) for r in rows]),
        )
        try:
            neighbours = top_k_similar(key, vectors, query, k=self.top_k)
        except Exception:
            scored = [(item_id, cosine(query, vec)) for item_id, vec in vectors]
            neighbours = sorted(scored, key=lambda p: p[1], reverse=True)[: self.top_k]

        def make(row: KnowledgeItem, similarity: float) -> Candidate:
            return Candidate(
                id=row.id,
                layer=row.layer,
                scope_id=row.scope_id,
                content=row.content,
                confidence=row.confidence,
                staleness_hint=row.staleness_hint,
                similarity=similarity,
                graph_centrality=centrality.get(row.id, 1.0),
                knowledge_version=row.knowledge_version,
            )

        candidates = [make(by_id[item_id], sim) for item_id, sim in neighbours if item_id in by_id]
        candidates.extend(make(by_id[item_id], 0.0) for item_id in unembedded)
        seen = {c.id for c in candidates}
        not_considered: dict[str, int] = {}
        for row in rows:
            if row.id not in seen:
                not_considered[row.layer] = not_considered.get(row.layer, 0) + 1
        return RetrievalResult(candidates, not_considered)
