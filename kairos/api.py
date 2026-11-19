"""HTTP API: a thin adapter over kairos.pipeline.ContextService and
kairos.knowledge. Each route authenticates, authorizes through
kairos.access, calls the core, and maps core errors to status codes.

See docs/guide/api.md for the endpoint reference.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

import kairos
from kairos import knowledge
from kairos.access import (
    AccessDenied,
    Caller,
    InvalidRequest,
    NotFound,
    check_can_write,
    person_in_org,
    require_role,
    scope_in_tenant,
)
from kairos.auth import caller_via_query_or_header, current_caller, generate_api_key
from kairos.config import load_settings
from kairos.dashboard import compute_dashboard_stats, render_dashboard_html
from kairos.db import get_db
from kairos.models import ApiKey, CandidateKnowledge, KnowledgeItem
from kairos.pipeline import AssembleResult, ContextPrincipal, ContextService, Task
from kairos.ratelimit import install_rate_limiting, limiter
from kairos.telemetry import install_tracing

app = FastAPI(title="Kairos", version=kairos.__version__)
install_rate_limiting(app)
install_tracing(app)  # added last, so it wraps rate limiting and traces 429s too


@app.exception_handler(AccessDenied)
def _access_denied(request: Request, exc: AccessDenied):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(NotFound)
def _not_found(request: Request, exc: NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc) or "not found"})


@app.exception_handler(InvalidRequest)
def _invalid(request: Request, exc: InvalidRequest):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def get_service() -> ContextService:
    """Built per request so configuration changes apply without a restart
    of the import; construction is cheap (the embedder is cached)."""
    return ContextService.from_settings()


# ---- health ---------------------------------------------------------------


@app.get("/healthz")
@limiter.exempt
def healthz(request: Request):
    """Liveness: the process answers. No dependencies checked."""
    return {"status": "ok", "version": kairos.__version__}


@app.get("/readyz")
@limiter.exempt
def readyz(request: Request, db: OrmSession = Depends(get_db)):
    """Readiness: the database answers."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database not reachable") from None
    return {"status": "ok"}


# ---- schemas --------------------------------------------------------------

Layer = Literal["org", "team", "project", "personal", "session"]
StalenessHint = Literal["low", "medium", "high"]
Origin = Literal["git", "slack", "jira", "meeting", "human", "llm_summary"]


class ContextPrincipalIn(BaseModel):
    user_id: str | None = Field(default=None, description="Defaults to the person the credential is bound to.")
    agent_type: str = Field(default="unknown", max_length=64)
    repo: str | None = None
    session_id: str | None = None

    def to_core(self) -> ContextPrincipal:
        return ContextPrincipal(
            user_id=self.user_id, agent_type=self.agent_type, repo=self.repo, session_id=self.session_id
        )


class TaskIn(BaseModel):
    description: str = Field(min_length=1, max_length=20_000)
    recent_messages: list[str] = Field(default_factory=list)


class AssembleContextRequest(BaseModel):
    context_principal: ContextPrincipalIn = Field(default_factory=ContextPrincipalIn)
    task: TaskIn
    budget_tokens: int = 4000


class SearchKnowledgeRequest(BaseModel):
    context_principal: ContextPrincipalIn = Field(default_factory=ContextPrincipalIn)
    query: str = Field(min_length=1, max_length=20_000)
    budget_tokens: int = 2000


class BundleItemOut(BaseModel):
    # Plain str on output: tolerate whatever is stored; validate on input.
    layer: str
    content: str
    source: str


class CacheInfoOut(BaseModel):
    hit: bool
    outcome: str
    similarity_score: float
    cache_key: str


class ScopeRefOut(BaseModel):
    layer: str
    scope_id: str


class ScopeOut(BaseModel):
    layers: list[ScopeRefOut]
    notes: list[str]


class AssembleContextResponse(BaseModel):
    context_bundle: list[BundleItemOut]
    cache: CacheInfoOut
    tokens_used: int
    latency_ms: float
    dropped: list[dict] = Field(default_factory=list)
    scope: ScopeOut


class SearchKnowledgeResponse(BaseModel):
    results: list[BundleItemOut]
    tokens_used: int
    dropped: list[dict] = Field(default_factory=list)
    scope: ScopeOut


def _scope_out(result: AssembleResult) -> ScopeOut:
    return ScopeOut(
        layers=[ScopeRefOut(layer=r.layer, scope_id=r.scope_id) for r in result.scope.chain],
        notes=list(result.scope.notes),
    )


def _bundle_out(result: AssembleResult) -> list[BundleItemOut]:
    return [BundleItemOut(layer=b.layer, content=b.content, source=b.source) for b in result.bundle]


# ---- context --------------------------------------------------------------


@app.post("/v1/assemble_context", response_model=AssembleContextResponse)
def assemble_context(
    req: AssembleContextRequest,
    db: OrmSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
    service: ContextService = Depends(get_service),
):
    result = service.assemble(
        db,
        caller,
        req.context_principal.to_core(),
        Task(req.task.description, tuple(req.task.recent_messages)),
        req.budget_tokens,
    )
    return AssembleContextResponse(
        context_bundle=_bundle_out(result),
        cache=CacheInfoOut(
            hit=result.cache.hit,
            outcome=result.cache.outcome,
            similarity_score=round(result.cache.similarity, 4),
            cache_key=result.cache.cache_key,
        ),
        tokens_used=result.tokens_used,
        latency_ms=round(result.latency_ms, 2),
        dropped=result.dropped,
        scope=_scope_out(result),
    )


@app.post("/v1/search_knowledge", response_model=SearchKnowledgeResponse)
def search_knowledge(
    req: SearchKnowledgeRequest,
    db: OrmSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
    service: ContextService = Depends(get_service),
):
    result = service.search(db, caller, req.context_principal.to_core(), req.query, req.budget_tokens)
    return SearchKnowledgeResponse(
        results=_bundle_out(result), tokens_used=result.tokens_used, dropped=result.dropped, scope=_scope_out(result)
    )


# ---- knowledge writes -----------------------------------------------------


class KnowledgeItemIn(BaseModel):
    layer: Layer
    scope_id: str
    content: str = Field(min_length=1, max_length=20_000)
    staleness_hint: StalenessHint = "medium"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    origin: Origin = "human"
    source_id: str | None = None


class ReviseKnowledgeItemIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class GitExtractRequest(BaseModel):
    repo_path: str
    scope_id: str
    layer: Layer = "project"
    limit: int = Field(default=50, ge=1, le=1000)


@app.post("/v1/knowledge_items")
def post_knowledge_item(
    req: KnowledgeItemIn, db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)
):
    check_can_write(db, caller, req.layer, req.scope_id)
    item = knowledge.create_knowledge_item(
        db,
        layer=req.layer,
        scope_id=req.scope_id,
        content=req.content,
        staleness_hint=req.staleness_hint,
        confidence=req.confidence,
        origin=req.origin,
        source_id=req.source_id,
    )
    db.commit()
    return {"id": item.id, "knowledge_version": item.knowledge_version}


@app.post("/v1/knowledge_items/{item_id}/revise")
def post_revise_knowledge_item(
    item_id: str, req: ReviseKnowledgeItemIn, db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)
):
    """Replaces a fact: the old item is superseded (kept for the audit
    trail), a new item holds the new content."""
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise NotFound()
    try:
        check_can_write(db, caller, item.layer, item.scope_id)
    except AccessDenied:
        raise NotFound() from None  # do not reveal items the caller cannot write
    try:
        new = knowledge.revise_knowledge_item(db, item_id, req.content)
    except knowledge.AlreadySupersededError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.commit()
    return {"id": new.id, "supersedes": item_id, "knowledge_version": new.knowledge_version}


def _check_git_path(repo_path: str) -> str:
    roots = load_settings().git_extract_roots
    if not roots:
        raise AccessDenied("git extraction over the API is disabled; set KAIROS_GIT_EXTRACT_ROOTS to enable it")
    path = Path(repo_path).expanduser().resolve()
    if not any(path == root or root in path.parents for root in roots):
        raise AccessDenied("repo_path is outside KAIROS_GIT_EXTRACT_ROOTS")
    return str(path)


@app.post("/v1/extract/git")
def post_extract_git(
    req: GitExtractRequest, db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)
):
    """Reads `git log` from a path on the *server*. Admin only, and only
    under the directories listed in KAIROS_GIT_EXTRACT_ROOTS."""
    require_role(caller, "admin")
    check_can_write(db, caller, req.layer, req.scope_id)
    path = _check_git_path(req.repo_path)
    try:
        items = knowledge.extract_from_git_log(db, path, req.scope_id, req.layer, req.limit)
    except Exception:
        raise HTTPException(status_code=400, detail="could not read git log at repo_path") from None
    db.commit()
    return {"created": len(items), "ids": [i.id for i in items]}


# ---- proposals ------------------------------------------------------------


class ProposeCandidateRequest(BaseModel):
    proposed_content: str = Field(min_length=1, max_length=20_000)
    proposed_layer: Layer
    proposed_scope_id: str
    source_session_id: str | None = None
    supersedes_item_id: str | None = None


class ReviewCandidateRequest(BaseModel):
    reviewed_by: str = Field(min_length=1, max_length=200)


@app.post("/v1/candidates")
def post_candidate(
    req: ProposeCandidateRequest, db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)
):
    check_can_write(db, caller, req.proposed_layer, req.proposed_scope_id)
    candidate = knowledge.propose_candidate(
        db,
        req.proposed_content,
        req.proposed_layer,
        req.proposed_scope_id,
        req.source_session_id,
        req.supersedes_item_id,
    )
    db.commit()
    return {"id": candidate.id, "status": candidate.status}


def _own_candidate(db: OrmSession, candidate_id: int, caller: Caller) -> CandidateKnowledge:
    """404 both for a missing candidate and for one the caller cannot write
    to, so ids cannot be probed across scopes or tenants."""
    require_role(caller, "member")
    candidate = db.get(CandidateKnowledge, candidate_id)
    if candidate is None:
        raise NotFound()
    try:
        check_can_write(db, caller, candidate.proposed_layer, candidate.proposed_scope_id)
    except (AccessDenied, InvalidRequest):
        raise NotFound() from None
    return candidate


@app.get("/v1/candidates/{candidate_id}")
def get_candidate(candidate_id: int, db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)):
    c = _own_candidate(db, candidate_id, caller)
    return {
        "id": c.id,
        "status": c.status,
        "proposed_layer": c.proposed_layer,
        "proposed_scope_id": c.proposed_scope_id,
        "proposed_content": c.proposed_content,
        "promoted_item_id": c.promoted_item_id,
    }


@app.post("/v1/candidates/{candidate_id}/approve")
def post_approve_candidate(
    candidate_id: int,
    req: ReviewCandidateRequest,
    db: OrmSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    require_role(caller, "admin")  # approval makes the fact visible to everyone in its scope
    _own_candidate(db, candidate_id, caller)
    try:
        candidate = knowledge.approve_candidate(db, candidate_id, req.reviewed_by)
    except (knowledge.CandidateNotPendingError, knowledge.AlreadySupersededError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except knowledge.ScopeMismatchError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    db.commit()
    return {"id": candidate.id, "status": candidate.status, "promoted_item_id": candidate.promoted_item_id}


@app.post("/v1/candidates/{candidate_id}/reject")
def post_reject_candidate(
    candidate_id: int,
    req: ReviewCandidateRequest,
    db: OrmSession = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    _own_candidate(db, candidate_id, caller)
    try:
        candidate = knowledge.reject_candidate(db, candidate_id, req.reviewed_by)
    except knowledge.CandidateNotPendingError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.commit()
    return {"id": candidate.id, "status": candidate.status}


# ---- API keys -------------------------------------------------------------


class CreateApiKeyRequest(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    role: Literal["admin", "member", "viewer"] = "member"
    person_id: str | None = Field(default=None, description="Bind the key to this person (recommended).")


@app.post("/v1/api_keys")
def post_create_api_key(
    req: CreateApiKeyRequest, db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)
):
    require_role(caller, "admin")
    if req.person_id is not None:
        if person_in_org(db, req.person_id, caller.org_id) is None:
            raise AccessDenied("person does not belong to this tenant")
    raw_key = generate_api_key(db, caller.tenant_id, req.label, req.role, person_id=req.person_id)
    db.commit()
    return {"api_key": raw_key, "label": req.label, "role": req.role, "person_id": req.person_id}


@app.get("/v1/api_keys")
def get_api_keys(db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)):
    require_role(caller, "admin")
    rows = db.query(ApiKey).filter(ApiKey.tenant_id == caller.tenant_id).order_by(ApiKey.id).all()
    return [
        {
            "id": r.id,
            "label": r.label,
            "role": r.role,
            "person_id": r.person_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        }
        for r in rows
    ]


@app.post("/v1/api_keys/{key_id}/revoke")
def post_revoke_api_key(key_id: int, db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)):
    require_role(caller, "admin")
    row = db.get(ApiKey, key_id)
    if row is None or row.tenant_id != caller.tenant_id:
        raise NotFound()
    row.revoked_at = datetime.now(UTC)
    db.commit()
    return {"id": row.id, "revoked": True}


# ---- dashboard ------------------------------------------------------------


@app.get("/v1/dashboard")
def get_dashboard_json(db: OrmSession = Depends(get_db), caller: Caller = Depends(current_caller)):
    return compute_dashboard_stats(db, caller)


@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard_html(db: OrmSession = Depends(get_db), caller: Caller = Depends(caller_via_query_or_header)):
    """Open /dashboard?api_key=... in a browser. The key in the URL ends up
    in browser history and access logs; prefer a viewer key here."""
    return HTMLResponse(
        content=render_dashboard_html(compute_dashboard_stats(db, caller)),
        headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
    )


__all__ = ["app", "scope_in_tenant"]
