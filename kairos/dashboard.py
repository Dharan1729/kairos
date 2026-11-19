"""Per-tenant usage dashboard: cache outcomes, tokens, latency, knowledge
freshness, and which layers bundles draw from. Aggregates only: no item
content and no per-user breakdown is shown.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session as OrmSession

from kairos.access import Caller
from kairos.directory import Person, Project, SessionRecord, Team
from kairos.models import AuditLedger, CacheEntry, KnowledgeItem

OUTCOMES = ["hit", "miss", "stale", "disabled", "bypass"]


def _tenant_scope_ids(db: OrmSession, org_id: str) -> set[str]:
    """Every scope id in one org (knowledge_items has no tenant column)."""
    team_ids = [t.id for t in db.query(Team).filter(Team.org_id == org_id).all()]
    if not team_ids:
        return {org_id}
    project_ids = [p.id for p in db.query(Project).filter(Project.team_id.in_(team_ids)).all()]
    person_ids = [p.id for p in db.query(Person).filter(Person.team_id.in_(team_ids)).all()]
    session_ids = (
        [s.id for s in db.query(SessionRecord).filter(SessionRecord.person_id.in_(person_ids)).all()]
        if person_ids
        else []
    )
    return {org_id, *team_ids, *project_ids, *person_ids, *session_ids}


def _knowledge_freshness(db: OrmSession, org_id: str) -> dict:
    items = db.query(KnowledgeItem).filter(KnowledgeItem.scope_id.in_(_tenant_scope_ids(db, org_id))).all()
    total = len(items)
    superseded = sum(1 for i in items if i.valid_until is not None)
    breakdown: dict[str, int] = {}
    for i in items:
        breakdown[i.staleness_hint] = breakdown.get(i.staleness_hint, 0) + 1
    return {
        "total_items": total,
        "superseded_items": superseded,
        "share_superseded": round(superseded / total, 4) if total else 0.0,
        "staleness_breakdown": breakdown,
    }


def _context_source_breakdown(db: OrmSession, tenant_id: str) -> dict:
    counts: dict[str, int] = {}
    total = 0
    for entry in db.query(CacheEntry).filter(CacheEntry.tenant_id == tenant_id).all():
        for item in json.loads(entry.bundle).get("bundle", []):
            counts[item["layer"]] = counts.get(item["layer"], 0) + 1
            total += 1
    return {layer: round(n / total, 4) for layer, n in counts.items()} if total else {}


def compute_dashboard_stats(db: OrmSession, caller: Caller) -> dict:
    rows = db.query(AuditLedger).filter(AuditLedger.tenant_id == caller.tenant_id).all()
    total = len(rows)
    outcome_counts = {o: 0 for o in OUTCOMES}
    for r in rows:
        outcome_counts[r.outcome] = outcome_counts.get(r.outcome, 0) + 1
    assemble_rows = [r for r in rows if r.request_type == "assemble_context"]
    tokens_used_total = sum(r.tokens_used for r in rows)
    top = (
        db.query(CacheEntry)
        .filter(CacheEntry.tenant_id == caller.tenant_id, CacheEntry.hit_count > 0)
        .order_by(CacheEntry.hit_count.desc())
        .limit(5)
        .all()
    )
    return {
        "tenant_id": caller.tenant_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_requests": total,
        "outcome_counts": outcome_counts,
        "cache_hit_rate": round(sum(1 for r in assemble_rows if r.cache_hit) / len(assemble_rows), 4)
        if assemble_rows
        else 0.0,
        "tokens_used_total": tokens_used_total,
        "tokens_saved_total": sum(r.tokens_saved_estimate for r in rows),
        "avg_latency_ms": round(sum(r.latency_ms for r in rows) / total, 2) if total else 0.0,
        "avg_context_size_tokens": round(tokens_used_total / total, 1) if total else 0.0,
        "most_reused_cache_entries": [{"cache_key": e.cache_key, "hit_count": e.hit_count} for e in top],
        "knowledge_freshness": _knowledge_freshness(db, caller.org_id),
        "context_source_breakdown": _context_source_breakdown(db, caller.tenant_id),
    }


def render_dashboard_html(stats: dict) -> str:
    """Every string is HTML-escaped, including values that look safe today."""
    e = html.escape
    rows = lambda d, fmt=str: "".join(f"<tr><td>{e(str(k))}</td><td>{e(fmt(v))}</td></tr>" for k, v in d.items())  # noqa: E731
    reused = (
        "".join(
            f"<tr><td>{e(x['cache_key'])}</td><td>{x['hit_count']}</td></tr>"
            for x in stats["most_reused_cache_entries"]
        )
        or "<tr><td colspan='2'><em>none yet</em></td></tr>"
    )
    sources = (
        rows(stats["context_source_breakdown"], lambda v: f"{v * 100:.1f}%")
        or "<tr><td colspan='2'><em>no cached bundles yet</em></td></tr>"
    )
    fresh = stats["knowledge_freshness"]
    tenant = e(stats["tenant_id"])
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Kairos dashboard: {tenant}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
 h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
 table {{ border-collapse: collapse; }} td {{ padding: 4px 12px; border-bottom: 1px solid #ddd; }}
 .stat {{ display: inline-block; margin: 0 2rem 1rem 0; }} .v {{ font-size: 1.6rem; font-weight: 600; }}
 .l {{ font-size: 0.8rem; color: #666; }}
</style></head><body>
<h1>Kairos dashboard: tenant {tenant}</h1>
<p class="l">generated {e(stats["generated_at"])}</p>
<div class="stat"><div class="v">{stats["total_requests"]}</div><div class="l">requests</div></div>
<div class="stat"><div class="v">{stats["cache_hit_rate"] * 100:.1f}%</div><div class="l">cache hit rate</div></div>
<div class="stat"><div class="v">{stats["tokens_saved_total"]}</div><div class="l">tokens served from cache</div></div>
<div class="stat"><div class="v">{stats["avg_latency_ms"]} ms</div><div class="l">mean latency</div></div>
<div class="stat"><div class="v">{stats["avg_context_size_tokens"]}</div><div class="l">mean bundle size (tokens)</div></div>
<h2>Outcomes</h2><table>{rows(stats["outcome_counts"])}</table>
<h2>Most reused bundles</h2><table>{reused}</table>
<h2>Knowledge freshness</h2>
<p>{fresh["total_items"]} items, {fresh["superseded_items"]} superseded ({fresh["share_superseded"] * 100:.1f}%)</p>
<table>{rows(fresh["staleness_breakdown"])}</table>
<h2>Layers in cached bundles</h2><table>{sources}</table>
</body></html>
"""
