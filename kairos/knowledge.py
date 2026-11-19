"""Writing knowledge: items, supersession, proposals and git extraction.

These functions assume the caller has already been authorized
(kairos.access.check_can_write). Adapters call that first.
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from kairos.embeddings import embed, to_json
from kairos.graph import link_item
from kairos.llm import LlmProvider
from kairos.models import CandidateKnowledge, KnowledgeItem

GIT_LOG_CONFIDENCE = 0.5  # a bare commit subject is a weak signal
LLM_SUMMARY_CONFIDENCE = 0.6  # a model's summary of several commits
APPROVED_PROPOSAL_CONFIDENCE = 0.8


class ScopeMismatchError(Exception):
    """A proposal tried to supersede an item outside its own scope."""


class AlreadySupersededError(Exception):
    """Superseding an item twice would orphan the first replacement."""


class CandidateNotPendingError(Exception):
    pass


def next_knowledge_version(db: OrmSession, scope_id: str) -> int:
    current = db.query(func.max(KnowledgeItem.knowledge_version)).filter(KnowledgeItem.scope_id == scope_id).scalar()
    return (current or 0) + 1


def create_knowledge_item(
    db: OrmSession,
    layer: str,
    scope_id: str,
    content: str,
    staleness_hint: str = "medium",
    confidence: float = 1.0,
    origin: str = "human",
    source_id: str | None = None,
    item_id: str | None = None,
) -> KnowledgeItem:
    item = KnowledgeItem(
        id=item_id or f"ki-{uuid.uuid4().hex[:10]}",
        layer=layer,
        scope_id=scope_id,
        content=content,
        embedding_ref=to_json(embed(content)),
        staleness_hint=staleness_hint,
        confidence=confidence,
        origin=origin,
        source_id=source_id,
        knowledge_version=next_knowledge_version(db, scope_id),
    )
    db.add(item)
    db.flush()
    link_item(db, item)
    return item


def supersede_knowledge_item(
    db: OrmSession, item_id: str, superseded_by_id: str, expected_scope_id: str
) -> KnowledgeItem:
    """Bi-temporal invalidation: the old fact gets valid_until=now and stays
    in the table. A replacement can only supersede an item in its own scope."""
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise ValueError(f"no knowledge item {item_id}")
    if item.scope_id != expected_scope_id:
        raise ScopeMismatchError(f"item {item_id} is not in scope {expected_scope_id!r}")
    if item.valid_until is not None:
        raise AlreadySupersededError(f"item {item_id} was already superseded")
    item.valid_until = datetime.now(UTC)
    item.superseded_by_id = superseded_by_id
    item.knowledge_version = next_knowledge_version(db, item.scope_id)
    db.flush()
    return item


def revise_knowledge_item(db: OrmSession, item_id: str, new_content: str) -> KnowledgeItem:
    """Edits a fact the bi-temporal way: a new item with the new content, and
    the old one superseded. Returns the new item."""
    old = db.get(KnowledgeItem, item_id)
    if old is None:
        raise ValueError(f"no knowledge item {item_id}")
    if old.valid_until is not None:
        raise AlreadySupersededError(f"item {item_id} was already superseded")
    new = create_knowledge_item(
        db,
        layer=old.layer,
        scope_id=old.scope_id,
        content=new_content,
        staleness_hint=old.staleness_hint,
        confidence=old.confidence,
        origin=old.origin,
        source_id=old.source_id,
    )
    supersede_knowledge_item(db, old.id, new.id, old.scope_id)
    return new


# ---- proposals (human-in-the-loop) ---------------------------------------


def propose_candidate(
    db: OrmSession,
    proposed_content: str,
    proposed_layer: str,
    proposed_scope_id: str,
    source_session_id: str | None = None,
    supersedes_item_id: str | None = None,
) -> CandidateKnowledge:
    candidate = CandidateKnowledge(
        proposed_content=proposed_content,
        proposed_layer=proposed_layer,
        proposed_scope_id=proposed_scope_id,
        source_session_id=source_session_id,
        supersedes_item_id=supersedes_item_id,
        status="pending",
    )
    db.add(candidate)
    db.flush()
    return candidate


def approve_candidate(db: OrmSession, candidate_id: int, reviewed_by: str) -> CandidateKnowledge:
    candidate = db.get(CandidateKnowledge, candidate_id)
    if candidate is None:
        raise ValueError(f"no candidate {candidate_id}")
    if candidate.status != "pending":
        raise CandidateNotPendingError(f"candidate {candidate_id} is {candidate.status}, not pending")
    item = create_knowledge_item(
        db,
        layer=candidate.proposed_layer,
        scope_id=candidate.proposed_scope_id,
        content=candidate.proposed_content,
        confidence=APPROVED_PROPOSAL_CONFIDENCE,
        origin="human",
        source_id=candidate.source_session_id,
    )
    if candidate.supersedes_item_id:
        # Raises before commit if the target is in another scope; the new
        # item is rolled back with the request.
        supersede_knowledge_item(db, candidate.supersedes_item_id, item.id, candidate.proposed_scope_id)
    candidate.status = "approved"
    candidate.reviewed_by = reviewed_by
    candidate.promoted_item_id = item.id
    db.flush()
    return candidate


def reject_candidate(db: OrmSession, candidate_id: int, reviewed_by: str) -> CandidateKnowledge:
    candidate = db.get(CandidateKnowledge, candidate_id)
    if candidate is None:
        raise ValueError(f"no candidate {candidate_id}")
    if candidate.status != "pending":
        raise CandidateNotPendingError(f"candidate {candidate_id} is {candidate.status}, not pending")
    candidate.status = "rejected"
    candidate.reviewed_by = reviewed_by
    db.flush()
    return candidate


# ---- git extraction -------------------------------------------------------


@dataclass
class GitCommit:
    sha: str
    subject: str
    author_date: str


def read_git_log(repo_path: str, limit: int = 50) -> list[GitCommit]:
    result = subprocess.run(
        ["git", "log", f"-n{int(limit)}", "--pretty=format:%H%x1f%s%x1f%ai"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for line in result.stdout.splitlines():
        if line.strip():
            sha, subject, author_date = line.split("\x1f")
            commits.append(GitCommit(sha=sha, subject=subject, author_date=author_date))
    return commits


def extract_from_git_log(
    db: OrmSession, repo_path: str, scope_id: str, layer: str = "project", limit: int = 50
) -> list[KnowledgeItem]:
    """One item per commit subject, skipping commits already ingested."""
    commits = read_git_log(repo_path, limit=limit)
    existing = {
        row[0]
        for row in db.query(KnowledgeItem.source_id)
        .filter(KnowledgeItem.origin == "git", KnowledgeItem.source_id.in_([c.sha for c in commits]))
        .all()
    }
    return [
        create_knowledge_item(
            db,
            layer=layer,
            scope_id=scope_id,
            content=c.subject,
            staleness_hint="medium",
            confidence=GIT_LOG_CONFIDENCE,
            origin="git",
            source_id=c.sha,
        )
        for c in commits
        if c.sha not in existing
    ]


def propose_llm_commit_summary(
    db: OrmSession, llm_provider: LlmProvider | None, scope_id: str, layer: str, commit_subjects: list[str]
) -> KnowledgeItem | None:
    """One summary item for a batch of new commits. Returns None, never
    raises, when there is no provider, nothing to summarize, or the call fails."""
    if llm_provider is None or not commit_subjects:
        return None
    commit_list = "\n".join(f"- {s}" for s in commit_subjects)
    try:
        summary = llm_provider.summarize(f"Recent commits in this repository:\n{commit_list}", max_tokens=150)
    except Exception:
        return None
    if not summary:
        return None
    return create_knowledge_item(
        db,
        layer=layer,
        scope_id=scope_id,
        content=summary,
        staleness_hint="medium",
        confidence=LLM_SUMMARY_CONFIDENCE,
        origin="llm_summary",
    )
