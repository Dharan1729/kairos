"""Bundle cache: hit rate, latency, fidelity and leaks on the benchmark org.

Workload per task (20 tasks):
  1. the owner asks the canonical text      (first time: miss)
  2. the owner asks it again                (exact repeat)
  3. the owner asks the paraphrase          (semantic repeat)
  4. a teammate on the same project asks it (cross-user)
  5. two new hires with no personal items ask it (cross-user, no private items)

For every hit, the served bundle is compared with a fresh assembly of the
same request with the cache off ("fidelity": was the reused bundle exactly
what the caller would have got?). For every response, items the caller may
not read are counted (leaks). Runs at several similarity thresholds, with
cross-user reuse off (default) and on, and once with the cache disabled.

    python -m benchmarks.cache_bench
"""

from __future__ import annotations

import argparse
import time

from benchmarks import common
from benchmarks import dataset as ds

THRESHOLDS = (0.75, 0.85, 0.90, 0.95)
BUDGET = 200
NEW_HIRES = ("hire-a", "hire-b")


def add_new_hires(db) -> None:
    from kairos.directory import Person

    for pid in NEW_HIRES:
        if db.get(Person, pid) is None:
            db.add(Person(id=pid, name=pid, team_id="team-pay"))
    db.commit()


def teammate(t: ds.BenchTask) -> str:
    team = ds.PEOPLE[t.person]
    return next(p for p, tm in ds.PEOPLE.items() if tm == team and p != t.person)


def run_config(db, threshold: float, cross_user: bool, cache_enabled: bool) -> dict:
    from kairos.access import Caller
    from kairos.models import CacheEntry
    from kairos.pipeline import ContextPrincipal, ContextService, Task
    from kairos.pipeline.budget import GreedyBudgeter
    from kairos.pipeline.cache import ScopedBundleCache

    db.query(CacheEntry).delete()
    db.commit()
    service = ContextService(
        cache=ScopedBundleCache(threshold, 3600),
        cache_enabled=cache_enabled,
        cross_user_reuse=cross_user,
        budgeter=GreedyBudgeter(lambda: None),
    )
    fresh = ContextService(cache_enabled=False, budgeter=GreedyBudgeter(lambda: None))
    rows = ds.item_rows()
    stats = {k: {"requests": 0, "hits": 0} for k in ("first", "repeat", "paraphrase", "teammate", "new_hire")}
    hit_ms, miss_ms = [], []
    wrong_hits, leaks = 0, 0
    for t in ds.tasks_with_gold():
        repo = ds.PROJECTS[t.project][1]
        team = ds.PEOPLE[t.person]
        steps = [
            ("first", t.person, t.text, ds.session_id(t)),
            ("repeat", t.person, t.text, ds.session_id(t)),
            ("paraphrase", t.person, t.paraphrase, ds.session_id(t)),
            ("teammate", teammate(t), t.text, None),
        ]
        if team == "team-pay":
            steps += [("new_hire", h, t.text, None) for h in NEW_HIRES]
        for kind, person, text, session in steps:
            caller = Caller(tenant_id=common.BENCH_TENANT, org_id=ds.ORG, role="member", person_id=person)
            principal = ContextPrincipal(user_id=person, agent_type="bench", repo=repo, session_id=session)
            t0 = time.perf_counter()
            res = service.assemble(db, caller, principal, Task(text), BUDGET)
            ms = (time.perf_counter() - t0) * 1000
            stats[kind]["requests"] += 1
            readable = {ref.scope_id for ref in res.scope.chain}
            served = [b.source for b in res.bundle]
            leaks += sum(1 for s in served if next(r for r in rows if r["id"] == s)["scope"] not in readable)
            if res.cache.hit:
                stats[kind]["hits"] += 1
                hit_ms.append(ms)
                truth = fresh.assemble(db, caller, principal, Task(text), BUDGET)
                if [b.source for b in truth.bundle] != served:
                    wrong_hits += 1
            else:
                miss_ms.append(ms)
    total_hits = sum(v["hits"] for v in stats.values())
    return {
        "threshold": threshold,
        "cross_user_reuse": cross_user,
        "cache_enabled": cache_enabled,
        "by_request_kind": stats,
        "hit_rate": round(total_hits / sum(v["requests"] for v in stats.values()), 3),
        "hits_differing_from_fresh_assembly": wrong_hits,
        "items_served_from_unreadable_scopes": leaks,
        "latency_hit": common.summarize_ms(hit_ms),
        "latency_miss_or_uncached": common.summarize_ms(miss_ms),
    }


def main():
    p = argparse.ArgumentParser(description="Bundle cache benchmark")
    p.add_argument("--out", default=str(common.OUT_DIR / "cache_bench.json"))
    args = p.parse_args()
    t0 = time.perf_counter()
    db = common.open_db()
    add_new_hires(db)
    # Warm the embedder so the first measured request does not include model load.
    from kairos.embeddings import embed

    embed("warm up")
    configs = [run_config(db, 0.90, False, False)]
    for th in THRESHOLDS:
        configs.append(run_config(db, th, False, True))
    configs.append(run_config(db, 0.90, True, True))
    db.close()
    payload = {
        "benchmark": "cache",
        "budget_tokens": BUDGET,
        "configs": configs,
        "environment": common.environment(),
        "seconds": round(time.perf_counter() - t0, 1),
    }
    common.write_json(common.Path(args.out), payload)
    for c in configs:
        kinds = " ".join(f"{k}={v['hits']}/{v['requests']}" for k, v in c["by_request_kind"].items())
        print(
            f"cache={c['cache_enabled']!s:5} th={c['threshold']:.2f} cross={c['cross_user_reuse']!s:5} "
            f"hit_rate={c['hit_rate']:.2f} {kinds} wrong={c['hits_differing_from_fresh_assembly']} "
            f"leaks={c['items_served_from_unreadable_scopes']} hit_p50={c['latency_hit']['p50_ms']} "
            f"miss_p50={c['latency_miss_or_uncached']['p50_ms']}"
        )


if __name__ == "__main__":
    main()
