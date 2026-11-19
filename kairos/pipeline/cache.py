"""The bundle cache: reuse an assembled bundle only when it is provably the
bundle this caller would get anyway.

Safety rules (each is enforced in code below and tested in
tests/test_cache_safety.py):

1. Partition first, compare second. Similarity search only runs inside one
   partition. The partition key is a hash of everything that must match
   exactly: tenant, the caller's resolved scope chain, who the caller is,
   the id and version of every item the caller can read, the budget and the
   embedder. Two callers whose readable knowledge differs in any way never
   share a partition, so they can never be served each other's bundles.
2. Private layers are never shared. By default the caller's person id is
   part of the key, so nothing is reused across users. With
   ``KAIROS_CACHE_CROSS_USER_REUSE=true`` the person and session scopes are
   left out of the key, but the readable-item fingerprint still covers the
   caller's personal and session items: two users share a bundle only if
   neither has any private items in play and their shared scopes match
   exactly.
3. Any change invalidates. Adding, superseding, expiring or editing (through
   the ORM) any readable item changes the fingerprint and therefore the
   partition. On every hit, each item in the bundle is re-read and checked:
   still valid, still in a scope the caller can read, same content hash. If
   any check fails the entry is deleted and the request is assembled fresh
   (outcome ``stale``). This catches edits made with raw SQL too.
4. Budgets are exact. The budget is part of the key, so a bundle built for
   a larger budget is never served to a smaller one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy.orm import Session as OrmSession

from kairos.embeddings import cosine, from_json, to_json
from kairos.models import CacheEntry, KnowledgeItem, content_hash
from kairos.pipeline.types import ResolvedScope

MAX_ENTRIES_PER_PARTITION = 32
KEY_VERSION = 2


def partition_key(
    *,
    scope: ResolvedScope,
    readable_fingerprint: str,
    budget_tokens: int,
    embedder_name: str,
    cross_user_reuse: bool,
    kind: str = "assemble",
) -> str:
    if cross_user_reuse:
        chain = scope.shared_chain
        who = "shared"  # safe only because readable_fingerprint covers private items
    else:
        chain = scope.chain
        who = f"person:{scope.person_id}"
    payload = {
        "v": KEY_VERSION,
        "kind": kind,
        "tenant": scope.tenant_id,
        "chain": [[ref.layer, ref.scope_id] for ref in chain],
        "who": who,
        "readable": readable_fingerprint,
        "budget": budget_tokens,
        "embedder": embedder_name,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass
class CacheLookup:
    entry: CacheEntry | None
    similarity: float
    outcome: str  # hit | miss | stale


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class ScopedBundleCache:
    def __init__(self, similarity_threshold: float, ttl_seconds: int):
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds

    def lookup(
        self, db: OrmSession, tenant_id: str, key: str, task_vec: np.ndarray, scope: ResolvedScope
    ) -> CacheLookup:
        now = datetime.now(UTC)
        entries = (
            db.query(CacheEntry)
            .filter(
                CacheEntry.tenant_id == tenant_id,
                CacheEntry.partition_key == key,
            )
            .all()
        )
        best, best_sim = None, 0.0
        for entry in entries:
            expires = _utc(entry.expires_at)
            if expires is not None and expires <= now:
                continue
            try:
                sim = cosine(task_vec, from_json(entry.embedding))
            except Exception:
                continue
            if sim > best_sim:
                best, best_sim = entry, sim
        if best is None or best_sim < self.similarity_threshold:
            return CacheLookup(None, best_sim, "miss")
        if not self._still_valid(db, best, scope, now):
            db.delete(best)
            db.flush()
            return CacheLookup(None, best_sim, "stale")
        best.hit_count = (best.hit_count or 0) + 1
        return CacheLookup(best, best_sim, "hit")

    @staticmethod
    def _still_valid(db: OrmSession, entry: CacheEntry, scope: ResolvedScope, now: datetime) -> bool:
        """Re-reads every contributing item: it must still exist, be valid
        now, live in a scope this caller can read, and have the same content."""
        try:
            contributing = json.loads(entry.contributing)
            payload = json.loads(entry.bundle)
        except Exception:
            return False
        expected = {item_id: sha for item_id, sha in contributing}
        if {b.get("source") for b in payload.get("bundle", [])} - set(expected):
            return False  # every bundle item must be accounted for
        if not expected:
            return True
        rows = db.query(KnowledgeItem).filter(KnowledgeItem.id.in_(list(expected))).all()
        if len(rows) != len(expected):
            return False
        readable = scope.scope_ids
        for row in rows:
            until = _utc(row.valid_until)
            if until is not None and until <= now:
                return False
            if row.scope_id not in readable:
                return False
            if content_hash(row.content) != expected[row.id]:
                return False
        return True

    def store(
        self,
        db: OrmSession,
        tenant_id: str,
        key: str,
        task_vec: np.ndarray,
        payload: dict,
        contributing: list[tuple[str, str]],
    ) -> CacheEntry:
        now = datetime.now(UTC)
        db.query(CacheEntry).filter(
            CacheEntry.tenant_id == tenant_id,
            CacheEntry.expires_at.isnot(None),
            CacheEntry.expires_at <= now,
        ).delete(synchronize_session=False)
        existing = (
            db.query(CacheEntry)
            .filter(CacheEntry.partition_key == key)
            .order_by(CacheEntry.created_at.desc())
            .offset(MAX_ENTRIES_PER_PARTITION - 1)
            .all()
        )
        for old in existing:
            db.delete(old)
        entry = CacheEntry(
            cache_key=f"bundle_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            partition_key=key,
            embedding=to_json(task_vec),
            bundle=json.dumps(payload),
            contributing=json.dumps(contributing),
            created_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds) if self.ttl_seconds > 0 else None,
        )
        db.add(entry)
        db.flush()
        return entry
