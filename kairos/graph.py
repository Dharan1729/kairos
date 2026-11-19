"""Graph-centrality signal for ranking (after Aider's repo map, see
docs/prior-art.md).

Items in the same scope whose embeddings are similar get a ``related_to``
edge. Edges are created when an item is written (``link_item``), never on
the read path. At read time, PageRank over the edges among the candidates
gives each candidate a multiplier, rescaled so the mean is 1.0 and an item
with no edges is neutral.
"""

from __future__ import annotations

from datetime import UTC, datetime

import networkx as nx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from kairos.embeddings import cosine, from_json
from kairos.models import KnowledgeGraphEdge, KnowledgeItem

EDGE_SIMILARITY_THRESHOLD = 0.6


def _valid_items_in_scope(db: OrmSession, scope_id: str) -> list[KnowledgeItem]:
    now = datetime.now(UTC)
    return (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.scope_id == scope_id,
            KnowledgeItem.embedding_ref.isnot(None),
            (KnowledgeItem.valid_until.is_(None)) | (KnowledgeItem.valid_until > now),
        )
        .all()
    )


def link_item(db: OrmSession, item: KnowledgeItem) -> int:
    """Adds edges between ``item`` and similar valid items in its scope.
    Returns the number of edges created."""
    if not item.embedding_ref:
        return 0
    vec = from_json(item.embedding_ref)
    created = 0
    for other in _valid_items_in_scope(db, item.scope_id):
        if other.id == item.id:
            continue
        try:
            if cosine(vec, from_json(other.embedding_ref or "[]")) < EDGE_SIMILARITY_THRESHOLD:
                continue
        except Exception:
            continue  # a corrupt stored embedding must not block the write
        try:
            with db.begin_nested():  # a concurrent writer may have added the same pair
                db.add(KnowledgeGraphEdge(from_item_id=item.id, to_item_id=other.id, relation="related_to"))
                db.add(KnowledgeGraphEdge(from_item_id=other.id, to_item_id=item.id, relation="related_to"))
            created += 2
        except IntegrityError:
            pass
    db.flush()
    return created


def populate_same_scope_edges(db: OrmSession, scope_id: str) -> int:
    """Links every valid item in a scope (used to backfill edges)."""
    return sum(link_item(db, item) for item in _valid_items_in_scope(db, scope_id))


def compute_centrality(db: OrmSession, item_ids: set[str]) -> dict[str, float]:
    if not item_ids:
        return {}
    edges = (
        db.query(KnowledgeGraphEdge)
        .filter(
            KnowledgeGraphEdge.from_item_id.in_(item_ids),
            KnowledgeGraphEdge.to_item_id.in_(item_ids),
        )
        .all()
    )
    if not edges:
        return {item_id: 1.0 for item_id in item_ids}
    graph = nx.DiGraph()
    graph.add_nodes_from(item_ids)
    graph.add_edges_from((e.from_item_id, e.to_item_id) for e in edges)
    raw = nx.pagerank(graph)
    mean = sum(raw.values()) / len(raw) if raw else 1.0
    if mean == 0:
        return {item_id: 1.0 for item_id in item_ids}
    return {item_id: raw.get(item_id, mean) / mean for item_id in item_ids}
