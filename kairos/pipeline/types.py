"""Data types and stage interfaces for the context pipeline.

The pipeline has five stages, each behind a small protocol so it can be
replaced on its own:

    ScopeResolver      who is asking, and which scopes may they read?
    CandidateRetriever which readable items might be relevant to the task?
    Ranker             in what order should they be considered?
    Budgeter           which of them fit in the token budget (compressing the last)?
    BundleCache        has an equivalent bundle already been assembled?

``kairos.pipeline.service.ContextService`` wires them together. The HTTP API,
the CLI and (through the API) the MCP server are thin adapters over it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from sqlalchemy.orm import Session as OrmSession

from kairos.access import Caller
from kairos.directory import PRIVATE_LAYERS, SHARED_LAYERS

LAYER_ORDER = ("org", "team", "project", "personal", "session")


@dataclass(frozen=True)
class ContextPrincipal:
    """Who the context is for, as stated by the request. ``user_id`` is
    checked against the credential (kairos.access.acting_person_id); the
    session and repo are checked against the directory during resolution."""

    user_id: str | None = None
    agent_type: str = "unknown"
    repo: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class Task:
    description: str
    recent_messages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeRef:
    layer: str
    scope_id: str


@dataclass(frozen=True)
class ResolvedScope:
    """The scopes one caller may read for one request, in layer order.
    ``notes`` explain anything the request asked for that was left out."""

    tenant_id: str
    org_id: str
    person_id: str | None
    chain: tuple[ScopeRef, ...]
    notes: tuple[str, ...] = ()

    @property
    def scope_ids(self) -> frozenset[str]:
        return frozenset(ref.scope_id for ref in self.chain)

    @property
    def shared_chain(self) -> tuple[ScopeRef, ...]:
        return tuple(ref for ref in self.chain if ref.layer in SHARED_LAYERS)

    @property
    def private_chain(self) -> tuple[ScopeRef, ...]:
        return tuple(ref for ref in self.chain if ref.layer in PRIVATE_LAYERS)

    def scope_for(self, layer: str) -> str | None:
        return next((ref.scope_id for ref in self.chain if ref.layer == layer), None)


@dataclass
class Candidate:
    id: str
    layer: str
    scope_id: str
    content: str
    confidence: float
    staleness_hint: str
    similarity: float
    graph_centrality: float = 1.0
    knowledge_version: int = 1


@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: float


@dataclass
class RetrievalResult:
    candidates: list[Candidate]
    # Items in readable scopes that never reached ranking, by layer
    # (only when a scope holds more items than the retriever's top-k).
    not_considered: dict[str, int] = field(default_factory=dict)


@dataclass
class BundleItem:
    layer: str
    content: str
    source: str  # knowledge item id


@dataclass
class BudgetResult:
    bundle: list[BundleItem]
    dropped: list[dict] = field(default_factory=list)  # [{"layer", "count", "reason"}]
    tokens_used: int = 0


@dataclass
class CacheInfo:
    outcome: str  # hit | miss | stale | disabled | bypass
    similarity: float = 0.0
    cache_key: str = ""

    @property
    def hit(self) -> bool:
        return self.outcome == "hit"


@dataclass
class AssembleResult:
    bundle: list[BundleItem]
    dropped: list[dict]
    tokens_used: int
    cache: CacheInfo
    scope: ResolvedScope
    latency_ms: float


class ScopeResolver(Protocol):
    def resolve(self, db: OrmSession, caller: Caller, principal: ContextPrincipal) -> ResolvedScope: ...


class CandidateRetriever(Protocol):
    def retrieve(self, db: OrmSession, scope: ResolvedScope, query: np.ndarray) -> RetrievalResult: ...


class Ranker(Protocol):
    def rank(self, candidates: list[Candidate], scope: ResolvedScope) -> list[ScoredCandidate]: ...


class Budgeter(Protocol):
    def fit(self, ranked: list[ScoredCandidate], budget_tokens: int) -> BudgetResult: ...
