"""Ranking: one score per candidate, from separately inspectable factors.

    score = similarity x confidence x graph centrality x freshness x personal boost

- similarity: cosine between the task and the item.
- confidence: provenance weight (curated 1.0, approved proposal 0.8,
  LLM summary 0.6, raw commit message 0.5).
- graph centrality: PageRank multiplier, 1.0 when there is no graph signal.
- freshness: by the item's staleness hint (low 1.0, medium 0.85, high 0.6).
- personal boost: 1.2 for the caller's own personal items.

Candidates below ``min_similarity`` are not ranked at all; they are reported
as dropped with reason "relevance".
"""

from __future__ import annotations

from kairos.pipeline.types import Candidate, ResolvedScope, ScoredCandidate

FRESHNESS_MULTIPLIER = {"low": 1.0, "medium": 0.85, "high": 0.6}
UNKNOWN_FRESHNESS = 0.75
PERSONAL_BOOST = 1.2


class WeightedRanker:
    def __init__(self, min_similarity: float | None = None, personal_boost: float = PERSONAL_BOOST):
        self.min_similarity = min_similarity
        self.personal_boost = personal_boost

    def score(self, c: Candidate, person_id: str | None) -> float:
        s = c.similarity * c.confidence * c.graph_centrality
        s *= FRESHNESS_MULTIPLIER.get(c.staleness_hint, UNKNOWN_FRESHNESS)
        if person_id and c.layer == "personal" and c.scope_id == person_id:
            s *= self.personal_boost
        return s

    def rank(self, candidates: list[Candidate], scope: ResolvedScope) -> list[ScoredCandidate]:
        kept = candidates
        if self.min_similarity is not None:
            kept = [c for c in candidates if c.similarity >= self.min_similarity]
        scored = [ScoredCandidate(c, self.score(c, scope.person_id)) for c in kept]
        # Ties broken by id so the same inputs always give the same bundle.
        return sorted(scored, key=lambda sc: (-sc.score, sc.candidate.id))

    def filtered_out(self, candidates: list[Candidate]) -> list[Candidate]:
        if self.min_similarity is None:
            return []
        return [c for c in candidates if c.similarity < self.min_similarity]
