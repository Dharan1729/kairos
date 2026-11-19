# Configuration reference

Kairos reads environment variables on each request (no restart needed for most; the database URL and tracing are read at startup).

## Storage

| Variable | Default | |
|---|---|---|
| `KAIROS_HOME` | `./.kairos` | Local state: the default SQLite file and `dev_keys.json`. |
| `KAIROS_DATABASE_URL` | `sqlite:///$KAIROS_HOME/kairos.db` | Any SQLAlchemy URL. Postgres: `postgresql://user:pass@host:5432/db` (needs `pip install ".[postgres]"`). Alembic uses the same value. |

## Embeddings

| Variable | Default | |
|---|---|---|
| `KAIROS_EMBEDDER` | `sentence-transformers` | Or `hashing`: no download, no semantic matching (smoke tests only). |
| `KAIROS_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Any sentence-transformers model. After changing the embedder or model, run `kairos reembed`. |
| `HF_HUB_OFFLINE` | unset | Set to `1` to forbid model downloads once the model is cached. |

## Assembly and cache

| Variable | Default | |
|---|---|---|
| `KAIROS_MAX_BUDGET_TOKENS` | 32000 | Requests with `budget_tokens` outside 1..this are rejected (422). |
| `KAIROS_CACHE_ENABLED` | `true` | Bundle cache on or off. |
| `KAIROS_CACHE_SIMILARITY` | 0.90 | Minimum task-embedding similarity for a hit, within a partition. See [caching](caching.md#the-similarity-threshold). |
| `KAIROS_CACHE_TTL_SECONDS` | 86400 | Entry lifetime. 0 = no expiry (entries still become unreachable when knowledge changes). |
| `KAIROS_CACHE_CROSS_USER_REUSE` | `false` | Let users without private items share bundles. See [caching](caching.md#cross-user-reuse). |

## Optional LLM summaries

Off unless both are set. Turning this on sends knowledge content to the provider.

| Variable | Default | |
|---|---|---|
| `KAIROS_LLM_PROVIDER` | `none` | `anthropic` or `openai`. Used to compress the last item that does not fit, and by `kairos watch` to summarise new commits. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | | The selected provider's key. |
| `KAIROS_LLM_MODEL` | `claude-haiku-5-5` / `gpt-4o-mini` | Model override. |

## Server

| Variable | Default | |
|---|---|---|
| `KAIROS_RATE_LIMIT` | `120/minute` | Per tenant (per IP before a credential is known). Read at startup. |
| `KAIROS_GIT_EXTRACT_ROOTS` | unset (endpoint off) | `:`-separated directories under which `POST /v1/extract/git` may read repositories on the server. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (tracing off) | OTLP/HTTP collector, e.g. `http://127.0.0.1:4318`. |

`kairos serve --host 127.0.0.1 --port 8000` binds to loopback by default.

## Clients (CLI and MCP server)

| Variable | Default | |
|---|---|---|
| `KAIROS_URL` | `http://127.0.0.1:8000` | Server URL. |
| `KAIROS_API_KEY` | admin key (CLI) / demo_user key (MCP) from `dev_keys.json` | Credential. |
| `KAIROS_DEFAULT_REPO` | none | MCP: repo sent when a tool call gives none. |
| `KAIROS_BUDGET_TOKENS` | 3000 | MCP: budget for `get_context`. |

## Per-tenant settings (database)

`tenant_configs` holds SSO settings per tenant: `sso_issuer`, `sso_audience`, `sso_jwks_url` or `sso_jwks_static`, `sso_role_claim` (default `kairos_role`), `sso_default_role` (default `viewer`), `sso_user_claim` (default `sub`). See [deployment](deployment.md#sso).

## Docker Compose

| Variable | Default | |
|---|---|---|
| `KAIROS_HTTP_PORT` | 8000 | Host port for the API, bound to 127.0.0.1. |
| `KAIROS_PG_PORT` | 55432 | Host port for Postgres, bound to 127.0.0.1. |
| `KAIROS_PG_PASSWORD` | `kairos-local-only` | Change it for anything but local use. |
