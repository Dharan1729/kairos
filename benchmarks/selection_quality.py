"""Context-selection quality on the labelled benchmark org.

For each of the 20 tasks, asked in two phrasings (40 queries), every method
fills the same token budget through the same budgeter. We compare which
items it chose against the gold set (benchmarks/dataset.py).

Methods (all plugged into the real ContextService; cache off):
  kairos             default ranker (similarity x confidence x centrality x freshness x boost)
  kairos_floor       default ranker plus a minimum-similarity floor
  kairos_no_graph    default ranker without the graph-centrality factor
  vector_topk        similarity only
  recency            newest first
  dump_layers        org, team, project, personal, session in order, cut at the budget
  whole_org_vector   similarity over the whole org, no scope check, no validity filter
                     (shows what scope resolution prevents; not a fair quality baseline)

    python -m benchmarks.selection_quality
"""

from __future__ import annotations

import argparse
import statistics
import time

from benchmarks import common
from benchmarks import dataset as ds

BUDGETS = (100, 200, 400)
FLOOR = 0.30


def methods():
    from kairos.pipeline.ranking import WeightedRanker

    return {
        "kairos": {"ranker": WeightedRanker()},
        "kairos_floor": {"ranker": WeightedRanker(min_similarity=FLOOR)},
        "kairos_no_graph": {"ranker": common.NoCentralityRanker()},
        "vector_topk": {"ranker": common.VectorOnlyRanker()},
        "recency": {"ranker": common.RecencyRanker()},
        "dump_layers": {"ranker": common.LayerDumpRanker()},
        "whole_org_vector": {
            "ranker": common.VectorOnlyRanker(),
            "resolver": common.WholeOrgResolver(),
            "retriever": common.AllRowsRetriever(),
        },
    }


def run() -> dict:
    from kairos.pipeline import ContextService, Task
    from kairos.pipeline.budget import GreedyBudgeter, estimate_tokens

    db = common.open_db()
    tasks = ds.tasks_with_gold()
    rows = {r["id"]: r for r in ds.item_rows()}
    budgeter = GreedyBudgeter(lambda: None)  # no LLM: compression is truncation

    # "Dump all relevant layers": every readable, valid item, no budget.
    dump_all_tokens = []
    for t in tasks:
        readable = ds.readable_scopes(t.person, t.project, ds.session_id(t))
        dump_all_tokens.append(
            sum(estimate_tokens(r["text"]) for r in rows.values() if r["valid"] and r["scope"] in readable)
        )

    results: dict[str, dict] = {}
    per_task: list[dict] = []
    for name, parts in methods().items():
        service = ContextService(cache_enabled=False, budgeter=budgeter, **parts)
        for budget in BUDGETS:
            precisions, recalls, tokens, forbidden, over_budget = [], [], [], 0, 0
            for t in tasks:
                forbid = ds.forbidden_for(t, list(rows.values()))
                for phrasing, text in (("canonical", t.text), ("paraphrase", t.paraphrase)):
                    res = service.assemble(db, common.caller_for(t.person), common.principal_for(t), Task(text), budget)
                    chosen = [b.source for b in res.bundle]
                    hit = len(set(chosen) & t.gold)
                    precisions.append(hit / len(chosen) if chosen else 0.0)
                    recalls.append(hit / len(t.gold) if t.gold else 1.0)
                    tokens.append(res.tokens_used)
                    forbidden += len(set(chosen) & forbid)
                    over_budget += res.tokens_used > budget
                    per_task.append(
                        {
                            "method": name,
                            "budget": budget,
                            "task": t.id,
                            "phrasing": phrasing,
                            "chosen": chosen,
                            "gold": sorted(t.gold),
                            "hits": hit,
                            "tokens_used": res.tokens_used,
                        }
                    )
            results.setdefault(name, {})[str(budget)] = {
                "precision": round(statistics.fmean(precisions), 3),
                "recall": round(statistics.fmean(recalls), 3),
                "mean_tokens_used": round(statistics.fmean(tokens), 1),
                "max_tokens_used": max(tokens),
                "over_budget": over_budget,
                "forbidden_items_selected": forbidden,
            }
    db.close()
    return {
        "benchmark": "selection_quality",
        "queries": 2 * len(tasks),
        "tasks": len(tasks),
        "mean_gold_items": round(statistics.fmean(len(t.gold) for t in tasks), 2),
        "items": len(rows),
        "budgets": list(BUDGETS),
        "relevance_floor": FLOOR,
        "dump_all_relevant_tokens": {
            "mean": round(statistics.fmean(dump_all_tokens), 1),
            "min": min(dump_all_tokens),
            "max": max(dump_all_tokens),
        },
        "results": results,
        "per_query": per_task,
    }


def main():
    p = argparse.ArgumentParser(description="Context-selection quality benchmark")
    p.add_argument("--out", default=str(common.OUT_DIR / "selection_quality.json"))
    args = p.parse_args()
    t0 = time.perf_counter()
    payload = run()
    payload["environment"] = common.environment()
    payload["seconds"] = round(time.perf_counter() - t0, 1)
    common.write_json(common.Path(args.out), payload)
    for name, by_budget in payload["results"].items():
        line = "  ".join(
            f"@{b}: P={v['precision']:.2f} R={v['recall']:.2f} tok={v['mean_tokens_used']:.0f} "
            f"forbidden={v['forbidden_items_selected']}"
            for b, v in by_budget.items()
        )
        print(f"{name:18s} {line}")
    print(f"dump all relevant layers: {payload['dump_all_relevant_tokens']}")


if __name__ == "__main__":
    main()
