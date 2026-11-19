# Architecture

## One core call, three adapters

```
 Claude Code / Cursor          scripts, gateways           terminal
        |                            |                        |
   kairos-mcp (stdio) ----HTTP---> kairos.api (FastAPI) <--- kairos CLI (client commands)
                                     |                        |
                                     v                        v  (kairos try: in process)
                        kairos.auth -> Caller  (tenant, role, person)
                                     |
                         kairos.pipeline.ContextService.assemble()
       ScopeResolver -> BundleCache.lookup -> CandidateRetriever -> Ranker -> Budgeter -> BundleCache.store -> audit
                                     |
                      SQLAlchemy: SQLite or Postgres (directory, knowledge, cache, audit)
```

The HTTP API authenticates, calls `kairos.access` for write checks, calls the service, and maps `AccessDenied`, `NotFound` and `InvalidRequest` to 403, 404 and 422. The MCP server is a client of the HTTP API, so it goes through exactly the same authentication. The CLI's client commands do the same; `kairos try` calls the service in process as the demo user, through the same scope checks.

## Pipeline stages

Each stage is a protocol in [kairos/pipeline/types.py](../kairos/pipeline/types.py); `ContextService` takes any implementation. The benchmarks swap rankers, resolvers and retrievers this way.

| Stage | Default | Contract |
|---|---|---|
| `ScopeResolver` | `DirectoryScopeResolver` | caller + principal -> ordered scope chain with notes. Raises `AccessDenied`. |
| `CandidateRetriever` | `VectorRetriever` | items valid now in the chain, with cosine similarity (hnswlib ANN, brute-force fallback) and graph centrality. Reports items beyond top-k. |
| `Ranker` | `WeightedRanker` | similarity x confidence x centrality x freshness x personal boost; optional similarity floor. Deterministic tie-break by id. |
| `Budgeter` | `GreedyBudgeter` | greedy fill; the first item that does not fit is compressed (LLM summary if configured, else truncation) or dropped. Never exceeds the budget. |
| `BundleCache` | `ScopedBundleCache` | partition by exact key, similarity within a partition, validate on hit. |

Enrichments fail open: if the ANN index, the graph or the LLM fails, the request continues with brute-force similarity, neutral centrality or truncation. Authorization fails closed.

## Data model

- **Directory** ([kairos/directory.py](../kairos/directory.py)): `orgs`, `teams`, `people`, `projects`, `sessions`. Loaded from outside; read for every decision.
- **Knowledge** ([kairos/models.py](../kairos/models.py)): `knowledge_items` (layer, scope, content, embedding, confidence, staleness hint, origin, `knowledge_version`, `valid_from`/`valid_until`, `superseded_by_id`), `knowledge_graph_edges`, `candidate_knowledge` (proposals).
- **Access**: `tenant_configs` (tenant -> org, SSO settings), `api_keys` (hash, role, optional `person_id`).
- **Operational**: `cache_entries` (partition key, task embedding, bundle, contributing item hashes, expiry), `audit_ledger`.

## Decisions

**Identity comes from the credential.** 0.1.0 trusted `user_id`, `session_id` and `repo` from the request body and used tenant-wide keys, so any key could resolve to anyone's scopes. The leak probe measured 5 of 9 attack requests receiving forbidden items. Binding credentials to people, and deriving team and project from the directory, removes the class of bug instead of filtering its symptoms. Alternative considered: keep tenant-wide keys and add per-request checks of `user_id` against a header set by a gateway. Rejected as the default because it moves the security decision to every integrator; it remains possible with an unbound admin key.

**Cache key = everything that determines the bundle.** The alternative, keying on scopes plus a per-scope version counter (0.1.0), missed in-place edits and expiry and could not express "same readable knowledge". Hashing the id and version of every readable item costs one light query per request and makes equivalence exact. The hit-time content-hash check covers writes that bypass the ORM.

**Cross-user reuse is opt-in and conservative.** Sharing across users is only safe when the bundle would be identical for both, which is only guaranteed when neither has private items in play. A looser design (share bundles that happen to contain no private items) would be leak-free but would silently omit the second user's own notes. Measured gain of the conservative rule on the benchmark: hit rate 0.21 to 0.29.

**One cache, not two.** 0.1.0 also had a retrieval cache that reused candidates, with similarity scores computed for a different query, across users of a project. It saved little and ranked with the wrong scores. Removed.

**Exact budget in the key.** Bucketing budgets let a bundle built for 1,400 tokens be served to a 600-token request.

**Unified ranking, not per-layer quotas.** Fixed quotas (say 20% org, 40% project) force irrelevant items into a bundle to fill a layer's share. One score across layers, with the score decomposed into factors so a decision can be explained. The benchmark shows the extra factors are not yet earning their place (BENCH-002); they are kept because they are cheap and inspectable, and the result is reported.

**Bi-temporal facts.** Superseding sets `valid_until` rather than deleting, so history survives and "what did the agent see on Tuesday" stays answerable from the audit ledger and the items.

**Human approval for agent proposals.** An agent picking up a joke or a hypothetical as a fact would be served to everyone in the scope. Proposals wait for an admin.

**Graph edges on write.** 0.1.0 compared every pair of items in each scope on every read (and wrote edges on the read path). Linking a new item to its scope at write time is O(n) per write and keeps reads read-only.

**Synchronous SQLAlchemy.** Embedding dominates latency (3.8 of 5.9 ms median on SQLite); async database access would not change that and complicates tests.

**No vendor SDK dependency for the optional LLM.** Two small HTTP calls behind a protocol; off unless explicitly selected, because it sends knowledge to a third party.
