"""Leak and staleness probe: adversarial requests over the HTTP API.

Each scenario has a victim, an attacker and a list of item ids the attacker
must never receive (or, for staleness, content that must never be served
again). The victim warms the cache first, so both a scope-resolution leak
and a cache leak are caught. Results are counted per scenario.

The probe only uses the public HTTP API plus direct database edits, so it
runs unchanged against another source tree of Kairos (``--src``). That is
how the "before" numbers in docs/benchmarks.md were measured: on the 0.1.0
code, where every credential is tenant-wide, the attacker uses the tenant's
key; on this code, the attacker uses a key bound to their own person.

    python -m benchmarks.leak_probe                       # this tree
    python benchmarks/leak_probe.py --src /path/to/kairos-0.1.0 --label v0.1.0
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

A = "person-team-platform-0"  # victim: owns session-0 on proj-orchestrator-core
B = "person-team-platform-mgr"  # same team as A, owns session-8
ML = "person-team-ml-2"  # another team
TASK = "Add a /healthz endpoint to a new internal service"


def run(src: Path | None, label: str) -> dict:
    if src is not None:
        sys.path.insert(0, str(src))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    tmp = Path(tempfile.mkdtemp(prefix="kairos-leak-"))
    os.environ["KAIROS_HOME"] = str(tmp / "home")
    os.environ.pop("KAIROS_CACHE_CROSS_USER_REUSE", None)

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    import kairos.auth as auth
    from kairos.api import app
    from kairos.db import configure, create_all, get_sessionmaker
    from kairos.models import KnowledgeItem
    from kairos.seed import seed

    src_used = Path(sys.modules["kairos"].__file__).resolve().parent.parent
    configure(f"sqlite:///{tmp / 'probe.db'}")
    create_all()
    db = get_sessionmaker()()
    admin_key = seed(db)
    db.commit()

    binds_people = "person_id" in inspect.signature(auth.generate_api_key).parameters

    def key_for(person: str | None, role: str = "member") -> str:
        """The credential a real deployment of this version would give `person`."""
        if binds_people and person:
            k = auth.generate_api_key(db, "default", label=person, role=role, person_id=person)
        else:
            k = auth.generate_api_key(db, "default", label=f"tenant key for {person}", role=role)
        db.commit()
        return k

    def client(k: str) -> TestClient:
        return TestClient(app, headers={"Authorization": f"Bearer {k}"}, raise_server_exceptions=False)

    def scope_items(*scope_ids: str) -> set[str]:
        rows = db.query(KnowledgeItem.id).filter(KnowledgeItem.scope_id.in_(scope_ids)).all()
        return {r[0] for r in rows}

    def ask(c: TestClient, user=None, session=None, repo=None, task=TASK):
        resp = c.post(
            "/v1/assemble_context",
            json={
                "context_principal": {"user_id": user, "agent_type": "probe", "repo": repo, "session_id": session},
                "task": {"description": task},
                "budget_tokens": 4000,
            },
        )
        if resp.status_code != 200:
            return resp.status_code, [], None
        body = resp.json()
        return 200, body["context_bundle"], body["cache"]["outcome"]

    a_sessions = {"session-0"}
    a_private = scope_items(A, *a_sessions)
    platform_only = scope_items("team-platform", "proj-orchestrator-core")
    k_a, k_b, k_ml = key_for(A), key_for(B), key_for(ML)
    ca, cb, cml = client(k_a), client(k_b), client(k_ml)
    scenarios = []

    def leak(name, attempts, forbidden, desc):
        leaked_requests, leaked_items, refused, hits = 0, set(), 0, 0
        for status, bundle, outcome in attempts:
            if status != 200:
                refused += 1
                continue
            got = {i["source"] for i in bundle} & forbidden
            if got:
                leaked_requests += 1
                leaked_items |= got
            if outcome in ("hit", "assembly_hit"):
                hits += 1
        scenarios.append(
            {
                "scenario": name,
                "kind": "leak",
                "description": desc,
                "requests": len(attempts),
                "refused": refused,
                "requests_with_forbidden_items": leaked_requests,
                "forbidden_items_served": sorted(leaked_items),
                "cache_hits_among_requests": hits,
            }
        )

    # S1/S2: same team, B asks for A's private context directly.
    ask(ca, user=A, session="session-0")
    leak(
        "session_hijack",
        [ask(cb, user=B, session="session-0"), ask(cb, user=None, session="session-0")],
        a_private,
        "B names A's session id",
    )
    leak(
        "user_spoof",
        [ask(cb, user=A), ask(cb, user=A, repo="nimbus/orchestrator-core")],
        a_private,
        "B claims user_id=A",
    )
    # S3: identical task, B's own session on A's project, after A warmed the cache.
    leak(
        "same_project_cache_reuse",
        [ask(cb, user=B, session="session-8"), ask(cb, user=B, repo="nimbus/orchestrator-core")],
        a_private,
        "B, same project and task as A, own session",
    )
    # S4/S5: another team's repo, warmed by that team first.
    ask(ca, user=A, repo="nimbus/orchestrator-core")
    leak(
        "cross_team_repo",
        [ask(cml, user=ML, repo="nimbus/orchestrator-core"), ask(cml, user=None, repo="nimbus/orchestrator-core")],
        platform_only,
        "ML-team user names team-platform's repo",
    )
    leak(
        "unknown_user_repo",
        [ask(cml, user="nobody-in-directory", repo="nimbus/orchestrator-core")],
        platform_only,
        "a user id not in the directory plus another team's repo",
    )

    # Staleness: warm, change an item the bundle uses, ask again.
    def stale(name, desc, mutate, item_id, task=TASK):
        ask(ca, user=A, session="session-0", task=task)
        warm_status, before, _ = ask(ca, user=A, session="session-0", task=task)
        old = next((i["content"] for i in before if i["source"] == item_id), None)
        new_content = mutate()
        status, after, outcome = ask(ca, user=A, session="session-0", task=task)
        if status != 200:
            outcome = f"http_{status}"
        served = {i["source"]: i["content"] for i in after}
        stale_served = (old is not None and served.get(item_id) == old and new_content != old) or (
            new_content is None and item_id in served
        )
        scenarios.append(
            {
                "scenario": name,
                "kind": "staleness",
                "description": desc,
                "warm_request_status": warm_status,
                "item_in_warm_bundle": old is not None,
                "outcome_after_change": outcome,
                "stale_content_served": bool(stale_served),
            }
        )

    def orm_edit():
        item = db.get(KnowledgeItem, "ctx-0")
        item.content = "All services must expose /livez and /readyz (replaces /healthz)."
        db.commit()
        return item.content

    def sql_edit():
        db.execute(text("UPDATE knowledge_items SET content = 'Every service needs /readyz.' WHERE id = 'ctx-1'"))
        db.commit()
        return "Every service needs /readyz."

    def expire():
        db.execute(text("UPDATE knowledge_items SET valid_until = '2000-01-01 00:00:00' WHERE id = 'ctx-2'"))
        db.commit()
        return None

    def supersede():
        ac = client(admin_key)
        cid = ac.post(
            "/v1/candidates",
            json={
                "proposed_content": "Team-platform: trunk-based development with short-lived branches (max 2 days).",
                "proposed_layer": "team",
                "proposed_scope_id": "team-platform",
                "supersedes_item_id": "ctx-4",
            },
        ).json()["id"]
        ac.post(f"/v1/candidates/{cid}/approve", json={"reviewed_by": "probe"})
        return None

    stale(
        "superseded_via_api",
        "a bundled item superseded through the proposal workflow",
        supersede,
        "ctx-4",
        task="Set up Terraform for a new platform microservice",
    )

    stale("orm_in_place_edit", "content of a bundled org item edited through the ORM", orm_edit, "ctx-0")
    stale(
        "raw_sql_edit",
        "content of a bundled org item edited with SQL",
        sql_edit,
        "ctx-1",
        task="Add a /healthz endpoint to a new internal service.",
    )
    stale(
        "expired_item",
        "a bundled item's valid_until passes",
        expire,
        "ctx-2",
        task="Add a /healthz endpoint to a new internal service!",
    )

    # Robustness: after an item expires, a request that is not a cache hit
    # must still work (0.1.0 kept the expired item in its ANN index cache).
    status, _, outcome = ask(ca, user=A, session="session-0", task="Rotate the Kafka TLS certificates")
    scenarios.append(
        {
            "scenario": "fresh_request_after_expiry",
            "kind": "robustness",
            "description": "uncached request after a readable item expired",
            "status": status,
            "outcome": outcome,
        }
    )

    leaks = [s for s in scenarios if s["kind"] == "leak"]
    stales = [s for s in scenarios if s["kind"] == "staleness"]
    return {
        "label": label,
        "source": "this tree" if src is None else "external tree",
        "attacker_credential": "key bound to the attacker" if binds_people else "tenant-wide key (no per-user keys)",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": {
            "leak_requests": sum(s["requests"] for s in leaks),
            "leak_requests_with_forbidden_items": sum(s["requests_with_forbidden_items"] for s in leaks),
            "leak_requests_refused": sum(s["refused"] for s in leaks),
            "distinct_forbidden_items_served": len({i for s in leaks for i in s["forbidden_items_served"]}),
            "staleness_scenarios": len(stales),
            "staleness_scenarios_serving_stale": sum(s["stale_content_served"] for s in stales),
            "staleness_scenarios_with_server_error": sum(
                s["outcome_after_change"].startswith("http_5") for s in stales
            ),
            "fresh_request_after_expiry_status": scenarios[-1]["status"],
        },
        "scenarios": scenarios,
        "_src": str(src_used),  # printed for the operator, not written
    }


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--src", type=Path, default=None, help="another Kairos source tree to probe")
    p.add_argument("--label", default="this-tree")
    p.add_argument("--out", type=Path, default=Path("out/leak_probe.json"))
    args = p.parse_args()
    t = time.perf_counter()
    result = run(args.src.resolve() if args.src else None, args.label)
    result["seconds"] = round(time.perf_counter() - t, 1)
    print(f"probed kairos package at: {result.pop('_src')}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
