"""Tables Kairos owns: tenants and credentials, knowledge items and their
graph, proposed knowledge, the bundle cache, and the audit ledger.

The org directory (orgs, teams, people, projects, sessions) is in
``kairos/directory.py``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from kairos.db import Base


def utcnow():
    return datetime.now(UTC)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TenantConfig(Base):
    """One tenant maps to one org. The sso_* columns configure optional
    per-tenant JWT authentication against the tenant's own identity provider
    (kairos/sso.py)."""

    __tablename__ = "tenant_configs"

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    default_budget_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4000)

    sso_issuer: Mapped[str | None] = mapped_column(String, nullable=True, unique=True, index=True)
    sso_audience: Mapped[str | None] = mapped_column(String, nullable=True)
    sso_jwks_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # A pinned JWKS document. Used instead of fetching sso_jwks_url when set.
    sso_jwks_static: Mapped[str | None] = mapped_column(String, nullable=True)
    sso_role_claim: Mapped[str] = mapped_column(String, nullable=False, default="kairos_role")
    sso_default_role: Mapped[str] = mapped_column(String, nullable=False, default="viewer")
    # The claim naming the directory person (Person.id) a token acts as.
    sso_user_claim: Mapped[str | None] = mapped_column(String, nullable=True, default="sub")


class ApiKey(Base):
    """Only the SHA-256 of the raw key is stored. ``person_id`` binds the key
    to one person in the directory: a bound key can only ever act as that
    person. An unbound key is a service credential (see
    docs/guide/concepts.md, "Who is asking")."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, ForeignKey("tenant_configs.tenant_id"), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="member")  # viewer < member < admin
    person_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class KnowledgeItem(Base):
    """One fact, in one scope.

    Bi-temporal: ``valid_from``/``valid_until`` record when the fact holds;
    a superseded fact gets ``valid_until`` set and stays in the table for the
    audit trail. Only items valid *now* are retrieved.

    ``knowledge_version`` is set from a per-scope counter on insert and
    increases on every content change of the row (see the hook below). The
    bundle cache keys on the versions of every readable item."""

    __tablename__ = "knowledge_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    layer: Mapped[str] = mapped_column(String, nullable=False)  # org|team|project|personal|session
    scope_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_ref: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[float]
    staleness_hint: Mapped[str] = mapped_column(String, nullable=False, default="medium")  # low|medium|high
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # git | slack | jira | meeting | human | llm_summary
    origin: Mapped[str] = mapped_column(String, nullable=False, default="human")
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    knowledge_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # null = still valid
    superseded_by_id: Mapped[str | None] = mapped_column(String, nullable=True)


@event.listens_for(KnowledgeItem, "before_update")
def _bump_version_on_content_change(mapper, connection, target):
    """An in-place edit made through the ORM (an admin script, a data fix)
    must change the cache key of bundles that used the item. Raw SQL edits
    bypass this hook; the cache also re-checks the content hash of every item
    in a bundle before serving it (kairos/pipeline/cache.py)."""
    state = inspect(target)
    if any(state.attrs[name].history.has_changes() for name in ("content", "scope_id", "layer", "valid_until")):
        target.knowledge_version = (target.knowledge_version or 0) + 1


class KnowledgeGraphEdge(Base):
    __tablename__ = "knowledge_graph_edges"
    __table_args__ = (UniqueConstraint("from_item_id", "to_item_id", "relation", name="uq_edge_pair_relation"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_item_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_items.id"), nullable=False)
    to_item_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_items.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String, nullable=False)  # e.g. related_to


class CandidateKnowledge(Base):
    """A proposed fact waiting for human review."""

    __tablename__ = "candidate_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposed_content: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_layer: Mapped[str] = mapped_column(String, nullable=False)
    proposed_scope_id: Mapped[str] = mapped_column(String, nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending|approved|rejected
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    promoted_item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    supersedes_item_id: Mapped[str | None] = mapped_column(String, nullable=True)


class CacheEntry(Base):
    """An assembled bundle, reusable only inside its partition.

    ``partition_key`` hashes everything that must match exactly for reuse:
    tenant, the caller's resolved scope chain, the caller's identity (or the
    fingerprint of their private items when cross-user reuse is on), the
    version of every readable item, the budget and the embedder. Only the
    task embedding is compared by similarity, and only within a partition.
    ``contributing`` lists [item id, content sha256] for every item in the
    bundle; they are re-checked on every hit."""

    __tablename__ = "cache_entries"

    cache_key: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    partition_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    embedding: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list[float]
    bundle: Mapped[str] = mapped_column(Text, nullable=False)  # JSON payload
    contributing: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list[[item_id, sha256]]
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLedger(Base):
    """One row per assemble_context or search_knowledge call."""

    __tablename__ = "audit_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # assemble_context | search_knowledge
    request_type: Mapped[str] = mapped_column(String, nullable=False, default="assemble_context")
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_type: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)  # hit|miss|stale|disabled|bypass
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_saved_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
