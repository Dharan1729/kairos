"""assemble_context latency (p50/p95), in process, on SQLite or Postgres.

Measures the core call (no HTTP) over the 40 benchmark queries, repeated:
  uncached   cache disabled: resolve, embed, retrieve, rank, budget, audit
  miss       cache on, first time a request is seen (includes the store)
  hit        cache on, the identical request again
  embed      the task embedding alone (the floor under every path)

    python -m benchmarks.latency                                   # SQLite (temp file)
    python -m benchmarks.latency --database-url postgresql://...   # Postgres
"""

from __future__ import annotations

import argparse
import time

from benchmarks import common
from benchmarks import dataset as ds

REPS = 3
BUDGET = 400


def run(database_url: str | None) -> dict:
    from kairos.embeddings import embed
    from kairos.models import CacheEntry
    from kairos.pipeline import ContextService, Task
    from kairos.pipeline.budget import GreedyBudgeter

    db = common.open_db(database_url)
    queries = [(t, text) for t in ds.TASKS for text in (t.text, t.paraphrase)]
    embed("warm up")
    budgeter = GreedyBudgeter(lambda: None)
    uncached = ContextService(cache_enabled=False, budgeter=budgeter)
    cached = ContextService(cache_enabled=True, budgeter=budgeter)
    timings: dict[str, list[float]] = {"uncached": [], "miss": [], "hit": [], "embed": []}

    def timed(fn, *args):
        t0 = time.perf_counter()
        fn(*args)
        return (time.perf_counter() - t0) * 1000

    for _ in range(REPS):
        db.query(CacheEntry).delete()
        db.commit()
        for t, text in queries:
            caller, principal = common.caller_for(t.person), common.principal_for(t)
            args = (db, caller, principal, Task(text), BUDGET)
            timings["embed"].append(timed(embed, text))
            timings["uncached"].append(timed(uncached.assemble, *args))
            timings["miss"].append(timed(cached.assemble, *args))
            timings["hit"].append(timed(cached.assemble, *args))
    db.close()
    backend = "postgres" if database_url and database_url.startswith("postgres") else "sqlite"
    return {
        "backend": backend,
        "queries": len(queries),
        "reps": REPS,
        "budget_tokens": BUDGET,
        "latency": {k: common.summarize_ms(v) for k, v in timings.items()},
    }


def main():
    p = argparse.ArgumentParser(description="assemble_context latency")
    p.add_argument("--database-url", default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    t0 = time.perf_counter()
    payload = run(args.database_url)
    payload["environment"] = common.environment()
    if payload["backend"] == "postgres":
        from kairos.db import get_engine

        with get_engine().connect() as conn:
            payload["environment"]["postgres"] = conn.exec_driver_sql("SHOW server_version").scalar()
    payload["seconds"] = round(time.perf_counter() - t0, 1)
    out = args.out or str(common.OUT_DIR / f"latency_{payload['backend']}.json")
    common.write_json(common.Path(out), payload)
    for k, v in payload["latency"].items():
        print(f"{payload['backend']:8s} {k:9s} p50={v['p50_ms']:7.2f} ms  p95={v['p95_ms']:7.2f} ms  n={v['n']}")


if __name__ == "__main__":
    main()
