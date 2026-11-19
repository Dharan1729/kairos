"""ContextService: the one core function every adapter calls.

    assemble(caller, principal, task, budget)
      1. resolve scope       (who may read what; fails closed)
      2. look up the cache   (scope-partitioned, validated on hit)
      3. retrieve candidates (readable, valid-now items + similarity)
      4. rank                (similarity x confidence x centrality x freshness x boost)
      5. fit the budget      (greedy, compress the last item, report drops)
      6. store in the cache, write an audit row

``search`` is the same pipeline for an ad-hoc query, without the cache.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict

from sqlalchemy.orm import Session as OrmSession

from kairos.access import Caller, InvalidRequest
from kairos.config import load_settings
from kairos.embeddings import Embedder, get_embedder
from kairos.models import AuditLedger, content_hash
from kairos.pipeline.budget import GreedyBudgeter, add_dropped
from kairos.pipeline.cache import ScopedBundleCache, partition_key
from kairos.pipeline.ranking import WeightedRanker
from kairos.pipeline.retrieval import VectorRetriever, fingerprint, readable_versions
from kairos.pipeline.scope import DirectoryScopeResolver
from kairos.pipeline.types import (
    AssembleResult,
    Budgeter,
    BudgetResult,
    BundleItem,
    CacheInfo,
    CandidateRetriever,
    ContextPrincipal,
    Ranker,
    ResolvedScope,
    ScopeResolver,
    Task,
)
from kairos.telemetry import annotate_current_span


class ContextService:
    def __init__(
        self,
        *,
        resolver: ScopeResolver | None = None,
        retriever: CandidateRetriever | None = None,
        ranker: Ranker | None = None,
        budgeter: Budgeter | None = None,
        cache: ScopedBundleCache | None = None,
        embedder: Embedder | None = None,
        cache_enabled: bool | None = None,
        cross_user_reuse: bool | None = None,
        max_budget_tokens: int | None = None,
    ):
        s = load_settings()
        self.resolver = resolver or DirectoryScopeResolver()
        self.retriever = retriever or VectorRetriever()
        self.ranker = ranker or WeightedRanker()
        self.budgeter = budgeter or GreedyBudgeter()
        self.cache = cache or ScopedBundleCache(s.cache_similarity, s.cache_ttl_seconds)
        self.embedder = embedder or get_embedder()
        self.cache_enabled = s.cache_enabled if cache_enabled is None else cache_enabled
        self.cross_user_reuse = s.cache_cross_user_reuse if cross_user_reuse is None else cross_user_reuse
        self.max_budget_tokens = max_budget_tokens or s.max_budget_tokens

    @classmethod
    def from_settings(cls) -> ContextService:
        return cls()

    def _check_budget(self, budget_tokens: int) -> None:
        if not 1 <= budget_tokens <= self.max_budget_tokens:
            raise InvalidRequest(f"budget_tokens must be between 1 and {self.max_budget_tokens}")

    def _run_pipeline(self, db: OrmSession, scope: ResolvedScope, query_vec, budget_tokens: int) -> BudgetResult:
        retrieved = self.retriever.retrieve(db, scope, query_vec)
        ranked = self.ranker.rank(retrieved.candidates, scope)
        result = self.budgeter.fit(ranked, budget_tokens)
        extra: dict[tuple[str, str], int] = {}
        for layer, count in retrieved.not_considered.items():
            add_dropped(extra, layer, "retrieval_limit", count)
        ranked_ids = {sc.candidate.id for sc in ranked}
        for c in retrieved.candidates:
            if c.id not in ranked_ids:
                add_dropped(extra, c.layer, "relevance")
        result.dropped.extend({"layer": la, "count": n, "reason": r} for (la, r), n in sorted(extra.items()))
        return result

    def assemble(
        self, db: OrmSession, caller: Caller, principal: ContextPrincipal, task: Task, budget_tokens: int
    ) -> AssembleResult:
        start = time.perf_counter()
        self._check_budget(budget_tokens)
        scope = self.resolver.resolve(db, caller, principal)
        query_vec = self.embedder.embed(task.description)

        cache_info = CacheInfo("disabled")
        key = None
        if self.cache_enabled:
            versions = readable_versions(db, scope.scope_ids)
            key = partition_key(
                scope=scope,
                readable_fingerprint=fingerprint(versions),
                budget_tokens=budget_tokens,
                embedder_name=self.embedder.name,
                cross_user_reuse=self.cross_user_reuse,
            )
            found = self.cache.lookup(db, caller.tenant_id, key, query_vec, scope)
            cache_info = CacheInfo(found.outcome, found.similarity, found.entry.cache_key if found.entry else "")
            if found.entry is not None:
                payload = json.loads(found.entry.bundle)
                result = BudgetResult(
                    bundle=[BundleItem(**b) for b in payload["bundle"]],
                    dropped=payload["dropped"],
                    tokens_used=payload["tokens_used"],
                )
                return self._finish(db, caller, principal, scope, result, cache_info, start, "assemble_context")

        result = self._run_pipeline(db, scope, query_vec, budget_tokens)
        if self.cache_enabled and key is not None:
            contents = self._contents(db, [b.source for b in result.bundle])
            entry = self.cache.store(
                db,
                caller.tenant_id,
                key,
                query_vec,
                {
                    "bundle": [asdict(b) for b in result.bundle],
                    "dropped": result.dropped,
                    "tokens_used": result.tokens_used,
                },
                [(item_id, content_hash(text)) for item_id, text in contents.items()],
            )
            cache_info.cache_key = entry.cache_key
        return self._finish(db, caller, principal, scope, result, cache_info, start, "assemble_context")

    def search(
        self, db: OrmSession, caller: Caller, principal: ContextPrincipal, query: str, budget_tokens: int
    ) -> AssembleResult:
        start = time.perf_counter()
        self._check_budget(budget_tokens)
        scope = self.resolver.resolve(db, caller, principal)
        result = self._run_pipeline(db, scope, self.embedder.embed(query), budget_tokens)
        return self._finish(db, caller, principal, scope, result, CacheInfo("bypass"), start, "search_knowledge")

    @staticmethod
    def _contents(db: OrmSession, item_ids: list[str]) -> dict[str, str]:
        from kairos.models import KnowledgeItem

        if not item_ids:
            return {}
        rows = db.query(KnowledgeItem.id, KnowledgeItem.content).filter(KnowledgeItem.id.in_(item_ids)).all()
        return {row.id: row.content for row in rows}

    @staticmethod
    def _finish(
        db: OrmSession,
        caller: Caller,
        principal: ContextPrincipal,
        scope: ResolvedScope,
        result: BudgetResult,
        cache: CacheInfo,
        start: float,
        request_type: str,
    ) -> AssembleResult:
        latency_ms = (time.perf_counter() - start) * 1000
        db.add(
            AuditLedger(
                tenant_id=caller.tenant_id,
                request_type=request_type,
                user_id=scope.person_id or principal.user_id or "(none)",
                agent_type=principal.agent_type,
                repo=principal.repo,
                session_id=principal.session_id,
                cache_hit=cache.hit,
                outcome=cache.outcome,
                tokens_used=result.tokens_used,
                tokens_saved_estimate=result.tokens_used if cache.hit else 0,
                latency_ms=latency_ms,
            )
        )
        db.commit()
        annotate_current_span(
            **{
                "kairos.tenant_id": caller.tenant_id,
                "kairos.cache_outcome": cache.outcome,
                "kairos.tokens_used": result.tokens_used,
            }
        )
        return AssembleResult(
            bundle=result.bundle,
            dropped=result.dropped,
            tokens_used=result.tokens_used,
            cache=cache,
            scope=scope,
            latency_ms=latency_ms,
        )
