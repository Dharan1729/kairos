"""Shared benchmark plumbing: an isolated database seeded with the benchmark
org, baseline rankers that plug into the real pipeline, and statistics."""

from __future__ import annotations

import json
import os
import platform
import statistics
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from benchmarks import dataset as ds

BENCH_TENANT = "bench"
OUT_DIR = Path("out")


def environment() -> dict:
    import numpy
    import sqlalchemy

    import kairos
    from kairos.config import load_settings

    s = load_settings()
    return {
        "kairos": kairos.__version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "embedder": s.embedder,
        "embedding_model": s.embedding_model,
        "sqlalchemy": sqlalchemy.__version__,
        "numpy": numpy.__version__,
    }


def isolated_home() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="kairos-bench-"))
    os.environ["KAIROS_HOME"] = str(tmp)
    os.environ.setdefault("HF_HUB_OFFLINE", os.environ.get("HF_HUB_OFFLINE", "0"))
    return tmp


def open_db(database_url: str | None = None, fresh: bool = True):
    """Configures Kairos against database_url (default: a temp SQLite file),
    creates tables, seeds the benchmark org, returns a session."""
    from kairos.db import Base, configure, create_all, get_engine, get_sessionmaker

    if database_url is None:
        database_url = f"sqlite:///{isolated_home() / 'bench.db'}"
    configure(database_url)
    if fresh:
        Base.metadata.drop_all(bind=get_engine())
    create_all()
    db = get_sessionmaker()()
    seed_bench_org(db)
    from kairos.vector_index import reset_cache_for_tests

    reset_cache_for_tests()
    return db


def seed_bench_org(db) -> None:
    from kairos.directory import Org, Person, Project, SessionRecord, Team
    from kairos.knowledge import create_knowledge_item
    from kairos.models import KnowledgeItem, TenantConfig

    if db.get(Org, ds.ORG) is not None:
        return
    db.add(Org(id=ds.ORG, name="Acme Logistics (synthetic)", policies="[]", standards="[]"))
    for tid, name in ds.TEAMS.items():
        db.add(Team(id=tid, org_id=ds.ORG, name=name))
    for pid, tid in ds.PEOPLE.items():
        db.add(Person(id=pid, name=pid, team_id=tid))
    for pid, (tid, repo) in ds.PROJECTS.items():
        db.add(Project(id=pid, team_id=tid, name=pid, repo_name=repo))
    for t in ds.TASKS:
        db.add(
            SessionRecord(
                id=ds.session_id(t),
                person_id=t.person,
                project_id=t.project,
                task_description=t.text,
                recent_messages="[]",
            )
        )
    db.add(TenantConfig(tenant_id=BENCH_TENANT, org_id=ds.ORG))
    db.flush()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for row in sorted(ds.item_rows(), key=lambda r: r["created_rank"]):
        item = create_knowledge_item(
            db, layer=row["layer"], scope_id=row["scope"], content=row["text"], item_id=row["id"], staleness_hint="low"
        )
        item.valid_from = base + timedelta(hours=row["created_rank"])
        if not row["valid"]:
            item.valid_until = base - timedelta(days=1)
    db.commit()
    assert db.query(KnowledgeItem).count() == len(ds.item_rows())


def caller_for(person: str):
    from kairos.access import Caller

    return Caller(tenant_id=BENCH_TENANT, org_id=ds.ORG, role="member", person_id=person)


def principal_for(task: ds.BenchTask):
    from kairos.pipeline import ContextPrincipal

    return ContextPrincipal(user_id=task.person, agent_type="bench", session_id=ds.session_id(task))


# ---- baseline stages ---------------------------------------------------------


class VectorOnlyRanker:
    """Pure similarity order (classic top-k retrieval)."""

    def rank(self, candidates, scope):
        from kairos.pipeline.types import ScoredCandidate

        return sorted((ScoredCandidate(c, c.similarity) for c in candidates), key=lambda s: (-s.score, s.candidate.id))


class RecencyRanker:
    """Newest first, ignoring the task."""

    def __init__(self):
        self.rank_of = {r["id"]: r["created_rank"] for r in ds.item_rows()}

    def rank(self, candidates, scope):
        from kairos.pipeline.types import ScoredCandidate

        scored = [ScoredCandidate(c, float(self.rank_of.get(c.id, 0))) for c in candidates]
        return sorted(scored, key=lambda s: (-s.score, s.candidate.id))


class LayerDumpRanker:
    """ "Dump every layer": org, team, project, personal, session, in
    insertion order within a layer, cut at the budget. What a hand-built
    CLAUDE.md concatenation does."""

    def __init__(self):
        self.rank_of = {r["id"]: r["created_rank"] for r in ds.item_rows()}

    def rank(self, candidates, scope):
        from kairos.pipeline.types import LAYER_ORDER, ScoredCandidate

        def key(c):
            layer = LAYER_ORDER.index(c.layer) if c.layer in LAYER_ORDER else 99
            return (layer, self.rank_of.get(c.id, 0), c.id)

        return [ScoredCandidate(c, 0.0) for c in sorted(candidates, key=key)]


class NoCentralityRanker:
    def __init__(self):
        from kairos.pipeline.ranking import WeightedRanker

        self.inner = WeightedRanker()

    def rank(self, candidates, scope):
        from dataclasses import replace

        return self.inner.rank([replace(c, graph_centrality=1.0) for c in candidates], scope)


class WholeOrgResolver:
    """No scope resolution: every scope in the org (naive RAG over the whole
    knowledge base). Used only to show what scoping prevents."""

    def resolve(self, db, caller, principal):
        from kairos.directory import Person, Project, SessionRecord, Team
        from kairos.pipeline.types import ResolvedScope, ScopeRef

        chain = [ScopeRef("org", caller.org_id)]
        chain += [ScopeRef("team", t.id) for t in db.query(Team).filter(Team.org_id == caller.org_id)]
        chain += [ScopeRef("project", p.id) for p in db.query(Project)]
        chain += [ScopeRef("personal", p.id) for p in db.query(Person)]
        chain += [ScopeRef("session", s.id) for s in db.query(SessionRecord)]
        return ResolvedScope(caller.tenant_id, caller.org_id, principal.user_id, tuple(chain))


class AllRowsRetriever:
    """Vector retrieval that also ignores valid_until (no bi-temporal filter)."""

    def retrieve(self, db, scope, query):
        from kairos.embeddings import cosine, from_json
        from kairos.models import KnowledgeItem
        from kairos.pipeline.types import Candidate, RetrievalResult

        rows = db.query(KnowledgeItem).filter(KnowledgeItem.scope_id.in_(list(scope.scope_ids))).all()
        return RetrievalResult(
            [
                Candidate(
                    id=r.id,
                    layer=r.layer,
                    scope_id=r.scope_id,
                    content=r.content,
                    confidence=r.confidence,
                    staleness_hint=r.staleness_hint,
                    similarity=cosine(query, from_json(r.embedding_ref or "[]")),
                )
                for r in rows
            ]
        )


# ---- stats -------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize_ms(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}
    return {
        "n": len(values),
        "p50_ms": round(percentile(values, 50), 2),
        "p95_ms": round(percentile(values, 95), 2),
        "mean_ms": round(statistics.fmean(values), 2) if values else None,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path}", file=sys.stderr)
