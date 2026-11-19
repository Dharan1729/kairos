# Changelog

## 0.2.0

First public release. The cache and access model were rebuilt after an adversarial probe showed the previous version leaking context across users and teams and serving stale bundles (see [docs/benchmarks.md](docs/benchmarks.md), BENCH-001).

**Security**

- **Credentials act as one person.** API keys can be bound to a directory person (`person_id`); SSO tokens are bound through a configurable claim (`sso_user_claim`, default `sub`). A request naming another user is refused. Unbound non-admin keys see the org layer only.
- **Scope resolution fails closed.** The team is the caller's own team; a project counts only if that team owns it; a session counts only if it is the caller's (someone else's is refused); unknown users get the org layer. Repo lookups are limited to the caller's org. Responses list the resolved scopes and a note for anything left out.
- **Scope-partitioned bundle cache.** The partition key covers tenant, scope chain, identity, the id and version of every readable item, budget and embedder. Hits are re-validated item by item (exists, valid now, readable, same content hash). Cross-user reuse is a switch (`KAIROS_CACHE_CROSS_USER_REUSE`, off by default) and never shares personal or session knowledge.
- **Write rules.** Org-layer writes need an admin; members write to their own team's scopes and their own personal and session scopes; the layer must match the scope's kind. Proposals follow the same rules; reading or rejecting another scope's proposal returns 404.
- Git extraction over the API is admin-only and limited to `KAIROS_GIT_EXTRACT_ROOTS`.
- LLM compression is opt-in (`KAIROS_LLM_PROVIDER`); an API key in the environment no longer turns it on.
- `/readyz` no longer returns database error text.

**Fixes**

- In-place edits and expiry no longer leave cached bundles or ANN indexes looking current. 0.1.0 returned HTTP 500 for an uncached request after an item expired.
- The budget is part of the cache key; 0.1.0 could serve a bundle built for a larger budget to a smaller one.
- Removed the retrieval cache, which reused similarity scores computed for a different query.
- Graph edges are created when an item is written, not on every read.
- Default cache similarity threshold raised from 0.75 to 0.90: at 0.75 every paraphrase hit in the benchmark served a different bundle than a fresh assembly.

**Structure**

- One core function (`kairos.pipeline.ContextService`) with explicit, swappable stages: scope resolver, retriever, ranker, budgeter, cache. The HTTP API, MCP server and CLI are thin adapters.
- Session-layer knowledge is retrieved (0.1.0 never included the session scope).
- `POST /v1/knowledge_items/{id}/revise` replaces a fact the bi-temporal way.
- `dropped` entries carry a `reason` (`budget`, `relevance`, `retrieval_limit`).
- Models use SQLAlchemy 2.0 typed mappings; mypy runs with `check_untyped_defs`.
- Migration `2b7c1e0d4a91`. The bundle cache table is recreated (cached bundles are discarded).

**Usability**

- `kairos quickstart`: seed a local SQLite demo, show a bundle, serve the API. `kairos try` assembles locally without a server. `kairos reembed`.
- Local state lives in `$KAIROS_HOME` (default `./.kairos`); dev keys are written there with mode 600.
- Docker Compose ports are configurable and bound to 127.0.0.1; the image uses CPU-only PyTorch and runs as a non-root user.
- Postgres driver is psycopg 3 (`pip install ".[postgres]"`).

**Evidence**

- Benchmarks with committed results: leak probe (run against 0.1.0 and 0.2.0), context-selection quality against four baselines, cache hit rate and fidelity, latency on SQLite and Postgres.

## 0.1.0

Private prototype: layered knowledge store, context budgeting, two semantic caches, FastAPI service, MCP server, CLI, RBAC, SSO, rate limiting, tracing.
